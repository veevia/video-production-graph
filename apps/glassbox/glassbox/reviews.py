"""Normalise reviewer files, defects, scores and qa-direct gates into stable shapes."""
from __future__ import annotations

import json
import re

from .timecode import parse_t0, split_leading_time

LEVEL_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4, "P?": 5}
BLOCKING_LEVELS = ("P1", "P2", "P3")
TEXT_CAP = 8000
_LEVEL = re.compile(r"\bP\s?([1-4])\b", re.I)
_LEVEL_WORDS = (
    (re.compile(r"\b(critical|blocker|showstopper)\b", re.I), "P1"),
    (re.compile(r"\b(major|high)\b", re.I), "P2"),
    (re.compile(r"\b(minor|medium|moderate)\b", re.I), "P3"),
    (re.compile(r"\b(nit|nits|nitpick|trivial|cosmetic|low)\b", re.I), "P4"),
)
_LEVEL_KEY = re.compile(r"^(P[1-4?]|nits?|critical|major|minor|blockers?)$", re.I)
_CARRIED_KEY = re.compile(r"^(v\d+[a-z0-9]*)_(defects|findings)$", re.I)
_TEXT_KEYS = ("what", "description", "message", "finding", "issue", "title", "summary", "text", "note")
_FIX_KEYS = ("fix", "smallest_fix", "suggestion", "remedy", "action")
_AT_KEYS = ("at", "time", "timecode", "t", "start", "where")
_PASS_WORDS = ("pass", "passed", "approve", "approved", "accept", "accepted", "ok", "release", "released", "ship")
_REWORK_PREFIXES = ("rework", "revis", "fail", "reject", "change", "request", "block", "hold")
_SENTENCE = re.compile(r"^(.+?[.!?])(?=\s|$)")
_WORD = re.compile(r"[A-Za-z]{3,}")
TITLE_CAP = 300
_CLOSED_WORDS = ("resolved", "closed", "fixed", "done", "verified")
_FIRST_WORD = re.compile(r"[a-z]+")


def finding_state(status):
    """'resolved' when a finding's status starts with a closing word ('resolved', 'closed', 'fixed', 'done',
    'verified'), else 'open'. Only the first word counts, so 'partly fixed' or 'neither fixed nor declined' stay open."""
    match = _FIRST_WORD.search(str(status or "").lower())
    return "resolved" if match and match.group(0) in _CLOSED_WORDS else "open"


def cap_text(value, cap=TEXT_CAP):
    if value is None:
        return None
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(value) <= cap else value[: cap - 1] + "\u2026"


def as_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[-+]?\d+", text):
            return int(text)
        if re.fullmatch(r"[-+]?\d*\.\d+", text):
            return float(text)
    return None


