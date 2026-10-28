"""The video library: every video under the library roots, indexed in a background thread and grouped into films.

Read-only on the roots. The only write is the index cache <data-dir>/library-index.json (atomic replace). Per-file
metadata (duration, fingerprint) is reused while a file's size and mtime_ns are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import sys
import threading
import time
from datetime import datetime, timezone

from . import fsio
from .library_group import ROLES, Context, group, natural_key

VIDEO_EXTS = {".mp4": True, ".m4v": True, ".mov": True, ".webm": True, ".mkv": False, ".avi": False}
MP4_FAMILY = frozenset({".mp4", ".m4v", ".mov"})
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm",
               ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
PRUNE = frozenset({"node_modules", ".git", ".venv", "venv", "__pycache__", "site-packages", "Library", "dist", "build",
                   ".next", "caches", "Caches"})
DEFAULT_DIRS = ("GitHub", "Downloads", "Desktop", "Movies", "Documents")
CACHE_FILE = "library-index.json"
MAP_FILE = "library-map.json"
SCHEMA = 2
MAX_STATE_BYTES = 32 * 1024 * 1024
CHUNK = 64 * 1024
MAX_BOXES = 4096
PAGE_MAX = 200
_ID = re.compile(r"^[0-9a-f]{16}$")


def default_roots(home=None):
    home = home or os.path.expanduser("~")
    return [os.path.join(home, name) for name in DEFAULT_DIRS]


def iso(timestamp):
    return fsio.iso_utc(timestamp) if timestamp else None


# ------------------------------------------------------------------------------------------------ file metadata
def _box(handle, start, end, kind):
    """(content start, content end) of the first box of `kind` among the sibling boxes in [start, end)."""
    position = start
    for _ in range(MAX_BOXES):
        if position + 8 > end:
            return None
        handle.seek(position)
        header = handle.read(8)
        if len(header) < 8:
            return None
        size, name = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            large = handle.read(8)
            if len(large) < 8:
                return None
            size = struct.unpack(">Q", large)[0]
            header_size = 16
        elif size == 0:
            size = end - position
        if size < header_size or position + size > end:
            return None
        if name == kind:
            return position + header_size, position + size
        position += size
    return None


def mp4_duration(path):
    """Seconds from an MP4 or QuickTime file's moov/mvhd box (32- and 64-bit box sizes, moov anywhere); None on failure."""
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            moov = _box(handle, 0, size, b"moov")
            if not moov:
                return None
            mvhd = _box(handle, moov[0], moov[1], b"mvhd")
            if not mvhd:
                return None
            handle.seek(mvhd[0])
            body = handle.read(min(mvhd[1] - mvhd[0], 32))
            if len(body) < 20:
                return None
            if body[0] == 1:
                if len(body) < 32:
                    return None
                timescale, duration = struct.unpack(">IQ", body[20:32])
                unknown = duration == 0xFFFFFFFFFFFFFFFF
            else:
                timescale, duration = struct.unpack(">II", body[12:20])
                unknown = duration == 0xFFFFFFFF
            if not timescale or unknown:
                return None
            return round(duration / timescale, 3)
    except (OSError, struct.error, ValueError):
        return None


def quick_fingerprint(path, size):
    """sha1 of the size, the first 64 KB and the last 64 KB: cheap duplicate detection, not a content hash."""
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha1(f"{size}:".encode())
            digest.update(handle.read(CHUNK))
            if size > 2 * CHUNK:
                handle.seek(-CHUNK, os.SEEK_END)
                digest.update(handle.read(CHUNK))
            elif size > CHUNK:
                digest.update(handle.read())
            return digest.hexdigest()
    except OSError:
        return None


def sidecars(folder, stem, names):
    """(poster, captions) paths beside a video. names: {lowercase file name: real name} of the folder."""
    low = stem.lower()
    poster = None
    for candidate in (f"{low}-poster.jpg", f"{low}-poster.png", f"{low}.jpg", f"{low}.png", "poster.jpg", "poster.png"):
        if candidate in names:
            poster = os.path.join(folder, names[candidate])
            break
    captions = None
    ordered = [f"{low}.en.vtt", f"{low}.vtt"]
    ordered += sorted(n for n in names if n.startswith("captions") and n.endswith(".vtt"))
    ordered += sorted(n for n in names if n.startswith("captions") and n.endswith(".srt"))
    ordered.append(f"{low}.srt")
    for candidate in ordered:
        if candidate in names:
            captions = os.path.join(folder, names[candidate])
            break
    return poster, captions


