"""Assemble API payloads for one job (a VPG project) from its files. Strictly read-only."""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import quote

from . import fsio, mapping, polarity
from .discovery import PROJECT_FILE
from .engine import RELEASE_NODE_NUM
from .governed import GovernedRun, _epoch
from .mapping import STATUS_LABELS, engine_words, gate_node_num, governed_class, reviewer_node_num, score_node_num
from .producer import owner_mark
from .reactions import _fmt_ts, matches_version
from .reviews import (BLOCKING_LEVELS, LEVEL_RANK, as_number, cap_text, finding_state, normalize_gate_result,
                      normalize_governed_defect, normalize_governed_review, normalize_qa, normalize_review, worst_level)
from .telemetry import Telemetry, evaluation_event, span_record
from .timecode import parse_captions, parse_keyframe_name

PRODUCTION_NODES = ("n11_asset_generation", "n12_voice_audio", "n13_motion_scenes", "n14_video_assembly")
ASSEMBLY = "n14_video_assembly"
PREVIEW_EXT = frozenset({".mp4", ".jpg", ".jpeg", ".png", ".vtt", ".srt", ".json"})
NOTE_CAP = 200 * 1024
TASK_PACKET_CAP = 64 * 1024
GATE_ARTIFACT_CAP = 64 * 1024
GATE_TEXT_EXT = (".json", ".md", ".txt", ".yaml", ".yml")


def _artifact_value(value):
    """A JSON value made safe to show: capped text, short lists of text, numbers and booleans."""
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    if isinstance(value, str):
        return cap_text(value, 3000)
    if isinstance(value, list):
        return [cap_text(item, 1200) if isinstance(item, str) else cap_text(json.dumps(item, ensure_ascii=False), 600)
                for item in value[:30]]
    if isinstance(value, dict):
        return [cap_text(f"{key}: {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}", 600)
                for key, item in list(value.items())[:30]]
    return cap_text(str(value), 600)
LIVE_WINDOW_S = 30 * 60
KEYFRAME_DIRS = ("key-frames", "keyframes")
LEDGER_TAIL = 20
LEDGER_EVALUATIONS = 5000
NODE_TELEMETRY_CAP = 60
METRIC_KEYS = ("integrated_lufs", "true_peak_dbtp", "bytes", "voice_over_music_median", "voice_over_music_p10", "bpm")
CAVEAT = "Automated gates check defects; they don't judge taste or regressions against earlier versions."
DECISION_ACTIONS = ["approve", "request_changes", "comment"]
_EMPTY_SLOT = {"gates": (), "scores": (), "defects": (), "reviews": ()}
# The mix-report objects that measure each compared metric, in priority order; the first that states a `basis` wins.
BASIS_SOURCES = {
    "duration_s": ("timeline",),
    "integrated_lufs": ("master",),
    "true_peak_dbtp": ("master",),
    "voice_over_music_median": ("voice_over_music_lu", "mix_measures.voice_over_music_lu_momentary"),
    "voice_over_music_p10": ("voice_over_music_lu", "mix_measures.voice_over_music_lu_momentary"),
    "bpm": ("timeline",),
}
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def _ledger_detail(kind, payload):
    """A short, readable summary of a ledger event's data for the run card."""
    def text(key, cap=120):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return cap_text(" ".join(value.split()), cap) if isinstance(value, str) and value.strip() else None

    if kind == "evaluation":
        name = text("name") or text("gen_ai.evaluation.name")
        label = text("gen_ai.evaluation.score.label") or text("label")
        return ": ".join(part for part in (name, label) if part) or None
    if kind == "direct_run":
        parts = [text("id"), text("label"), f"by {text('actor')}" if text("actor") else None]
        return " ".join(part for part in parts if part) or None
    if kind == "provisional_expired":
        expired = text("expired_at")
        parts = [f"approval by {text('actor')}" if text("actor") else None, f"expired {_fmt_ts(expired)}" if expired else None]
        return ", ".join(part for part in parts if part) or None
    if kind == "decision":
        parts = [text("decision"), f"by {text('actor')}" if text("actor") else None]
        return " ".join(part for part in parts if part) or None
    if kind == "gate":
        return text("outcome")
    return None


def _load_ledger(path, size):
    if size > 64 * 1024 * 1024:
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    count = 0
    linked = True
    previous = None
    awaiting = {}
    direct_runs = {}
    evaluations = deque(maxlen=LEDGER_EVALUATIONS)
    expired = []
    tail = deque(maxlen=LEDGER_TAIL)
    for raw in data.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        count += 1
        try:
            event = json.loads(raw)
        except ValueError:
            linked = False
            continue
        if not isinstance(event, dict):
            continue
        if previous is not None and event.get("prev") != previous:
            linked = False
        previous = event.get("hash")
        payload = event.get("data") if isinstance(event.get("data"), dict) else {}
        node = payload.get("node") if isinstance(payload.get("node"), str) else None
        kind = event.get("type")
        if kind == "awaiting_human" and node:
            awaiting[node] = event.get("ts")
        elif kind == "direct_run" and node:
            direct_runs.setdefault(node, []).append({
                "id": payload.get("id") if isinstance(payload.get("id"), str) else None,
                "label": payload.get("label") if isinstance(payload.get("label"), str) and payload.get("label") else None,
                "actor": payload.get("actor") if isinstance(payload.get("actor"), str) else None,
                "reason": payload.get("reason") if isinstance(payload.get("reason"), str) else None,
                "at": event.get("ts") if isinstance(event.get("ts"), str) else None,
                "seq": event.get("seq"),
                "source": "ledger",
            })
        elif kind == "evaluation":
            evaluations.append(dict(payload, time=event.get("ts"), source={"ledger_seq": event.get("seq")}))
        elif kind == "provisional_expired" and node:
            expired.append({"node": node, "ts": event.get("ts"), "expired_at": payload.get("expired_at")})
        tail.append({"seq": event.get("seq"), "ts": event.get("ts"), "type": kind, "node": node,
                     "detail": _ledger_detail(kind, payload)})
    return {"count": count, "last": list(reversed(tail)), "linked": linked if count else None, "awaiting": awaiting,
            "direct_runs": direct_runs, "evaluations": list(evaluations), "provisional_expired": expired}


_LEDGER = fsio.MtimeCache(_load_ledger, max_entries=256)


def _num_of(node_id):
    if isinstance(node_id, str) and node_id[:1] == "n" and node_id[1:3].isdigit():
        return int(node_id[1:3])
    return None


def _plural(count, word):
    return f"{count} {word}{'s' if count != 1 else ''}"


def _fmt_when(value, now):
    """Local time for labels ('Oct 29, 3:00 PM CDT'), with the year when it is not the current one."""
    text = _fmt_ts(value)
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone()
    if local.year == datetime.fromtimestamp(now).astimezone().year:
        return text
    return text.replace(f"{local.strftime('%b')} {local.day}, ", f"{local.strftime('%b')} {local.day}, {local.year}, ", 1)


