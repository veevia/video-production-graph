"""Find VPG projects (directories holding vpg.project.yaml) under the configured roots."""
from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
import time
from dataclasses import dataclass

from . import fsio

PROJECT_FILE = "vpg.project.yaml"
MAX_DEPTH = 9
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "candidates", "__pycache__", "outputs", "render", "archive"})
_PID = re.compile(r"^[0-9a-f]{10}$")


def project_id(path):
    return hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:10]


def is_skipped_dir(name):
    lower = name.lower()
    if name.startswith(".") or lower in SKIP_DIRS:
        return True
    return lower.lstrip("_").startswith("archive")


def find_project_dirs(root, max_depth=MAX_DEPTH):
    """Directories up to max_depth below root that contain vpg.project.yaml; projects do not nest."""
    found = []
    stack = [(os.path.abspath(root), 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError:
            continue
        if any(entry.name == PROJECT_FILE and entry.is_file(follow_symlinks=False) for entry in entries):
            found.append(current)
            continue
        if depth >= max_depth:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False) and not is_skipped_dir(entry.name):
                    stack.append((entry.path, depth + 1))
            except OSError:
                continue
    return sorted(found)


@dataclass(frozen=True)
class Project:
    id: str
    path: str
    root: str
    rel_path: str
    name: str
    slug: str | None


def load_project(path, root):
    path = os.path.abspath(path)
    meta = fsio.read_yaml(os.path.join(path, PROJECT_FILE))
    meta = meta if isinstance(meta, dict) else {}
    rel_path = os.path.relpath(path, root)
    if rel_path == ".":
        rel_path = os.path.basename(path)
    name = None
    for key in ("name", "id"):
        value = meta.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip():
            name = str(value).strip()
            break
    slug = meta.get("id")
    return Project(
        id=project_id(path),
        path=path,
        root=root,
        rel_path=rel_path,
        name=name or rel_path,
        slug=str(slug) if isinstance(slug, (str, int)) and not isinstance(slug, bool) else None,
    )


class Registry:
    """Snapshot of discovered projects, refreshed on demand or by a background thread."""

    def __init__(self, roots, max_depth=MAX_DEPTH):
        self.roots = [os.path.abspath(os.path.expanduser(root)) for root in roots]
        self.max_depth = max_depth
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._projects = {}
        self._order = []
        self._scanned_at = None
        self.last_scan_seconds = None
        self._stop = threading.Event()

    def scan(self):
        with self._scan_lock:
            started = time.monotonic()
            projects = {}
            for root in self.roots:
                if not os.path.isdir(root):
                    continue
                for path in find_project_dirs(root, self.max_depth):
                    project = load_project(path, root)
                    projects.setdefault(project.id, project)
            ordered = sorted(projects.values(), key=lambda p: (p.name.lower(), p.rel_path))
            with self._lock:
                self._projects = projects
                self._order = ordered
                self._scanned_at = time.monotonic()
            self.last_scan_seconds = round(time.monotonic() - started, 3)
            return list(ordered)

    def projects(self):
        with self._lock:
            ready = self._scanned_at is not None
            ordered = list(self._order)
        return ordered if ready else self.scan()

    def get(self, pid):
        if not isinstance(pid, str) or not _PID.match(pid):
            return None
        with self._lock:
            project = self._projects.get(pid)
            age = None if self._scanned_at is None else time.monotonic() - self._scanned_at
        if project is None and (age is None or age > 5):
            self.scan()
            with self._lock:
                project = self._projects.get(pid)
        return project

    def start_background(self, interval=60.0):
        def loop():
            while not self._stop.wait(interval):
                try:
                    self.scan()
                except Exception as error:  # pragma: no cover - defensive
                    print(f"vpg-glassbox: rescan failed: {error}", file=sys.stderr)

        thread = threading.Thread(target=loop, name="vpg-glassbox-rescan", daemon=True)
        thread.start()
        return thread

    def stop(self):
        self._stop.set()
