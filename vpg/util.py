"""Small shared helpers: hashing, clock, atomic JSON, dotted state paths."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

PKG_ROOT = Path(__file__).resolve().parents[1]


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NotRegularFile(OSError):
    """A path that should be a regular file is a directory, pipe, socket or device."""


class ConcurrentChange(RuntimeError):
    """The run's state or ledger changed on disk since this engine last read or wrote it: another process changed the run.
    Writing now would lose that change or break the ledger's hash chain, so the engine refuses; reopen the run."""


def file_identity(path: str | os.PathLike) -> tuple[int, int, int] | None:
    """(inode, size, mtime_ns) of a file, or None when it doesn't exist. Cheap enough to check before every write."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


def _regular_fd(path: str | os.PathLike, flags: int) -> int:
    """Open without ever blocking, then insist on a regular file (the type is checked on the open descriptor, so a file
    swapped for a named pipe between a check and the open can't hang the run or its project lock)."""
    if os.path.lexists(path) and not os.path.isfile(path):
        raise NotRegularFile(f"{path} is not a regular file (a directory, pipe, socket or device)")
    fd = os.open(path, flags | getattr(os, "O_NONBLOCK", 0), 0o644)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFile(f"{path} is not a regular file (a directory, pipe, socket or device)")
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_regular(path: str | os.PathLike, mode: str = "rb"):
    """Read a regular file ("rb" or "r", UTF-8 with universal newlines). Anything else raises NotRegularFile."""
    fd = _regular_fd(path, os.O_RDONLY)
    return os.fdopen(fd, mode) if "b" in mode else os.fdopen(fd, mode, encoding="utf-8")


def open_append(path: str | os.PathLike):
    """Append to a regular file, creating it if needed. A pipe or device raises NotRegularFile instead of blocking."""
    return os.fdopen(_regular_fd(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT), "a", encoding="utf-8")


def read_text(path: str | os.PathLike) -> str:
    with open_regular(path, "r") as f:
        return f.read()


def is_human_name(actor: str | None, dry_run_actor: str = "dry-run-auto") -> bool:
    """A named human: not empty, not an agent (`agent:…` in any case) and not a dry-run actor."""
    a = (actor or "").strip().lower()
    return bool(a) and not a.startswith("agent") and not a.startswith("dry-run") and a != dry_run_actor.strip().lower()


def sha256_file(path: str | os.PathLike) -> str:
    """Hash a regular file. Anything else raises NotRegularFile instead of hanging."""
    h = hashlib.sha256()
    with open_regular(path) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj: Any) -> str:
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json", by_alias=True)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical(obj).encode())


def read_json(path: str | os.PathLike) -> Any:
    return json.loads(read_text(path))


def write_json_atomic(path: str | os.PathLike, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, BaseModel):
        text = obj.model_dump_json(indent=1, by_alias=True)
    else:
        text = json.dumps(obj, indent=1, ensure_ascii=False, default=str)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text + "\n")
    os.replace(tmp, path)


def get_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, BaseModel):
            cur = getattr(cur, part)
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def set_path(obj: Any, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = getattr(cur, part) if isinstance(cur, BaseModel) else cur[part]
    last = parts[-1]
    if isinstance(cur, BaseModel):
        setattr(cur, last, value)
    else:
        cur[last] = value


def model_has_path(model: type[BaseModel], dotted: str) -> bool:
    """True when `dotted` names a field path in a pydantic model (dict fields accept any sub-key)."""
    cur: Any = model
    for part in dotted.split("."):
        if isinstance(cur, type) and issubclass(cur, BaseModel):
            field = cur.model_fields.get(part)
            if field is None:
                return False
            cur = _inner_type(field.annotation)
        else:
            return True  # inside a dict/Any field: free-form keys
    return True


def _inner_type(annotation: Any) -> Any:
    args = getattr(annotation, "__args__", None)
    origin = getattr(annotation, "__origin__", None)
    if origin in (dict, list):
        return dict
    if args:
        for a in args:
            if isinstance(a, type) and issubclass(a, BaseModel):
                return a
        return dict
    return annotation


def rel(path: str | os.PathLike, root: str | os.PathLike) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


def glob_files(root: Path, pattern: str) -> list[Path]:
    """Files matching a project-relative pattern, sorted. A trailing `**` means every file below (pathlib's `**` alone
    matches directories only)."""
    if pattern.endswith("**"):
        pattern += "/*"
    return sorted(p for p in Path(root).glob(pattern) if p.is_file())
