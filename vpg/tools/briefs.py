"""Checks for briefs, product truth, concepts, creative decisions and control-document completeness (N03–N06, N09)."""

from __future__ import annotations

import re

from ..context import CheckResult
from ..util import read_text
from ..controls import missing_sections, placeholder_sections, split_front_matter

VAGUE = {"better", "engaging", "compelling", "great", "nice", "clean", "modern", "awesome", "impactful", "amazing", "good"}


def doc_complete(ctx, docs: list[str], front_matter: dict[str, list[str]] | None = None) -> CheckResult:
    msgs = []
    for doc in docs:
        p = ctx.control_path(doc)
        if not p.exists():
            msgs.append(f"{doc}.md is missing")
            continue
        text = read_text(p)
        miss = missing_sections(doc, text)
        if miss:
            msgs.append(f"{doc}.md is missing sections: {miss}")
        ph = placeholder_sections(doc, text)
        if ph:
            msgs.append(f"{doc}.md still has placeholders or empty sections: {ph}")
        fm, _ = split_front_matter(text)
        for key in (front_matter or {}).get(doc, []):
            if not fm.get(key):
                msgs.append(f"{doc}.md front-matter needs `{key}` (tools read it)")
    return CheckResult(not msgs, msgs)


def check_brief(ctx) -> CheckResult:
    b = ctx.load("brief") or {}
    msgs = []
    obj = b.get("objective", "")
    words = set(re.findall(r"[a-z]+", obj.lower()))
    if not obj:
        msgs.append("objective is empty")
    elif words and len(words - VAGUE - {"the", "a", "more", "to", "make", "it", "and"}) < 4:
        msgs.append("objective is an adjective, not an observable target (goal-harness: never 'make it better')")
    if not b.get("audience"):
        msgs.append("audience is empty")
    crit = b.get("success_criteria", [])
    if len(crit) < 3:
        msgs.append("need ≥3 success criteria")
    vague = [c for c in crit if not re.search(r"\d|viewer can|must|never|always|within|≤|≥", c.lower())]
    if vague:
        msgs.append(f"success criteria must be checkable (a number, a must/never, or 'viewer can …'): {vague}")
    t, m = b.get("runtime_target_s", 0), b.get("runtime_max_s", 0)
    if not (0 < t <= m):
        msgs.append("runtime_target_s must be > 0 and ≤ runtime_max_s")
    return CheckResult(not msgs, msgs)


def check_concepts(ctx) -> CheckResult:
    c = (ctx.load("concepts") or {}).get("concepts", [])
    msgs = []
    if len(c) < 3:
        msgs.append("need ≥3 concept directions (diverse anchors defeat overfitting to one idea)")
    anchors = {x.get("anchor", "").strip().lower() for x in c}
    if len(anchors) < min(3, len(c)):
        msgs.append("concept anchors are not distinct")
    for x in c:
        if not x.get("risks") or not x.get("structure"):
            msgs.append(f"concept {x.get('id')} needs a structure and named risks")
    return CheckResult(not msgs, msgs)


def check_decision(ctx) -> CheckResult:
    d = ctx.load("creative_decision") or {}
    concepts = {x["id"] for x in (ctx.load("concepts") or {}).get("concepts", [])}
    msgs = []
    if d.get("selected") not in concepts:
        msgs.append(f"selected concept {d.get('selected')} is not one of {sorted(concepts)}")
    rejected = {r.get("id") for r in d.get("rejected", []) if r.get("reason")}
    if concepts and rejected != concepts - {d.get("selected")}:
        msgs.append("every non-selected concept needs a recorded rejection reason")
    if not d.get("rationale"):
        msgs.append("rationale is empty")
    return CheckResult(not msgs, msgs)