class JobReader:
    """Read one project's governed run, candidates, reviews, gates and reactions."""

    def __init__(self, project, graph, store, now=None, workflow=None):
        self.project = project
        self.pid = project.id
        self.graph = graph
        self.store = store
        self.now = time.time() if now is None else now
        self.workflow = workflow
        self._memo = {}

    def _cached(self, key, compute):
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    # ---------------------------------------------------------------- paths
    def path(self, *parts):
        return os.path.join(self.project.path, *parts)

    def vdir(self, version, *parts):
        return os.path.join(self.project.path, "candidates", version, *parts)

    def media_url(self, version, rel):
        encoded = "/".join(quote(part, safe="") for part in rel.split("/"))
        return f"/media/{self.pid}/{quote(version, safe='')}/{encoded}"

    def run_url(self, rel):
        """URL of a governed-run file (a project-relative path listed in state.json)."""
        encoded = "/".join(quote(part, safe="") for part in rel.split("/"))
        return f"/media/{self.pid}/_run/{encoded}"

    def node_num(self, node_id):
        return self.graph.num(node_id) or _num_of(node_id)

    def run_model(self):
        return self._cached("run_model", lambda: GovernedRun(fsio.read_json(self.path(".vpg", "state.json")), self.project.path))

    def candidate_versions(self):
        def compute():
            names = []
            try:
                with os.scandir(self.path("candidates")) as iterator:
                    for entry in iterator:
                        if fsio.safe_segment(entry.name) and entry.is_dir(follow_symlinks=False):
                            names.append(entry.name)
            except OSError:
                pass
            return set(names)
        return self._cached("candidate_versions", compute)

    def versions(self):
        """Candidate folders and governed-run versions (history.versions), in natural order."""
        def compute():
            names = set(self.candidate_versions()) | set(self.run_model().labels())
            return sorted(names, key=fsio.version_key)
        return self._cached("versions", compute)

    def is_candidate(self, version):
        return version in self.candidate_versions()

    def run_record(self, version):
        return self.run_model().record(version) if isinstance(version, str) else None

    def version_source(self, version):
        candidate, governed = self.is_candidate(version), self.run_record(version) is not None
        return "both" if candidate and governed else "governed" if governed else "candidate" if candidate else None

    def run_artifact_path(self, version, key):
        """Project-relative path of a governed artifact for a version, when the file exists."""
        run = self.run_model()
        path = run.path_of(run.artifact_for(self.run_record(version), key))
        return path if path and fsio.is_file(self.path(path)) else None

    def has_version(self, version):
        return isinstance(version, str) and version in self.versions()

    def latest_version(self):
        versions = self.versions()
        return versions[-1] if versions else None

    def focus_version(self):
        """Latest version with a master; else the latest version."""
        versions = self.versions()
        with_master = [v for v in versions if self.has_master(v)]
        return (with_master or versions or [None])[-1]

    # ---------------------------------------------------------------- governed run
    def runtime(self):
        def compute():
            state = fsio.read_json(self.path(".vpg", "state.json"))
            runtime = state.get("runtime") if isinstance(state, dict) else None
            return runtime if isinstance(runtime, dict) else None
        return self._cached("runtime", compute)

    def governed_nodes(self):
        runtime = self.runtime()
        nodes = runtime.get("nodes") if runtime else None
        return nodes if isinstance(nodes, dict) else {}

    def governed(self, node_id):
        entry = self.governed_nodes().get(node_id)
        if not isinstance(entry, dict):
            return None
        failures = entry.get("failures")
        status = entry.get("status")

        def scalar(key):
            value = entry.get(key)
            return value if isinstance(value, (str, int, float, bool)) or value is None else cap_text(value, 300)

        return {
            "status": str(status) if status is not None else None,
            "status_class": governed_class(status),
            "status_label": engine_words(status),
            "reached": governed_class(status) not in (None, "not_run"),
            "attempts": scalar("attempts"),
            "outcome": scalar("outcome"),
            "started_at": scalar("started_at"),
            "finished_at": scalar("finished_at"),
            "executions": scalar("executions"),
            "fail_streak": scalar("fail_streak"),
            "failures": len(failures) if isinstance(failures, list) else None,
            "task_packet": entry.get("task_packet") if isinstance(entry.get("task_packet"), str) else None,
        }

    def ledger(self):
        return self._cached("ledger", lambda: _LEDGER.get(self.path(".vpg", "ledger.jsonl")))

    def waiting_since(self, node_id):
        ledger = self.ledger()
        if ledger and node_id in ledger.get("awaiting", {}):
            return ledger["awaiting"][node_id]
        governed = self.governed(node_id)
        return governed.get("started_at") if governed else None

    def run(self):
        runtime = self.runtime()
        if runtime is None:
            return None
        nodes = {}
        for node_id, entry in self.governed_nodes().items():
            if isinstance(entry, dict):
                nodes[node_id] = {"status": entry.get("status"), "attempts": entry.get("attempts"), "outcome": entry.get("outcome")}
        waiting = [node_id for node_id, entry in nodes.items() if governed_class(entry["status"]) == "waiting"]
        waiting.sort(key=lambda node_id: self.node_num(node_id) or 99)
        ledger = self.ledger()
        status = runtime.get("status")
        status_class = governed_class(status)
        if waiting:
            where = ", ".join(f"N{self.node_num(n):02d}" if self.node_num(n) else n for n in waiting)
            label = f"governed run: waiting for you at {where}"
        elif status_class in (None, "unknown"):
            label = f"governed run: status {status or 'unknown'}"
        else:
            label = f"governed run: {STATUS_LABELS[status_class]}"
        return {
            "run_id": runtime.get("run_id"),
            "status": status,
            "status_class": status_class,
            "current_version": runtime.get("current_version"),
            "label": label,
            "waiting_at": waiting,
            "nodes": nodes,
            "ledger": {
                "count": ledger["count"] if ledger else 0,
                "last": ledger["last"] if ledger else [],
                "linked": ledger["linked"] if ledger else None,
            },
        }

    def readme_head(self):
        return fsio.head_lines(self.path("README.md"), 40)

    def project_meta(self):
        meta = fsio.read_yaml(self.path(PROJECT_FILE))
        return meta if isinstance(meta, dict) else {}

    # ---------------------------------------------------------------- direct runs and telemetry
    def direct_runs(self, node_id, version=None):
        """Direct runs the engine recorded for a node (history.direct_runs and direct_run ledger events), newest first.

        With a version, a run counts when its label names that version or it has no label."""
        def compute():
            runs = self.run_model().direct_runs_for(node_id)
            seen = {run["id"] for run in runs if run["id"]}
            ledger = self.ledger() or {}
            for run in (ledger.get("direct_runs") or {}).get(node_id, []):
                if run["id"] and run["id"] in seen:
                    continue
                runs.append(dict(run))
            runs.sort(key=lambda run: str(run.get("at") or ""), reverse=True)
            return runs
        runs = self._cached(("direct_runs", node_id), compute)
        if version is None:
            return list(runs)
        return [run for run in runs if run.get("label") in (None, version)]

    def telemetry(self):
        return self._cached("telemetry", lambda: Telemetry(self.project.path))

    def telemetry_summary(self):
        runtime = self.runtime() or {}
        run_id = runtime.get("run_id") if isinstance(runtime.get("run_id"), str) else None
        summary = self.telemetry().summary(run_id)
        if summary is None:
            ledger = self.ledger() or {}
            if ledger.get("evaluations"):
                return {"present": False, "ledger_evaluations": len(ledger["evaluations"])}
            return None
        return summary

    def evidence_link(self, path):
        """A servable URL for a project-relative path named in telemetry evidence, or None."""
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or "\x00" in path:
            return None
        clean = os.path.normpath(path).replace(os.sep, "/")
        if clean.startswith("../") or clean == "..":
            return None
        if clean in self.run_model().allowed_media():
            return self.run_url(clean)
        parts = clean.split("/")
        if len(parts) >= 3 and parts[0] == "candidates" and self.is_candidate(parts[1]):
            ext = os.path.splitext(parts[-1])[1].lower()
            wav_ok = ext == ".wav" and parts[2] in ("n12_voice_audio", "n14_video_assembly")
            if (ext in PREVIEW_EXT or wav_ok) and fsio.is_file(self.vdir(parts[1], *parts[2:])):
                return self.media_url(parts[1], "/".join(parts[2:]))
        return None

    def node_telemetry(self, node_id):
        """Evaluation events and spans for one node, newest first; ledger evaluation events when no telemetry files."""
        telemetry = self.telemetry()
        evaluations = telemetry.evaluations_raw()
        spans = telemetry.spans_raw()
        source = "files"
        skipped = sum((part[1] or 0) for part in (evaluations, spans) if part)
        raw_events = evaluations[0] if evaluations else None
        if raw_events is None:
            ledger = self.ledger() or {}
            raw_events = ledger.get("evaluations") or []
            source = "ledger" if raw_events else None
        events = []
        for raw in raw_events:
            node = raw.get("node")
            if isinstance(node, str) and self._resolve_node(node) == node_id:
                events.append(raw)
        events.sort(key=lambda raw: str(raw.get("time") or ""), reverse=True)
        node_spans = []
        for raw in spans[0] if spans else []:
            attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
            node = attributes.get("vpg.node.id")
            if isinstance(node, str) and self._resolve_node(node) == node_id:
                node_spans.append(raw)
        node_spans.sort(key=lambda raw: str(raw.get("start") or ""), reverse=True)
        if source is None and not node_spans and not telemetry.present():
            return {"present": False, "source": None, "evaluations": [], "spans": [], "total": 0, "skipped_lines": 0}
        return {
            "present": True,
            "source": "ledger" if source == "ledger" else "files",
            "evaluations": [evaluation_event(raw, self.evidence_link) for raw in events[:NODE_TELEMETRY_CAP]],
            "total": len(events),
            "spans": [span_record(raw) for raw in node_spans[:20]],
            "spans_total": len(node_spans),
            "skipped_lines": skipped,
        }

    # ---------------------------------------------------------------- candidate and governed evidence
    def reviews(self, version):
        def compute():
            reviews = []
            entries = []
            if self.is_candidate(version):
                try:
                    with os.scandir(self.vdir(version, "reviews")) as iterator:
                        entries = sorted(iterator, key=lambda entry: entry.name)
                except OSError:
                    entries = []
            for entry in entries:
                name = entry.name
                if not name.endswith(".json") or name == "reviewed.json" or name.startswith("."):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                doc = fsio.read_json(entry.path)
                if not isinstance(doc, dict):
                    continue
                key = name[:-5]
                review = normalize_review(doc, key)
                num = reviewer_node_num(key)
                review["node"] = self.graph.resolve_num(num)
                review["node_num"] = num
                review["file"] = f"reviews/{name}"
                review["url"] = self.media_url(version, review["file"])
                for defect in review["defects"]:
                    defect["node"] = self.graph.node_ref(defect.get("fix_node")) or review["node"]
                    defect["node_num"] = self.node_num(defect["node"])
                    defect["reviewer"] = key
                    defect["version"] = version
                for score in review["scores"]:
                    score["node"] = self.graph.resolve_num(score_node_num(score["dimension"], num))
                reviews.append(review)
            reviews.extend(self._run_reviews(version))
            reviews.sort(key=lambda review: (review["node_num"] or 99, review["key"]))
            return reviews
        return self._cached(("reviews", version), compute)

    def _resolve_node(self, value, fallback=None):
        if isinstance(value, str) and value in self.graph.by_id:
            return value
        return self.graph.node_ref(value) or fallback

    def run_defects(self, version):
        """evaluation.defects seen in this version (first_seen <= V <= last_seen or current), mapped to traced_node."""
        def compute():
            record = self.run_record(version)
            if not record:
                return []
            defects = []
            for raw in self.run_model().defects_for(record):
                defect = normalize_governed_defect(raw)
                defect["node"] = self._resolve_node(raw.get("traced_node"), self.graph.resolve_num(16))
                defect["node_num"] = self.node_num(defect["node"])
                defect["reviewer"] = None
                defect["version"] = version
                defects.append(defect)
            return defects
        return self._cached(("run_defects", version), compute)

    def _run_reviews(self, version):
        record = self.run_record(version)
        if not record:
            return []
        defects = self.run_defects(version)
        raws = self.run_model().reviews_for(record)
        reviewers = [r.get("reviewer") for r in raws]
        out = []
        for raw in raws:
            review = normalize_governed_review(raw, defects)
            node = self._resolve_node(raw.get("node"), self.graph.resolve_num(reviewer_node_num(review["reviewer"])))
            num = self.node_num(node)
            review["key"] = node
            review["node"] = node
            review["node_num"] = num
            if reviewers.count(raw.get("reviewer")) > 1 and num:
                review["label"] = f"{review['label']} (N{num:02d})"
            review["file"] = None
            review["url"] = None
            for defect in review["defects"]:
                defect["reviewer"] = review["key"]
            for score in review["scores"]:
                score["node"] = self.graph.resolve_num(score_node_num(score["dimension"], num))
            out.append(review)
        return out

    def run_gates(self, version):
        """evaluation.gate_results for this version, mapped to their node."""
        def compute():
            record = self.run_record(version)
            if not record:
                return []
            gates = []
            for raw in self.run_model().gates_for(record):
                gate = normalize_gate_result(raw)
                gate["node"] = self._resolve_node(gate["node_raw"], self.graph.resolve_num(21))
                gate["node_num"] = self.node_num(gate["node"])
                gates.append(gate)
            return gates
        return self._cached(("run_gates", version), compute)

    def all_gates(self, version):
        qa = self.qa(version)
        return (qa["gates"] if qa else []) + self.run_gates(version)

    def qa(self, version):
        def compute():
            qa = normalize_qa(fsio.read_json(self.vdir(version, "qa", "qa-direct.json")))
            if qa is None:
                return None
            for gate in qa["gates"]:
                num = gate_node_num(gate["name"])
                if self.graph.available and self.graph.resolve_num(num) not in self.graph.by_id:
                    num = 15
                gate["node"] = self.graph.resolve_num(num)
                gate["node_num"] = num
                for finding in gate["findings_norm"]:
                    finding.update(node=gate["node"], node_num=num, version=version, reviewer=None, gate=gate["name"])
            qa["url"] = self.media_url(version, "qa/qa-direct.json")
            return qa
        return self._cached(("qa", version), compute)

    def assembly_dir(self, version):
        """Project-relative folder holding the assembled master: candidates/V/n14 or the governed attempt folder."""
        if self.is_candidate(version) and fsio.is_real_dir(self.vdir(version, ASSEMBLY)):
            return os.path.relpath(self.vdir(version, ASSEMBLY), self.project.path)
        master = self.run_artifact_path(version, "master")
        return os.path.dirname(master) if master else None

    def _assembly_json(self, version, name):
        folder = self.assembly_dir(version)
        doc = fsio.read_json(self.path(folder, name)) if folder else None
        return doc if isinstance(doc, dict) else {}

    def duration(self, version):
        mix = self._assembly_json(version, "mix-report.json")
        timeline = mix.get("timeline") if isinstance(mix.get("timeline"), dict) else {}
        value = as_number(timeline.get("film_seconds"))
        if value:
            return round(float(value), 3)
        outputs = self._assembly_json(version, "outputs.json")
        data = outputs.get("data") if isinstance(outputs.get("data"), dict) else {}
        for key in ("film_seconds", "duration_s", "duration", "seconds", "runtime_s"):
            value = as_number(data.get(key))
            if value:
                return round(float(value), 3)
        master = mix.get("master") if isinstance(mix.get("master"), dict) else {}
        value = as_number(master.get("seconds"))
        return round(float(value), 3) if value else None

    def metrics(self, version):
        mix = self._assembly_json(version, "mix-report.json")
        outputs = self._assembly_json(version, "outputs.json")
        data = outputs.get("data") if isinstance(outputs.get("data"), dict) else {}
        master = mix.get("master") if isinstance(mix.get("master"), dict) else {}
        vom = mix.get("voice_over_music_lu") if isinstance(mix.get("voice_over_music_lu"), dict) else {}
        timeline = mix.get("timeline") if isinstance(mix.get("timeline"), dict) else {}

        def first(*values):
            for value in values:
                number = as_number(value)
                if number is not None:
                    return number
            return None

        size = None
        if data.get("bytes") is None and master.get("bytes") is None:
            folder = self.assembly_dir(version)
            try:
                size = os.stat(self.path(folder, "master.mp4")).st_size if folder else None
            except OSError:
                size = None
        return {
            "integrated_lufs": first(data.get("integrated_lufs"), master.get("integrated_lufs")),
            "true_peak_dbtp": first(data.get("true_peak_dbtp"), master.get("true_peak_dbtp")),
            "bytes": first(data.get("bytes"), master.get("bytes"), size),
            "voice_over_music_median": first(vom.get("median")),
            "voice_over_music_p10": first(vom.get("p10"), data.get("voice_over_music_p10_lu")),
            "bpm": first(timeline.get("bpm")),
        }

    def has_master(self, version):
        if self.is_candidate(version) and fsio.is_file(self.vdir(version, ASSEMBLY, "master.mp4")):
            return True
        return self.run_artifact_path(version, "master") is not None

    def media(self, version):
        def url_if(*parts):
            return self.media_url(version, "/".join(parts)) if fsio.is_file(self.vdir(version, *parts)) else None

        if self.is_candidate(version) and fsio.is_file(self.vdir(version, ASSEMBLY, "master.mp4")):
            vtt = url_if(ASSEMBLY, "captions.en.vtt")
            if vtt is None:
                try:
                    names = sorted(n for n in os.listdir(self.vdir(version, ASSEMBLY)) if n.lower().endswith(".vtt"))
                except OSError:
                    names = []
                vtt = url_if(ASSEMBLY, names[0]) if names else None
            srt = url_if(ASSEMBLY, "captions.srt")
            return {
                "source": "candidate",
                "master": url_if(ASSEMBLY, "master.mp4"),
                "poster": url_if(ASSEMBLY, "poster.jpg"),
                "vtt": vtt or (f"{srt}.vtt" if srt else None),
                "srt": srt,
                "stems": [],
            }
        out = {"source": "governed" if self.run_record(version) else None, "master": None, "poster": None,
               "vtt": None, "srt": None, "stems": []}
        if not self.run_record(version):
            return out
        master = self.run_artifact_path(version, "master")
        out["master"] = self.run_url(master) if master else None
        captions = self.run_artifact_path(version, "captions")
        if captions:
            out["srt" if captions.lower().endswith(".srt") else "vtt"] = self.run_url(captions)
            if captions.lower().endswith(".srt"):
                out["vtt"] = self.run_url(captions) + ".vtt"
        for key in ("silent", "voice_stem", "music_stem"):
            path = self.run_artifact_path(version, key)
            if path:
                out["stems"].append({"key": key, "url": self.run_url(path)})
        return out

    def nodes_present(self, version):
        present = set()
        if self.is_candidate(version):
            present |= {node for node in PRODUCTION_NODES if fsio.is_real_dir(self.vdir(version, node))}
        record = self.run_record(version)
        if record:
            present |= self.run_model().version_nodes(record) & set(PRODUCTION_NODES)
        return [node for node in PRODUCTION_NODES if node in present]

    def vmtime(self, version):
        """When a version last changed: the candidate folder and its immediate sub-folders, ignoring dotfiles and OS
        metadata (a Finder .DS_Store write changes a folder's mtime too; see fsio.folder_change_time)."""
        if not self.is_candidate(version):
            record = self.run_record(version)
            return _epoch(record["created_at"]) if record else None
        base = self.vdir(version)
        folders = [base]
        try:
            with os.scandir(base) as iterator:
                for entry in iterator:
                    if not fsio.is_os_metadata(entry.name) and entry.is_dir(follow_symlinks=False):
                        folders.append(entry.path)
        except OSError:
            pass
        times = [t for t in (fsio.folder_change_time(folder) for folder in folders) if t]
        if not times:
            times = [t for t in (fsio.mtime(folder) for folder in folders) if t]
        return max(times) if times else None

    def status_md_mtime(self, version):
        return fsio.mtime(self.path("work", f"film-{version}", "STATUS.md"))

    def reviewer_nodes(self, version):
        return {review["node"] for review in self.reviews(version)}

    def expected_reviewer_nodes(self, version):
        """Reviewer nodes of the previous reviewed version: the set a new version is expected to collect."""
        earlier = [v for v in self.versions() if fsio.version_key(v) < fsio.version_key(version)]
        for candidate in reversed(earlier):
            nodes = self.reviewer_nodes(candidate)
            if nodes:
                return nodes
        return set()

    def reviews_complete(self, version):
        covered = self.reviewer_nodes(version)
        return bool(covered) and self.expected_reviewer_nodes(version) <= covered

    def updated_at(self, version):
        times = [t for t in (self.vmtime(version) if self.has_version(version) else None,
                             self.status_md_mtime(version)) if t is not None]
        return max(times) if times else None

    def is_live(self, version):
        updated = self.updated_at(version)
        # A time in the future (clock skew or a placeholder date) is not evidence of recent work.
        if updated is None or not 0 <= self.now - updated < LIVE_WINDOW_S:
            return False
        return not (self.has_version(version) and self.reviews_complete(version))

    def live_versions(self):
        def compute():
            versions = set(self.versions())
            names = set(versions)
            try:
                with os.scandir(self.path("work")) as iterator:
                    for entry in iterator:
                        if entry.name.startswith("film-") and entry.is_dir(follow_symlinks=False):
                            name = entry.name[5:]
                            if fsio.safe_segment(name):
                                names.add(name)
            except OSError:
                pass
            return sorted((v for v in names if self.is_live(v)), key=fsio.version_key)
        return self._cached("live", compute)

    def keyframes(self, version):
        if self.is_candidate(version):
            for folder in KEYFRAME_DIRS:
                directory = self.vdir(version, folder)
                if not fsio.is_real_dir(directory):
                    continue
                frames = []
                try:
                    with os.scandir(directory) as iterator:
                        for entry in iterator:
                            if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                                continue
                            info = parse_keyframe_name(entry.name)
                            if info:
                                info["url"] = self.media_url(version, f"{folder}/{entry.name}")
                                frames.append(info)
                except OSError:
                    continue
                frames.sort(key=lambda frame: (frame["t"] is None, frame["t"] or 0.0, frame["file"]))
                return frames
        record = self.run_record(version)
        if not record:
            return []
        run = self.run_model()
        frames = []
        for artifact in run.keyframes(record):
            path = run.path_of(artifact)
            info = parse_keyframe_name(os.path.basename(path)) if path else None
            if info and fsio.is_file(self.path(path)):
                info["url"] = self.run_url(path)
                frames.append(info)
        frames.sort(key=lambda frame: (frame["t"] is None, frame["t"] or 0.0, frame["file"]))
        return frames

    def captions(self, version):
        if self.is_candidate(version):
            for name in ("captions.en.vtt", "captions.vtt", "captions.srt"):
                text, _ = fsio.read_text(self.vdir(version, ASSEMBLY, name), 2 * 1024 * 1024)
                if text:
                    return parse_captions(text)
        path = self.run_artifact_path(version, "captions") if self.run_record(version) else None
        if path:
            text, _ = fsio.read_text(self.path(path), 2 * 1024 * 1024)
            if text:
                return parse_captions(text)
        return []

    def notes(self, version):
        directory = self.path("work", f"film-{version}")
        try:
            with os.scandir(directory) as iterator:
                names = [e.name for e in iterator if e.name.lower().endswith(".md") and e.is_file(follow_symlinks=False)]
        except OSError:
            return []

        def order(name):
            lowered = name.lower()
            rank = 0 if lowered == "brief.md" else 1 if lowered == "status.md" else 2 if lowered.startswith("director-notes") else 3
            return (rank, lowered)

        notes = []
        for name in sorted(names, key=order)[:30]:
            text, truncated = fsio.read_text(os.path.join(directory, name), NOTE_CAP)
            if text is not None:
                notes.append({"name": name, "text": text, "truncated": truncated})
        return notes

    def node_files(self, version, node_id):
        directory = self.vdir(version, node_id)
        if not fsio.is_real_dir(directory):
            return None
        entries = fsio.list_entries(directory)
        stems_ok = node_id in ("n12_voice_audio", "n14_video_assembly")
        for entry in entries:
            ext = os.path.splitext(entry["name"])[1].lower()
            if not entry.get("dir") and (ext in PREVIEW_EXT or (ext == ".wav" and stems_ok)):
                entry["url"] = self.media_url(version, f"{node_id}/{entry['name']}")
        return entries

    def node_context(self, version, node_id):
        return self.node_context_at(os.path.relpath(self.vdir(version, node_id), self.project.path))

    def node_context_at(self, folder):
        """context.json in a project-relative folder (candidate node folder or governed attempt folder)."""
        target = os.path.realpath(os.path.join(self.project.path, folder, "context.json"))
        if not fsio.is_within(target, self.project.path):
            return None
        doc = fsio.read_json(target)
        if not isinstance(doc, dict):
            return None
        controls = doc.get("controls") if isinstance(doc.get("controls"), dict) else {}
        producer = doc.get("producer") if isinstance(doc.get("producer"), dict) else {}
        inputs = doc.get("inputs") if isinstance(doc.get("inputs"), dict) else {}
        return {
            "node": doc.get("node") if isinstance(doc.get("node"), str) else None,
            "attempt": doc.get("attempt") if isinstance(doc.get("attempt"), (int, str)) else None,
            "controls": {str(k): self._relative(v) if isinstance(v, str) else cap_text(v, 600) for k, v in controls.items()},
            "producer": {
                "node": producer.get("node") if isinstance(producer.get("node"), str) else None,
                "command": producer.get("command") if isinstance(producer.get("command"), str) else None,
            } if producer else None,
            "inputs": {str(k): (len(v) if isinstance(v, (list, dict)) else cap_text(v, 300)) for k, v in inputs.items()},
        }

    def _relative(self, value):
        prefix = self.project.path + os.sep
        return value[len(prefix):] if value.startswith(prefix) else value

    def _file_inside_project(self, relative):
        target = os.path.realpath(os.path.join(self.project.path, relative))
        return fsio.is_within(target, self.project.path) and fsio.is_file(target)

    def _read_inside_project(self, relative, cap):
        target = os.path.realpath(os.path.join(self.project.path, relative))
        if not fsio.is_within(target, self.project.path) or not fsio.is_file(target):
            return None, False
        return fsio.read_text(target, cap)

    # ---------------------------------------------------------------- reactions and gates
    def reactions(self):
        return self._cached("reactions", lambda: self.store.query(pid=self.pid))

    def version_reactions(self, version):
        return [r for r in self.reactions() if matches_version(r, version)]

    def owner_decision(self, node_id, version=None, since=None):
        """Latest owner approve/request_changes recorded for a human gate (per version for the release gate)."""
        release = self.node_num(node_id) == RELEASE_NODE_NUM
        for record in self.reactions():
            if record.get("actor") != "owner" or record.get("kind") not in ("approve", "request_changes"):
                continue
            if since and (record.get("ts") or "") < since:
                continue
            target = record.get("target") or {}
            hit = target.get("type") in ("gate", "node") and target.get("id") == node_id and (
                version is None or record.get("version") == version)
            if release and version and target.get("type") == "version" and target.get("id") == version:
                hit = True
            if hit:
                return {key: record.get(key) for key in ("id", "pid", "ts", "kind", "text", "version", "acks", "thread", "state")}
        return None

    def release_checks(self, version):
        """(gates_passed, reason) for a version: master, a complete review set, passing verdicts, no P1-P3, no blocking gates."""
        if not self.has_version(version):
            return False, "no such version"
        if not self.has_master(version):
            return False, "no master file"
        reviews = self.reviews(version)
        if not reviews:
            return False, "no reviews yet"
        missing = self.expected_reviewer_nodes(version) - self.reviewer_nodes(version)
        if missing:
            return False, f"{_plural(len(missing), 'expected review')} missing"
        not_passing = [r for r in reviews if r["verdict_class"] != "pass"]
        if not_passing:
            names = ", ".join(r["label"] for r in not_passing[:3])
            return False, f"{_plural(len(not_passing), 'reviewer verdict')} not pass ({names})"
        defects = [d for r in reviews if not r.get("governed") for d in r["defects"]] + self.run_defects(version)
        blocking = sum(1 for d in defects if d["level"] in BLOCKING_LEVELS and str(d.get("status") or "").lower() != "resolved")
        if blocking:
            return False, f"{_plural(blocking, 'blocking defect')} (P1-P3)"
        qa = self.qa(version)
        qa_blocking = (qa.get("summary") or {}).get("blocking") if qa else None
        if qa_blocking:
            return False, f"qa-direct reports {_plural(int(qa_blocking), 'blocking finding')}"
        failing = [g["name"] for g in self.run_gates(version) if not g["pass"]]
        if failing:
            return False, f"engine gate not passed ({', '.join(failing[:2])})"
        return True, f"all {_plural(len(reviews), 'reviewer verdict')} pass and no P1-P3 defects"

    def compare_with(self, version):
        """The latest earlier version you preferred or approved; else the previous version."""
        earlier = [v for v in self.versions() if fsio.version_key(v) < fsio.version_key(version)] if version else []
        if not earlier:
            return None
        release = self.graph.release_node()
        liked = set()
        for record in self.reactions():
            if record.get("actor") != "owner":
                continue
            target = record.get("target") or {}
            if record.get("kind") == "prefer" and record.get("choice") in ("a", "b"):
                liked.add(record.get(record["choice"]))
            elif record.get("kind") == "approve":
                if target.get("type") == "version":
                    liked.add(target.get("id"))
                elif target.get("id") == release and record.get("version"):
                    liked.add(record.get("version"))
        if release:
            run = self.run_model()
            for approval in run.approvals:
                if approval.get("node") == release and str(approval.get("decision") or "").lower().startswith("approv"):
                    number = run.approval_version(approval)
                    if number:
                        liked.add(f"v{number}")
        chosen = [v for v in earlier if v in liked]
        return chosen[-1] if chosen else earlier[-1]

    def human_gate_state(self, node_id, version):
        """Where a human gate stands, and which actions make sense there (approve only when it actually waits)."""
        gate = self.graph.human_gate(node_id)
        if not gate:
            return None
        governed = self.governed(node_id)
        engine_class = governed["status_class"] if governed else None
        release = self.node_num(node_id) == RELEASE_NODE_NUM
        run = self.run_model()
        record = self.run_record(version) if release and version else None
        engine_decision = run.latest_approval(node_id, version if record else None) if (not release or record) else None
        expired = False
        if engine_decision:
            expires = _epoch(engine_decision.get("expires_at"))
            engine_decision["expired"] = bool(engine_decision["standing"] and expires is not None and expires <= self.now)
            engine_decision["expires_label"] = (_fmt_when(engine_decision["expires_at"], self.now)
                                                if engine_decision.get("expires_at") else None)
            expired = engine_decision["expired"]
        since = self.waiting_since(node_id) if engine_class == "waiting" else None
        if expired and not since:
            since = engine_decision["expires_at"]
        owner = self.owner_decision(node_id, version if release else None, since=since)
        gates_ok = gates_reason = None
        if release and version:
            gates_ok, gates_reason = self.release_checks(version)
        current_run_version = (not record) or run.is_current(version)
        engine_waits = engine_class == "waiting" and current_run_version
        if engine_class == "waiting" and current_run_version:
            state = "recorded" if owner else "waiting"
        elif expired:
            # An expired provisional approval decides nothing: the gate waits for a named human again.
            state = "recorded" if owner else "waiting"
        elif engine_decision:
            state = "engine_decided"
        elif release and version and not record:
            state = "recorded" if owner else "open"
        elif release and version and record and not run.is_current(version):
            state = "not_reached"
        elif engine_class in ("passed", "failed", "running"):
            state = engine_class
        else:
            state = "recorded" if owner else "not_reached"
        actions = list(DECISION_ACTIONS) if state in ("waiting", "open") else ["comment"]
        # A recorded decision can be replaced by a newer one while the gate still stands open: the engine still waits
        # there, or the release is still the latest fully reviewed version's. It stays a note for the producer.
        redecide = False
        if state == "recorded" and owner:
            if engine_waits:
                redecide = True
            elif release and version and not record:
                redecide = self.latest_reviewed_version() == version
            if redecide:
                actions = list(DECISION_ACTIONS)
        superseded_by = None
        if release and version and state in ("waiting", "open"):
            reviewed = self.latest_reviewed_version()
            if reviewed and reviewed != version and fsio.version_key(reviewed) > fsio.version_key(version):
                superseded_by = reviewed
        return dict(
            gate,
            node=node_id,
            num=self.node_num(node_id),
            release=release,
            version=version if release else None,
            engine_status=governed["status"] if governed else None,
            engine_status_label=governed["status_label"] if governed else "no record",
            engine_decision=engine_decision,
            provisional_expired=expired,
            state=state,
            actions=actions,
            engine_waits=engine_waits,
            redecide=redecide,
            since=since,
            decision_record=owner,
            gates_passed=gates_ok,
            gates_reason=gates_reason,
            caveat=CAVEAT if release else None,
            compare_with=self.compare_with(version) if release and version else None,
            label=self._gate_label(state, owner, engine_decision, gates_ok, gates_reason, bool(record)),
            superseded_by=superseded_by,
        )

    @staticmethod
    def _gate_label(state, owner, engine_decision, gates_ok, gates_reason, governed_version):
        expired = bool(engine_decision and engine_decision.get("expired"))
        if state == "waiting" and expired:
            actor = engine_decision.get("actor") or "unknown actor"
            return (f"Waiting for you again: the provisional approval by {actor} expired "
                    f"{engine_decision.get('expires_label') or engine_decision.get('expires_at')}; confirm or reject it.")
        if state == "waiting":
            return "Waiting for you; the governed run is paused here."
        if state == "recorded":
            verb = "approved" if owner["kind"] == "approve" else "requested changes"
            return f"You {verb}; {_pickup_words(owner)}. The engine is not changed yet."
        if state == "engine_decided":
            decision = engine_decision.get("decision") or "decided"
            actor = engine_decision.get("actor") or "unknown actor"
            text = f"Recorded decision in the engine: {decision} by {actor}."
            if engine_decision.get("provisional"):
                when = engine_decision.get("expires_label")
                text += f" Provisional (assumed), expires {when}." if when else " Provisional (assumed)."
            return text
        if state == "open":
            if gates_ok:
                return "Gates passed, your decision."
            return f"Gates not passed yet ({gates_reason}); your decision."
        if state == "not_reached":
            if governed_version:
                return "The governed run hasn't reached this gate for this version."
            return "The governed run hasn't reached this gate."
        return {"passed": "Passed in the governed run.", "failed": "Failed in the governed run.",
                "running": "Running in the governed run."}.get(state, "Unknown.")

    # ---------------------------------------------------------------- node mapping
    def node_index(self, version):
        def compute():
            index = {}

            def slot(node_id):
                return index.setdefault(node_id, {"gates": [], "scores": [], "defects": [], "reviews": []})

            if version:
                for review in self.reviews(version):
                    slot(review["node"])["reviews"].append(review)
                    for score in review["scores"]:
                        slot(score["node"])["scores"].append(dict(score, reviewer=review["key"], reviewer_label=review["label"]))
                    if not review.get("governed"):
                        for defect in review["defects"]:
                            slot(defect["node"])["defects"].append(defect)
                for defect in self.run_defects(version):
                    slot(defect["node"])["defects"].append(defect)
                for gate in self.all_gates(version):
                    slot(gate["node"])["gates"].append(gate)
            return index
        return self._cached(("node_index", version), compute)

    def _finding_nodes(self, version):
        def compute():
            nodes = {}
            if version:
                for review in self.reviews(version):
                    for defect in review["defects"]:
                        nodes[defect["uid"]] = defect["node"]
                for defect in self.run_defects(version):
                    nodes[defect["uid"]] = defect["node"]
                for gate in self.all_gates(version):
                    nodes[f"gate:{gate['name']}"] = gate["node"]
                    for finding in gate["findings_norm"]:
                        nodes[finding["uid"]] = gate["node"]
            return nodes
        return self._cached(("finding_nodes", version), compute)

    def _reaction_node(self, record, version):
        """The node a reaction belongs to for this version, or None."""
        target = record.get("target") or {}
        kind, ident = target.get("type"), target.get("id")
        if kind in ("node", "gate") and ident in self.graph.by_id:
            return ident if record.get("version") in (None, version) else None
        if not version or record.get("version") != version:
            return None
        nodes = self._finding_nodes(version)
        if kind == "finding":
            return nodes.get(ident)
        if kind == "gate":
            return nodes.get(f"gate:{ident}")
        if kind == "version" and record.get("kind") in ("approve", "request_changes"):
            return self.graph.release_node()
        return None

    def _node_contributions(self, node_id, version, slot, present, gate_state):
        contributions = []
        governed = self.governed(node_id)
        engine_class = governed["status_class"] if governed else None
        if engine_class:
            contributions.append((engine_class, f"governed run: {governed['status_label']}"))
        recorded = bool(self.direct_runs(node_id, version)) if version else bool(self.direct_runs(node_id))
        where = "recorded in ledger" if recorded else "not in engine ledger"
        if (present or slot["reviews"] or slot["gates"]) and engine_class in (None, "not_run"):
            contributions.append(("direct", f"direct run ({where})"))
        elif slot["scores"] and engine_class in (None, "not_run"):
            contributions.append(("direct", f"{len(slot['scores'])} scores from other reviewers (direct run, {where})"))
        elif recorded and engine_class in (None, "not_run"):
            contributions.append(("direct", "direct run (recorded in ledger)"))
        contributions += mapping.gate_contributions(list(slot["gates"]))
        contributions += mapping.review_contributions(list(slot["reviews"]))
        open_defects = [d for d in slot["defects"] if str(d.get("status") or "").lower() != "resolved"]
        contributions += mapping.defect_contributions(open_defects)
        if gate_state:
            state = gate_state["state"]
            owner = gate_state["decision_record"]
            engine_decision = gate_state["engine_decision"]
            if gate_state.get("provisional_expired") and state == "waiting":
                actor = engine_decision.get("actor") or "unknown actor"
                when = engine_decision.get("expires_label")
                contributions.append(("waiting", f"provisional approval by {actor} expired{f' {when}' if when else ''}; "
                                                 "waiting for you again"))
            elif state == "recorded" and engine_class in (None, "not_run"):
                if owner["kind"] == "approve":
                    contributions.append(("passed", "you approved (recorded for the producer; engine unchanged)"))
                else:
                    contributions.append(("failed", "you requested changes (recorded for the producer; engine unchanged)"))
            elif state == "recorded":
                contributions.append(("waiting", "your decision is recorded; the engine still waits"))
            elif state == "engine_decided":
                decision = str(engine_decision.get("decision") or "").lower()
                text = f"{decision or 'decided'} by {engine_decision.get('actor') or 'unknown actor'} (engine record)"
                if engine_decision.get("provisional"):
                    when = engine_decision.get("expires_label")
                    text += f"; provisional (assumed), expires {when}" if when else "; provisional (assumed)"
                contributions.append(("passed" if decision.startswith(("approv", "accept", "pass")) else "failed", text))
            elif state == "open" and gate_state["gates_passed"]:
                contributions.append(("waiting", "gates passed, your decision (not recorded yet)"))
            elif state == "open":
                contributions.append(("not_run", f"gates not passed yet: {gate_state['gates_reason']}"))
            elif state == "not_reached" and gate_state["release"] and version and self.run_record(version):
                contributions.append(("not_run", "the governed run did not take this version to the release gate"))
        return contributions, engine_class

    def node_present(self, version, node_id):
        if not version:
            return False
        if self.is_candidate(version) and fsio.is_real_dir(self.vdir(version, node_id)):
            return True
        record = self.run_record(version)
        return bool(record) and node_id in self.run_model().version_nodes(record)

    def run_node_artifacts(self, version, node_id):
        """Files the governed run produced at a node for this version (from the version's artifact map)."""
        record = self.run_record(version)
        if not record:
            return []
        run = self.run_model()
        out = []
        seen = set()
        for key, sha in sorted(record["artifacts"].items()):
            artifact_id = run.by_sha.get(sha)
            artifact = run.artifacts.get(artifact_id) if artifact_id else None
            path = run.path_of(artifact)
            if not artifact or artifact.get("node") != node_id or not path or path in seen:
                continue
            seen.add(path)
            exists = fsio.is_file(self.path(path))
            ext = os.path.splitext(path)[1].lower()
            servable = exists and (ext in PREVIEW_EXT or (ext == ".wav" and node_id in ("n12_voice_audio", "n14_video_assembly")))
            out.append({"name": path, "key": key, "size": artifact.get("bytes") if isinstance(artifact.get("bytes"), int) else None,
                        "url": self.run_url(path) if servable else None, "missing": not exists})
        return out

    def _node_entry(self, node_id, version, reactions_count):
        summary = self.graph.summary(node_id)
        slot = self.node_index(version).get(node_id, _EMPTY_SLOT)
        present = self.node_present(version, node_id)
        gate_state = self.human_gate_state(node_id, version) if summary["human_gate"] else None
        contributions, engine_class = self._node_contributions(node_id, version, slot, present, gate_state)
        status, reason = mapping.aggregate(contributions)
        if gate_state and gate_state["state"] == "recorded" and gate_state.get("engine_waits") and status == "waiting":
            # Needs you means only the owner: once he decided, the gate reads as his, though the engine still waits.
            status, reason = "decided", "you decided (recorded for the producer); the engine run still waits here until it is approved in the engine"
        if engine_class not in (None, "not_run"):
            provenance = "governed"
        elif present or slot["reviews"] or slot["gates"] or slot["scores"]:
            provenance = "direct"
        else:
            provenance = "none"
        direct_runs = self.direct_runs(node_id, version) if version else self.direct_runs(node_id)
        if provenance == "none" and direct_runs:
            provenance = "direct"
        levels = {"P1": 0, "P2": 0, "P3": 0, "P4": 0, "P?": 0}
        for defect in slot["defects"]:
            levels[defect["level"]] = levels.get(defect["level"], 0) + 1
        files = len([r for r in slot["reviews"] if r.get("file")])
        if present and self.is_candidate(version) and fsio.is_real_dir(self.vdir(version, node_id)):
            files += sum(1 for entry in fsio.list_entries(self.vdir(version, node_id)) if not entry.get("dir"))
        elif present:
            files += len(self.run_node_artifacts(version, node_id))
        governed = self.governed(node_id)
        return dict(
            summary,
            status=status,
            status_label=STATUS_LABELS[status],
            status_reason=reason,
            provenance=provenance,
            direct_recorded=bool(direct_runs) and provenance == "direct",
            direct_runs=direct_runs[:5] if provenance == "direct" else [],
            governed={k: governed[k] for k in ("status", "status_label", "reached", "attempts", "outcome")} if governed else None,
            owns_gates=bool(slot["gates"]) or bool(summary["contract_gate"]),
            evidence_counts={
                "files": files,
                "gates": len(slot["gates"]),
                "gate_findings": sum(len(gate["findings"]) for gate in slot["gates"]),
                "scores": len(slot["scores"]),
                "defects_by_level": levels,
            },
            reactions_count=reactions_count,
            gate_state=gate_state,
        )

    def node_entries(self, version):
        counts = {}
        for record in self.reactions():
            node_id = self._reaction_node(record, version)
            if node_id:
                counts[node_id] = counts.get(node_id, 0) + 1
        return [self._node_entry(node["id"], version, counts.get(node["id"], 0)) for node in self.graph.nodes]

    def node_detail(self, node_id, version):
        reactions = [r for r in self.reactions() if self._reaction_node(r, version) == node_id]
        entry = self._node_entry(node_id, version, len(reactions))
        slot = self.node_index(version).get(node_id, _EMPTY_SLOT)
        governed = self.governed(node_id)
        if governed:
            governed = dict(governed)
            if governed.get("task_packet"):
                text, truncated = self._read_inside_project(governed["task_packet"], TASK_PACKET_CAP)
                governed["task_packet_text"] = text
                governed["task_packet_truncated"] = truncated
            work = self.path("work", node_id)
            governed["work_folder"] = f"work/{node_id}" if fsio.is_real_dir(work) else None
            governed["work_files"] = fsio.list_entries(work, limit=200) if governed["work_folder"] else []
        evidence = {"provenance": entry["provenance"], "folder": None, "files": [], "context": None,
                    "review_files": [], "qa_file": None}
        if version and self.is_candidate(version) and fsio.is_real_dir(self.vdir(version, node_id)):
            evidence["folder"] = f"candidates/{version}/{node_id}"
            evidence["files"] = self.node_files(version, node_id) or []
            evidence["context"] = self.node_context(version, node_id)
        elif version and self.run_record(version):
            files = self.run_node_artifacts(version, node_id)
            if files:
                folder = os.path.dirname(files[0]["name"])
                evidence["folder"] = folder
                evidence["files"] = files
                evidence["context"] = self.node_context_at(folder)
        evidence["review_files"] = [{"name": r["file"], "url": r["url"], "reviewer": r["label"]} for r in slot["reviews"] if r.get("file")]
        if any(not g.get("governed") for g in slot["gates"]):
            evidence["qa_file"] = {"name": "qa/qa-direct.json", "url": self.media_url(version, "qa/qa-direct.json")}
        defects = sorted(slot["defects"], key=lambda d: (LEVEL_RANK.get(d["level"], 9), d["t0"] if d["t0"] is not None else 1e9))
        kind, num = entry.get("kind"), entry.get("num")
        default_tab = "reviews" if kind == "review" else "evidence" if num in (11, 12, 13, 14) else "checks" if kind == "tool" else "evidence"
        return {
            "pid": self.pid,
            "version": version,
            "node": entry,
            "default_tab": default_tab,
            "contract": self.graph.contract(node_id),
            "governed": governed,
            "evidence": evidence,
            "previews": self.node_previews(version, node_id),
            "gates": [self._gate_out(gate) for gate in slot["gates"]],
            "scores": [dict(score) for score in slot["scores"]],
            "defects": defects,
            "reviews": [{k: r.get(k) for k in ("key", "label", "reviewer", "verdict", "verdict_class", "summary", "worst_level",
                                               "defect_count", "carried", "url", "pending_human", "tool_findings", "governed")}
                        | {"score_count": len(r["scores"])} for r in slot["reviews"]],
            "reactions": reactions,
            "telemetry": self.node_telemetry(node_id),
        }

    def gate_evidence(self, node_id):
        """What a human gate asks you to approve, without a version: its decision and state, the contract essentials,
        the governed run's task packet and the artifacts the node recorded in state.json (JSON parsed into fields)."""
        summary = self.graph.summary(node_id)
        contract = self.graph.contract(node_id) or {}
        governed = self.governed(node_id)
        packet = None
        if governed and governed.get("task_packet"):
            text, truncated = self._read_inside_project(governed["task_packet"], TASK_PACKET_CAP)
            packet = {"path": governed["task_packet"], "text": text, "truncated": truncated}
        run = self.run_model()
        items = sorted(((key, item) for key, item in run.artifacts.items() if item.get("node") == node_id),
                       key=lambda pair: (pair[1].get("status") != "current", pair[0]))
        artifacts = []
        for artifact_id, artifact in items[:12]:
            rel = run.path_of(artifact)
            if not rel:
                continue
            entry = {"id": artifact_id, "key": artifact.get("key"), "path": rel, "status": artifact.get("status"),
                     "exists": self._file_inside_project(rel), "fields": None, "text": None, "truncated": False}
            if entry["exists"] and os.path.splitext(rel)[1].lower() in GATE_TEXT_EXT:
                text, truncated = self._read_inside_project(rel, GATE_ARTIFACT_CAP)
                data = None
                if rel.lower().endswith(".json") and text is not None and not truncated:
                    try:
                        data = json.loads(text)
                    except ValueError:
                        data = None
                if isinstance(data, dict):
                    entry["fields"] = [{"key": str(key), "value": _artifact_value(value)} for key, value in list(data.items())[:40]]
                elif text is not None:
                    entry["text"] = text[:12000]
                    entry["truncated"] = truncated or len(text) > 12000
            artifacts.append(entry)
        work = self.path("work", node_id)
        return {
            "pid": self.pid, "node": node_id, "num": self.node_num(node_id), "name": summary["name"],
            "short": summary["short"],
            "gate": self.human_gate_state(node_id, None),
            "contract": {key: contract.get(key) for key in ("purpose", "outputs", "completion", "human_review")},
            "reached": bool(governed and governed.get("reached")),
            "engine_status": governed.get("status_label") if governed else None,
            "task_packet": packet,
            "work_folder": f"work/{node_id}" if fsio.is_real_dir(work) else None,
            "artifacts": artifacts,
        }

    def node_previews(self, version, node_id):
        """Previews for build nodes: key frames and poster (N13, N14), voice and stems (N12, N14)."""
        num = self.node_num(node_id)
        out = {"poster": None, "video": None, "keyframes": [], "audio": []}
        if not version:
            return out
        if num in (13, 14):
            media = self.media(version)
            out["poster"] = media["poster"]
            out["video"] = media["master"] if num == 14 else None
            out["keyframes"] = self.keyframes(version)[:48]
        if num in (12, 14):
            out["audio"] = self.stem_audio(version, node_id)
        return out

    def stem_audio(self, version, node_id):
        audio = []
        if self.is_candidate(version) and fsio.is_real_dir(self.vdir(version, node_id)):
            for entry in fsio.list_entries(self.vdir(version, node_id)):
                name = entry["name"].lower()
                wanted = name.startswith("voice") if node_id == "n12_voice_audio" else name in (
                    "voice.wav", "voice_stem.wav", "music_stem.wav", "music.wav", "mix.wav")
                if not entry.get("dir") and name.endswith(".wav") and wanted:
                    audio.append({"label": entry["name"], "url": self.media_url(version, f"{node_id}/{entry['name']}")})
        for item in self.run_node_artifacts(version, node_id):
            if item["url"] and item["name"].lower().endswith(".wav"):
                audio.append({"label": os.path.basename(item["name"]), "url": item["url"]})
        return audio[:4]

    @staticmethod
    def _gate_out(gate):
        return {key: gate[key] for key in ("name", "node", "node_num", "ok", "pass", "findings", "findings_norm", "messages", "extra")}

    # ---------------------------------------------------------------- payloads
    def summary(self):
        versions = self.versions()
        run = self.run()
        return {
            "id": self.pid,
            "name": self.project.name,
            "rel_path": self.project.rel_path,
            "run": {k: run[k] for k in ("run_id", "status", "status_class", "current_version", "label", "waiting_at")} if run else None,
            "candidate_count": len(versions),
            "latest_version": versions[-1] if versions else None,
            "versions": versions,
            "live": bool(self.live_versions()),
        }

    def candidate_summary(self, version):
        qa = self.qa(version)
        qa_out = None
        if qa:
            summary = qa.get("summary") or {}
            findings = summary.get("findings")
            if findings is None:
                findings = sum(len(gate["findings"]) for gate in qa["gates"])
            qa_out = {"findings": findings, "blocking": summary.get("blocking"), "gates": len(qa["gates"]),
                      "failing": sum(1 for gate in qa["gates"] if not gate["pass"])}
        run_gates = self.run_gates(version)
        if run_gates and not qa_out:
            qa_out = {"findings": sum(len(g["findings"]) for g in run_gates),
                      "blocking": sum(len(g["findings"]) for g in run_gates), "gates": len(run_gates),
                      "failing": sum(1 for g in run_gates if not g["pass"])}
        return {
            "version": version,
            "source": self.version_source(version),
            "duration_s": self.duration(version),
            "has_master": self.has_master(version),
            "nodes_present": self.nodes_present(version),
            "qa": qa_out,
            "reviews": [{k: r[k] for k in ("key", "reviewer", "label", "node", "verdict", "verdict_class", "worst_level", "defect_count")}
                        for r in self.reviews(version)],
            "reactions_count": len(self.version_reactions(version)),
            "mtime": fsio.iso_utc(self.vmtime(version)),
            "live": self.is_live(version),
        }

    def reviewer_columns(self):
        columns = {}
        for version in self.versions():
            for review in self.reviews(version):
                columns.setdefault(review["key"], {"key": review["key"], "label": review["label"], "node": review["node"],
                                                   "node_num": review["node_num"]})
        return sorted(columns.values(), key=lambda c: (c["node_num"] or 99, c["key"]))

    def detail(self):
        versions = self.versions()
        governed = self.governed_nodes()
        graph_rows = []
        for node in self.graph.nodes:
            summary = self.graph.summary(node["id"])
            entry = governed.get(node["id"])
            status = entry.get("status") if isinstance(entry, dict) and entry.get("status") else None
            graph_rows.append({k: summary[k] for k in ("id", "num", "name", "short", "phase", "kind")}
                              | {"status": status or "not run", "status_label": engine_words(status)})
        run = self.run_model()
        return {
            "id": self.pid,
            "name": self.project.name,
            "slug": self.project.slug,
            "path": self.project.path,
            "rel_path": self.project.rel_path,
            "root": self.project.root,
            "readme_head": self.readme_head(),
            "run": self.run(),
            "workflow": self.workflow or self.default_workflow_info(),
            "telemetry": self.telemetry_summary(),
            "graph_available": self.graph.available,
            "phases": self.graph.phases,
            "graph": graph_rows,
            "versions": versions,
            "governed_versions": run.labels(),
            "latest_version": versions[-1] if versions else None,
            "focus_version": self.focus_version(),
            "candidates": [dict(self.candidate_summary(v), open_levels=self.open_levels(v), notes_count=self.notes_count(v),
                                owner=self.owner_marks().get(v), script_target_s=self.script_target(v))
                           for v in reversed(versions)],
            "brief_runtime": self.brief_runtime(),
            "reviewer_columns": self.reviewer_columns(),
            "live_versions": self.live_versions(),
            "deliverables": [{k: d[k] for k in ("key", "rel")} | {"url": self.run_url(d["rel"]) if d["rel"] and os.path.splitext(d["rel"])[1].lower() in PREVIEW_EXT else None}
                             for d in run.deliverables()],
        }

    def findings(self, version):
        """All defects and gate findings for a version, by severity then time."""
        reviews = self.reviews(version)
        items = [d for r in reviews if not r.get("governed") for d in r["defects"]]
        items += [f for gate in self.all_gates(version) for f in gate["findings_norm"]]
        items += self.run_defects(version)
        for item in items:
            item["state"] = finding_state(item.get("status"))
        items.sort(key=lambda d: (LEVEL_RANK.get(d["level"], 9), d["t0"] if d["t0"] is not None else 1e9, d["uid"]))
        return items

    def finding_titles(self, version):
        return self._cached(("finding_titles", version), lambda: {f["uid"]: f["title"] for f in self.findings(version)})

    def default_workflow_info(self):
        """Workflow info when the reader was built without the app's selection (the graph it was given)."""
        requested = self.project_meta().get("workflow")
        return {
            "requested": requested if isinstance(requested, str) else None,
            "name": None, "found": None, "note": None, "file": self.graph.path,
            "id": self.graph.workflow.get("id"), "title": self.graph.workflow.get("name"),
            "version": self.graph.workflow.get("version"), "nodes": len(self.graph.nodes),
            "human_gates": len(self.graph.human_gate_ids()),
        }

    # ---------------------------------------------------------------- what the owner decides next
    def latest_reviewed_version(self):
        """The newest version whose review set is complete, or None."""
        def compute():
            for version in reversed(self.versions()):
                if self.reviews_complete(version):
                    return version
            return None
        return self._cached("latest_reviewed", compute)

    def newest_version(self):
        """The newest version known: a candidate folder, a governed version or a version in flight under work/."""
        names = set(self.versions()) | set(self.live_versions())
        return max(names, key=fsio.version_key) if names else None

    def current_version(self):
        """The version being worked on: the newest one when it has a master or is in flight, else the latest with a master."""
        newest = self.newest_version()
        if newest and (newest in self.live_versions() or self.has_master(newest)):
            return newest
        return self.focus_version()

    def open_defects(self, version):
        """Reviewer and engine defects of a version that are not resolved (gate findings excluded)."""
        candidates = [d for r in self.reviews(version) if not r.get("governed") for d in r["defects"]] + self.run_defects(version)
        return [d for d in candidates if finding_state(d.get("status")) == "open"]

    def open_levels(self, version):
        """Open reviewer and engine findings of a version, counted by level (the same rule as open_defects)."""
        counts = {}
        for defect in self.open_defects(version):
            level = defect.get("level") or "P?"
            counts[level] = counts.get(level, 0) + 1
        return counts

    def notes_count(self, version):
        """The owner's notes, flags and decisions on a version (retracted ones are already left out by the store)."""
        return sum(1 for r in self.version_reactions(version)
                   if r.get("actor", "owner") == "owner" and r.get("kind") in ("note", "flag", "approve", "request_changes"))

    def owner_marks(self):
        """{version: the owner's latest approve or request_changes on that version (its release gate or the version
        itself), with where the producer is}. Decisions on other gates are not verdicts on a version."""
        def compute():
            out = {}
            release = self.graph.release_node()
            for record in self.reactions():
                if record.get("actor") != "owner" or record.get("kind") not in ("approve", "request_changes"):
                    continue
                version = record.get("version")
                target = record.get("target") or {}
                if not version or version in out or (target.get("type") == "gate" and target.get("id") != release):
                    continue
                out[version] = owner_mark(record)
            return out
        return self._cached("owner_marks", compute)

    def script_target(self, version):
        """The version's own script runtime target in seconds, or None."""
        window = self.runtime_target(version)
        return window.get("target") if window and "script target" in str(window.get("source") or "") else None

    def master_size(self, version):
        """Bytes of the version's master film, or None."""
        if self.is_candidate(version) and fsio.is_file(self.vdir(version, ASSEMBLY, "master.mp4")):
            path = self.vdir(version, ASSEMBLY, "master.mp4")
        else:
            rel = self.run_artifact_path(version, "master")
            path = self.path(rel) if rel else None
        try:
            return os.path.getsize(path) if path else None
        except OSError:
            return None

    def expired_provisionals(self):
        """Human gates whose standing provisional approval has expired: they wait for a named human again."""
        out = []
        current = self.run_model().current
        for node_id in self.graph.human_gate_ids():
            release = self.node_num(node_id) == RELEASE_NODE_NUM
            version = f"v{current}" if release and current else None
            state = self.human_gate_state(node_id, version)
            if state and state.get("provisional_expired"):
                out.append(state)
        return out

    def candidate_detail(self, version):
        reviews = self.reviews(version)
        qa = self.qa(version)
        gates = self.all_gates(version)
        findings = self.findings(version)
        versions = self.versions()
        index = versions.index(version)
        release_node = self.graph.release_node()
        release = self.human_gate_state(release_node, version) if release_node else None
        failing = sum(1 for gate in gates if not gate["pass"])
        strip = [dict({k: r[k] for k in ("key", "label", "node", "node_num", "verdict", "verdict_class", "worst_level", "defect_count")},
                      type="reviewer") for r in reviews]
        strip.append({
            "type": "gates", "label": "Automatic gates", "node": None, "total": len(gates), "failing": failing,
            "findings": sum(len(g["findings"]) for g in gates),
            "blocking": (qa.get("summary") or {}).get("blocking") if qa else None,
            "verdict_class": "none" if not gates else ("rework" if failing else "pass"),
        })
        if release:
            strip.append({"type": "human_gate", "label": f"N{release['num']:02d} release", "node": release_node,
                          "state": release["state"], "state_label": release["label"]})
        candidate = self.is_candidate(version)
        return {
            "pid": self.pid,
            "project": self.project.name,
            "version": version,
            "source": self.version_source(version),
            "versions": versions,
            "prev": versions[index - 1] if index > 0 else None,
            "next": versions[index + 1] if index + 1 < len(versions) else None,
            "compare_with": self.compare_with(version),
            "duration_s": self.duration(version),
            "runtime_window": self.runtime_target(version),
            "has_master": self.has_master(version),
            "media": self.media(version),
            "metrics": self.metrics(version),
            "nodes": [{"node": node, "present": self.node_present(version, node),
                       "files": (self.node_files(version, node) or []) if candidate else self.run_node_artifacts(version, node),
                       "context": self.node_context(version, node) if candidate else None}
                      for node in PRODUCTION_NODES],
            "qa": {"summary": qa["summary"] if qa else None, "url": qa["url"] if qa else None,
                   "gates": [{k: g[k] for k in ("name", "node", "node_num", "ok", "pass", "findings", "messages", "extra")} for g in gates]}
            if gates or qa else None,
            "reviews": reviews,
            "findings": findings,
            "verdict_strip": strip,
            "release_gate": release,
            "captions": self.captions(version),
            "keyframes": self.keyframes(version),
            "notes": self.notes(version),
            "reactions": self.version_reactions(version),
            "live": self.is_live(version),
            "mtime": fsio.iso_utc(self.vmtime(version)),
        }

    def gates_board(self, version):
        human = []
        for node_id in self.graph.human_gate_ids():
            state = self.human_gate_state(node_id, version)
            summary = self.graph.summary(node_id)
            human.append(dict(state, name=summary["name"], short=summary["short"], phase=summary["phase"]))
        qa = self.qa(version) if version else None
        gates = self.all_gates(version) if version else []
        automatic = [dict(self._gate_out(gate), findings_count=len(gate["findings"]), governed=bool(gate.get("governed")),
                          outcome=gate.get("outcome")) for gate in gates]
        reviewers = [{k: r.get(k) for k in ("key", "label", "reviewer", "node", "node_num", "verdict", "verdict_class", "worst_level",
                                            "defect_count", "summary", "url", "governed")} for r in (self.reviews(version) if version else [])]
        return {
            "pid": self.pid,
            "version": version,
            "versions": self.versions(),
            "human": human,
            "automatic": automatic,
            "qa_summary": qa["summary"] if qa else None,
            "qa_url": qa["url"] if qa else None,
            "reviewers": reviewers,
        }

    def constraint_texts(self):
        """vpg.project.yaml constraints as one-line strings (a list, a string or a mapping)."""
        constraints = self.project_meta().get("constraints")
        if isinstance(constraints, str):
            items = [constraints]
        elif isinstance(constraints, list):
            items = [item for item in constraints if isinstance(item, (str, int, float)) and not isinstance(item, bool)]
        elif isinstance(constraints, dict):
            items = [f"{key}: {value}" for key, value in constraints.items() if isinstance(value, (str, int, float))]
        else:
            items = []
        return [" ".join(str(item).split()) for item in items if str(item).strip()]

    def evaluation_thresholds(self):
        """controls/evaluation.md front matter `thresholds:` (the engine's per-project overrides), or {}."""
        text, _ = self._read_inside_project(os.path.join("controls", "evaluation.md"), 256 * 1024)
        match = _FRONT_MATTER.match(text or "")
        doc = fsio.parse_yaml(match.group(1)) if match else None
        thresholds = doc.get("thresholds") if isinstance(doc, dict) else None
        return thresholds if isinstance(thresholds, dict) else {}

    def brief_runtime(self):
        """runtime_target_s and runtime_max_s from the governed run's brief artifact, plus the node that wrote it, or {}."""
        run = self.run_model()
        briefs = [a for a in run.artifacts.values() if a.get("key") == "brief"]
        briefs.sort(key=lambda a: (a.get("status") == "current", str(a.get("id") or "")))
        for artifact in reversed(briefs):
            path = run.path_of(artifact)
            doc = fsio.read_json(self.path(path)) if path and fsio.is_within(self.path(path), self.project.path) else None
            if isinstance(doc, dict):
                out = {key: doc.get(key) for key in ("runtime_target_s", "runtime_max_s")}
                out["node"] = artifact.get("node") if isinstance(artifact.get("node"), str) else None
                return out
        return {}

    def targets(self):
        """Project-level targets: the loudness target (runtime windows are per version, see runtime_target)."""
        def compute():
            return {"loudness": polarity.loudness_target(self.constraint_texts(), self.evaluation_thresholds())}
        return self._cached("targets", compute)

    def _project_json(self, *parts):
        """A JSON object inside the project (its real path must stay inside), or None."""
        path = self.path(*parts)
        if not fsio.is_within(path, self.project.path):
            return None
        doc = fsio.read_json(path)
        return doc if isinstance(doc, dict) else None

    def gate_overrides(self):
        return self._cached("gate_overrides", lambda: self._project_json("pipeline", "gate-overrides.json") or {})

    def runtime_target(self, version):
        """The runtime window a version was made against; the first source that states one wins: the version's own
        candidates/V/plan/script.json runtime_target_s, the project's plan/script.json, controls/evaluation.md
        thresholds, the governed run's brief, then the constraints text. None when nothing states one."""
        def compute():
            scripts = []
            if version and self.is_candidate(version):
                scripts.append((self._project_json("candidates", version, "plan", "script.json"), f"{version} script target",
                                f"candidates/{version}/plan/script.json"))
            scripts.append((self._project_json("plan", "script.json"), "project script target", "plan/script.json"))
            for doc, what, file in scripts:
                target = _positive(doc.get("runtime_target_s")) if doc else None
                window = polarity.script_window(target, self.gate_overrides()) if target else None
                if window:
                    low, high, how = window
                    return {"low": low, "high": high, "target": target, "label": polarity.window_label(low, high),
                            "source": f"{what} {polarity.fmt_num(target)} s",
                            "detail": f"{file} runtime_target_s {polarity.fmt_num(target)}; {how}"}
            th = self.evaluation_thresholds()
            nested = th.get("runtime") if isinstance(th.get("runtime"), dict) else {}
            target = _positive(th.get("runtime_target_s")) or _positive(nested.get("target_s")) or _positive(nested.get("target"))
            low = _positive(th.get("runtime_min_s")) or _positive(nested.get("min_s")) or _positive(nested.get("min"))
            high = _positive(th.get("runtime_max_s")) or _positive(nested.get("max_s")) or _positive(nested.get("max"))
            bounds = polarity.band(target, low, high)
            if bounds:
                return {"low": bounds[0], "high": bounds[1], "target": target, "label": polarity.window_label(*bounds),
                        "source": "controls/evaluation.md thresholds", "detail": "controls/evaluation.md front matter thresholds"}
            window = polarity.runtime_window(self.constraint_texts(), self.brief_runtime())
            if window:
                return dict(window, detail=window["text"] or window["source"])
            return None
        return self._cached(("runtime_target", version), compute)

    def basis_index(self, version):
        """Every object in a version's mix report that states a measurement `basis`: {dotted path: text}."""
        def compute():
            out = {}

            def walk(obj, path, depth):
                text = obj.get("basis")
                if isinstance(text, str) and text.strip():
                    out[path or "(report)"] = " ".join(text.split())
                if depth >= 6:
                    return
                for key, value in obj.items():
                    if isinstance(value, dict):
                        walk(value, f"{path}.{key}" if path else str(key), depth + 1)

            walk(self._assembly_json(version, "mix-report.json") if version else {}, "", 0)
            return out
        return self._cached(("basis_index", version), compute)

    def metric_bases(self, version):
        """The measurement basis behind each compared metric: the first object of its family in the mix report that
        states one ({text, path}), else None (not stated)."""
        index = self.basis_index(version)
        out = {}
        for key, paths in BASIS_SOURCES.items():
            out[key] = next(({"text": index[path], "path": path} for path in paths if path in index), None)
        return out

    def compare_changes(self, a, b, side_a, side_b, verdicts, metrics_delta):
        """The 'What changed' strip: one row per metric with its rule, polarity (better, worse, unchanged, neutral or
        n/a) and a reason in words; a metric measured on different bases on A and B is flagged and counts as uncertain;
        plus the one-line summary."""
        targets = self.targets()
        loud = targets["loudness"]
        wa, wb = self.runtime_target(a), self.runtime_target(b)
        rows = list(verdicts.values())
        pass_a = sum(1 for row in rows if row["a_class"] == "pass")
        pass_b = sum(1 for row in rows if row["b_class"] == "pass")
        reviewed = any(row["a_class"] != "none" for row in rows) and any(row["b_class"] != "none" for row in rows)
        verdict_polarity = (polarity.direction(pass_a, pass_b, True, counts=True) if reviewed
                            else {"polarity": "n/a", "reason": "not reviewed on both versions"})
        rule, rule_detail = _duration_rule(wa, wb)
        changes = [
            dict(key="duration_s", label="Duration", rule=rule, rule_detail=rule_detail,
                 a=side_a["duration_s"], b=side_b["duration_s"], delta=metrics_delta.get("duration_s"),
                 **polarity.duration_between(side_a["duration_s"], side_b["duration_s"], wa, wb, a, b)),
            dict(key="integrated_lufs", label="Loudness (LUFS)",
                 rule=f"closer to {polarity.fmt_num(loud['lufs'])} LUFS is better",
                 a=side_a["metrics"].get("integrated_lufs"), b=side_b["metrics"].get("integrated_lufs"),
                 delta=metrics_delta.get("integrated_lufs"),
                 **polarity.loudness(side_a["metrics"].get("integrated_lufs"), side_b["metrics"].get("integrated_lufs"), loud)),
            dict(key="voice_over_music_p10", label="Voice over music p10 (LU)", rule="higher is better",
                 a=side_a["metrics"].get("voice_over_music_p10"), b=side_b["metrics"].get("voice_over_music_p10"),
                 delta=metrics_delta.get("voice_over_music_p10"),
                 **polarity.direction(side_a["metrics"].get("voice_over_music_p10"), side_b["metrics"].get("voice_over_music_p10"),
                                      True, " LU")),
            dict(key="open_defects", label="Open defects", rule="fewer is better, from P1 down",
                 a=side_a["open_by_level"], b=side_b["open_by_level"], delta=None,
                 **polarity.defect_levels(side_a["open_by_level"], side_b["open_by_level"])),
            dict(key="verdicts_passing", label="Verdicts passing", rule="more is better",
                 a=pass_a, b=pass_b, total=len(rows), delta=pass_b - pass_a, **verdict_polarity),
            dict(key="findings", label="Findings", rule="fewer is better",
                 a=side_a["findings_count"], b=side_b["findings_count"], delta=side_b["findings_count"] - side_a["findings_count"],
                 **polarity.direction(side_a["findings_count"], side_b["findings_count"], False, counts=True)),
        ]
        bases_a, bases_b = self.metric_bases(a), self.metric_bases(b)
        metric_bases = {}
        for key in BASIS_SOURCES:
            ta = (bases_a.get(key) or {}).get("text")
            tb = (bases_b.get(key) or {}).get("text")
            metric_bases[key] = {"a": bases_a.get(key), "b": bases_b.get(key), "differs": ta != tb}
        for change in changes:
            basis = metric_bases.get(change["key"])
            if basis and basis["differs"]:
                change["basis"] = {"a": basis["a"], "b": basis["b"]}
                change["uncertain"] = change["polarity"] != "n/a"
        index_a, index_b = self.basis_index(a), self.basis_index(b)
        basis_changes = [{"path": path, "a": index_a.get(path), "b": index_b.get(path)}
                         for path in sorted(set(index_a) | set(index_b)) if index_a.get(path) != index_b.get(path)]
        return changes, polarity.summarize(changes), dict(targets, runtime={"a": wa, "b": wb}), metric_bases, basis_changes

    def _side(self, version):
        findings = self.findings(version) if version else []
        levels = {"P1": 0, "P2": 0, "P3": 0, "P4": 0, "P?": 0}
        for finding in findings:
            if str(finding.get("status") or "").lower() != "resolved":
                levels[finding["level"]] = levels.get(finding["level"], 0) + 1
        return {
            "version": version,
            "source": self.version_source(version),
            "duration_s": self.duration(version),
            "has_master": self.has_master(version),
            "media": self.media(version),
            "metrics": self.metrics(version),
            "qa": self.candidate_summary(version)["qa"],
            "findings_count": len(findings),
            "open_by_level": levels,
            "reviews": [{k: r[k] for k in ("key", "label", "node", "verdict", "verdict_class", "worst_level", "defect_count")}
                        for r in self.reviews(version)],
        }

    def compare(self, a, b):
        side_a, side_b = self._side(a), self._side(b)
        metrics_delta = {}
        for key in METRIC_KEYS:
            va, vb = side_a["metrics"].get(key), side_b["metrics"].get(key)
            metrics_delta[key] = round(vb - va, 3) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
        if isinstance(side_a["duration_s"], (int, float)) and isinstance(side_b["duration_s"], (int, float)):
            metrics_delta["duration_s"] = round(side_b["duration_s"] - side_a["duration_s"], 3)
        reviews_a = {r["key"]: r for r in self.reviews(a)}
        reviews_b = {r["key"]: r for r in self.reviews(b)}
        keys = sorted(set(reviews_a) | set(reviews_b), key=lambda k: ((reviews_a.get(k) or reviews_b.get(k))["node_num"], k))
        scores, verdicts = [], {}
        for key in keys:
            ra, rb = reviews_a.get(key), reviews_b.get(key)
            label = (rb or ra)["label"]
            dims_a, dims_b = _scores_by_dimension(ra), _scores_by_dimension(rb)
            for dimension in list(dims_a) + [d for d in dims_b if d not in dims_a]:
                sa, sb = dims_a.get(dimension), dims_b.get(dimension)
                va = sa["score"] if sa else None
                vb = sb["score"] if sb else None
                delta = round(vb - va, 3) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
                change = None if delta is None else ("better" if delta > 0 else "worse" if delta < 0 else "same")
                scores.append({
                    "reviewer": key, "reviewer_label": label, "dimension": dimension,
                    "node": (sb or sa)["node"], "a": va, "b": vb,
                    "a_result": sa["result"] if sa else None, "b_result": sb["result"] if sb else None,
                    "delta": delta, "change": change,
                })
            verdicts[key] = {
                "label": label, "node": (rb or ra)["node"],
                "a": ra["verdict"] if ra else None, "a_class": ra["verdict_class"] if ra else "none",
                "a_worst": ra["worst_level"] if ra else None, "a_defects": ra["defect_count"] if ra else None,
                "b": rb["verdict"] if rb else None, "b_class": rb["verdict_class"] if rb else "none",
                "b_worst": rb["worst_level"] if rb else None, "b_defects": rb["defect_count"] if rb else None,
            }
        preferences = [r for r in self.reactions() if r.get("kind") == "prefer" and {r.get("a"), r.get("b")} == {a, b}]
        changes, summary, targets, metric_bases, basis_changes = self.compare_changes(a, b, side_a, side_b, verdicts, metrics_delta)
        return {"pid": self.pid, "a": side_a, "b": side_b, "metrics_delta": metrics_delta, "scores": scores,
                "verdicts": verdicts, "preferences": preferences, "compare_with": self.compare_with(b),
                "changes": changes, "change_summary": summary, "targets": targets, "metric_bases": metric_bases,
                "basis_changes": basis_changes}


