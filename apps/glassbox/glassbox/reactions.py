"""Owner reactions: strict validation, an append-only JSONL store and a Markdown export."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from .fsio import version_key

KINDS = ("approve", "request_changes", "agree", "disagree", "note", "flag", "prefer", "ack", "retract", "resolve", "reopen")
RETRACTABLE = frozenset({"agree", "disagree", "note", "flag", "prefer"})
# The producer channel: an agent picks an owner reaction up (ack, with its plan) and says where it was addressed
# (resolve, with the version); the owner reopens a resolve that did not land. These three form a reaction's thread.
THREAD_KINDS = ("ack", "resolve", "reopen")
AGENT_KINDS = ("ack", "resolve")
OWNER_ROOT_KINDS = ("approve", "request_changes", "agree", "disagree", "note", "flag", "prefer")
THREAD_STATES = ("open", "picked_up", "resolved")
MAX_PRODUCER_CHARS = 80
MAX_BATCH = 100
TARGET_TYPES = ("version", "node", "gate", "finding", "time")
KIND_TARGETS = {
    "approve": frozenset({"version", "gate"}),
    "request_changes": frozenset({"version", "gate"}),
    "agree": frozenset({"finding", "gate"}),
    "disagree": frozenset({"finding", "gate"}),
    "note": frozenset(TARGET_TYPES),
    "flag": frozenset(TARGET_TYPES),
}
QUICK_TAGS = ("look", "type", "colour", "motion", "story", "voice", "music", "mix", "accuracy", "accessibility")
BODY_KEYS = frozenset({"pid", "kind", "version", "target", "a", "b", "choice", "ref", "t", "tags", "text", "producer"})
TARGET_KEYS = frozenset({"type", "id", "version", "t"})
MAX_TAGS = 10
MAX_TAG_CHARS = 40
MAX_TEXT_CHARS = 4000
MAX_TARGET_ID_CHARS = 120
MAX_T = 24 * 3600.0
FILE_NAME = "reactions.jsonl"
LIBRARY_PID = "library"
LIBRARY_KINDS = ("note", "flag", "prefer", "ack")
LIBRARY_TARGET_KEYS = frozenset({"type", "id", "t"})
_LIBRARY_ID = re.compile(r"^[0-9a-f]{16}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NODE_ID = re.compile(r"^n\d{2}_[a-z0-9_]+$")
KIND_LABELS = {
    "approve": "Approve", "request_changes": "Request changes", "agree": "Agree", "disagree": "Disagree",
    "note": "Note", "flag": "Flag", "prefer": "Prefer", "ack": "Acknowledged", "retract": "Retracted",
    "resolve": "Addressed", "reopen": "Reopened",
}


class ReactionError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _string(value, field, max_chars, allow_newlines=False):
    if not isinstance(value, str):
        raise ReactionError(f"{field} must be a string")
    if len(value) > max_chars:
        raise ReactionError(f"{field} is longer than {max_chars} characters")
    probe = value.replace("\n", "").replace("\t", "") if allow_newlines else value
    if _CONTROL.search(probe) or (not allow_newlines and ("\n" in value or "\t" in value)):
        raise ReactionError(f"{field} contains control characters")
    return value


def _seconds(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReactionError(f"{field} must be a number of seconds")
    if not math.isfinite(value) or value < 0 or value > MAX_T:
        raise ReactionError(f"{field} must be between 0 and {int(MAX_T)} seconds")
    return round(float(value), 3)


def _tags(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReactionError("tags must be a list of strings")
    if len(value) > MAX_TAGS:
        raise ReactionError(f"at most {MAX_TAGS} tags")
    tags = []
    for item in value:
        tag = _string(item, "tag", MAX_TAG_CHARS).strip()
        if not tag:
            raise ReactionError("tags must not be empty")
        if tag not in tags:
            tags.append(tag)
    return tags


def _version(ctx, project, value, field):
    if value is None:
        return None
    _string(value, field, 64)
    if value not in ctx.versions(project):
        raise ReactionError(f"{field} {value!r} is not a candidate of this job")
    return value


def _library_video(ctx, value, field):
    if not isinstance(value, str) or not _LIBRARY_ID.match(value):
        raise ReactionError(f"{field} must be the id of a library video")
    item = ctx.library_item(value) if hasattr(ctx, "library_item") else None
    if item is None:
        raise ReactionError(f"{field} is not a video in the library index")
    return item


def _library_reaction(body, kind, record, t, ctx):
    """note, flag and prefer on library videos (pid 'library'): targets are indexed video ids, never paths."""
    if kind not in LIBRARY_KINDS:
        raise ReactionError("library videos take note, flag, prefer or ack reactions")
    if kind == "prefer":
        for field in ("target", "ref", "version"):
            if body.get(field) is not None:
                raise ReactionError(f"prefer does not take {field}")
        a = _library_video(ctx, body.get("a"), "a")
        b = _library_video(ctx, body.get("b"), "b")
        if a["id"] == b["id"]:
            raise ReactionError("a and b must differ")
        choice = body.get("choice")
        if choice not in ("a", "b", "tie"):
            raise ReactionError("choice must be 'a', 'b' or 'tie'")
        chosen = b if choice == "b" else a
        record.update(a=a["id"], b=b["id"], choice=choice, t=t, target={"type": "library", "id": chosen["id"]},
                      library={"a": a, "b": b})
        return record
    for field in ("a", "b", "choice", "ref", "version"):
        if body.get(field) is not None:
            raise ReactionError(f"{kind} on a library video does not take {field}")
    target = body.get("target")
    if not isinstance(target, dict):
        raise ReactionError("a library reaction needs target {type: 'library', id}")
    extra = sorted(set(target) - LIBRARY_TARGET_KEYS)
    if extra:
        raise ReactionError(f"unknown target field{'s' if len(extra) > 1 else ''}: {', '.join(extra)}")
    if target.get("type") != "library":
        raise ReactionError("pid library takes only target.type 'library'")
    item = _library_video(ctx, target.get("id"), "target.id")
    target_t = _seconds(target.get("t"), "target.t")
    if t is not None and target_t is not None and abs(t - target_t) > 1e-6:
        raise ReactionError("t and target.t differ")
    t = t if t is not None else target_t
    if kind == "note" and not record["text"]:
        raise ReactionError("a note needs text")
    clean = {"type": "library", "id": item["id"]}
    if t is not None:
        clean["t"] = t
    record.update(target=clean, t=t, library=item)
    return record


def _retract(body, pid, record, ctx):
    """Undo: a retract names one of the owner's notes, agrees, disagrees, flags or preferences, which then leaves the
    active lists; the export still lists it as retracted."""
    for field in ("target", "a", "b", "choice", "version", "t", "tags"):
        if body.get(field) not in (None, []):
            raise ReactionError(f"retract does not take {field}")
    ref = body.get("ref")
    if not isinstance(ref, str) or not ref:
        raise ReactionError("retract needs ref, the id of one of your reactions")
    original = ctx.find_reaction(ref)
    if original is None or original.get("pid") != pid:
        raise ReactionError("ref does not name a reaction of this job")
    if original.get("actor") != "owner" or original.get("kind") not in RETRACTABLE:
        raise ReactionError("only your notes, agrees, disagrees, flags and preferences can be retracted")
    if hasattr(ctx, "is_retracted") and ctx.is_retracted(ref):
        raise ReactionError("this reaction is already retracted", status=409)
    record.update(ref=ref, version=original.get("version"), target=original.get("target"), t=original.get("t"), tags=[])
    if original.get("library"):
        record["library"] = original["library"]
    return record


def thread_state(events):
    """open (waiting for the producer), picked_up or resolved: the last ack, resolve or reopen of a thread decides."""
    state = "open"
    for event in events or []:
        kind = event.get("kind")
        if kind == "ack":
            state = "picked_up"
        elif kind == "resolve":
            state = "resolved"
        elif kind == "reopen":
            state = "open"
    return state


def _thread_root(ctx, pid, ref, kind):
    """The owner reaction an ack or resolve answers."""
    if not isinstance(ref, str) or not ref:
        raise ReactionError(f"{kind} needs ref, the id of an existing reaction")
    original = ctx.find_reaction(ref)
    if original is None or original.get("pid") != pid:
        raise ReactionError("ref does not name a reaction of this job")
    if kind == "ack" and original.get("kind") == "ack":
        raise ReactionError("an ack cannot acknowledge another ack")
    if original.get("kind") not in OWNER_ROOT_KINDS or original.get("actor") != "owner":
        raise ReactionError(f"{kind} needs ref, the id of an owner reaction (not a {original.get('kind')})")
    return original


def _thread_events(ctx, root_id):
    return ctx.thread_events(root_id) if hasattr(ctx, "thread_events") else []


def _resolve(body, pid, project, record, ctx):
    """An agent says where an owner reaction was addressed: ref, the version that addresses it, and what changed."""
    for field in ("target", "a", "b", "choice", "t"):
        if body.get(field) is not None:
            raise ReactionError(f"resolve does not take {field}")
    if record["tags"]:
        raise ReactionError("resolve does not take tags")
    original = _thread_root(ctx, pid, body.get("ref"), "resolve")
    if hasattr(ctx, "is_retracted") and ctx.is_retracted(original["id"]):
        raise ReactionError("the owner retracted this reaction", status=409)
    version = _version(ctx, project, body.get("version"), "version")
    if version is None:
        raise ReactionError("resolve needs version, the version that addresses it")
    if not record["text"]:
        raise ReactionError("resolve needs text: what changed")
    if thread_state(_thread_events(ctx, original["id"])) == "resolved":
        raise ReactionError("this reaction is already addressed; the owner can reopen it", status=409)
    record.update(ref=original["id"], version=version, target=original.get("target"), t=original.get("t"))
    return record


def _reopen(body, pid, record, ctx):
    """The owner says a resolve did not land: ref names the resolve; the text says what is still wrong."""
    for field in ("target", "a", "b", "choice", "version", "t"):
        if body.get(field) is not None:
            raise ReactionError(f"reopen does not take {field}")
    if record["tags"]:
        raise ReactionError("reopen does not take tags")
    ref = body.get("ref")
    if not isinstance(ref, str) or not ref:
        raise ReactionError("reopen needs ref, the id of a resolve")
    resolve = ctx.find_reaction(ref)
    if resolve is None or resolve.get("pid") != pid:
        raise ReactionError("ref does not name a reaction of this job")
    if resolve.get("kind") != "resolve":
        raise ReactionError("reopen needs ref, the id of a resolve")
    if not record["text"]:
        raise ReactionError("reopen needs text: what did not land")
    events = _thread_events(ctx, resolve.get("ref"))
    if thread_state(events) != "resolved" or (events and events[-1].get("id") != ref):
        raise ReactionError("this resolve is no longer the latest word on the reaction", status=409)
    record.update(ref=ref, root=resolve.get("ref"), version=resolve.get("version"), target=resolve.get("target"),
                  t=resolve.get("t"))
    return record


def validate_reaction(body, actor, ctx):
    """Check a POST body; return the record to store (without id and ts).

    ctx provides project(pid), versions(project), node_ids(), human_gate_ids(),
    release_node() and find_reaction(id); for pid 'library' also library_enabled() and library_item(id);
    thread_events(id) and is_retracted(id) when available.
    """
    if not isinstance(body, dict):
        raise ReactionError("body must be a JSON object")
    unknown = sorted(set(body) - BODY_KEYS)
    if unknown:
        raise ReactionError(f"unknown field{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}")
    if actor not in ("owner", "agent"):
        raise ReactionError("X-Actor must be 'owner' or 'agent'")
    pid = body.get("pid")
    if not isinstance(pid, str):
        raise ReactionError("pid is required")
    if pid == LIBRARY_PID:
        if not (hasattr(ctx, "library_enabled") and ctx.library_enabled()):
            raise ReactionError("the library is disabled on this server")
        project = None
    else:
        project = ctx.project(pid)
        if project is None:
            raise ReactionError("unknown pid")
    kind = body.get("kind")
    if kind not in KINDS:
        raise ReactionError(f"kind must be one of: {', '.join(KINDS)}")
    if actor == "agent" and kind not in AGENT_KINDS:
        raise ReactionError("the agent actor may only post ack and resolve reactions")
    if kind == "retract" and actor != "owner":
        raise ReactionError("only the owner retracts a reaction")
    if kind == "reopen" and actor != "owner":
        raise ReactionError("only the owner reopens a resolved reaction")
    if kind in AGENT_KINDS and actor != "agent":
        raise ReactionError(f"{kind} requires the header X-Actor: agent")
    text = body.get("text")
    text = _string(text, "text", MAX_TEXT_CHARS, allow_newlines=True).strip() if text is not None else ""
    record = {
        "actor": actor, "pid": pid, "project_path": project.path if project else None, "kind": kind,
        "version": None, "target": None, "t": None, "tags": _tags(body.get("tags")), "text": text,
    }
    producer = body.get("producer")
    if producer is not None:
        if kind not in AGENT_KINDS:
            raise ReactionError("producer applies to ack and resolve only")
        producer = _string(producer, "producer", MAX_PRODUCER_CHARS).strip()
    if kind in AGENT_KINDS:
        record["producer"] = producer or None
    t = _seconds(body.get("t"), "t")
    if kind == "retract":
        return _retract(body, pid, record, ctx)
    if pid == LIBRARY_PID and kind in ("resolve", "reopen"):
        raise ReactionError("resolve and reopen apply to job reactions, not library videos")
    if pid == LIBRARY_PID and kind != "ack":
        return _library_reaction(body, kind, record, t, ctx)
    if kind == "resolve":
        return _resolve(body, pid, project, record, ctx)
    if kind == "reopen":
        return _reopen(body, pid, record, ctx)

    if kind == "prefer":
        for field in ("target", "ref", "version"):
            if body.get(field) is not None:
                raise ReactionError(f"prefer does not take {field}")
        a = _version(ctx, project, body.get("a"), "a")
        b = _version(ctx, project, body.get("b"), "b")
        if a is None or b is None:
            raise ReactionError("prefer needs versions a and b")
        if a == b:
            raise ReactionError("a and b must differ")
        choice = body.get("choice")
        if choice not in ("a", "b", "tie"):
            raise ReactionError("choice must be 'a', 'b' or 'tie'")
        record.update(a=a, b=b, choice=choice, t=t)
        return record

    if kind == "ack":
        for field in ("target", "a", "b", "choice", "version", "t"):
            if body.get(field) is not None:
                raise ReactionError(f"ack does not take {field}")
        original = _thread_root(ctx, pid, body.get("ref"), "ack")
        record.update(ref=original["id"], version=original.get("version"), target=original.get("target"),
                      t=original.get("t"))
        return record

    for field in ("a", "b", "choice", "ref"):
        if body.get(field) is not None:
            raise ReactionError(f"{kind} does not take {field}")
    version = _version(ctx, project, body.get("version"), "version")
    target = body.get("target")
    if target is None:
        if version is None:
            raise ReactionError("version or target is required")
        target = {"type": "version", "id": version}
    if not isinstance(target, dict):
        raise ReactionError("target must be an object")
    extra = sorted(set(target) - TARGET_KEYS)
    if extra:
        raise ReactionError(f"unknown target field{'s' if len(extra) > 1 else ''}: {', '.join(extra)}")
    target_type = target.get("type")
    if target_type not in TARGET_TYPES:
        raise ReactionError(f"target.type must be one of: {', '.join(TARGET_TYPES)}")
    target_id = _string(target.get("id"), "target.id", MAX_TARGET_ID_CHARS).strip() if target.get("id") is not None else ""
    if not target_id:
        raise ReactionError("target.id is required")
    target_version = _version(ctx, project, target.get("version"), "target.version")
    if version is not None and target_version is not None and version != target_version:
        raise ReactionError("version and target.version differ")
    version = version or target_version
    target_t = _seconds(target.get("t"), "target.t")
    if t is not None and target_t is not None and abs(t - target_t) > 1e-6:
        raise ReactionError("t and target.t differ")
    t = t if t is not None else target_t
    if target_type not in KIND_TARGETS[kind]:
        raise ReactionError(f"{kind} does not apply to a {target_type} target")
    if target_type == "version":
        _version(ctx, project, target_id, "target.id")
        if version is not None and version != target_id:
            raise ReactionError("version and target.id differ")
        version = target_id
    elif target_type == "node":
        node_ids = ctx.node_ids()
        if (node_ids and target_id not in node_ids) or (not node_ids and not _NODE_ID.match(target_id)):
            raise ReactionError("target.id is not a node of the engine graph")
    elif target_type == "gate":
        if kind in ("approve", "request_changes") and target_id not in ctx.human_gate_ids():
            raise ReactionError("approve and request_changes apply to human gates only")
        if target_id == ctx.release_node() and version is None:
            raise ReactionError("the release gate needs a version")
    if target_type in ("finding", "time") and version is None:
        raise ReactionError(f"a {target_type} target needs a version")
    if target_type == "time" and t is None:
        raise ReactionError("a time target needs t")
    if kind == "note" and not text:
        raise ReactionError("a note needs text")
    if kind in ("approve", "request_changes") and hasattr(ctx, "decision_allowed"):
        gate_id = target_id if target_type == "gate" else ctx.release_node()
        if gate_id:
            allowed, why = ctx.decision_allowed(project, gate_id, version)
            if not allowed:
                raise ReactionError(f"this gate is not waiting for a decision ({why})", status=409)
    clean_target = {"type": target_type, "id": target_id}
    if version is not None:
        clean_target["version"] = version
    if t is not None:
        clean_target["t"] = t
    record.update(version=version, target=clean_target, t=t)
    return record


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ReactionStore:
    """Append-only JSONL at <data_dir>/reactions.jsonl; writes take an exclusive flock."""

    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(os.path.expanduser(data_dir))
        self.path = os.path.join(self.data_dir, FILE_NAME)
        self._lock = threading.Lock()
        self._cache = (None, [])
        self._threads = (None, {})

    def append(self, record):
        return self.append_many([record])[0]

    def append_many(self, records):
        """Append several records with one locked write (all or nothing as far as this process is concerned)."""
        stamp = now_iso()
        fulls = []
        for record in records:
            full = {"id": str(uuid.uuid4()), "ts": stamp}
            full.update(record)
            fulls.append(full)
        data = "".join(json.dumps(full, ensure_ascii=False, separators=(",", ":")) + "\n" for full in fulls).encode("utf-8")
        os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return fulls

    def records(self):
        """All stored records in file order (oldest first); malformed lines are skipped."""
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        signature = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if self._cache[0] == signature:
                return self._cache[1]
        try:
            fd = os.open(self.path, os.O_RDONLY)
        except OSError:
            return []
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            try:
                with os.fdopen(os.dup(fd), "rb") as handle:
                    data = handle.read()
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        records = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                records.append(item)
        with self._lock:
            self._cache = (signature, records)
        return records

    def find(self, reaction_id):
        for record in self.records():
            if record.get("id") == reaction_id:
                return record
        return None

    def threads(self):
        """{owner reaction id: [ack, resolve and reopen events in file order]}; an event is {id, kind, actor, ts, text,
        producer, version} (version: where a resolve says it was addressed)."""
        records = self.records()
        signature = self._cache[0]
        with self._lock:
            if signature is not None and self._threads[0] == signature:
                return self._threads[1]
        by_id = {r.get("id"): r for r in records}
        out = {}
        for record in records:
            kind = record.get("kind")
            if kind not in THREAD_KINDS:
                continue
            root = record.get("ref")
            if kind == "reopen":
                root = record.get("root") or (by_id.get(root) or {}).get("ref")
            if not isinstance(root, str) or not root:
                continue
            out.setdefault(root, []).append({
                "id": record.get("id"), "kind": kind, "actor": record.get("actor"), "ts": record.get("ts"),
                "text": record.get("text") or "", "producer": record.get("producer"),
                "version": record.get("version") if kind in ("resolve", "reopen") else None,
            })
        with self._lock:
            self._threads = (signature, out)
        return out

    def thread_of(self, reaction_id):
        return self.threads().get(reaction_id, [])

    def retractions(self):
        """{original id: {id, ts}} for every retracted reaction."""
        out = {}
        for record in self.records():
            if record.get("kind") == "retract" and record.get("ref") and record["ref"] not in out:
                out[record["ref"]] = {"id": record["id"], "ts": record.get("ts")}
        return out

    def query(self, pid=None, version=None, kind=None, target_type=None, target_id=None, actor=None, include_acks=False,
              include_retracted=False):
        """Newest first; each owner record carries acks:[{id, ts, text, producer}], its thread (ack, resolve and reopen
        events, oldest first) and its state (open, picked_up or resolved). Thread records list only with include_acks.
        Retract records never list; a retracted record lists only with include_retracted, then carrying
        retracted:{id, ts}."""
        records = self.records()
        retracted = self.retractions()
        threads = self.threads()
        out = []
        for record in reversed(records):
            if record.get("kind") in THREAD_KINDS and not include_acks:
                continue
            if record.get("kind") == "retract":
                continue
            if record.get("id") in retracted and not include_retracted:
                continue
            if pid and record.get("pid") != pid:
                continue
            if kind and record.get("kind") != kind:
                continue
            if actor and record.get("actor") != actor:
                continue
            if version and not matches_version(record, version):
                continue
            target = record.get("target") or {}
            if target_type and target.get("type") != target_type:
                continue
            if target_id and target.get("id") != target_id:
                continue
            item = dict(record)
            if record.get("kind") not in THREAD_KINDS:
                events = threads.get(record["id"], [])
                item["acks"] = [{"id": e["id"], "ts": e["ts"], "text": e["text"], "producer": e["producer"]}
                                for e in events if e["kind"] == "ack"]
                item["thread"] = events
                item["state"] = thread_state(events)
            if record.get("id") in retracted:
                item["retracted"] = retracted[record["id"]]
            out.append(item)
        return out


def matches_version(record, version):
    if record.get("version") == version:
        return True
    return record.get("kind") == "prefer" and version in (record.get("a"), record.get("b"))


def _fmt_t(seconds):
    if seconds is None:
        return None
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes):02d}:{rest:04.1f}"


def _fmt_ts(ts):
    """Local, human time for the export ('Oct 21, 6:38 PM CDT'); the raw value when it cannot be parsed."""
    if not isinstance(ts, str):
        return "unknown time"
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone()
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local.strftime('%b')} {local.day}, {hour}:{local.strftime('%M %p')} {local.strftime('%Z')}".strip()


def _library_route(record):
    lib = record.get("library") if isinstance(record.get("library"), dict) else {}
    if record.get("kind") == "prefer":
        a = lib.get("a") if isinstance(lib.get("a"), dict) else {}
        b = lib.get("b") if isinstance(lib.get("b"), dict) else {}
        if a.get("film_id") and a.get("film_id") == b.get("film_id"):
            return (f"#/library/f/{quote(str(a['film_id']), safe='')}/compare?a={quote(str(record.get('a')), safe='')}"
                    f"&b={quote(str(record.get('b')), safe='')}")
        return f"#/library/i/{quote(str(record.get('a')), safe='')}"
    target = record.get("target") or {}
    ident = quote(str(target.get("id")), safe="")
    t = f"t={target['t']}" if target.get("t") is not None else ""
    if lib.get("film_id"):
        return f"#/library/f/{quote(str(lib['film_id']), safe='')}?i={ident}" + (f"&{t}" if t else "")
    return f"#/library/i/{ident}" + (f"?{t}" if t else "")


def _library_name(entry):
    entry = entry if isinstance(entry, dict) else {}
    where = " / ".join(str(x) for x in (entry.get("film"), entry.get("version")) if x)
    name = entry.get("name") or entry.get("id") or "unknown video"
    return f"{where}: {name}" if where else str(name)


def _library_label(record):
    lib = record.get("library") if isinstance(record.get("library"), dict) else {}
    if record.get("kind") == "prefer":
        return f"Preference between {_library_name(lib.get('a'))} and {_library_name(lib.get('b'))}"
    if not lib:
        return f"Library video {(record.get('target') or {}).get('id')}"
    return f"Library video {_library_name(lib)}"


def route_of(record):
    """The UI hash route that opens a reaction's target."""
    if record.get("pid") == LIBRARY_PID:
        return _library_route(record)
    pid = record.get("pid")
    version = record.get("version")
    target = record.get("target") or {}
    if record.get("kind") == "prefer":
        return f"#/p/{pid}/compare?a={quote(str(record.get('a')), safe='')}&b={quote(str(record.get('b')), safe='')}"
    kind, ident = target.get("type"), target.get("id")
    v = quote(str(version), safe="") if version else None
    if kind == "finding" and v:
        route = f"#/p/{pid}/v/{v}?f={quote(str(ident), safe='')}"
        return route + (f"&t={target['t']}" if target.get("t") is not None else "")
    if kind == "time" and v:
        return f"#/p/{pid}/v/{v}?t={target.get('t')}"
    if kind in ("node", "gate") and isinstance(ident, str) and _NODE_ID.match(ident):
        return f"#/p/{pid}/n/{quote(ident, safe='')}" + (f"?v={v}" if v else "")
    if kind == "gate" and v:
        return f"#/p/{pid}/gates?v={v}"
    if v:
        return f"#/p/{pid}/v/{v}"
    return f"#/p/{pid}"


