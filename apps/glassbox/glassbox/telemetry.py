"""Engine telemetry in .vpg/telemetry/: evaluation events, spans and the coverage summary, read tolerantly.

The files are derived by `vpg telemetry --write` (the glass-box data contract in the engine's observability docs).
Bad lines are skipped and counted; unknown fields are ignored; nothing here writes.
"""
from __future__ import annotations

import json
import os

from . import fsio
from .governed import _epoch
from .reviews import as_number, cap_text
from .timecode import parse_t0

TELEMETRY_DIR = (".vpg", "telemetry")
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 50000
MAX_EVIDENCE = 20
EVIDENCE_TAGS = ("observed", "reported", "assumed")
_TIME_KEYS = ("t", "t0", "time_s", "timecode", "at", "start", "start_s", "time")
_TEXT_KEYS = ("text", "message", "description", "explanation", "evidence", "note")
_SHOWN_FIELDS = ("check", "severity", "element", "traced_node", "artifact_key", "key", "id", "version", "decision",
                 "expires_at", "fingerprint", "more")


def _load_jsonl(path, size):
    """(records, skipped): dict lines in file order; blank lines ignored, anything else that fails counts as skipped."""
    if size > MAX_FILE_BYTES:
        return [], None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return [], None
    records, skipped = [], 0
    for raw in data.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except (ValueError, RecursionError):
            skipped += 1
            continue
        if not isinstance(item, dict):
            skipped += 1
            continue
        records.append(item)
        if len(records) >= MAX_RECORDS:
            break
    return records, skipped


_JSONL = fsio.MtimeCache(_load_jsonl, max_entries=128)


def _str(value, cap=300):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return cap_text(text, cap) if text else None
    return None


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def evidence_item(raw, link=None):
    """One evidence entry: kind, text, a seekable time when one is named, frame, metric, a file and the other fields.

    link(path) returns a servable URL for a project-relative path, or None."""
    if not isinstance(raw, dict):
        text = _str(raw, 1200) if not isinstance(raw, (list, dict)) else cap_text(raw, 1200)
        return {"kind": "note", "text": text, "t0": parse_t0(text), "at_text": None, "frame": None, "metric": None,
                "value": None, "unit": None, "path": None, "url": None, "sha256": None, "key": None, "fields": []}
    texts = [(key, _str(raw.get(key), 1200)) for key in _TEXT_KEYS]
    texts = [(key, value) for key, value in texts if value]
    text_key, text = texts[0] if texts else (None, None)
    at_value = None
    for key in _TIME_KEYS:
        if raw.get(key) not in (None, "") and not isinstance(raw.get(key), bool):
            at_value = raw.get(key)
            break
    t0 = parse_t0(at_value) if at_value is not None else None
    for _, value in texts:
        if t0 is not None:
            break
        t0 = parse_t0(value)
    named_metric = raw.get("metric") or raw.get("metric_id")
    value = raw.get("value")
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        value = None
    path = _str(raw.get("path"), 600)
    sha = _str(raw.get("sha256"), 64)
    fields = [[key, value_text] for key, value_text in texts[1:]]
    for key in _SHOWN_FIELDS:
        shown = _str(raw.get(key), 300)
        if shown and not (key == "key" and path):
            fields.append([key, shown])
    if value is not None and not named_metric:
        fields.append(["value", _str(value, 300)])
    envelope = raw.get("envelope")
    if isinstance(envelope, (list, dict, str, int, float)) and not isinstance(envelope, bool):
        fields.append(["envelope", cap_text(envelope, 300)])
    return {
        "kind": _str(raw.get("kind"), 60) or "evidence",
        "text": text,
        "text_key": text_key,
        "t0": t0,
        "at_text": _str(at_value, 120) if at_value is not None else None,
        "frame": _int(raw.get("frame")),
        "metric": _str(named_metric, 120) if named_metric else None,
        "value": (value if isinstance(value, (int, float)) else _str(value, 120)) if named_metric else None,
        "unit": _str(raw.get("unit"), 40),
        "path": path,
        "url": link(path) if (link and path) else None,
        "sha256": sha[:16] if sha else None,
        "key": _str(raw.get("key"), 120) if path else None,
        "fields": fields[:10],
    }


def _source_text(source):
    if not isinstance(source, dict):
        return None
    if source.get("ledger_seq") is not None:
        return f"ledger event {source.get('ledger_seq')}"
    if isinstance(source.get("state"), str):
        return f"state {source['state']}"
    return None


