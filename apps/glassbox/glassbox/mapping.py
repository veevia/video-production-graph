"""Map reviews, scores, defects and qa-direct gates onto engine nodes, and aggregate node status."""
from __future__ import annotations

import re

STATUSES = ("failed", "waiting", "running", "warnings", "unknown", "passed", "direct", "not_run")
STATUS_LABELS = {
    "failed": "failed / rework",
    "waiting": "waiting for you",
    "decided": "decided by you; engine waits",
    "running": "running",
    "warnings": "warnings (P4 only)",
    "unknown": "unknown",
    "passed": "passed",
    "direct": "direct run",
    "not_run": "not run",
}
REVIEWER_RULES = (
    ("technical-qa", 15), ("technical", 15), ("tech-qa", 15),
    ("creative-critic", 16), ("creative", 16),
    ("design", 17),
    ("accessibility", 18), ("legibility", 18), ("a11y", 18),
    ("product-accuracy", 19), ("accuracy", 19),
    ("takeaway", 20), ("narrative", 20), ("pacing", 20),
)
DEFAULT_REVIEWER_NODE = 16
SCENE_GATES = frozenset({
    "STILLS", "MOTION", "HOLD", "EDGES", "COVER", "LOUPE", "RINGS", "NATIVE",
    "DENSITY", "TEXT-MAIN", "CONCISE", "STYLE", "BEAT",
})
_T_DIMENSION = re.compile(r"^T[1-5](?![0-9])")
_N_PREFIX = re.compile(r"^N(\d{1,2})(?![0-9])")
_PASSED = {"STEP_PASS", "PASS", "PASSED", "DONE", "COMPLETE", "COMPLETED", "SUCCEEDED", "SUCCESS", "APPROVED", "RELEASED"}
_NOT_RUN = {"PLANNED", "PENDING", "QUEUED", "READY", "NOT_STARTED", "IDLE", "NEW", "SKIPPED", "BLOCKED_UPSTREAM"}
_RUNNING = {"RUNNING", "IN_PROGRESS", "DISPATCHED", "STARTED", "SUBMITTED", "ACTIVE", "EXECUTING"}
_FAILED_WORDS = ("FAIL", "ERROR", "ESCALAT", "REJECT", "REWORK", "ABORT")


def reviewer_node_num(key):
    """Reviewer file stem -> node number (technical-qa -> 15, ..., takeaway-60 -> 20; others -> 16)."""
    lowered = str(key or "").lower()
    for prefix, num in REVIEWER_RULES:
        if lowered.startswith(prefix):
            return num
    return DEFAULT_REVIEWER_NODE


def _prefixed(text, *prefixes):
    for prefix in prefixes:
        if text == prefix or (text.startswith(prefix) and text[len(prefix)] in "-_ :."):
            return True
    return False


def score_node_num(dimension, reviewer_num):
    """Score dimension prefix -> node number; unknown prefixes stay with the reviewer's node."""
    dim = str(dimension or "").strip().upper()
    if "CAPTION" in dim:
        return 18
    if _prefixed(dim, "TEC"):
        return 15
    if _prefixed(dim, "CRE"):
        return 16
    if _prefixed(dim, "DES", "UNCHANGED"):
        return 17
    if _prefixed(dim, "LEG", "CAP") or dim.startswith("A11Y"):
        return 18
    if _prefixed(dim, "ACC", "PA"):
        return 19
    if _prefixed(dim, "NAR") or dim.startswith("TAKEAWAY") or _T_DIMENSION.match(dim):
        return 20
    if _prefixed(dim, "VOI"):
        return 12
    return reviewer_num


def gate_node_num(name):
    """qa-direct gate name -> node number; anything unrecognised belongs to N15 technical QA."""
    upper = str(name or "").strip().upper()
    match = _N_PREFIX.match(upper)
    if match and 1 <= int(match.group(1)) <= 99:
        return int(match.group(1))
    if upper.startswith("CAP"):
        return 18
    if upper.startswith("VOI-"):
        return 12
    if upper.startswith("PAC-"):
        return 20
    if upper == "RUNTIME":
        return 14
    if upper in SCENE_GATES:
        return 13
    if upper.startswith("MIX-") or upper == "SCORE":
        return 14
    if upper == "NAME":
        return 19
    return 15


def governed_class(status):
    """Engine status string -> status vocabulary entry; None when there is no status."""
    if status is None or status == "":
        return None
    upper = str(status).strip().upper()
    if upper in _PASSED or upper.endswith("_PASS") or upper.endswith("_PASSED") or upper.endswith("_DONE"):
        return "passed"
    if "WAITING" in upper or upper in {"AWAITING_HUMAN", "NEEDS_HUMAN"}:
        return "waiting"
    if upper in _RUNNING or "RUNNING" in upper:
        return "running"
    if upper in _NOT_RUN:
        return "not_run"
    if any(word in upper for word in _FAILED_WORDS):
        return "failed"
    return "unknown"


ENGINE_WORDS = {
    "passed": "passed", "waiting": "waiting for you", "running": "running",
    "not_run": "not reached", "failed": "failed", "unknown": "unknown",
}


def engine_words(status):
    """Human words for a raw engine status (PLANNED reads 'not reached'); the raw value goes in a tooltip."""
    category = governed_class(status)
    if category is None:
        return "no record"
    return ENGINE_WORDS[category] if category != "unknown" else str(status).lower().replace("_", " ")


def aggregate(contributions):
    """Worst of [(status, reason)] in STATUSES order; reasons listed worst first."""
    if not contributions:
        return "not_run", "no governed record and no evidence for this version"
    ranked = sorted(contributions, key=lambda item: STATUSES.index(item[0]))
    reasons = []
    for _, reason in ranked:
        if reason and reason not in reasons:
            reasons.append(reason)
    return ranked[0][0], "; ".join(reasons[:4])


def defect_contributions(defects):
    contributions = []
    blocking = [d for d in defects if d["level"] in ("P1", "P2", "P3")]
    minor = [d for d in defects if d["level"] == "P4"]
    unrated = [d for d in defects if d["level"] == "P?"]
    if blocking:
        levels = sorted({d["level"] for d in blocking})
        contributions.append(("failed", f"{len(blocking)} blocking defect{'s' * (len(blocking) != 1)} ({', '.join(levels)})"))
    if minor:
        contributions.append(("warnings", f"{len(minor)} P4 defect{'s' * (len(minor) != 1)}"))
    if unrated:
        contributions.append(("unknown", f"{len(unrated)} defect{'s' * (len(unrated) != 1)} without a severity"))
    return contributions


def gate_contributions(gates):
    if not gates:
        return []
    failing = [g for g in gates if not g["pass"]]
    if failing:
        names = ", ".join(g["name"] for g in failing[:3])
        more = f" +{len(failing) - 3}" if len(failing) > 3 else ""
        return [("failed", f"{len(failing)} of {len(gates)} automatic gates fail ({names}{more})")]
    return [("passed", f"{len(gates)} automatic gate{'s' * (len(gates) != 1)} pass")]


def review_contributions(reviews):
    contributions = []
    for review in reviews:
        verdict = review.get("verdict_class")
        label = review.get("label") or review.get("key")
        if verdict == "pass":
            contributions.append(("passed", f"{label}: {review.get('verdict')}"))
        elif verdict == "rework":
            contributions.append(("failed", f"{label}: {review.get('verdict')}"))
        else:
            shown = review.get("verdict") or "no verdict"
            contributions.append(("unknown", f"{label}: {shown}"))
    return contributions