def normalize_level(*values):
    """'P1'..'P4' from severity-like values ('P3 (blocks)', 'nit (P4)', 2), else 'P?'."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if 1 <= value <= 4 and float(value).is_integer():
                return f"P{int(value)}"
            continue
        match = _LEVEL.search(str(value))
        if match:
            return f"P{match.group(1)}"
    for value in values:
        if isinstance(value, str):
            for pattern, level in _LEVEL_WORDS:
                if pattern.search(value):
                    return level
    return "P?"


def worst_level(defects):
    levels = [d["level"] for d in defects]
    return min(levels, key=lambda level: LEVEL_RANK.get(level, 9)) if levels else None


def verdict_class(verdict):
    text = str(verdict or "").strip().lower()
    if not text:
        return "none"
    if text in _PASS_WORDS or text.startswith("pass"):
        return "pass"
    if text.startswith(_REWORK_PREFIXES):
        return "rework"
    return "unknown"


def reviewer_label(reviewer, key):
    text = str(reviewer or "").strip()
    if text.lower().startswith("agent:"):
        text = text[6:].strip()
    if text.lower().endswith("-reviewer") and len(text) > 9:
        text = text[:-9]
    if not text or len(text) > 32:
        return key
    return text


def _first(raw, keys):
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return None


def _flat(text, cap=TITLE_CAP):
    flat = " ".join(text.split())
    return flat if len(flat) <= cap else flat[: cap - 1] + "\u2026"


def first_sentence(text):
    """The first sentence of a prose string; None for blanks and for bare timecodes or numbers."""
    if not isinstance(text, str) or not _WORD.search(text):
        return None
    flat = " ".join(text.split())
    match = _SENTENCE.match(flat)
    return _flat(match.group(1) if match else flat)


def finding_title(what=None, description=None, title=None, evidence=None, dimension=None, ident=None):
    """A finding's one-line title: what, description, title, the evidence's first sentence, the dimension,
    else 'Untitled finding (id)'. Never empty."""
    for value in (what, description, title):
        if isinstance(value, str) and value.strip():
            return _flat(value)
    sentence = first_sentence(evidence)
    if sentence:
        return sentence
    if isinstance(dimension, str) and dimension.strip():
        return _flat(dimension)
    return f"Untitled finding ({ident})" if ident else "Untitled finding"


def normalize_defect(raw, source, index, default_level=None):
    """One defect or finding, from a dict or a string, in the normalised shape."""
    if isinstance(raw, dict):
        at = _first(raw, _AT_KEYS)
        if isinstance(at, (int, float)) and not isinstance(at, bool):
            at_text = f"{at} s"
        elif isinstance(at, (list, tuple)):
            at_text = ", ".join(str(item) for item in at)
        else:
            at_text = str(at) if at is not None else None
        ident = _first(raw, ("id", "defect_id", "check", "code"))
        severity = _first(raw, ("severity", "priority"))
        defect = {
            "id": str(ident) if ident is not None else None,
            "level": normalize_level(severity, raw.get("level"), default_level),
            "severity": cap_text(severity if severity is not None else raw.get("level"), 200),
            "dimension": cap_text(raw.get("dimension"), 200),
            "at_text": cap_text(at_text, 600),
            "t0": parse_t0(at),
            "what": cap_text(_first(raw, _TEXT_KEYS)),
            "fix": cap_text(_first(raw, _FIX_KEYS)),
            "fix_node": cap_text(_first(raw, ("fix_node", "traced_node", "node")), 300),
            "evidence": cap_text(raw.get("evidence")),
            "status": cap_text(raw.get("status"), 600),
        }
    else:
        text = str(raw).strip()
        at_text, what = split_leading_time(text)
        defect = {
            "id": None,
            "level": normalize_level(default_level),
            "severity": cap_text(default_level, 200),
            "dimension": None,
            "at_text": at_text,
            "t0": parse_t0(at_text) if at_text else parse_t0(text),
            "what": cap_text(what),
            "fix": None,
            "fix_node": None,
            "evidence": None,
            "status": None,
        }
    defect["uid"] = f"{source}:{defect['id'] or '#' + str(index + 1)}"
    defect["source"] = source
    if isinstance(raw, dict):
        evidence = raw.get("evidence")
        defect["title"] = finding_title(defect["what"], raw.get("description"), raw.get("title"),
                                        evidence if isinstance(evidence, str) else None,
                                        defect["dimension"], defect["id"] or defect["uid"])
    else:
        defect["title"] = finding_title(defect["what"], ident=defect["uid"])
    return defect


def collect_items(value):
    """Flatten a findings value into [(item, default_level)]: lists, level-keyed dicts, dicts or strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [(item, None) for item in value if item not in (None, "")]
    if isinstance(value, dict):
        if value and all(_LEVEL_KEY.match(str(key)) for key in value):
            items = []
            for level, entries in value.items():
                entries = entries if isinstance(entries, list) else [entries]
                items.extend((entry, level) for entry in entries if entry not in (None, ""))
            return items
        return [(value, None)]
    if isinstance(value, str) and value.strip():
        return [(value, None)]
    return []


def _dedupe_uids(defects):
    seen = {}
    for defect in defects:
        uid = defect["uid"]
        if uid in seen:
            seen[uid] += 1
            defect["uid"] = f"{uid}~{seen[uid]}"
        else:
            seen[uid] = 1
    return defects


def normalize_score(raw, index):
    if not isinstance(raw, dict):
        return {"dimension": f"#{index + 1}", "name": None, "score": as_number(raw),
                "result": None if as_number(raw) is not None else cap_text(str(raw), 600),
                "threshold": None, "kind": None, "rubric": None, "evidence": None}
    dimension = _first(raw, ("dimension", "name", "check", "id"))
    dimension = str(dimension) if dimension is not None else f"#{index + 1}"
    score = raw.get("score")
    number = as_number(score)
    result = raw.get("result")
    if number is None and isinstance(score, str) and score.strip() and result is None:
        result = score
    name = raw.get("name")
    return {
        "dimension": dimension,
        "name": name if isinstance(name, str) and name != dimension else None,
        "score": number,
        "result": cap_text(result, 600) if result is not None else None,
        "threshold": cap_text(raw.get("threshold"), 200),
        "kind": raw.get("kind") if isinstance(raw.get("kind"), str) else None,
        "rubric": cap_text(raw.get("rubric"), 1200),
        "evidence": cap_text(raw.get("evidence")),
    }