def evaluation_event(raw, link=None):
    """An evaluation event (gen_ai.evaluation.result shape) in a flat, display-ready form."""
    name = _str(raw.get("name") or raw.get("gen_ai.evaluation.name"), 200) or "evaluation"
    score = as_number(raw.get("gen_ai.evaluation.score.value"))
    if score is None:
        score = as_number(raw.get("score"))
    label = _str(raw.get("gen_ai.evaluation.score.label") or raw.get("label"), 80)
    evidence = raw.get("evidence")
    items = evidence if isinstance(evidence, list) else ([evidence] if evidence not in (None, "", {}) else [])
    tag = _str(raw.get("evidence_tag"), 20)
    return {
        "name": name,
        "short": name[4:] if name.startswith("vpg.") else name,
        "score": score,
        "label": label,
        "explanation": _str(raw.get("gen_ai.evaluation.explanation") or raw.get("explanation"), 2000),
        "node": _str(raw.get("node"), 120),
        "attempt": _int(raw.get("attempt")),
        "evaluator": _str(raw.get("evaluator"), 120),
        "evidence_tag": tag if tag in EVIDENCE_TAGS else (tag or None),
        "time": _str(raw.get("time"), 40),
        "version": _str(raw.get("version"), 40),
        "source": _source_text(raw.get("source")),
        "evidence": [evidence_item(item, link) for item in items[:MAX_EVIDENCE]],
        "evidence_more": max(0, len(items) - MAX_EVIDENCE),
    }


def span_record(raw):
    attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    start, end = _str(raw.get("start"), 40), _str(raw.get("end"), 40)
    duration = None
    if start and end:
        a, b = _epoch(start), _epoch(end)
        if a is not None and b is not None and b >= a:
            duration = round(b - a, 1)
    return {
        "name": _str(raw.get("name"), 200) or "span",
        "kind": _str(raw.get("kind"), 20),
        "status": _str(raw.get("status"), 20),
        "start": start,
        "end": end,
        "duration_s": duration,
        "node": _str(attributes.get("vpg.node.id"), 120),
        "attempt": _int(attributes.get("vpg.attempt")),
        "outcome": _str(attributes.get("vpg.outcome") or attributes.get("vpg.human.decision"), 120),
        "actor": _str(attributes.get("vpg.actor") or attributes.get("vpg.human.actor"), 200),
        "evidence_tag": _str(attributes.get("vpg.evidence_tag"), 20),
    }


def _missing(coverage):
    missing = coverage.get("missing")
    if isinstance(missing, list):
        items = [str(item)[:200] for item in missing if item not in (None, "")]
        return len(items), items[:30]
    if isinstance(missing, bool):
        return (0 if not missing else 1), []
    if isinstance(missing, (int, float)):
        return int(missing), []
    if isinstance(missing, dict):
        items = [f"{k}: {v}" for k, v in missing.items()][:30]
        return len(missing), items
    return 0, []


class Telemetry:
    """One project's .vpg/telemetry/ folder; files whose real path leaves the project are ignored."""

    def __init__(self, project_path):
        self.project_path = project_path
        self.dir = os.path.join(project_path, *TELEMETRY_DIR)

    def _file(self, name):
        path = os.path.join(self.dir, name)
        return path if fsio.is_file(path) and fsio.is_within(path, self.project_path) else None

    def _jsonl(self, name):
        path = self._file(name)
        if path is None:
            return None
        value = _JSONL.get(path)
        return value if value is not None else ([], None)

    def present(self):
        return any(self._file(name) for name in ("evaluations.jsonl", "spans.jsonl", "summary.json"))

    def evaluations_raw(self):
        return self._jsonl("evaluations.jsonl")

    def spans_raw(self):
        return self._jsonl("spans.jsonl")

    def summary(self, run_id=None):
        """Coverage for the run card: ok, how many items are missing, counts; None when there is no telemetry."""
        if not self.present():
            return None
        summary_path = self._file("summary.json")
        doc = fsio.read_json(summary_path) if summary_path else None
        evaluations = self.evaluations_raw()
        spans = self.spans_raw()
        out = {
            "present": True,
            "summary_file": isinstance(doc, dict),
            "schema": _str(doc.get("schema"), 60) if isinstance(doc, dict) else None,
            "run_id": _str(doc.get("run_id"), 120) if isinstance(doc, dict) else None,
            "ok": None,
            "missing_count": None,
            "missing": [],
            "counts": {},
            "evaluations": len(evaluations[0]) if evaluations else 0,
            "spans": len(spans[0]) if spans else 0,
            "skipped_lines": sum((part[1] or 0) for part in (evaluations, spans) if part),
        }
        coverage = doc.get("coverage") if isinstance(doc, dict) and isinstance(doc.get("coverage"), dict) else None
        if coverage is not None:
            count, items = _missing(coverage)
            ok = coverage.get("ok") if isinstance(coverage.get("ok"), bool) else (count == 0)
            out.update(ok=ok and count == 0, missing_count=count, missing=items)
            for key in ("nodes_run", "nodes_with_evidence", "gates", "gate_events", "decisions", "decision_events",
                        "evaluations", "spans"):
                number = _int(coverage.get(key))
                if number is not None:
                    out["counts"][key] = number
            orphans = coverage.get("orphan_events")
            if isinstance(orphans, list) and orphans:
                out["counts"]["orphan_events"] = len(orphans)
        out["stale"] = bool(run_id and out["run_id"] and out["run_id"] != run_id)
        return out