def target_label(record, describe=None):
    if record.get("pid") == LIBRARY_PID:
        return _library_label(record)
    if record.get("kind") == "prefer":
        return f"Preference between {record.get('a')} and {record.get('b')}"
    target = record.get("target") or {}
    custom = describe(record) if describe else None
    if custom:
        return custom
    kind = target.get("type")
    ident = target.get("id")
    if kind == "version":
        return f"Version {ident}"
    if kind == "node":
        return f"Node {ident}"
    if kind == "gate":
        return f"Gate {ident}"
    if kind == "finding":
        return f"Finding {ident}"
    if kind == "time":
        return f"Moment at {_fmt_t(target.get('t'))}"
    return "Job"


def _item_line(record):
    """'* <Kind>, <time>[, at mm:ss.s]: <text> Tags: ... Route: #/... [Picked up ...] (Id: ...)'."""
    kind = record.get("kind")
    label = KIND_LABELS.get(kind, str(kind))
    if kind == "prefer":
        choice = record.get("choice")
        chosen = record.get(choice)
        if record.get("pid") == LIBRARY_PID and isinstance(record.get("library"), dict):
            chosen = (record["library"].get(choice) or {}).get("name") or chosen
        label = "Tie" if choice == "tie" else f"Prefer {choice.upper()} ({chosen})"
    head = f"{label}, {_fmt_ts(record.get('ts'))}"
    if record.get("t") is not None:
        head += f", at {_fmt_t(record['t'])}"
    if record.get("actor") and record.get("actor") != "owner":
        head += f", by {record['actor']}"
    text = (record.get("text") or "").strip()
    line = f"* {head}" + (f": {_sentence(text)}" if text else ".")
    if record.get("tags"):
        line += f" Tags: {', '.join(record['tags'])}."
    line += f" Route: {route_of(record)}"
    thread = record.get("thread")
    if thread is None:
        thread = [dict(ack, kind="ack") for ack in record.get("acks") or []]
    for event in thread:
        line += thread_sentence(event, prefix=" ")
    if record.get("retracted"):
        line += f" Retracted by the owner, {_fmt_ts(record['retracted'].get('ts'))}."
    line += f" (Id: {record.get('id')})"
    return line.replace("\n", "\n  ")


