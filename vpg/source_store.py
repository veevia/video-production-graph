"""Frozen-source provenance: a content-addressed copy of each registered source, and the check that the files still match.

When the inputs freeze, each local source that still matches its registered hash is copied, read-only, to
`.vpg/sources/<sha256>`, so the registered version survives an edit to its file, including one made through a hard link
from another project. `warnings()` re-checks every frozen source: missing, changed, a hard link or a symbolic link.

A file is hashed again only when its stat identity (inode, size, modification and change time) differs from the one last
verified. A writable engine keeps those records in `.vpg/sources/verified.json`, so `vpg status` stays fast on large
recordings. A file changed in the last RACY_S seconds is hashed but not recorded, because on a file system with coarse
timestamps a second edit within the same tick would keep its identity (git's "racy clean" rule). The records only save
work: copies are verified on the bytes copied, and N23 re-hashes every frozen source.
The store writes only a comment-only `.gitignore` note. Source exclusions come from the workspace root policy, not from
a nested blanket rule.
Warnings name a source by its id, never its path, because a file name can carry a person's name."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Iterable

from .models import SourceRef
from .tools.sources import REMOTE_KINDS
from .util import open_regular, read_json, rel, write_json_atomic

SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def linked_part(root: Path, path: Path) -> Path | None:
    """The first symbolic link on the way from the project root down to `path`, the file included (None if there is none)."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:   # outside the project: only the file itself can be checked
        return path if path.is_symlink() else None
    here = root
    for part in parts:
        here = here / part
        if here.is_symlink():
            return here
    return None