def normalize_carried(value, origin):
    carried = []
    for index, (item, _) in enumerate(collect_items(value)):
        if isinstance(item, dict):
            ident = _first(item, ("id", "defect_id"))
            carried.append({
                "id": str(ident) if ident is not None else f"#{index + 1}",
                "from": origin,
                "status": cap_text(item.get("status"), 1200),
                "evidence": cap_text(item.get("evidence")),
                "what": cap_text(_first(item, _TEXT_KEYS)),
            })
        else:
            carried.append({"id": f"#{index + 1}", "from": origin, "status": None, "evidence": None,
                            "what": cap_text(str(item))})
    return carried


def normalize_review(doc, key):
    """A reviewer file (reviews/<key>.json) -> verdict, summary, scores, defects, carried items."""
    reviewer = doc.get("reviewer") if isinstance(doc.get("reviewer"), str) else None
    verdict = doc.get("verdict")
    verdict = verdict if isinstance(verdict, str) or verdict is None else str(verdict)
    defects = []
    for field in ("defects", "findings", "issues"):
        for item, level in collect_items(doc.get(field)):
            defects.append(normalize_defect(item, key, len(defects), level))
    carried = []
    for field, value in doc.items():
        match = _CARRIED_KEY.match(str(field))
        if match:
            carried.extend(normalize_carried(value, match.group(1)))
    scores_raw = doc.get("scores")
    if isinstance(scores_raw, dict):
        scores_raw = [{"dimension": k, "score": v} if not isinstance(v, dict) else dict(v, dimension=k)
                      for k, v in scores_raw.items()]
    scores = [normalize_score(item, index) for index, item in enumerate(scores_raw or []) if item is not None] \
        if isinstance(scores_raw, list) else []
    summary = doc.get("summary")
    _dedupe_uids(defects)
    return {
        "key": key,
        "reviewer": reviewer or key,
        "label": reviewer_label(reviewer, key),
        "verdict": verdict,
        "verdict_class": verdict_class(verdict),
        "summary": cap_text(summary) if summary is not None else None,
        "scores": scores,
        "defects": defects,
        "carried": carried,
        "worst_level": worst_level(defects),
        "defect_count": len(defects),
    }


def readable(item):
    """Raw-but-readable view of a gate finding: text, or key/value pairs."""
    if isinstance(item, dict):
        return {"fields": [[str(k), cap_text(v, 1200)] for k, v in item.items()]}
    return {"text": cap_text(str(item), 2000)}


def normalize_gate(name, raw):
    if not isinstance(raw, dict):
        raw = {"findings": raw} if isinstance(raw, list) else {}
    ok = raw.get("ok") if isinstance(raw.get("ok"), bool) else None
    items = collect_items(raw.get("findings"))
    messages = raw.get("messages")
    messages = [cap_text(m if isinstance(m, str) else m, 800) for m in messages if m not in (None, "")] \
        if isinstance(messages, list) else []
    source = f"gate:{name}"
    findings_norm = _dedupe_uids([normalize_defect(item, source, i, level) for i, (item, level) in enumerate(items)])
    return {
        "name": name,
        "ok": ok,
        "pass": not items and ok is not False,
        "findings": [readable(item) for item, _ in items],
        "findings_norm": findings_norm,
        "messages": messages,
        "extra": {str(k): cap_text(v, 1500) for k, v in raw.items() if k not in ("findings", "ok", "messages")},
    }


def normalize_qa(doc):
    """qa/qa-direct.json -> {version, summary, gates:[...]} or None."""
    if not isinstance(doc, dict):
        return None
    raw_gates = doc.get("gates")
    if isinstance(raw_gates, dict):
        pairs = list(raw_gates.items())
    elif isinstance(raw_gates, list):
        pairs = [(g.get("name") or g.get("id") or f"gate {i + 1}", g) for i, g in enumerate(raw_gates) if isinstance(g, dict)]
    else:
        pairs = []
    summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
    return {
        "version": doc.get("version") if isinstance(doc.get("version"), str) else None,
        "summary": {
            "findings": as_number(summary.get("findings")),
            "blocking": as_number(summary.get("blocking")),
        } if summary else None,
        "gates": [normalize_gate(str(name), gate) for name, gate in pairs],
    }