def _sentence(text):
    return text if text[-1:] in ".!?" else f"{text}."


def producer_name(event):
    name = (event.get("producer") or "").strip() if isinstance(event.get("producer"), str) else ""
    return name or "the producer"


def thread_sentence(event, prefix=""):
    """'Picked up by <producer>, <time> ("<plan>").' / 'Addressed in vN by <producer>, <time>: <text>' /
    'Reopened by the owner, <time>: <text>'."""
    kind = event.get("kind")
    text = (event.get("text") or "").strip()
    when = _fmt_ts(event.get("ts"))
    if kind == "ack":
        return f'{prefix}Picked up by {producer_name(event)}, {when}' + (f' ("{text}").' if text else ".")
    if kind == "resolve":
        return f"{prefix}Addressed in {event.get('version')} by {producer_name(event)}, {when}" + (f": {_sentence(text)}" if text else ".")
    if kind == "reopen":
        return f"{prefix}Reopened by the owner, {when}" + (f": {_sentence(text)}" if text else ".")
    return ""


def _state(record):
    return record.get("state") or ("picked_up" if record.get("acks") else "open")



def export_markdown(records, projects, now=None, describe=None, base_url=None):
    """Markdown for chat: unacknowledged first, then grouped by job, version and target."""
    now = now or now_iso()
    active = [r for r in records if r.get("kind") not in THREAD_KINDS and r.get("kind") != "retract"]
    pending = [r for r in active if not r.get("retracted") and _state(r) == "open"]
    done = [r for r in active if not r.get("retracted") and _state(r) == "picked_up"]
    addressed = [r for r in active if not r.get("retracted") and _state(r) == "resolved"]
    withdrawn = [r for r in active if r.get("retracted")]
    lines = ["# VPG Glass Box reactions", ""]
    names = [p.name for p in projects]
    has_library = any(r.get("pid") == LIBRARY_PID for r in records)
    if has_library and not names:
        scope = "Library videos."
    elif has_library:
        scope = f"Jobs: {len(names)}, and library videos."
    else:
        scope = f"Job: {names[0]}." if len(names) == 1 else f"Jobs: {len(names)}."
    tail = f", {len(withdrawn)} retracted by the owner" if withdrawn else ""
    lines.append(f"{scope} Exported {_fmt_ts(now)}. {len(pending)} not yet acknowledged, {len(done)} acknowledged, "
                 f"{len(addressed)} addressed{tail}.")
    lines.append("")
    if base_url:
        lines.append(f"Each route opens in VPG Glass Box at {base_url} followed by the route.")
        lines.append("")
    lines.append("To acknowledge one, POST /api/reactions with kind ack, the job pid and ref set to its id, "
                 "and the header X-Actor: agent.")
    lines.append("")
    lines.append("Producers can also run python3 inbox.py in the Glass Box folder: --ack <id> --reply \"<plan>\" when they "
                 "pick a note up, then --resolve <id> --version vN --reply \"<what changed>\" when a version addresses it.")
    by_pid = {p.id: p for p in projects}
    sections = [("Not yet acknowledged", pending), ("Acknowledged", done), ("Addressed by the producer", addressed)]
    if withdrawn:
        sections.append(("Retracted by the owner (ignore these)", withdrawn))
    for title, items in sections:
        lines += ["", f"## {title}", ""]
        if not items:
            lines.append("None.")
            continue
        groups = {}
        for record in items:
            version = record.get("version")
            groups.setdefault((record.get("pid"), version), []).append(record)
        ordered = sorted(groups, key=lambda key: (
            by_pid[key[0]].name.lower() if key[0] in by_pid else str(key[0]),
            key[1] is None,
            tuple(-part if isinstance(part, int) else part for part in version_key(key[1] or "")),
        ))
        first_group = True
        for pid, version in ordered:
            project = by_pid.get(pid)
            heading = version if version else "No version"
            if pid == LIBRARY_PID:
                heading = "Library videos"
            elif len(projects) > 1:
                heading = f"{project.name if project else pid}: {heading}"
            if not first_group:
                lines.append("")
            first_group = False
            lines += [f"### {heading}", ""]
            targets = {}
            for record in groups[(pid, version)]:
                targets.setdefault(target_label(record, describe), []).append(record)
            first_target = True
            for label, records_for_target in targets.items():
                if not first_target:
                    lines.append("")
                first_target = False
                lines += [f"#### {label}", ""]
                lines += [_item_line(record) for record in records_for_target]
    return "\n".join(lines).rstrip() + "\n"
