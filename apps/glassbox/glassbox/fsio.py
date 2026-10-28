"""Read-only file helpers: mtime-keyed caches for JSON and YAML, capped text and listings.

Nothing in this module writes to disk. Parsed objects are shared between requests, so
callers must treat them as immutable and build new dicts for API payloads.
"""
from __future__ import annotations

import json
import os
import re
import stat
import threading
from collections import OrderedDict
from datetime import datetime, timezone

from . import miniyaml

try:  # PyYAML is optional; miniyaml is the fallback.
    import yaml as _yaml
except Exception:  # pragma: no cover - depends on the interpreter
    _yaml = None

MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_YAML_BYTES = 4 * 1024 * 1024
_VERSION = re.compile(r"^(\D*)(\d+)(.*)$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


class MtimeCache:
    """Cache values derived from a file, invalidated when (mtime_ns, size) changes."""

    def __init__(self, loader, max_entries=4096):
        self._loader = loader
        self._max = max_entries
        self._lock = threading.Lock()
        self._data = OrderedDict()

    def get(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        signature = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._data.get(path)
            if hit is not None and hit[0] == signature:
                self._data.move_to_end(path)
                return hit[1]
        value = self._loader(path, st.st_size)
        with self._lock:
            self._data[path] = (signature, value)
            self._data.move_to_end(path)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
        return value

    def clear(self):
        with self._lock:
            self._data.clear()


def _load_json(path, size):
    if size > MAX_JSON_BYTES:
        return None
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read())
    except (OSError, ValueError, RecursionError):
        return None


def parse_yaml(text, force_fallback=False):
    if _yaml is not None and not force_fallback:
        try:
            return _yaml.safe_load(text)
        except Exception:
            return None
    try:
        return miniyaml.loads(text)
    except Exception:
        return None


def _load_yaml(path, size):
    if size > MAX_YAML_BYTES:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return parse_yaml(handle.read())
    except OSError:
        return None


_JSON = MtimeCache(_load_json)
_YAML = MtimeCache(_load_yaml, max_entries=512)


def read_json(path):
    return _JSON.get(path)


def read_yaml(path):
    return _YAML.get(path)


def yaml_backend():
    return "pyyaml" if _yaml is not None else "builtin-subset"


def read_text(path, cap):
    """Return (text, truncated) for at most cap bytes, or (None, False) when unreadable."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(cap + 1)
    except OSError:
        return None, False
    return data[:cap].decode("utf-8", errors="replace"), len(data) > cap


def head_lines(path, count, max_bytes=256 * 1024):
    text, _ = read_text(path, max_bytes)
    if text is None:
        return None
    return "\n".join(text.splitlines()[:count])


def is_file(path):
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def is_real_dir(path):
    """A directory that is not itself a symlink."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def list_entries(path, limit=2000):
    """Immediate entries of a directory: files with size, sub-directories with an entry count."""
    files, dirs = [], []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": entry.name, "dir": True, "count": _count_entries(entry.path)})
                    elif entry.is_file(follow_symlinks=False):
                        files.append({"name": entry.name, "size": entry.stat(follow_symlinks=False).st_size})
                except OSError:
                    continue
                if len(files) + len(dirs) >= limit:
                    break
    except OSError:
        return []
    files.sort(key=lambda item: item["name"].lower())
    dirs.sort(key=lambda item: item["name"].lower())
    return files + dirs


def _count_entries(path, cap=10000):
    count = 0
    try:
        with os.scandir(path) as iterator:
            for _ in iterator:
                count += 1
                if count >= cap:
                    break
    except OSError:
        return None
    return count


def version_key(name):
    """Natural order for candidate folders: numeric part first, then suffix (v2 < v10 < v22 < v22r1)."""
    match = _VERSION.match(name)
    if not match:
        return (1, 0, name.lower(), "")
    prefix, number, suffix = match.groups()
    return (0, int(number), suffix.lower(), prefix.lower())


def safe_segment(name):
    return isinstance(name, str) and bool(_SAFE_SEGMENT.match(name)) and ".." not in name


_OS_METADATA = frozenset({"thumbs.db", "ehthumbs.db", "desktop.ini", "icon\r"})
_FOLDER_TIMES = OrderedDict()
_FOLDER_LOCK = threading.Lock()
ATTRIBUTION_S = 2.0


def is_os_metadata(name):
    """Dotfiles (.DS_Store, ._* AppleDouble files) and other OS or editor droppings (Thumbs.db, *.swp, *~)."""
    return (name.startswith(".") or name.lower() in _OS_METADATA or name.endswith("~")
            or name.lower().endswith((".swp", ".swo")))


def folder_change_time(path):
    """When a folder last changed, ignoring dotfiles and OS metadata: its mtime, unless that change coincides with a
    metadata file and no other file (a Finder .DS_Store write), then the newest other file's mtime (None when there is
    none). Sub-folders are not looked into. Cached until the folder's mtime changes."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode):
        return None
    with _FOLDER_LOCK:
        hit = _FOLDER_TIMES.get(path)
        if hit is not None and hit[0] == st.st_mtime_ns:
            _FOLDER_TIMES.move_to_end(path)
            return hit[1]
    folder_time = st.st_mtime
    newest = None
    metadata_near = other_near = False
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        continue
                    when = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                near = when >= folder_time - ATTRIBUTION_S
                if is_os_metadata(entry.name):
                    metadata_near = metadata_near or near
                else:
                    other_near = other_near or near
                    newest = when if newest is None else max(newest, when)
    except OSError:
        return folder_time
    value = newest if (metadata_near and not other_near) else folder_time
    with _FOLDER_LOCK:
        _FOLDER_TIMES[path] = (st.st_mtime_ns, value)
        _FOLDER_TIMES.move_to_end(path)
        while len(_FOLDER_TIMES) > 8192:
            _FOLDER_TIMES.popitem(last=False)
    return value


def iso_utc(timestamp):
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_ts(value):
    """'2026-10-21T23:38:19+00:00' -> '2026-10-21 23:38 UTC' (input is assumed to be UTC)."""
    if not isinstance(value, str) or len(value) < 16:
        return value
    return value[:10] + " " + value[11:16] + " UTC"


def is_within(path, root):
    """True when the real path of `path` is `root` or inside it."""
    try:
        real_path = os.path.realpath(path)
        real_root = os.path.realpath(root)
        return os.path.commonpath([real_path, real_root]) == real_root
    except ValueError:
        return False