def normalize_governed_defect(raw):
    """evaluation.defects entry -> the normalised defect shape (traced_node becomes fix_node)."""
    ident = raw.get("id")
    evidence = raw.get("evidence")
    defect = {
        "id": str(ident) if ident is not None else None,
        "level": normalize_level(raw.get("severity")),
        "severity": cap_text(raw.get("severity"), 200),
        "dimension": cap_text(raw.get("dimension"), 200),
        "at_text": cap_text(evidence, 600) if isinstance(evidence, str) else None,
        "t0": parse_t0(evidence),
        "what": cap_text(raw.get("description")),
        "fix": cap_text(raw.get("smallest_fix")),
        "fix_node": cap_text(raw.get("traced_node"), 300),
        "evidence": None,
        "acceptance": cap_text(raw.get("acceptance_check"), 1200),
        "status": cap_text(raw.get("status"), 600),
        "needs_human": raw.get("needs_human") if isinstance(raw.get("needs_human"), bool) else None,
        "first_seen": raw.get("first_seen_version"),
        "last_seen": raw.get("last_seen_version"),
        "review_id": raw.get("source") if isinstance(raw.get("source"), str) else None,
        "source": "run",
    }
    defect["uid"] = f"run:{defect['id'] or '#'}"
    defect["title"] = finding_title(None, raw.get("description"), raw.get("title"),
                                    evidence if isinstance(evidence, str) else None,
                                    defect["dimension"], defect["id"] or defect["uid"])
    return defect


def normalize_governed_review(raw, defects):
    """evaluation.reviews entry -> the normalised review shape; defects are the version's normalised defects."""
    review_id = raw.get("id") if isinstance(raw.get("id"), str) else None
    node = raw.get("node") if isinstance(raw.get("node"), str) else None
    reviewer = raw.get("reviewer") if isinstance(raw.get("reviewer"), str) else None
    ids = {str(i) for i in raw.get("defect_ids") or []}
    mine = [d for d in defects if (review_id and d.get("review_id") == review_id) or (d.get("id") in ids)]
    scores = []
    for index, item in enumerate(raw.get("scores") or []):
        score = normalize_score(item, index)
        if isinstance(item, dict):
            passed = item.get("passed")
            if score["result"] is None and isinstance(passed, bool):
                score["result"] = "pass" if passed else "fail"
            if isinstance(item.get("level"), str):
                score["kind"] = item["level"]
        scores.append(score)
    verdict = raw.get("verdict") if isinstance(raw.get("verdict"), str) else None
    pending = [cap_text(p, 600) for p in raw.get("pending_human") or [] if p]
    tools = [readable(f) for f in raw.get("tool_findings") or [] if f not in (None, "")]
    key = node or reviewer or review_id or "review"
    return {
        "key": key,
        "id": review_id,
        "reviewer": reviewer or key,
        "label": reviewer_label(reviewer, key),
        "verdict": verdict,
        "verdict_class": verdict_class(verdict),
        "summary": None,
        "scores": scores,
        "defects": mine,
        "carried": [],
        "worst_level": worst_level(mine),
        "defect_count": len(mine),
        "pending_human": pending,
        "tool_findings": tools,
        "created_at": raw.get("created_at") if isinstance(raw.get("created_at"), str) else None,
        "governed": True,
    }


def normalize_gate_result(raw):
    """evaluation.gate_results entry -> a gate in the same shape as qa-direct gates."""
    outcome = str(raw.get("outcome") or "")
    reasons = [cap_text(r if isinstance(r, str) else r, 800) for r in raw.get("reasons") or [] if r not in (None, "")]
    blocking = [b for b in raw.get("open_blocking") or [] if b not in (None, "")]
    passed = outcome.lower() in ("pass", "passed", "ok", "go", "approved")
    name = raw.get("id") if isinstance(raw.get("id"), str) else f"gate {raw.get('node')}"
    extra = {}
    if raw.get("trend") is not None:
        extra["trend"] = cap_text(raw.get("trend"), 1500)
    return {
        "name": name,
        "ok": passed,
        "pass": passed and not blocking,
        "outcome": outcome or None,
        "findings": [readable(b) for b in blocking],
        "findings_norm": [],
        "messages": reasons,
        "extra": extra,
        "node_raw": raw.get("node") if isinstance(raw.get("node"), str) else None,
        "created_at": raw.get("created_at") if isinstance(raw.get("created_at"), str) else None,
        "governed": True,
    }