def _pruned(name):
    return name.startswith(".") or name in PRUNE


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def governed_videos(project_dir):
    """{project-relative video path: [label, why]} for the videos of governed versions in .vpg/state.json.

    A version's videos are the artifacts of its node attempt (history.versions[].node_attempt, e.g.
    'n14_video_assembly#a64'), else the artifacts that carry its version number. {} when there is no readable state."""
    path = os.path.join(project_dir, ".vpg", "state.json")
    try:
        if os.path.getsize(path) > MAX_STATE_BYTES:
            return {}
        with open(path, "rb") as handle:
            state = json.loads(handle.read())
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(state, dict):
        return {}
    history = state.get("history") if isinstance(state.get("history"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    by_attempt, by_number = {}, {}
    for artifact_id, artifact in artifacts.items():
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            continue
        rel = os.path.normpath(artifact["path"])
        if os.path.isabs(rel) or rel.startswith("..") or os.path.splitext(rel)[1].lower() not in VIDEO_EXTS:
            continue
        if "@" in str(artifact_id):
            by_attempt.setdefault(str(artifact_id).split("@", 1)[1], []).append(rel)
        number = _int(artifact.get("version"))
        if number is not None:
            by_number.setdefault(number, []).append(rel)
    out = {}
    for record in history.get("versions") or []:
        number = _int(record.get("version")) if isinstance(record, dict) else None
        if number is None:
            continue
        attempt = record.get("node_attempt") if isinstance(record.get("node_attempt"), str) else None
        paths = by_attempt.get(attempt) if attempt else None
        why = f"governed version v{number} in .vpg/state.json" + (f" ({attempt.replace('#', ' ')})" if attempt else "")
        for rel in paths or by_number.get(number, []):
            out.setdefault(rel, [f"v{number}", why])
    return out


# ------------------------------------------------------------------------------------------------ the library
class Library:
    """Background indexer plus the grouped index served by the API."""

    def __init__(self, roots, data_dir, home=None, jobs=None):
        self.roots = [os.path.abspath(os.path.expanduser(r)) for r in roots]
        self.real_roots = [os.path.realpath(r) for r in self.roots]
        self.data_dir = os.path.abspath(os.path.expanduser(data_dir))
        self.cache_path = os.path.join(self.data_dir, CACHE_FILE)
        self.map_path = os.path.join(self.data_dir, MAP_FILE)
        self.home = os.path.abspath(home or os.path.expanduser("~"))
        self.jobs = jobs or (lambda: {})
        self._lock = threading.Lock()
        self._thread = None
        self._index = None
        self._progress = {}
        self._state = {"status": "idle", "error": None, "from_cache": False, "scanned_at": None, "scan_seconds": None}

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        """Serve the cached index right away (when there is one) and rescan in the background."""
        cached = self._load_cache()
        if cached:
            try:
                index = self._group(cached["files"], cached.get("projects") or {})
                with self._lock:
                    self._index = index
                    self._state.update(from_cache=True, scanned_at=cached.get("scanned_at"),
                                       scan_seconds=cached.get("scan_seconds"))
            except Exception as error:  # pragma: no cover - a damaged cache never blocks a scan
                print(f"vpg-glassbox: library cache ignored: {error}", file=sys.stderr)
        return self.rescan()

    def rescan(self):
        """Start a background scan; False when one is already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._state.update(status="scanning", error=None)
            self._progress = {"phase": "walking", "dirs": 0, "videos": 0, "read": 0, "to_read": 0, "root": None,
                              "started": time.time()}
            self._thread = threading.Thread(target=self._run, name="vpg-glassbox-library", daemon=True)
            self._thread.start()
        return True

    def scan_now(self):
        """Scan in the calling thread (tests and tools)."""
        with self._lock:
            self._state.update(status="scanning", error=None)
            self._progress = {"phase": "walking", "dirs": 0, "videos": 0, "read": 0, "to_read": 0, "root": None,
                              "started": time.time()}
        self._run()
        return self.overview()

    def wait(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self):
        started = time.time()
        try:
            files, projects = self._walk()
            self._set_progress(phase="reading")
            files = self._metadata(files)
            self._set_progress(phase="grouping")
            index = self._group(files, projects)
            seconds = round(time.time() - started, 1)
            scanned_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save_cache(files, projects, scanned_at, seconds)
            with self._lock:
                self._index = index
                self._state.update(status="ready", from_cache=False, scanned_at=scanned_at, scan_seconds=seconds)
        except Exception as error:  # pragma: no cover - defensive: the server keeps running without a library
            with self._lock:
                self._state.update(status="error", error=str(error)[:300])
            print(f"vpg-glassbox: library scan failed: {error}", file=sys.stderr)

    def _set_progress(self, **values):
        with self._lock:
            self._progress.update(values)

    # ------------------------------------------------------------------ scanning
    def _walk(self):
        files, projects = [], {}
        seen = set()
        for root in self.roots:
            if not os.path.isdir(root):
                continue
            self._set_progress(root=root)
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not _pruned(d) and os.path.join(dirpath, d) != self.data_dir)
                with self._lock:
                    self._progress["dirs"] = self._progress.get("dirs", 0) + 1
                if "vpg.project.yaml" in filenames:
                    meta = fsio.read_yaml(os.path.join(dirpath, "vpg.project.yaml"))
                    meta = meta if isinstance(meta, dict) else {}
                    name = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name").strip() else None
                    ident = meta.get("id") if isinstance(meta.get("id"), (str, int)) else None
                    projects[dirpath] = {"name": name, "id": str(ident) if ident is not None else None,
                                         "candidates": "candidates" in dirnames, "governed": governed_videos(dirpath)}
                videos = [f for f in filenames if not f.startswith(".") and os.path.splitext(f)[1].lower() in VIDEO_EXTS]
                if not videos:
                    continue
                names = {f.lower(): f for f in filenames if not f.startswith(".")}
                for name in sorted(videos):
                    path = os.path.join(dirpath, name)
                    if path in seen:
                        continue
                    try:
                        st = os.lstat(path)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    seen.add(path)
                    stem, ext = os.path.splitext(name)
                    poster, captions = sidecars(dirpath, stem, names)
                    files.append({"path": path, "size": st.st_size, "mtime": st.st_mtime, "mtime_ns": st.st_mtime_ns,
                                  "ext": ext.lower(), "playable": VIDEO_EXTS[ext.lower()], "poster": poster,
                                  "captions": captions})
                with self._lock:
                    self._progress["videos"] = len(files)
        return files, projects

    def _metadata(self, files):
        cached = self._load_cache() or {}
        previous = {f["path"]: f for f in cached.get("files") or [] if isinstance(f, dict) and f.get("path")}
        todo = [f for f in files if not self._reuse(f, previous.get(f["path"]))]
        self._set_progress(to_read=len(todo), read=0)
        for count, item in enumerate(todo, start=1):
            item["duration_s"] = mp4_duration(item["path"]) if item["ext"] in MP4_FAMILY else None
            item["fingerprint"] = quick_fingerprint(item["path"], item["size"])
            if count % 25 == 0:
                self._set_progress(read=count)
        self._set_progress(read=len(todo))
        return files

    @staticmethod
    def _reuse(item, old):
        if not old or old.get("size") != item["size"] or old.get("mtime_ns") != item["mtime_ns"]:
            return False
        item["duration_s"] = old.get("duration_s")
        item["fingerprint"] = old.get("fingerprint")
        return bool(item["fingerprint"])

    def _overrides(self):
        """Owner rules from <data-dir>/library-map.json (read only; bad rules are skipped)."""
        doc = fsio.read_json(self.map_path)
        rules = []
        for rule in doc if isinstance(doc, list) else []:
            if not isinstance(rule, dict) or not isinstance(rule.get("match"), str):
                continue
            try:
                re.compile(rule["match"])
                if isinstance(rule.get("version_regex"), str):
                    re.compile(rule["version_regex"])
            except re.error:
                continue
            rules.append({k: rule[k] for k in ("match", "film", "version_regex", "role") if k in rule})
        return rules

    def _group(self, files, projects):
        jobs = {}
        try:
            jobs = dict(self.jobs() or {})
        except Exception:  # pragma: no cover - job discovery problems never block the library
            jobs = {}
        ctx = Context(self.home, self.roots, projects, jobs, self._overrides())
        index = group(files, ctx)
        # Every file says which job it belongs to: through its film's job, or by sitting inside a job's folder.
        job_roots = sorted(ctx.jobs.items(), key=lambda pair: -len(pair[0]))
        for item in index["items"].values():
            film = index["films"].get(item.get("film_id")) if item.get("film_id") else None
            pid = film.get("job_pid") if film else None
            if pid is None and not item.get("archived"):
                pid = next((job for root, job in job_roots if item["path"].startswith(root + os.sep)), None)
            item["job_pid"] = pid
        return index

    # ------------------------------------------------------------------ cache
    def _load_cache(self):
        try:
            if os.path.getsize(self.cache_path) > fsio.MAX_JSON_BYTES:
                return None
            with open(self.cache_path, "rb") as handle:
                doc = json.loads(handle.read())
        except (OSError, ValueError, RecursionError):
            return None
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("files"), list):
            return None
        if sorted(doc.get("roots") or []) != sorted(self.roots):
            return None
        return doc

    def _save_cache(self, files, projects, scanned_at, seconds):
        keys = ("path", "size", "mtime", "mtime_ns", "ext", "playable", "poster", "captions", "duration_s", "fingerprint")
        payload = {"schema": SCHEMA, "roots": self.roots, "scanned_at": scanned_at, "scan_seconds": seconds,
                   "files": [{k: f.get(k) for k in keys} for f in files], "projects": projects}
        try:
            os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
            temp = os.path.join(self.data_dir, f".{CACHE_FILE}.{os.getpid()}.tmp")
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.chmod(temp, 0o600)
            os.replace(temp, self.cache_path)
        except OSError as error:  # pragma: no cover - the index still serves from memory
            print(f"vpg-glassbox: could not write {self.cache_path}: {error}", file=sys.stderr)

    # ------------------------------------------------------------------ payloads
    def status(self):
        with self._lock:
            state = dict(self._state)
            progress = dict(self._progress)
            index = self._index
        if progress.get("started"):
            progress["elapsed_s"] = round(time.time() - progress.pop("started"), 1)
        return {"enabled": True, "status": state["status"], "error": state["error"], "from_cache": state["from_cache"],
                "scanned_at": state["scanned_at"], "scan_seconds": state["scan_seconds"], "roots": self.roots,
                "progress": progress if state["status"] == "scanning" else None,
                "counts": index["counts"] if index else None}

    def _snapshot(self):
        with self._lock:
            return self._index

    def overview(self):
        out = self.status()
        index = self._snapshot()
        out["films"] = [self._film_summary(f) for f in index["films"].values()] if index else []
        out["films"].sort(key=lambda f: f["latest_mtime"] or "", reverse=True)
        out["other"] = [self._collection_summary(c) for c in index["collections"].values()] if index else []
        out["other"].sort(key=lambda c: natural_key(c["label"]))
        return out

    @staticmethod
    def _film_summary(film):
        labels = [v["label"] for v in film["versions"]]
        return {
            "id": film["id"], "name": film["name"], "area": film["area"], "archived": film["archived"],
            "batch": film.get("batch"), "kind": film["kind"], "job_pid": film.get("job_pid"),
            "versions_count": len(film["versions"]), "videos": film["videos"], "latest_mtime": iso(film["latest_mtime"]),
            "total_size": film["total_size"], "primary_poster": film.get("primary_poster"),
            "latest_version": labels[0] if labels else None, "version_labels": labels[:8],
            "primary_id": film["versions"][0]["primary"]["id"] if film["versions"] else None,
            "primary_playable": bool(film["versions"][0]["primary"].get("playable")) if film["versions"] else False,
            "masters": sum(1 for v in film["versions"] if v["primary"]["role"] in ("master", "review")),
            "root": film.get("root"),
            "search": " ".join([film["name"], film.get("root") or "", film["area"]] + labels).lower(),
        }

    @staticmethod
    def _collection_summary(coll):
        return {"id": coll["id"], "label": coll["label"], "folder": coll["folder"], "area": coll["area"],
                "roles": coll["roles"], "why": coll["why"], "videos": coll["videos"], "total_size": coll["total_size"],
                "latest_mtime": iso(coll["latest_mtime"])}

    @staticmethod
    def public_item(item):
        """A file as the API shows it; versions only mean something inside a film."""
        if item is None:
            return None
        in_film = bool(item.get("film_id"))
        return {
            "id": item["id"], "name": item["name"], "path": item["path"], "folder": item["folder"], "size": item.get("size"),
            "mtime": iso(item.get("mtime")), "ext": item.get("ext"), "playable": bool(item.get("playable")),
            "duration_s": item.get("duration_s"), "role": item["role"], "role_why": item["role_why"],
            "film_id": item.get("film_id"), "film": item.get("film"), "film_why": item.get("film_why"),
            "version": item.get("version") if in_film else None,
            "version_why": item.get("version_why") if in_film else None, "is_primary": item.get("is_primary"),
            "archived": item.get("archived"), "area": item.get("area"), "duplicate_of": item.get("duplicate_of"),
            "copies": item.get("copies") or 0, "has_poster": bool(item.get("poster")),
            "has_captions": bool(item.get("captions")), "collection_id": item.get("collection_id"),
            "job_pid": item.get("job_pid"),
        }

    def film(self, film_id):
        index = self._snapshot()
        film = index["films"].get(film_id) if index and isinstance(film_id, str) else None
        if film is None:
            return None
        out = self._film_summary(film)
        out.update(why=film["why"], root=film.get("root"), versions=[{
            "label": v["label"], "why": v["why"], "mtime": iso(v["mtime"]),
            "primary": self.public_item(v["primary"]),
            "variants": [self.public_item(m) for m in v["items"] if m is not v["primary"]],
        } for v in film["versions"]])
        return out

    def collection(self, collection_id):
        index = self._snapshot()
        coll = index["collections"].get(collection_id) if index and isinstance(collection_id, str) else None
        if coll is None:
            return None
        out = self._collection_summary(coll)
        out["items"] = [self.public_item(m) for m in coll["items"]]
        return out

    def item(self, item_id):
        index = self._snapshot()
        if not index or not isinstance(item_id, str) or not _ID.match(item_id):
            return None
        return index["items"].get(item_id)

    def item_detail(self, item_id):
        """One video with where it sits (film and version, or its Other collection) and its byte-identical copies."""
        index = self._snapshot()
        item = self.item(item_id)
        if item is None:
            return None
        out = self.public_item(item)
        film = index["films"].get(item.get("film_id")) if item.get("film_id") else None
        out["film_summary"] = ({"id": film["id"], "name": film["name"], "archived": film["archived"],
                                "job_pid": film.get("job_pid"), "area": film["area"]} if film else None)
        coll = index["collections"].get(item.get("collection_id")) if item.get("collection_id") else None
        out["collection"] = {"id": coll["id"], "label": coll["label"], "why": coll["why"]} if coll else None
        fingerprint = item.get("fingerprint")
        out["same_file"] = [self.public_item(m) for m in index["items"].values()
                            if fingerprint and m is not item and m.get("fingerprint") == fingerprint][:50]
        return out

    def context_of(self, item_id):
        """What a reaction stores about a library video: film, version, name and path at the time of the reaction."""
        item = self.item(item_id)
        if item is None:
            return None
        return {"id": item["id"], "film_id": item.get("film_id"), "film": item.get("film"),
                "version": item.get("version") if item.get("film_id") else None,
                "name": item["name"], "path": item["path"], "collection_id": item.get("collection_id")}

    def items(self, q="", role="", area="", sort="newest", offset=0, limit=60):
        index = self._snapshot()
        rows = list(index["items"].values()) if index else []
        words = [w for w in str(q or "").lower().split() if w]
        if role:
            rows = [r for r in rows if r["role"] == role]
        if area:
            rows = [r for r in rows if r["area"] == area]
        if words:
            rows = [r for r in rows if all(w in " ".join([r["path"], r.get("film") or "", r.get("version") or ""]).lower()
                                           for w in words)]
        if sort == "name":
            rows.sort(key=lambda r: natural_key(r["name"]))
        elif sort == "size":
            rows.sort(key=lambda r: -(r.get("size") or 0))
        else:
            rows.sort(key=lambda r: -(r.get("mtime") or 0))
        offset = max(0, int(offset))
        limit = max(1, min(PAGE_MAX, int(limit)))
        return {"total": len(rows), "offset": offset, "limit": limit,
                "items": [self.public_item(r) for r in rows[offset:offset + limit]]}

    # ------------------------------------------------------------------ media (ids only; paths never come from clients)
    def _inside(self, path):
        real = os.path.realpath(path)
        for root in self.real_roots:
            try:
                if os.path.commonpath([real, root]) == root:
                    return real
            except ValueError:
                continue
        return None

    def _regular(self, path, types):
        if not path or os.path.splitext(path)[1].lower() not in types:
            return None
        real = self._inside(path)
        if not real or os.path.splitext(real)[1].lower() not in types:
            return None
        try:
            if not stat.S_ISREG(os.stat(real).st_mode):
                return None
        except OSError:
            return None
        return real

    def media_path(self, item_id, kind="video"):
        """(real path, content type, convert_srt) for an indexed item's video, poster or captions; None otherwise."""
        item = self.item(item_id)
        if item is None:
            return None
        if kind == "video":
            real = self._regular(item["path"], VIDEO_TYPES)
            return (real, VIDEO_TYPES[os.path.splitext(real)[1].lower()], False) if real else None
        if kind == "poster":
            real = self._regular(item.get("poster"), IMAGE_TYPES)
            return (real, IMAGE_TYPES[os.path.splitext(real)[1].lower()], False) if real else None
        if kind == "captions":
            real = self._regular(item.get("captions"), {".vtt": 1, ".srt": 1})
            return (real, "text/vtt; charset=utf-8", real.lower().endswith(".srt")) if real else None
        return None


ROLE_NAMES = ROLES