def _pickup_words(owner):
    """Where the producer is with an owner decision: not yet picked up, picked up by X, or addressed in vN by X."""
    events = owner.get("thread") or [dict(ack, kind="ack") for ack in owner.get("acks") or []]
    state = owner.get("state") or ("picked_up" if events else "open")
    last = next((e for e in reversed(events) if e.get("kind") in ("ack", "resolve")), None)
    who = (last.get("producer") or "").strip() if last and isinstance(last.get("producer"), str) else ""
    who = who or "the producer"
    if state == "resolved" and last:
        return f"addressed in {last.get('version')} by {who}"
    if state == "picked_up":
        return f"picked up by {who}"
    if events and any(e.get("kind") == "reopen" for e in events):
        return "reopened by you; waiting for the producer"
    return "waiting for the producer"


def _positive(value):
    """A positive number from JSON or YAML (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _duration_rule(wa, wb):
    """The duration cell's rule line, naming where each version's runtime window came from, and its tooltip."""
    detail = "; ".join(f"{side}: {window['detail']}" for side, window in (("A", wa), ("B", wb)) if window) or None
    if not wa and not wb:
        return "neutral: no runtime target known", None
    if wa and wb and polarity.same_window(wa, wb):
        named = wb["source"] if wa["source"] == wb["source"] else f"{wb['source']}; {wa['source']}"
        return f"within {wb['label']} ({named})", detail

    def side(name, window):
        return f"{name} within {window['label']} ({window['source']})" if window else f"{name}: no runtime target"

    return f"{side('A', wa)}; {side('B', wb)}", detail


def _scores_by_dimension(review):
    out = {}
    if not review:
        return out
    for score in review["scores"]:
        name = score["dimension"]
        if name in out:
            suffix = 2
            while f"{name} ({suffix})" in out:
                suffix += 1
            name = f"{name} ({suffix})"
        out[name] = score
    return out
