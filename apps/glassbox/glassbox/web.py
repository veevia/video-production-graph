"""HTTP layer: Host and Origin checks, routing, JSON, media with Range, the static UI and reaction POSTs."""
from __future__ import annotations

import json
import math
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, fsio, media
from .adapters import JobReader
from .discovery import PROJECT_FILE, Registry
from .engine import Engine
from .films import films_payload
from .library import ROLE_NAMES, Library
from .needs import needs_you
from .producer import display_name, inbox_markdown, inbox_payload, inbox_summary, parse_states, summary_markdown
from .reactions import (KINDS, LIBRARY_PID, MAX_BATCH, QUICK_TAGS, TARGET_TYPES, THREAD_KINDS, ReactionError,
                        ReactionStore, _fmt_t, export_markdown, validate_reaction)
from .timecode import srt_to_vtt

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
}
MAX_BODY = 64 * 1024
CHUNK = 256 * 1024
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; "
       "connect-src 'self'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
_LIB_ID = re.compile(r"^[0-9a-f]{16}$")
POST_PATHS = ("/api/reactions", "/api/reactions/batch", "/api/library/rescan")


def _finite(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


class _Context:
    """What reaction validation needs to know about jobs, versions, nodes and stored reactions.

    validate_reaction asks for the project first, so the graph follows that project's `workflow:`."""

    def __init__(self, app):
        self.app = app
        self.graph = app.engine.graph()
        self._versions = {}

    def project(self, pid):
        project = self.app.registry.get(pid)
        if project is not None:
            self.graph = self.app.workflow_of(project).graph
        return project

    def versions(self, project):
        if project.id not in self._versions:
            self._versions[project.id] = set(self.app.reader(project).versions())
        return self._versions[project.id]

    def node_ids(self):
        return set(self.graph.by_id)

    def human_gate_ids(self):
        return set(self.graph.human_gate_ids())

    def release_node(self):
        return self.graph.release_node()

    def decision_allowed(self, project, gate_id, version):
        """(allowed, why): approve and request_changes only where the gate actually waits."""
        state = self.app.reader(project).human_gate_state(gate_id, version)
        if state is None:
            return False, "not a human gate"
        return "approve" in state["actions"], state["label"]

    def find_reaction(self, reaction_id):
        return self.app.store.find(reaction_id)

    def library_enabled(self):
        return self.app.library is not None

    def is_retracted(self, reaction_id):
        return reaction_id in self.app.store.retractions()

    def thread_events(self, reaction_id):
        return self.app.store.thread_of(reaction_id)

    def library_item(self, item_id):
        """What a reaction records about an indexed library video (film, version, name, path) or None."""
        return self.app.library.context_of(item_id) if self.app.library is not None else None


class App:
    def __init__(self, registry, engine, store, port=None, library=None, static_dir=None):
        self.registry = registry
        self.engine = engine
        self.store = store
        self.port = port
        self.library = library
        self.static_dir = os.path.abspath(static_dir) if static_dir else STATIC_DIR

    def library_jobs(self):
        """{project folder: pid} of the discovered jobs, so library films can link to their job."""
        return {p.path: p.id for p in self.registry.projects()}

    def library_meta(self):
        if self.library is None:
            return {"enabled": False, "status": "disabled", "roles": list(ROLE_NAMES)}
        status = self.library.status()
        return {"enabled": True, "status": status["status"], "roots": status["roots"], "roles": list(ROLE_NAMES),
                "cache_file": self.library.cache_path, "map_file": self.library.map_path}

    def allowed_hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def allowed_origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

    def workflow_of(self, project):
        """The engine workflow a project selects with `workflow:` in vpg.project.yaml (default when absent)."""
        meta = fsio.read_yaml(os.path.join(project.path, PROJECT_FILE))
        value = meta.get("workflow") if isinstance(meta, dict) else None
        return self.engine.select(value, project.path)

    def reader(self, project):
        workflow = self.workflow_of(project)
        return JobReader(project, workflow.graph, self.store, workflow=workflow.info(self.engine.dir))

    def meta(self):
        graph = self.engine.graph()
        return {
            "app": "vpg-glassbox",
            "version": __version__,
            "read_only": True,
            "roots": self.registry.roots,
            "engine": self.engine.dir,
            "graph_available": graph.available,
            "graph_nodes": len(graph.nodes),
            "phases": graph.phases,
            "workflows": self.engine.workflows(),
            "data_dir": self.store.data_dir,
            "reactions_file": self.store.path,
            "yaml": fsio.yaml_backend(),
            "kinds": list(KINDS),
            "target_types": list(TARGET_TYPES),
            "quick_tags": list(QUICK_TAGS),
            "last_scan_seconds": self.registry.last_scan_seconds,
            "library": self.library_meta(),
        }

    def projects_list(self):
        out = []
        for project in self.registry.projects():
            reader = self.reader(project)
            item = reader.summary()
            needs = needs_you([reader])
            decisions = [n for n in needs if n.get("group") == "decision"]
            item["needs_you"] = len(decisions)
            item["needs_gates"] = sum(1 for n in decisions if n["kind"] == "human_gate")
            item["needs_fyi"] = len(needs) - len(decisions)
            out.append(item)
        return out

    def needs(self, pid=None):
        if pid:
            project = self.registry.get(pid)
            projects = [project] if project else []
        else:
            projects = self.registry.projects()
        return needs_you([self.reader(p) for p in projects])

    def reaction_list(self, query):
        records = self.store.query(
            pid=query.get("pid") or None,
            version=query.get("version") or None,
            kind=query.get("kind") or None,
            target_type=query.get("target_type") or None,
            target_id=query.get("target_id") or None,
            actor=query.get("actor") or None,
            include_acks=query.get("include_acks") == "1",
            include_retracted=query.get("include_retracted") == "1",
        )
        names = {p.id: p.name for p in self.registry.projects()}
        names[LIBRARY_PID] = "Library"
        readers = {}
        return [dict(r, project=names.get(r.get("pid")),
                     target_words=self.target_words(r, readers) if r.get("pid") != LIBRARY_PID else None) for r in records]

    def base_url(self):
        return f"http://127.0.0.1:{self.port}/"

    def project_for_path(self, path):
        """The job whose folder is path or holds it (the deepest match), or None."""
        real = os.path.realpath(path)
        best, best_len = None, -1
        for project in self.registry.projects():
            folder = os.path.realpath(project.path)
            if (real == folder or real.startswith(folder.rstrip(os.sep) + os.sep)) and len(folder) > best_len:
                best, best_len = project, len(folder)
        return best

    def target_words(self, record, readers):
        """A reaction's target for the producer: the gate or node by its display name and number, the version,
        the finding id and title, the time."""
        project = self.registry.get(record.get("pid")) if isinstance(record.get("pid"), str) else None
        if project is None or record.get("kind") == "prefer":
            return None
        reader = readers.get(project.id)
        if reader is None:
            reader = readers[project.id] = self.reader(project)
        graph = reader.graph
        target = record.get("target") or {}
        kind, ident, version = target.get("type"), target.get("id"), record.get("version")
        on = f" on {version}" if version else ""
        at = f" at {_fmt_t(target.get('t'))}" if target.get("t") is not None else ""
        if kind in ("node", "gate") and ident in graph.by_id:
            summary = graph.summary(ident)
            name = display_name(summary.get("name"), summary.get("short"))
            tag = f" (N{summary['num']:02d})" if summary.get("num") else ""
            if kind == "gate" and ident == graph.release_node() and version:
                return f"Release decision for {version}, gate {name}{tag}"
            return f"{'Gate' if kind == 'gate' else 'Node'} {name}{tag}{on}"
        if kind == "finding" and isinstance(ident, str) and version and reader.has_version(version):
            title = reader.finding_titles(version).get(ident)
            return f"Finding {ident}{on}{at}" + (f": {title}" if title else "")
        return None

    def inbox(self, project, states):
        readers = {}
        return inbox_payload(project, self.store.query(pid=project.id), states,
                             describe=lambda record: self.target_words(record, readers), base_url=self.base_url())

    def inbox_summary(self, with_items=False):
        readers = {}
        return {"jobs": inbox_summary(self.registry.projects(), self.store.query(), with_items=with_items,
                                      describe=(lambda record: self.target_words(record, readers)) if with_items else None),
                "base_url": self.base_url()}

    def describe_target(self, record, readers=None):
        """A readable target label for the export: node and gate names from the job's graph, finding titles."""
        project = self.registry.get(record.get("pid")) if isinstance(record.get("pid"), str) else None
        target = record.get("target") or {}
        ident = target.get("id")
        if project is None:
            return None
        cache = readers if readers is not None else {}
        reader = cache.get(project.id)
        if reader is None:
            reader = cache[project.id] = self.reader(project)
        graph = reader.graph
        if target.get("type") in ("node", "gate") and ident in graph.by_id:
            summary = graph.summary(ident)
            prefix = "Gate" if target.get("type") == "gate" else "Node"
            num = f"N{summary['num']:02d} " if summary.get("num") else ""
            return f"{prefix} {num}{summary['name']}"
        version = record.get("version")
        if target.get("type") == "finding" and isinstance(ident, str) and version and reader.has_version(version):
            title = reader.finding_titles(version).get(ident)
            if title:
                return f"Finding {ident}: {title}"
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "vpg-glassbox"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, format, *args):  # noqa: A002 - signature from BaseHTTPRequestHandler
        if not getattr(self.server, "quiet", False):
            sys.stderr.write("vpg-glassbox: %s\n" % (format % args))

    # ------------------------------------------------------------------ plumbing
    def _common_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)

    def _send_bytes(self, status, body, content_type, head=False, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _json(self, payload, status=200, head=False):
        body = json.dumps(_finite(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", head)

    def _error(self, status, message, head=False):
        if self.path.startswith("/api/") or self.command == "POST":
            body = json.dumps({"error": message}).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        else:
            body = f"{message}\n".encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        self._send_bytes(status, body, content_type, head)

    def _reject(self, status, message):
        """Refuse a POST before parsing it: drain a small body so the client reads the reply, then close."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if 0 < length <= 1024 * 1024 and not self.headers.get("Transfer-Encoding"):
            self.rfile.read(length)
        self.close_connection = True
        self._error(status, message)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.app.allowed_hosts()

    def _safe(self, action, *args):
        try:
            action(*args)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # never leak a traceback to the client
            traceback.print_exc(file=sys.stderr)
            self.close_connection = True
            try:
                self._error(500, "internal error")
            except Exception:
                pass

    # ------------------------------------------------------------------ methods
    def do_GET(self):
        self._safe(self._get, False)

    def do_HEAD(self):
        self._safe(self._get, True)

    def do_POST(self):
        self._safe(self._post)

    def _not_allowed(self):
        self.close_connection = True
        self._send_bytes(405, b'{"error":"method not allowed"}', "application/json; charset=utf-8",
                         extra={"Allow": "GET, HEAD, POST"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _not_allowed

    # ------------------------------------------------------------------ GET
    def _get(self, head):
        if not self._host_ok():
            self.close_connection = True
            return self._error(421, "misdirected request: this server answers only to 127.0.0.1 and localhost", head)
        if len(self.path) > 4096 or "\x00" in self.path or "\\" in self.path:
            return self._error(400, "bad request", head)
        parts = urlsplit(self.path)
        path = parts.path
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        if path in ("/", "/index.html"):
            return self._static("index.html", head)
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):], head)
        if path.startswith("/media/"):
            return self._media(path, head)
        if path.startswith("/api/"):
            return self._api(path, query, head)
        return self._error(404, "not found", head)

    def _api(self, path, query, head):
        segments = [unquote(s) for s in path.split("/")[2:]]
        app = self.app
        if segments == ["meta"]:
            return self._json(app.meta(), head=head)
        if segments == ["projects"]:
            return self._json(app.projects_list(), head=head)
        if segments == ["needs-you"]:
            return self._json(app.needs(query.get("pid") or None), head=head)
        if segments == ["films"]:
            return self._json(films_payload(app), head=head)
        if segments == ["reactions"]:
            return self._json(app.reaction_list(query), head=head)
        if segments == ["reactions", "export"]:
            return self._export(query, head)
        if segments == ["inbox"]:
            return self._inbox(query, head)
        if segments == ["inbox", "summary"]:
            payload = app.inbox_summary(with_items=query.get("items") == "1")
            if query.get("format") in ("md", "markdown"):
                text = summary_markdown(payload["jobs"], base_url=payload["base_url"])
                return self._send_bytes(200, text.encode("utf-8"), "text/markdown; charset=utf-8", head)
            return self._json(payload, head=head)
        if segments and segments[0] == "library":
            return self._library_api(segments[1:], query, head)
        if len(segments) >= 2 and segments[0] == "projects":
            project = app.registry.get(segments[1])
            if project is None:
                return self._error(404, "unknown project", head)
            reader = app.reader(project)
            rest = segments[2:]
            if not rest:
                return self._json(reader.detail(), head=head)
            if len(rest) == 2 and rest[0] == "candidates":
                if not reader.has_version(rest[1]):
                    return self._error(404, "unknown version", head)
                return self._json(reader.candidate_detail(rest[1]), head=head)
            if rest == ["compare"]:
                a, b = query.get("a"), query.get("b")
                if not reader.has_version(a) or not reader.has_version(b):
                    return self._error(404, "compare needs two existing versions a and b", head)
                return self._json(reader.compare(a, b), head=head)
            if len(rest) == 2 and rest[0] == "gates":
                if not reader.graph.human_gate(rest[1]):
                    return self._error(404, "unknown human gate", head)
                return self._json(reader.gate_evidence(rest[1]), head=head)
            if rest[0] in ("nodes", "gates"):
                version = query.get("version") or query.get("v")
                if version and not reader.has_version(version):
                    return self._error(404, "unknown version", head)
                version = version or reader.focus_version()
                if rest == ["nodes"]:
                    return self._json(reader.node_entries(version), head=head)
                if rest == ["gates"]:
                    return self._json(reader.gates_board(version), head=head)
                if len(rest) == 2 and rest[0] == "nodes":
                    if rest[1] not in reader.graph.by_id:
                        return self._error(404, "unknown node", head)
                    return self._json(reader.node_detail(rest[1], version), head=head)
        return self._error(404, "not found", head)

    def _library_api(self, rest, query, head):
        library = self.app.library
        if library is None:
            if rest in ([], ["status"]):
                return self._json({"enabled": False, "status": "disabled", "films": [], "other": [], "counts": None},
                                  head=head)
            return self._error(404, "the library is disabled on this server (--no-library)", head)
        if not rest:
            return self._json(library.overview(), head=head)
        if rest == ["status"]:
            return self._json(library.status(), head=head)
        if rest == ["items"]:
            try:
                offset = int(query.get("offset") or 0)
                limit = int(query.get("limit") or 60)
            except ValueError:
                return self._error(400, "offset and limit must be integers", head)
            role = query.get("role") or ""
            if role and role not in ROLE_NAMES:
                return self._error(400, "unknown role", head)
            sort = query.get("sort") or "newest"
            if sort not in ("newest", "name", "size"):
                return self._error(400, "sort must be newest, name or size", head)
            return self._json(library.items(q=(query.get("q") or "")[:200], role=role, area=(query.get("area") or "")[:200],
                                            sort=sort, offset=offset, limit=limit), head=head)
        if len(rest) == 2 and rest[0] in ("films", "collections", "items"):
            ident = rest[1]
            if not _LIB_ID.match(ident):
                return self._error(404, "not found", head)
            if rest[0] == "films":
                payload = library.film(ident)
            elif rest[0] == "collections":
                payload = library.collection(ident)
            else:
                payload = library.item_detail(ident)
            if payload is None:
                return self._error(404, "not found in the library index", head)
            return self._json(payload, head=head)
        return self._error(404, "not found", head)

    def _export(self, query, head):
        if query.get("format", "md") not in ("md", "markdown"):
            return self._error(400, "format must be md", head)
        pid = query.get("pid") or None
        if pid == LIBRARY_PID:
            projects = []
            records = self.app.store.query(pid=pid, include_retracted=True)
        elif pid:
            project = self.app.registry.get(pid)
            if project is None:
                return self._error(404, "unknown project", head)
            projects = [project]
            records = self.app.store.query(pid=pid, include_retracted=True)
        else:
            records = self.app.store.query(include_retracted=True)
            pids = {r.get("pid") for r in records}
            projects = [p for p in self.app.registry.projects() if p.id in pids]
        readers = {}
        text = export_markdown(records, projects, describe=lambda record: self.app.describe_target(record, readers),
                               base_url=f"http://127.0.0.1:{self.app.port}/")
        if pid == LIBRARY_PID:
            slug = "library"
        else:
            slug = _SLUG.sub("-", (projects[0].slug or projects[0].name) if len(projects) == 1 else "all-jobs").strip("-")[:60]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        disposition = "attachment" if query.get("download") == "1" else "inline"
        filename = f"vpg-reactions-{slug or 'job'}-{stamp}.md"
        self._send_bytes(200, text.encode("utf-8"), "text/markdown; charset=utf-8", head,
                         extra={"Content-Disposition": f'{disposition}; filename="{filename}"'})

    def _inbox(self, query, head):
        """GET /api/inbox?pid=<pid> or ?project=<absolute job folder>[&state=...][&format=md]: the owner's reactions
        that wait for the job's producer, with their threads."""
        try:
            states = parse_states(query.get("state"))
        except ValueError as error:
            return self._error(400, str(error), head)
        pid, folder = query.get("pid"), query.get("project")
        if pid:
            project = self.app.registry.get(pid)
            if project is None:
                return self._error(404, "unknown project", head)
        elif folder:
            if len(folder) > 4096 or not os.path.isabs(folder):
                return self._error(400, "project must be the absolute path of a job folder", head)
            project = self.app.project_for_path(folder)
            if project is None:
                return self._error(404, "no Glass Box job at this folder: it must hold vpg.project.yaml under a scanned root",
                                   head)
        else:
            return self._error(400, "pid or project is required", head)
        payload = self.app.inbox(project, states)
        if query.get("format") in ("md", "markdown"):
            return self._send_bytes(200, inbox_markdown(payload).encode("utf-8"), "text/markdown; charset=utf-8", head)
        return self._json(payload, head=head)

    def _static(self, name, head):
        segments = name.split("/")
        if not name or any(s in ("", ".", "..") or s.startswith(".") for s in segments):
            return self._error(404, "not found", head)
        root = self.app.static_dir
        target = os.path.realpath(os.path.join(root, *[unquote(s) for s in segments]))
        content_type = STATIC_TYPES.get(os.path.splitext(target)[1].lower())
        if content_type is None or not fsio.is_within(target, root) or not os.path.isfile(target):
            return self._error(404, "not found", head)
        return self._file(target, content_type, head, ranges=False)

    def _library_media(self, raw, head):
        """/media/lib/{id}, /media/lib/{id}/poster, /media/lib/{id}/captions.vtt: indexed ids only."""
        library = self.app.library
        if library is None or len(raw) not in (2, 3) or not _LIB_ID.match(raw[1]):
            return self._error(404, "not found", head)
        kind = "video" if len(raw) == 2 else {"poster": "poster", "captions.vtt": "captions"}.get(raw[2])
        found = library.media_path(raw[1], kind) if kind else None
        if not found:
            return self._error(404, "not found", head)
        target, content_type, convert = found
        if convert:
            text, _ = fsio.read_text(target, 2 * 1024 * 1024)
            return self._send_bytes(200, srt_to_vtt(text or "").encode("utf-8"), content_type, head)
        return self._file(target, content_type, head, ranges=kind == "video")

    def _media(self, path, head):
        raw = path.split("/")[2:]
        if raw and raw[0] == "lib":
            return self._library_media(raw, head)
        if len(raw) < 3:
            return self._error(404, "not found", head)
        segments = [unquote(s) for s in raw]
        project = self.app.registry.get(segments[0])
        if project is None:
            return self._error(404, "not found", head)
        try:
            if segments[1] == "_run":
                allowed = self.app.reader(project).run_model().allowed_media()
                target, convert = media.resolve_run(project.path, segments[2:], allowed)
            else:
                target, convert = media.resolve(project.path, segments[1], segments[2:])
        except media.MediaError:
            return self._error(404, "not found", head)
        if convert:
            text, _ = fsio.read_text(target, 2 * 1024 * 1024)
            return self._send_bytes(200, srt_to_vtt(text or "").encode("utf-8"), media.content_type(target, True), head)
        return self._file(target, media.content_type(target), head, ranges=True)

    def _file(self, target, content_type, head, ranges):
        try:
            handle = open(target, "rb")
        except OSError:
            return self._error(404, "not found", head)
        with handle:
            size = os.fstat(handle.fileno()).st_size
            span = media.parse_range(self.headers.get("Range"), size) if ranges else None
            if span == "unsatisfiable":
                return self._send_bytes(416, b"", content_type, head, extra={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
            if span:
                start, end = span
                status, length = 206, end - start + 1
            else:
                start, status, length = 0, 200, size
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            if ranges:
                self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{start + length - 1}/{size}")
            self._common_headers()
            self.end_headers()
            if head:
                return
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ------------------------------------------------------------------ POST
    def _post(self):
        if not self._host_ok():
            return self._reject(421, "misdirected request: this server answers only to 127.0.0.1 and localhost")
        route = urlsplit(self.path).path
        if route not in POST_PATHS:
            return self._reject(404, "not found")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.app.allowed_origins():
            return self._reject(403, "cross-origin request refused")
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if fetch_site and fetch_site not in ("same-origin", "none"):
            return self._reject(403, "cross-site request refused")
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return self._reject(415, "Content-Type must be application/json")
        if self.headers.get("Transfer-Encoding"):
            return self._reject(411, "a Content-Length is required")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._reject(411, "a Content-Length is required")
        if length < 0:
            return self._reject(400, "bad Content-Length")
        if length > MAX_BODY:
            return self._reject(413, "body larger than 64 KB")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._error(400, "body is not valid JSON")
        if route == "/api/library/rescan":
            return self._rescan(body)
        actor_header = self.headers.get("X-Actor")
        actor = "owner" if actor_header is None else actor_header.strip().lower()
        if route == "/api/reactions/batch":
            return self._batch(body, actor)
        try:
            record = validate_reaction(body, actor, _Context(self.app))
        except ReactionError as error:
            return self._error(error.status, str(error))
        return self._json(self.app.store.append(record), status=201)

    def _batch(self, body, actor):
        """{"reactions": [...]} with at most 100 bodies: every one is validated before any is stored."""
        reactions = body.get("reactions") if isinstance(body, dict) else None
        if not isinstance(body, dict) or set(body) != {"reactions"} or not isinstance(reactions, list) or not reactions:
            return self._error(400, 'the batch body must be {"reactions": [one or more reaction bodies]}')
        if len(reactions) > MAX_BATCH:
            return self._error(413, f"at most {MAX_BATCH} reactions per batch")
        ctx = _Context(self.app)
        records = []
        for index, item in enumerate(reactions):
            if isinstance(item, dict) and (item.get("kind") == "retract" or item.get("kind") in THREAD_KINDS):
                return self._json({"error": "a batch takes notes and reactions, not retract, ack, resolve or reopen",
                                   "index": index}, status=400)
            try:
                records.append(validate_reaction(item, actor, ctx))
            except ReactionError as error:
                return self._json({"error": f"reaction {index + 1}: {error}", "index": index}, status=error.status)
        return self._json({"records": self.app.store.append_many(records)}, status=201)

    def _rescan(self, body):
        if not isinstance(body, dict) or body:
            return self._error(400, "the rescan body must be an empty JSON object {}")
        library = self.app.library
        if library is None:
            return self._error(409, "the library is disabled on this server (--no-library)")
        if not library.rescan():
            return self._json({"error": "a library scan is already running", "status": library.status()}, status=409)
        return self._json({"started": True, "status": library.status()}, status=202)


class GlassboxServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, app, port=0, quiet=False):
        self.app = app
        self.quiet = quiet
        super().__init__(("127.0.0.1", port), Handler)
        app.port = self.server_address[1]


def make_server(roots, engine_dir, data_dir, port=0, quiet=True, library_roots=None, library_home=None, static_dir=None):
    """library_roots None disables the library; indexing requires explicitly supplied roots.
    static_dir serves another UI folder (for example static_next while a redesign is verified)."""
    for root in [*roots, *(library_roots or []), engine_dir, static_dir or STATIC_DIR]:
        if fsio.is_within(data_dir, root):
            raise ValueError(f"data directory must be outside the read-only roots: {root}")
    app = App(Registry(roots), Engine(engine_dir), ReactionStore(data_dir), static_dir=static_dir)
    if library_roots is not None:
        app.library = Library(library_roots, data_dir, home=library_home, jobs=app.library_jobs)
    return GlassboxServer(app, port=port, quiet=quiet)
