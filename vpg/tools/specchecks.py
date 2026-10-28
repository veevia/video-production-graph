"""Deterministic checks of a production against its spec family (vpg/spec.py).

Each check reads a spec document through the control store (`ctx.fm("spec-<doc>")`, the family's control ids in
vpg/spec.py), so a node that runs it declares the document in `controls.read` and its fingerprint covers it. Findings
cite the spec's rule ids, never a free-text rule.
"""

from __future__ import annotations

import re
from typing import Any

from ..context import CheckResult
from ._common import finding


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower().replace("’", "'")).strip() + " "


def _says(text: str, term: str) -> bool:
    """`term` as whole words in normalised `text`, its last word also as a plural (sub-agent counts "sub-agents"; a
    hyphen, a space, an apostrophe and case are the same, so a possessive counts too)."""
    words = _norm(term).split()
    if not words:
        return False
    return re.search(" " + " ".join(re.escape(w) for w in words) + r"(?:s|es)? ", text) is not None


def term_report(must_say: list[str], banned: list[str], spoken: str, shown: str = "") -> dict[str, Any]:
    """Which must-say terms are spoken, and which banned phrases are spoken or shown (whole words, case-free, plurals and
    possessives of the last word included)."""
    said, seen = _norm(spoken), _norm(spoken + " " + shown)
    terms = [t for t in must_say if isinstance(t, str) and t.strip()]
    covered = [t for t in terms if _says(said, t)]
    hits = [b for b in banned if isinstance(b, str) and b.strip() and _says(seen, b)]
    return {"covered": covered, "missing": [t for t in terms if t not in covered], "banned_hits": hits,
            "share": round(len(covered) / len(terms), 4) if terms else 1.0}


def _spoken(script: dict[str, Any] | None) -> str:
    return " ".join(u.get("text", "") for ch in (script or {}).get("chapters", []) for u in ch.get("units", []))


def _shown(ctx) -> str:
    texts = [layer.get("text", "") for layer in (ctx.load("text_layers") or {}).get("layers", [])]
    texts += [t for b in (ctx.load("storyboard") or {}).get("beats", []) for t in (b.get("on_screen_text") or [])]
    return " ".join(t for t in texts if isinstance(t, str))


def story_terms(ctx) -> CheckResult:
    """ST-TERMS / ST-BAN: every must-say term in STORY.md is spoken; no banned phrase is spoken or shown."""
    story = ctx.fm("spec-story")
    script = ctx.load("script")
    if not story:
        return CheckResult(False, ["STORY.md is missing or has no front matter; the spec family must define it"])
    if script is None:
        return CheckResult(False, ["no script to check the story terms against"])
    rep = term_report(story.get("must_say") or [], story.get("banned_phrases") or [], _spoken(script), _shown(ctx))
    rule_of = {r.get("id"): r for r in story.get("rules") or [] if isinstance(r, dict)}
    terms_rule = next((rid for rid in rule_of if rid.startswith("ST-TERMS")), "ST-TERMS-001")
    ban_rule = next((rid for rid in rule_of if rid.startswith("ST-BAN")), "ST-BAN-001")
    findings = []
    for t in rep["missing"]:
        findings.append(finding("specchecks:story_terms", "SPEC-STORY", "P2", f"{terms_rule}: the must-say term '{t}' is "
                                f"never spoken", evidence=f"covered {len(rep['covered'])} of {len(rep['covered']) + len(rep['missing'])}",
                                traced="n07_script_development", key="script", fix=f"say '{t}' in the unit that carries it"))
    for b in rep["banned_hits"]:
        findings.append(finding("specchecks:story_terms", "SPEC-STORY", "P2", f"{ban_rule}: the banned phrase '{b}' is "
                                "spoken or shown", traced="n07_script_development", key="script",
                                fix=f"replace '{b}' with what the product actually does"))
    msgs = [f.message for f in findings]
    return CheckResult(not findings, msgs, findings=findings, metrics={"term_share": rep["share"], **rep})
