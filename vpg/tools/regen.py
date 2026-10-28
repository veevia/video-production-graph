"""Targeted regeneration planning (N22).

For every open blocking defect: take its traced node, the smallest correction and the acceptance check. Compute the
invalidation closure (the targets and their flow descendants) and record what is preserved. Snapshot the targets'
current output hashes so the gate can compare before and after. Only the targets re-execute. Descendants re-run only
if their input fingerprints changed; otherwise they are memo-skipped.
"""

from __future__ import annotations

from ..context import CheckResult, NodeResult
from ..graph import WORKFLOWS, GraphIndex, load_workflow
from ..models import EdgeKind, NodeStatus
from ..util import artifact_identity, write_json_atomic


def plan(ctx) -> NodeResult:
    wf_path = ctx.config.get("workflow")
    wf = load_workflow(WORKFLOWS[wf_path] if wf_path in WORKFLOWS else (wf_path or None))
    index = GraphIndex(wf)
    policies = wf.policies
    loopable = {e.when.split(":", 1)[1] for e in index.outgoing(ctx.node.id, EdgeKind.LOOP) if e.when.startswith("target:")}
    blocking = [d for d in ctx.state.evaluation.defects if d.status in ("open", "persisting") and d.severity in policies.blocking_severities]
    by_target: dict[str, list[str]] = {}
    unroutable = []
    for d in blocking:
        if d.traced_node in loopable:
            by_target.setdefault(d.traced_node, []).append(d.id)
        else:
            unroutable.append(d.id)
    targets = sorted(by_target, key=index.order)
    closure = set(targets)
    for t in targets:
        closure |= index.descendants(t)
    preserved = [n for n in index.topo if n not in closure and ctx.state.runtime.nodes[n].status == NodeStatus.STEP_PASS]
    baseline = {}
    for t in targets:
        for o in wf.node(t).outputs:
            recs = ctx.current(o.key)
            if recs:
                baseline[o.key] = [artifact_identity(r) for r in recs]   # #171: with the files it names
    plan_doc = {
        "cycle": ctx.state.runtime.rework_cycles + 1, "targets": targets, "defects_by_target": by_target,
        "closure": [n for n in index.topo if n in closure], "preserved": preserved, "unroutable": unroutable,
        "corrections": [{"defect": d.id, "node": d.traced_node, "severity": d.severity, "dimension": d.dimension,
                         "smallest_fix": d.smallest_fix, "acceptance_check": d.acceptance_check} for d in blocking],
        "baseline": baseline,
    }
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "regeneration-plan.json", plan_doc)
    return NodeResult(outputs={"regeneration_plan": "regeneration-plan.json"},
                      data={"targets": targets, "defects_by_target": by_target, "defect_ids": [d.id for d in blocking if d.id not in unroutable],
                            "preserved": preserved, "baseline": baseline, "unroutable": unroutable})


def check_traces(ctx) -> CheckResult:
    """GAT-TRACE: every open blocking defect names a known node, a smallest fix and an acceptance check, so N22 can
    re-run exactly the node that caused it. The interpreter supplies a missing fix under `fixes` in interpretation.json."""
    wf_path = ctx.config.get("workflow")
    wf = load_workflow(WORKFLOWS[wf_path] if wf_path in WORKFLOWS else (wf_path or None))
    ids = {n.id for n in wf.nodes}
    msgs = []
    for d in ctx.state.evaluation.defects:
        if d.status not in ("open", "persisting") or d.severity not in wf.policies.blocking_severities:
            continue
        if d.traced_node not in ids:
            msgs.append(f"{d.id} traces to unknown node {d.traced_node}")
        if not d.smallest_fix.strip():
            msgs.append(f"{d.id} has no smallest fix (add it under `fixes` in interpretation.json)")
        if not d.acceptance_check.strip():
            msgs.append(f"{d.id} has no acceptance check")
    return CheckResult(not msgs, msgs)
