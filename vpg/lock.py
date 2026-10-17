"""Process locks: serialise every command that changes a production (or the rule base).

Parallel agents submit through separate `vpg` processes. Each process loads state.json, changes it and saves it, so two
unsynchronised writers would lose one another's work. An exclusive OS lock (fcntl on POSIX; the msvcrt fallback for
Windows is untested) is held for the whole command and released by the OS if the process dies, so a crash never
leaves a stale lock. The lock file records who holds it, for the waiting process's message.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import NotRegularFile, now, read_text

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
    import msvcrt


class Locked(Exception):
    """Another vpg process holds the lock for longer than the caller is willing to wait."""


def project_lock_path(project: str | Path) -> Path:
    return Path(project).resolve() / ".vpg" / "lock"


def rules_lock_path(rules: str | Path) -> Path:
    rules = Path(rules).resolve()
    return rules.parent / f".{rules.stem}.lock"


def _try(fd: int) -> bool:
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:  # pragma: no cover
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _release(fd: int) -> None:
    if fcntl:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:  # pragma: no cover
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _regular_or_absent(path: Path) -> None:
    if os.path.lexists(path) and not os.path.isfile(path):
        raise Locked(f"{path} is not a regular file (a directory, pipe or device); remove it, then retry")


def _read_info(path: Path) -> dict[str, Any]:
    try:
        return json.loads(read_text(path) or "{}")
    except (OSError, ValueError):
        return {}


def holder(path: Path) -> dict[str, Any] | None:
    """Who holds the lock now (None when it is free). Never blocks and never takes the lock for long."""
    if not path.exists():
        return None
    _regular_or_absent(path)
    fd = os.open(path, os.O_RDWR)
    try:
        if _try(fd):
            _release(fd)
            return None
        return _read_info(path) or {"pid": None}
    finally:
        os.close(fd)


def describe(info: dict[str, Any] | None) -> str:
    if not info:
        return "another vpg process"
    return f"pid {info.get('pid')} (`vpg {info.get('command', '?')}`, since {info.get('since', '?')})"


@contextmanager
def file_lock(path: Path, command: str, timeout: float = 900.0, poll: float = 0.1) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    _regular_or_absent(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + max(0.0, timeout)
        announced = False
        while not _try(fd):
            if time.monotonic() >= deadline:
                raise Locked(f"{path} is held by {describe(_read_info(path))}; waited {timeout:g}s. "
                             "Retry when it finishes (`vpg status` shows progress without waiting).")
            if not announced:
                print(f"waiting for the lock held by {describe(_read_info(path))} …", file=sys.stderr)
                announced = True
            time.sleep(poll)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps({"pid": os.getpid(), "command": command, "since": now()}).encode())
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
            _release(fd)
    finally:
        os.close(fd)


def project_lock(project: str | Path, command: str, timeout: float = 900.0) -> Any:
    return file_lock(project_lock_path(project), command, timeout)


def rules_lock(rules: str | Path, command: str, timeout: float = 900.0) -> Any:
    return file_lock(rules_lock_path(rules), command, timeout)
