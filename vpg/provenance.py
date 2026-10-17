"""Provenance: an append-only, hash-chained event ledger per production run.

Every state transition (dispatch, submission, validation, gate decision, retry, invalidation, approval,
escalation, checkpoint, rollback) is appended as one JSON line. Each line carries the hash of the previous
line, so an edit or a deletion inside the chain is detectable with `vpg ledger-verify`. Every state save also
records the last event (seq and hash); a ledger that no longer contains that event lost events at its end, or was
replaced, and `vpg ledger-verify` and N23 refuse it.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import ConcurrentChange, canonical, file_identity, now, open_append, open_regular, sha256_bytes

GENESIS = "0" * 64


class LedgerCorrupt(ValueError):
    """A complete ledger line is not a valid event (edited, or damaged outside vpg)."""


class LedgerBehind(RuntimeError):
    """The state was saved but its ledger events could not be written yet. They are kept in the state (write-ahead)
    and written by the next command that changes the run, so the ledger catches up without losing or duplicating an event."""


class Ledger:
    def __init__(self, path: Path, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only
        self._pending: list[str] | None = None   # lines collected by deferred(), not yet on disk
        self.stamp: dict[str, Any] = {}          # merged into every event while set (a granted action's grant and grantor)
        self._behind = False
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._last = self._tail()
        except LedgerCorrupt:
            if not read_only:
                raise
            self._last = (0, GENESIS)   # a read-only ledger never appends; verify() reports the damage
        self._known = file_identity(self.path)

    # ------------------------------------------------------------------ reading
    def _lines(self) -> Iterator[tuple[int, str]]:
        """Complete lines with their 1-based numbers. A last line without its newline is a torn append and is skipped."""
        if not self.path.exists():
            return
        with open_regular(self.path, "r") as f:
            for n, line in enumerate(f, 1):
                if line.strip() and line.endswith("\n"):
                    yield n, line

    @staticmethod
    def _parse(n: int, line: str) -> dict:
        try:
            ev = json.loads(line)
            ev["seq"], ev["hash"]
            return ev
        except (ValueError, KeyError, TypeError) as exc:
            raise LedgerCorrupt(f"ledger line {n} is not a valid event ({exc.__class__.__name__}); it was edited or damaged "
                                "outside vpg. `vpg ledger-verify` reports it; restore the ledger from a backup") from None

    def _tail(self) -> tuple[int, str]:
        last = None
        for n, line in self._lines():
            last = (n, line)
        if last is None:
            return 0, GENESIS
        ev = self._parse(*last)
        return ev["seq"], ev["hash"]

    def events(self, type_: str | None = None) -> Iterator[dict]:
        out = [ev for ev in (self._parse(n, line) for n, line in self._lines()) if type_ is None or ev["type"] == type_]
        return iter(out)

    # ------------------------------------------------------------------ writing
    def _set_aside_torn_line(self) -> None:
        """A write cut short leaves a last line without its newline. Move that fragment to a side file before appending,
        so the next event is never glued onto it (the chain continues from the last complete line)."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        with open(self.path, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) == b"\n":
                return
            f.seek(0)
            data = f.read()
            cut = data.rfind(b"\n") + 1
            side = self.path.with_name(f"{self.path.name}.torn-{int(time.time() * 1000)}")
            side.write_bytes(data[cut:])
            f.truncate(cut)

    def _write(self, text: str) -> None:
        """Append to the ledger file only if it is exactly as this ledger last saw it (see util.ConcurrentChange)."""
        if file_identity(self.path) != self._known:
            raise ConcurrentChange(f"{self.path} changed since this command opened the run (another vpg process); "
                                   "nothing was written. Retry the command")
        self._set_aside_torn_line()
        with open_append(self.path) as f:
            f.write(text)
        self._known = file_identity(self.path)

    def append(self, type_: str, **data: Any) -> dict:
        if self.read_only:
            raise PermissionError(f"read-only ledger: refused to append '{type_}'")
        if self._behind:
            raise LedgerBehind("the ledger is behind the saved state; the next command that changes the run (for example "
                               "`vpg run`) writes the pending events")
        seq, prev = self._last
        if self.stamp:
            data = {**data, **{k: v for k, v in self.stamp.items() if k not in data}}
        body = {"seq": seq + 1, "ts": now(), "type": type_, "data": data, "prev": prev}
        body["hash"] = sha256_bytes((prev + canonical({k: body[k] for k in ("seq", "ts", "type", "data")})).encode())
        line = json.dumps(body, ensure_ascii=False, default=str) + "\n"
        if self._pending is not None:
            self._pending.append(line)
        else:
            self._write(line)
        self._last = (body["seq"], body["hash"])
        return body

    @contextmanager
    def deferred(self) -> Iterator[list[str]]:
        """Collect appended events instead of writing them. On success the caller commits them (write-ahead inside the
        state save, then write_lines); on failure they are dropped and the chain position is restored."""
        if self._pending is not None:   # nested: the outer block decides
            yield self._pending
            return
        self._pending, start = [], self._last
        try:
            yield self._pending
        except BaseException:
            self._last = start
            raise
        finally:
            self._pending = None

    def write_lines(self, lines: list[str]) -> None:
        """Write committed events. On failure the ledger stops accepting appends until the run is reopened."""
        try:
            self._write("".join(lines))
        except BaseException:
            self._behind = True
            raise

    def recover(self, lines: list[str]) -> int:
        """Write events committed with the state but missing from the ledger (a crash or failure between the two).
        Events already on disk are skipped, so recovery is idempotent. Returns how many were written."""
        seq, prev = self._tail()
        todo = []
        for i, line in enumerate(lines, 1):
            ev = self._parse(i, line)
            if ev["seq"] <= seq:
                continue
            if ev["seq"] != seq + 1 or ev["prev"] != prev:
                raise LedgerCorrupt(f"pending event {ev['seq']} does not continue the ledger (tail {seq}); restore the ledger")
            todo.append(line)
            seq, prev = ev["seq"], ev["hash"]
        if todo:
            self._known = file_identity(self.path)
            self._write("".join(todo))
        self._last = (seq, prev)
        return len(todo)

    def pending_fill(self, tail: list | tuple | None, pending: list[str]) -> bool:
        """True when events committed with the state but not yet written continue this ledger exactly up to `tail`."""
        if not tail or not pending:
            return False
        seq, prev = self._tail()
        for i, line in enumerate(pending, 1):
            ev = self._parse(i, line)
            if ev["seq"] <= seq:
                continue
            if ev["seq"] != seq + 1 or ev["prev"] != prev:
                return False
            seq, prev = ev["seq"], ev["hash"]
        return [seq, prev] == list(tail[:2])

    def reaches(self, tail: list | tuple | None) -> str | None:
        """None when the ledger contains the event the state last recorded; otherwise why not."""
        if not tail:
            return None
        seq, digest = tail[0], tail[1]
        if seq == 0:
            return None
        try:
            at = next((e for e in self.events() if e["seq"] == seq), None)
        except LedgerCorrupt as exc:
            return str(exc)
        if at is None:
            return (f"the ledger ends before event {seq}, which the state recorded: events were deleted or the ledger was "
                    "replaced; restore .vpg/ledger.jsonl from a backup")
        if at["hash"] != digest:
            return (f"ledger event {seq} is not the one the state recorded: the ledger was rewritten; restore "
                    ".vpg/ledger.jsonl from a backup")
        return None

    def verify(self) -> tuple[bool, int, str]:
        prev, n = GENESIS, 0
        try:
            evs = list(self.events())
        except LedgerCorrupt as exc:
            return False, n, str(exc)
        for ev in evs:
            n += 1
            if ev["prev"] != prev:
                return False, n, f"event {ev['seq']}: prev hash does not match the preceding event"
            expect = sha256_bytes((prev + canonical({k: ev[k] for k in ("seq", "ts", "type", "data")})).encode())
            if expect != ev["hash"]:
                return False, n, f"event {ev['seq']}: content hash mismatch (edited)"
            if ev["seq"] != n:
                return False, n, f"event {ev['seq']}: sequence gap (deleted event)"
            prev = ev["hash"]
        return True, n, "ok"