class SourceStore:
    RACY_S = 2.0   # a file changed this recently is hashed but not recorded

    def __init__(self, root: Path, vpg: Path):
        self.root, self.dir = Path(root), Path(vpg) / "sources"
        self.records = self.dir / "verified.json"
        try:
            raw = read_json(self.records)
            self._verified = {k: v for k, v in raw.items() if isinstance(v, list) and len(v) == 5} if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            self._verified = {}
        self._dirty = False

    @staticmethod
    def _identity(st: os.stat_result) -> list[int]:
        return [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

    def digest(self, path: Path) -> str:
        """sha256 of the file's content (following a symbolic link), hashed again only when its stat identity changed.
        A file that changes while it is read is hashed, but its record is not kept."""
        key, ident = rel(path, self.root), self._identity(os.stat(path))
        known = self._verified.get(key)
        if known and known[:4] == ident:
            return known[4]
        h = hashlib.sha256()
        with open_regular(path) as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        after = os.stat(path)
        if self._identity(after) == ident and time.time() - max(after.st_mtime, after.st_ctime) >= self.RACY_S:
            self._verified[key], self._dirty = ident + [h.hexdigest()], True
        return h.hexdigest()

    def _ensure_dir(self) -> None:
        """Create the store and a comment-only `.gitignore` note if it is missing."""
        self.dir.mkdir(parents=True, exist_ok=True)
        ignore = self.dir / ".gitignore"
        if not os.path.lexists(ignore):
            ignore.write_text(
                "# Source snapshots may be versioned when they contain no credentials.\n"
                "# Secret exclusions are owned by the repository root policy.\n"
                "# This file intentionally contains no active ignore rules.\n"
            )

    def _record(self, path: Path, sha256: str) -> None:
        """Record a copy the engine itself just wrote and verified, under the project lock (exempt from RACY_S)."""
        self._verified[rel(path, self.root)], self._dirty = self._identity(os.stat(path)) + [sha256], True

    def copy_of(self, sha256: str | None) -> Path | None:
        """Where the copy of a registered version lives. A malformed hash never names a path."""
        return self.dir / sha256 if SHA256_HEX.fullmatch(sha256 or "") else None

    def has_copy(self, sha256: str | None) -> bool:
        """A regular file at the copy's path whose content is the registered version (a torn or edited copy is not)."""
        copy = self.copy_of(sha256)
        try:
            return copy is not None and stat.S_ISREG(os.lstat(copy).st_mode) and self.digest(copy) == sha256
        except OSError:
            return False

    @staticmethod
    def _local(sources: Iterable[SourceRef]) -> list[SourceRef]:
        return [s for s in sources if s.kind not in REMOTE_KINDS and s.sha256]

    def keep(self, sources: Iterable[SourceRef]) -> list[str]:
        """Copy each local source that still matches its registered hash and has no intact copy yet; return their ids.
        Only a writable engine calls this, under the project lock, so a leftover `.part` file is a crashed copy (and holds
        source content): it is removed first."""
        local, kept = self._local(sources), []
        if self.dir.is_dir():
            self._ensure_dir()
            for part in self.dir.glob(".*.part"):
                part.unlink(missing_ok=True)
        for src in local:
            copy = self.copy_of(src.sha256)
            if copy is None or self.has_copy(src.sha256):
                continue
            tmp = None
            try:
                path = self.root / src.path
                if self.digest(path) != src.sha256:
                    continue   # it drifted before a copy was made: there is nothing left to keep
                self._ensure_dir()
                h = hashlib.sha256()
                with open_regular(path) as fin:
                    fd, name = tempfile.mkstemp(dir=self.dir, prefix=f".{src.sha256[:16]}.", suffix=".part")
                    tmp = Path(name)
                    with os.fdopen(fd, "wb") as fout:
                        for chunk in iter(lambda: fin.read(1 << 20), b""):
                            h.update(chunk)
                            fout.write(chunk)
                        fout.flush()
                        os.fsync(fout.fileno())
                if h.hexdigest() != src.sha256:   # it changed while it was copied
                    continue
                os.chmod(tmp, 0o444)
                os.replace(tmp, copy)   # also replaces a damaged copy
                tmp = None
                self._record(copy, src.sha256)
                kept.append(src.id)
            except OSError:   # missing, unreadable or not a regular file: warnings() names it
                pass
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
        wanted = ({rel(self.root / src.path, self.root) for src in local}
                  | {rel(self.dir / src.sha256, self.root) for src in local if self.copy_of(src.sha256)})
        stale = [k for k in self._verified if k not in wanted]
        for k in stale:
            del self._verified[k]
        if self._dirty or stale:
            self._ensure_dir()
            write_json_atomic(self.records, self._verified)
            self._dirty = False
        return kept

    def warnings(self, sources: Iterable[SourceRef]) -> list[str]:
        """Frozen local sources whose file is missing, unreadable, changed since the freeze, a hard link or a symbolic link."""
        out = []
        for src in self._local(sources):
            path, kept = self.root / src.path, self.has_copy(src.sha256)
            where = (f"its registered version is kept at .vpg/sources/{src.sha256}" if kept
                     else "no intact copy of its registered version exists")
            try:
                link = linked_part(self.root, path)
                if link is not None:
                    where_link = "is a symbolic link" if link == path else "is inside a symbolically linked directory"
                    out.append(f"source {src.id} {where_link}, so an edit to what it points to changes it; replace it with a copy")
                digest, links = self.digest(path), os.stat(path).st_nlink
            except FileNotFoundError:
                out.append(f"source {src.id} is missing; {where}")
                continue
            except OSError as exc:
                out.append(f"source {src.id} could not be read ({type(exc).__name__}); {where}")
                continue
            if digest != src.sha256:
                fix = ("Restore it: delete its file first, so a hard link cannot carry the restore into another project's "
                       "file, then copy the kept version to its path (listed under inputs.sources in .vpg/state.json)"
                       if kept else "Put the registered version back")
                out.append(f"source {src.id} changed after the inputs were frozen; {where}. {fix}, or a human re-opens "
                           "intake (`vpg invalidate … n01_context_intake`) to register the new version")
            if links > 1:
                out.append(f"source {src.id} is a hard link ({links} paths share it), so an edit made through another path "
                           "rewrites it; replace it with a copy (delete the file, then copy it back)")
        return out
