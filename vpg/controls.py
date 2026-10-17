"""Control documents: product.md, creative.md, design.md, production.md, evaluation.md, memory.md.

Each document has YAML front-matter (the machine contract that tools read) and a Markdown body (the human contract).
The engine enforces three rules:
  1. A node may change a control document only if the document is in the node's `controls.write` list.
  2. An approved document is locked. It can change only through an approved change request. Otherwise the edit is
     rejected and the approved snapshot is restored.
  3. Approval records the document hash and keeps a restorable snapshot in `.vpg/controls/<doc>@v<n>.md`.
Every accepted version is also kept by hash (`.vpg/controls/<doc>@<sha256[:16]>.md`), so a revert restores exactly the
version the saved state accepted, even after a crash between accepting an edit and saving the state.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from .models import ChangeRequest, ControlDocState, NodeDef, ProjectState
from .util import now, read_text, sha256_file

CONTROL_DOCS = ["product", "creative", "design", "production", "evaluation", "memory"]

# Required body sections per document (the brief's §6 lists).
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "product": ["Product truth", "User problem", "Audience", "Value proposition", "Capabilities", "Required interactions",
                "Demo scenarios", "Claims that must be verified", "Accuracy constraints", "Content that must not be invented"],
    "creative": ["Creative concept", "Narrative structure", "Emotional progression", "Storytelling devices", "Tone",
                 "Pacing", "Audience experience", "Creative boundaries"],
    "design": ["Visual language", "Layout system", "Typography", "Color", "Composition", "Illustration principles",
               "Motion behavior", "Accessibility", "Legibility", "Brand constraints", "Approved and prohibited patterns"],
    "production": ["Technical pipeline", "Tools and models", "Asset specifications", "Scene structure",
                   "Resolution and frame rate", "Audio requirements", "Naming conventions", "Folder structure",
                   "Rendering rules", "Assembly instructions", "Delivery formats"],
    "evaluation": ["Evaluation dimensions", "Quality gates", "Scoring rubrics", "Approval thresholds", "Blocking defects",
                   "Human-review checkpoints", "Rework and escalation rules"],
    "memory": ["Reusable lessons", "Stable design preferences", "Successful production patterns", "Known failure modes",
               "Tool-specific learnings", "Changes to future default behavior"],
}

PLACEHOLDER = re.compile(r"\bTBD\b|<fill[^>]*>|\{\{[^}]+\}\}")


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    m = re.search(r"\n---\s*\n", text[3:])
    if not m:
        return {}, text
    fm = yaml.safe_load(text[3:3 + m.start()]) or {}
    return fm, text[3 + m.end():]


def sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current, buf = None, []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def missing_sections(doc: str, text: str) -> list[str]:
    _, body = split_front_matter(text)
    have = sections(body)
    return [s for s in REQUIRED_SECTIONS[doc] if s not in have]


def placeholder_sections(doc: str, text: str) -> list[str]:
    _, body = split_front_matter(text)
    have = sections(body)
    return [s for s in REQUIRED_SECTIONS[doc] if s in have and (not have[s].strip() or PLACEHOLDER.search(have[s]))]


class IntegrityViolation(Exception):
    pass


class ControlStore:
    def __init__(self, project_dir: Path, state: ProjectState, vpg_dir: Path):
        self.dir = Path(project_dir) / "controls"
        self.vpg = Path(vpg_dir)
        self.state = state

    def path(self, doc: str) -> Path:
        return self.dir / f"{doc}.md"

    def front_matter(self, doc: str) -> dict[str, Any]:
        p = self.path(doc)
        return split_front_matter(read_text(p))[0] if p.exists() else {}

    def register_all(self) -> None:
        for doc in CONTROL_DOCS:
            p = self.path(doc)
            st = self.state.controls.get(doc) or ControlDocState(doc=doc, path=str(p.relative_to(self.dir.parent)))
            st.sha256 = sha256_file(p) if p.exists() else None
            self.state.controls[doc] = st

    def hashes(self) -> dict[str, str | None]:
        return {doc: (sha256_file(self.path(doc)) if self.path(doc).exists() else None) for doc in CONTROL_DOCS}

    def _open_cr(self, doc: str) -> ChangeRequest | None:
        for cr in self.state.history.change_requests:
            if cr.doc == doc and cr.status == "approved":
                return cr
        return None

    def accepted_copy(self, doc: str) -> Path:
        return self.vpg / "controls" / f"{doc}.accepted.md"

    def version_copy(self, doc: str, sha: str) -> Path:
        return self.vpg / "controls" / f"{doc}@{sha[:16]}.md"

    def _accept(self, doc: str) -> None:
        st = self.state.controls[doc]
        st.sha256 = sha256_file(self.path(doc))
        self.accepted_copy(doc).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path(doc), self.accepted_copy(doc))
        if not self.version_copy(doc, st.sha256).exists():
            shutil.copy2(self.path(doc), self.version_copy(doc, st.sha256))

    def _accepted_source(self, doc: str) -> Path | None:
        """The copy of the version the state accepted. The latest copy can be ahead of the saved state after a crash."""
        sha = self.state.controls[doc].sha256
        for cand in ([self.version_copy(doc, sha)] if sha else []) + [self.accepted_copy(doc)]:
            if cand.exists() and sha256_file(cand) == sha:
                return cand
        return None

    def accept_all(self) -> None:
        for doc in CONTROL_DOCS:
            if self.path(doc).exists():
                self._accept(doc)

    def check_changes(self, node: NodeDef, others_inflight: list[str] | tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
        """Attribute every control-document change since the last accepted version, and revert violations.

        Returns (violations charged to this node, documents whose editor cannot be identified). A change is accepted
        when the completing node may write the document and, if the document is approved, an approved change request
        exists. A control-document writer always runs alone (Engine.select_batch), so a change this node may not make
        is charged to it only when nothing else was in flight; otherwise the edit is reverted and reported as
        unattributable, never charged to a sibling that may be innocent.
        """
        violations, ambiguous = [], []
        for doc in CONTROL_DOCS:
            p = self.path(doc)
            st = self.state.controls[doc]
            if not p.is_file():   # deleted, or replaced by a directory, pipe or device
                if st.sha256 is None and not os.path.lexists(p):
                    continue       # never accepted and still absent
                restored = self._recreate(doc)
                what = f"deleted controls/{doc}.md or replaced it with something that is not a regular file"
                if others_inflight:
                    ambiguous.append(doc)
                else:
                    violations.append(f"{node.id} {what} ({'restored' if restored else 'could not restore it: see the integrity block'})")
                continue
            cur = sha256_file(p)
            if cur == st.sha256:
                continue
            if doc not in node.controls.write:
                self._revert(doc)
                if others_inflight:
                    ambiguous.append(doc)
                else:
                    violations.append(f"unauthorized control change: {node.id} may not edit {doc}.md (reverted)")
                continue
            if st.status == "approved":
                cr = self._open_cr(doc)
                if cr is None:
                    violations.append(f"approved {doc}.md changed without an approved change request (reverted)")
                    self._revert(doc)
                    continue
                cr.status = "applied"
                st.status = "draft"
                st.version += 1
            self._accept(doc)
        return violations, ambiguous

    def _revert(self, doc: str) -> None:
        src = self._accepted_source(doc)
        if src is not None:
            shutil.copy2(src, self.path(doc))
            if src != self.accepted_copy(doc):   # the latest copy was ahead of the state: bring it back in line
                shutil.copy2(src, self.accepted_copy(doc))

    def put_back(self, doc: str, keep: Path | None) -> None:
        """Undo a restore: the kept text goes back (a document that was missing is removed again), and the accepted
        copy follows the state's accepted version."""
        if keep is None:
            if self.path(doc).is_file():
                self.path(doc).unlink()
        else:
            shutil.copy2(keep, self.path(doc))
        src = self._accepted_source(doc)
        if src is not None and src != self.accepted_copy(doc):
            shutil.copy2(src, self.accepted_copy(doc))

    def restore_one(self, doc: str, tag: str, before_change=None) -> tuple[bool, Path | None]:
        """Put one document back to the version the state accepted, recreating it if it was deleted or replaced by a
        special file. Text that was there is kept as `.vpg/controls/<doc>.<tag>[-n].md`. `before_change(kept)` runs
        before the document is touched, so an undo is registered even if the copy fails partway.
        Returns (restored, kept copy or None)."""
        p, accepted = self.path(doc), self.state.controls[doc].sha256
        if accepted is None or (p.is_file() and sha256_file(p) == accepted):
            return False, None
        if p.is_dir() and not p.is_symlink():
            raise IntegrityViolation(f"controls/{doc}.md is a directory; move it out of the way, then restore the document")
        src = self._accepted_source(doc)
        if src is None:
            raise IntegrityViolation(f"no copy of the accepted {doc}.md is kept in .vpg/controls/ (they may have been deleted); "
                                     f"restore it from a backup")
        keep = None
        if p.is_file():
            keep, n = self.vpg / "controls" / f"{doc}.{tag}.md", 1
            while keep.exists():   # never overwrite text kept earlier
                n += 1
                keep = self.vpg / "controls" / f"{doc}.{tag}-{n}.md"
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, keep)
        if before_change:
            before_change(keep)
        if os.path.lexists(p) and not p.is_file():
            p.unlink()
        shutil.copy2(src, p)
        if src != self.accepted_copy(doc):
            shutil.copy2(src, self.accepted_copy(doc))
        return True, keep

    def restore_to_state(self, tag: str, on_restored=None) -> list[tuple[str, Path]]:
        """Put every document back to the version the state accepted (after a rollback). `on_restored(doc, kept)` runs
        right after each one, so a failure partway can still undo the documents already restored."""
        out = []
        for doc in CONTROL_DOCS:
            restored, keep = self.restore_one(doc, tag, before_change=(lambda k, d=doc: on_restored(d, k)) if on_restored else None)
            if restored:
                out.append((doc, keep))
        return out

    def undo_interrupted(self, doc: str, keep: Path) -> bool:
        """Roll a document back to its accepted version because its writer was interrupted and will re-run from scratch.
        The discarded text is kept at `keep`. Returns False when there was nothing to undo or no accepted copy."""
        p = self.path(doc)
        if not p.is_file() or sha256_file(p) == self.state.controls[doc].sha256 or self._accepted_source(doc) is None:
            return False   # a missing or special file is left to the integrity check, which blocks with a message
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, keep)
        self._revert(doc)
        return True

    def unaccounted(self, inflight_writers: set[str]) -> list[str]:
        """Changes since the last accepted version that no in-flight node may account for (edits outside the workflow).
        A deleted accepted document counts: every later check would otherwise read an empty document and pass."""
        out = []
        for doc in CONTROL_DOCS:
            if doc in inflight_writers:
                continue
            p, accepted = self.path(doc), self.state.controls[doc].sha256
            if not p.is_file():
                if accepted is not None or os.path.lexists(p):
                    out.append(doc)
            elif sha256_file(p) != accepted:
                out.append(doc)
        return out

    def _recreate(self, doc: str) -> bool:
        """Put the accepted version back where the document was deleted or replaced by a special file. A directory is
        never deleted; the integrity check then reports it."""
        p = self.path(doc)
        if p.is_dir() and not p.is_symlink():
            return False
        src = self._accepted_source(doc)
        if src is None:
            return False
        if os.path.lexists(p):
            p.unlink()   # a pipe, socket, device or dangling link
        shutil.copy2(src, p)
        return True

    def approve(self, doc: str, actor: str) -> ControlDocState:
        st = self.state.controls[doc]
        st.status = "approved"
        st.version = max(st.version, 1) if st.approved_sha256 is None else st.version
        st.approved_sha256 = sha256_file(self.path(doc))
        st.sha256 = st.approved_sha256
        st.approved_by = actor
        st.approved_at = now()
        snap = self.vpg / "controls" / f"{doc}@v{st.version}.md"
        snap.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path(doc), snap)
        st.snapshot = str(snap.relative_to(self.dir.parent))
        self._accept(doc)
        return st

    def restore_approved(self, doc: str) -> None:
        st = self.state.controls[doc]
        if st.snapshot:
            shutil.copy2(self.dir.parent / st.snapshot, self.path(doc))
            self._accept(doc)
