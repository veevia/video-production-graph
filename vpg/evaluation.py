"""Evaluation: rubrics, review merging, defects, the review gate and before/after comparison.

Precedence rule: a deterministic tool finding on a MUST dimension cannot be overridden by an agent's score.
If the agent scored that dimension as passing, the pass is recorded as overridden and the dimension fails.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from .models import Comparison, Defect, Finding, GateResult, NodeDef, Policies, ProjectState, Review, Score
from .util import PKG_ROOT, now, sha256_json

BRIEF_DIMENSIONS = [
    "product_accuracy", "narrative_clarity", "audience_relevance", "visual_coherence", "design_system_compliance",
    "composition_quality", "asset_specificity", "legibility", "accessibility", "motion_quality", "voice_quality",
    "audio_quality", "pacing", "transition_quality", "technical_correctness", "rendering_quality",
    "cross_scene_consistency", "delivery_completeness",
]


def load_rubrics(root: Path = PKG_ROOT) -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted((root / "rubrics").glob("*.yaml")):
        data = yaml.safe_load(p.read_text())
        out[data["id"]] = data
    return out


def dimensions_for(node: NodeDef, rubrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    dims = []
    for rid in node.rubric:
        dims.extend(rubrics[rid]["dimensions"])
    return dims


SEVERITY_ORDER = ("P1", "P2", "P3", "nit")   # most severe first
UNRESOLVED = ("open", "persisting", "in_rework")
_STATUS_RANK = {"in_rework": 0, "persisting": 0, "open": 1, "resolved": 2, "waived": 3}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def signature(dimension: str, traced_node: str | None, check: str) -> str:
    """A defect's stable name, fixed when it is first recorded; it keys the defect's failure count. Only [a-z0-9-]
    appear in the check part, so '|' and '~' are safe separators."""
    return f"{dimension}|{traced_node or '?'}|{_slug(check)}"


def problem_key(dimension: str, check: str) -> tuple[str, str]:
    """What a finding is about, without its trace: the dimension and the check that found it."""
    return dimension, _slug(check)


def defect_key(d: Defect) -> tuple[str, str]:
    return d.dimension, d.signature.split("|", 2)[-1].split("~", 1)[0]   # the check it was first found by


def _pick(cands: list[Defect], failures: dict[str, int]) -> Defect:
    """Of several candidate defects, the one whose history must carry: in rework or persisting first, then the one
    with the most failed corrections (so no count is lost), then the oldest."""
    return min(cands, key=lambda d: (_STATUS_RANK.get(d.status, 9), -failures.get(d.signature, 0),
                                     int(d.id[1:]) if d.id[1:].isdigit() else 0, d.id))


def review_bindings(node: NodeDef, current: Callable[[str], list]) -> dict[str, str]:
    """The artifact inputs a review judges, each as one sha256: the file's own, or, for several files (per-item
    artifacts), the sha256 of their sorted hashes. A review echoes these as `reviewed` (v1.7, #62/#67), so the record
    shows which build it judged, and the engine refuses a review of a build that isn't the version's."""
    out: dict[str, str] = {}
    for ref in node.inputs:
        kind, _, name = ref.partition(":")
        if kind != "artifact":
            continue
        shas = sorted(a.sha256 for a in current(name))
        if shas:
            out[name] = shas[0] if len(shas) == 1 else sha256_json(shas)
    return out


def binding_problems(bound: dict[str, str], reviewed: Any, require: bool) -> list[str]:
    """Why a review's `reviewed` echo doesn't bind it to `bound`, or [] when it does. A digest may be shortened to a
    prefix of at least 12 hex characters. A mismatch is always refused; a missing echo only when the project requires it."""
    if reviewed in (None, {}):
        if not (require and bound):
            return []
        echo = json.dumps({k: v[:16] for k, v in bound.items()})
        return [f'the review must echo the build it judged: "reviewed": {echo} (vpg.project.yaml reviews.binding: require)']
    if not isinstance(reviewed, dict):
        return ['"reviewed" must map each judged input to its sha256, e.g. {"master": "<sha256 prefix>"}']
    out = []
    for key, sha in reviewed.items():
        s = str(sha).strip().lower()
        if key not in bound:
            out.append(f"reviewed.{key}: this review node doesn't judge '{key}' (it judges {', '.join(bound) or 'no artifacts'})")
        elif not re.fullmatch(r"[0-9a-f]{12,64}", s):
            out.append(f"reviewed.{key}: give the sha256 (at least 12 hex characters), not {sha!r}")
        elif not bound[key].startswith(s):
            out.append(f"reviewed.{key} is {s[:16]}, but this version's {key} is {bound[key][:16]}: the review judged a "
                       "different build; judge the registered one")
    if require:
        missing = [k for k in bound if k not in reviewed]
        if missing:
            out.append(f"the review doesn't echo {', '.join(missing)} (reviews.binding: require)")
    return out


def merge_review(node: NodeDef, version: int, pre_findings: list[Finding], agent_review: dict[str, Any] | None,
                 rubrics: dict[str, dict[str, Any]]) -> tuple[Review, list[Finding], list[str]]:
    """Merge tool findings and an agent's rubric scores into one review record.

    Returns the review, all findings (tool plus agent-reported defects) and the MUST dimensions the agent left
    unscored. The reviewer must check the whole rubric or say what it skipped.
    """
    dims = dimensions_for(node, rubrics)
    agent_review = agent_review or {}
    agent_scores = {s["dimension"]: s for s in agent_review.get("scores", [])}
    failing_tool_dims = {f.dimension for f in pre_findings if f.level == "MUST" and f.severity != "nit"}
    scores, overridden, unscored, pending_human = [], [], [], []
    for d in dims:
        level = d.get("level", "MUST")
        if d["check"]["type"] == "human":
            pending_human.append(d["id"])   # never scored by an agent: surfaced for the human release decision
            continue
        if d["check"]["type"] == "tool":
            passed = d["id"] not in failing_tool_dims and not any(f.dimension == d["id"] for f in pre_findings if f.severity != "nit")
            s = agent_scores.get(d["id"])
            if not passed and s is not None and float(s["score"]) >= float(d.get("threshold") or 4):
                overridden.append(d["id"])   # the agent tried to pass a dimension the tool failed: recorded, not honoured
            scores.append(Score(dimension=d["id"], score=1.0 if passed else 0.0, threshold=1.0, level=level, passed=passed,
                                evidence="deterministic tool check"))
            continue
        s = agent_scores.get(d["id"])
        if s is None:
            if level == "MUST":
                unscored.append(d["id"])
            continue
        threshold = float(d.get("threshold", 4))
        passed = float(s["score"]) >= threshold
        if passed and d["id"] in failing_tool_dims:
            overridden.append(d["id"])
            passed = False
        scores.append(Score(dimension=d["id"], score=float(s["score"]), threshold=threshold, level=level, passed=passed,
                            evidence=str(s.get("evidence", ""))))
    agent_findings = [Finding(check=f"review:{node.id}", dimension=x["dimension"], severity=x["severity"],
                              message=x["description"], evidence=x.get("evidence", ""), traced_node=x.get("traced_node"),
                              artifact_key=x.get("artifact_key"), smallest_fix=x.get("smallest_fix", ""),
                              level=x.get("level", "MUST"), element=str(x["element"]) if x.get("element") else None)
                      for x in agent_review.get("defects", [])]
    findings = list(pre_findings) + agent_findings
    failed_must = {s.dimension for s in scores if not s.passed and s.level == "MUST"}
    # A failed MUST dimension is never a nit, whatever severity the reviewer filed: it must block and route.
    findings = [f.model_copy(update={"severity": "P3"}) if f.dimension in failed_must and f.severity == "nit" else f for f in findings]
    review = Review(id=f"rev-{node.id}-v{version}", node=node.id, version=version, reviewer=node.agent,
                    rubric_ids=node.rubric, scores=scores, tool_findings=list(pre_findings), verdict=review_verdict(scores, findings),
                    overridden_agent_passes=overridden, pending_human=pending_human, created_at=now())
    return review, findings, unscored


def review_verdict(scores: list[Score], findings: list[Finding]) -> str:
    """A review fails on a blocking finding or a failed MUST score. The engine re-judges it once the rubric's levels have
    governed the findings (#162), so the verdict agrees with what the gate can route."""
    return "fail" if any(f.severity != "nit" for f in findings) or any(not s.passed and s.level == "MUST" for s in scores) else "pass"


def record_findings(state: ProjectState, findings: list[Finding], source: str, version: int, *, default_trace: str,
                    log: list[dict[str, Any]] | None = None) -> list[str]:
    """Turn findings into defects; return, per finding, the id of the defect it belongs to.

    Identity is the problem, not the trace: a finding belongs to a defect of the same dimension and check. Findings of
    one batch about the same problem on the same trace share one defect, and every description is kept, so separate
    items are never lost (`Finding.element` names them; the defect lists them in `elements`). Matching, in order:
      1. an unresolved defect of the problem on the finding's trace;
      2. an unresolved defect of the problem on another trace, which follows the new trace and keeps its failure
         count: re-tracing a persisting problem never counts as fixing it (`defect_retraced`);
      3. a resolved or waived defect of the problem on the finding's trace: a regression reopens it;
      4. otherwise a new defect. A waiver covers what was seen, so a waived defect never absorbs a later finding.
    Unresolved defects are always taken before a resolved twin is reopened, so a problem that moves between traces
    carries one failure count instead of reopening twins at zero.

    A re-seen defect moves: in rework → persisting (the correction failed; its count rises), resolved → open. Its
    severity is the latest review's (the most severe finding of the batch), so an escalation is never lost and the
    gate agrees with the review; the latest smallest fix replaces the earlier one. `log` receives re-trace and
    severity-change events for the ledger.

    An untraced finding is traced to `default_trace` (the graph's feedback-interpretation gate), which must map it."""
    log = log if log is not None else []
    defects = state.evaluation.defects
    failures = state.runtime.defect_failures
    earlier = list(defects)
    rows = [(f, f.traced_node or default_trace, problem_key(f.dimension, f.check)) for f in findings]
    target: list[Defect | None] = [None] * len(rows)
    placed: dict[tuple[tuple[str, str], str], Defect] = {}   # (problem, trace) → its defect in this batch

    def claimed(d: Defect) -> bool:
        return any(x is d for x in placed.values())

    def match(i: int, statuses: tuple[str, ...], same_trace: bool) -> Defect | None:
        f, trace, key = rows[i]
        cands = [d for d in earlier if d.status in statuses and defect_key(d) == key
                 and (d.traced_node == trace) == same_trace and not claimed(d)]
        return _pick(cands, failures) if cands else None

    for statuses, same_trace in ((UNRESOLVED, True), (UNRESOLVED, False), (("resolved",), True)):
        for i, (f, trace, key) in enumerate(rows):
            if target[i] is not None:
                continue
            d = placed.get((key, trace)) or match(i, statuses, same_trace)
            if d is None:
                continue
            if d.traced_node != trace:
                log.append({"event": "defect_retraced", "id": d.id, "from": d.traced_node, "to": trace, "by": source})
                d.traced_node = trace
            target[i] = placed[(key, trace)] = d
    for i, (f, trace, key) in enumerate(rows):
        if target[i] is not None or not str(f.check).startswith("tool:"):
            continue
        d = placed.get((key, trace)) or match(i, ("waived",), True)
        if d is not None:
            target[i] = placed[(key, trace)] = d
    created: set[str] = set()
    for i, (f, trace, key) in enumerate(rows):
        if target[i] is None:
            d = placed.get((key, trace))
            if d is None:
                did = f"D{max((int(x.id[1:]) for x in defects if x.id[1:].isdigit()), default=0) + 1:03d}"
                sig = signature(f.dimension, trace, f.check)
                if any(x.signature == sig for x in defects):   # a re-traced defect kept this name: stay unique
                    sig = f"{sig}~{did.lower()}"
                d = Defect(id=did, signature=sig, severity=f.severity, dimension=f.dimension, description=f.message,
                           evidence=f.evidence, source=source, traced_node=trace, artifact_key=f.artifact_key,
                           smallest_fix=f.smallest_fix, acceptance_check=f"re-run {f.check} and the finding is absent",
                           first_seen_version=version, last_seen_version=version)
                defects.append(d)
                placed[(key, trace)] = d
                created.add(d.id)
            target[i] = d
    groups: dict[str, list[Finding]] = {}
    for (f, _, _), d in zip(rows, target):
        groups.setdefault(d.id, []).append(f)
    by_id = {d.id: d for d in defects}
    for did, fs in groups.items():
        d = by_id[did]
        messages = list(dict.fromkeys(f.message for f in fs))
        d.description = messages[0] if len(messages) == 1 else " | ".join(messages)
        evidence = list(dict.fromkeys(f.evidence for f in fs if f.evidence))
        if evidence:
            d.evidence = " | ".join(evidence)
        fixes = list(dict.fromkeys(f.smallest_fix for f in fs if f.smallest_fix))
        if fixes:
            d.smallest_fix = " | ".join(fixes)
        d.elements = sorted({f.element for f in fs if f.element})
        severity = min((f.severity for f in fs), key=SEVERITY_ORDER.index)
        if severity != d.severity:
            if did not in created:
                log.append({"event": "defect_severity_changed", "id": d.id, "from": d.severity, "to": severity, "by": source})
            d.severity = severity
        if did in created:
            continue
        if d.status == "in_rework":
            d.status = "persisting"
            d.rework_count += 1
            failures[d.signature] = failures.get(d.signature, 0) + 1
        elif d.status in ("resolved", "waived"):
            d.status = "open"   # regression: the fix did not hold
        d.last_seen_version = version
    return [d.id for d in target]


def compare_after_rework(state: ProjectState, version: int, artifacts_changed: dict[str, list[str]],
                         resolved_by_interpreter: set[str] | None = None) -> list[Comparison]:
    """Decide, for every unresolved defect, whether fresh evidence shows it fixed. Resolution needs fresh evidence.

    A review-sourced defect is resolved when an independent reviewer that found it re-ran on a newer version than the one that
    last reported it (not carried forward) and no longer reports it. Defects are matched by problem, so a re-traced
    problem is still reported. A defect from human feedback alone is resolved only when the interpreter lists it as
    resolved, on a version after the one it was last seen on. This holds whether the fix came from a rework cycle or
    from outside one (an approved change request, a rollback), so a fixed defect never holds the gate.

    A defect that was in rework and shows no such evidence failed its correction: it persists and its failure count
    rises, which escalates after `max_same_defect_failures`, so a fix that changed nothing can never pass the gate. A
    defect no correction was attempted on keeps its status until fresh evidence resolves it.
    """
    out = []
    resolved_by_interpreter = resolved_by_interpreter or set()
    for d in state.evaluation.defects:
        if d.status not in UNRESOLVED:
            continue
        reworked = d.status == "in_rework"
        resolved, why = False, ""
        if d.source.startswith("rev-"):
            node = d.source[4:].rsplit("-v", 1)[0]
            source_review = next((r for r in state.evaluation.reviews if r.id == d.source), None)
            fresh = [r for r in state.evaluation.reviews if r.node == node and r.version > d.last_seen_version
                     and not r.carried_forward and (source_review is not None and source_review.independent is True and r.independent is not False
                                                    or not (source_review is not None and source_review.independent is True))]
            if fresh and d.id not in fresh[-1].defect_ids:
                resolved, why = True, f"{node} re-ran on version {fresh[-1].version} and no longer reports it"
            else:
                why = f"{node} has no fresh review since version {d.last_seen_version} (the correction changed nothing it checks)"
        elif d.id in resolved_by_interpreter and d.last_seen_version < version:
            resolved, why = True, "the interpreter confirmed the human feedback is addressed"
        else:
            why = "no evidence that the feedback is addressed"
        if not (resolved or reworked):
            continue   # no correction was attempted and nothing shows it fixed: it stays as it is
        if resolved:
            d.status, d.needs_human, d.human_hold = "resolved", False, ""
            if not reworked:
                why += "; it was fixed outside a rework cycle (e.g. by an approved change request)"
        else:
            d.status = "persisting"
            d.rework_count += 1
            state.runtime.defect_failures[d.signature] = state.runtime.defect_failures.get(d.signature, 0) + 1
        out.append(Comparison(rework_cycle=state.runtime.rework_cycles, defect_id=d.id, before_version=d.last_seen_version,
                              after_version=version, resolved=resolved, before=d.description, after=why, artifacts_changed=artifacts_changed))
    for d in state.evaluation.defects:
        if d.status == "persisting" and d.last_seen_version == version and not any(c.defect_id == d.id and c.after_version == version for c in out):
            out.append(Comparison(rework_cycle=state.runtime.rework_cycles, defect_id=d.id, before_version=d.first_seen_version,
                                  after_version=version, resolved=False, before=d.description,
                                  after="finding still present after the correction", artifacts_changed=artifacts_changed))
    state.evaluation.comparisons.extend(out)
    return out


def gate(state: ProjectState, policies: Policies, version: int, reviewer_nodes: list[str], node_id: str,
         extend: Callable[[], str | None] | None = None) -> GateResult:
    """The review gate's verdict for `version`. `extend` is asked only when the rework-cycle budget is the one thing
    stopping rework (never past a defect that persisted through `max_same_defect_failures` corrections, a hold or a
    missing review): it returns the reason it granted more cycles, or None to block as before (budget.py)."""
    reasons: list[str] = []
    granted: str | None = None
    current = {r.node: r for r in state.evaluation.reviews if r.version == version}
    missing = [n for n in reviewer_nodes if n not in current]
    blocking = [d for d in state.evaluation.defects   # in_rework is unresolved until fresh evidence resolves it
                if d.status in ("open", "persisting", "in_rework") and d.severity in policies.blocking_severities]
    outcome = "pass"
    if missing:
        reasons.append(f"missing reviews for version {version}: {missing}")
        outcome = "blocked"
    elif any(d.needs_human for d in blocking):
        outcome = "blocked"
        reasons.append("a blocking defect needs a human decision: " + ", ".join(
            d.id + (f" (on hold: {d.human_hold}; `vpg unblock` the gate to let rework route it)" if d.human_hold else "")
            for d in blocking if d.needs_human))
    elif any(state.runtime.defect_failures.get(d.signature, 0) >= policies.max_same_defect_failures for d in blocking):
        outcome = "blocked"
        reasons.append(f"a defect persisted through {policies.max_same_defect_failures} corrections; stop and report instead of looping")
    elif blocking and state.runtime.rework_cycles >= policies.max_rework_cycles and not (extend and (granted := extend())):
        outcome = "blocked"
        reasons.append(f"rework budget of {policies.max_rework_cycles} cycles is spent")
    elif blocking:
        outcome = "rework"
        if granted:
            reasons.append(granted)
        reasons.append("blocking defects open: " + ", ".join(f"{d.id} {d.severity} {d.dimension}→{d.traced_node}" for d in blocking))
    else:
        principal = (((state.runtime.accepted_policy or {}).get("autonomy") or {}).get("principal") or "Wei")
        resolved = {d.id for d in state.evaluation.defects if d.status == "resolved"
                    and (d.severity == "nit" or d.resolved_by == principal)}
        defects = {d.id: d for d in state.evaluation.defects}
        failing = [r.node for r in current.values() if r.verdict == "fail"
                   and (not r.defect_ids or any(did in defects and defects[did].severity in policies.blocking_severities
                                                and did not in resolved for did in r.defect_ids))]
        held = [f for f in state.evaluation.feedback if f.hold]
        if failing:
            # Never pass a version a current review failed. With no blocking defect there is nothing to route to
            # N22, so rework would stall; a human decides instead.
            outcome = "blocked"
            reasons.append(f"reviews failed with no blocking defect to route: {failing}; a human must decide")
        elif held:
            # Never pass a version while a human's feedback was dismissed without that human's word, or while an agent's
            # note waits for a human's triage (#14).
            outcome = "blocked"
            notes = [f.id for f in held if f.triage == "pending"]
            if notes:
                reasons.append(f"an agent's note awaits a human's triage ({', '.join(notes)}: `vpg feedback --triage <id> "
                               "--accept|--dismiss`), so the version cannot pass")
            if len(notes) < len(held):
                reasons.append("a human's feedback leads to no open blocking defect, so the version cannot pass")
        else:
            reasons.append(f"all {len(reviewer_nodes)} reviews pass for version {version}; no blocking defects")
    holds = ([f"{d.id} ({d.human_hold})" for d in state.evaluation.defects if d.human_hold and d.status in UNRESOLVED]
             + [f"{f.id} ({f.hold})" for f in state.evaluation.feedback if f.hold])
    if holds:   # every gate result names every hold, so an unblock never releases one its human was not shown
        reasons.append("on hold for a named human: " + "; ".join(holds) + ". Unblocking the gate accepts them (a feedback "
                       "hold only when the unblocking human is the feedback's author or the gate's owner; an agent's note "
                       "only through its triage)")
    return GateResult(id=f"gate-v{version}-{len(state.evaluation.gate_results) + 1}", node=node_id, version=version,
                      outcome=outcome, reasons=reasons, open_blocking=[d.id for d in blocking], created_at=now())
