"""Post-production learning (N25): build the governed learning proposal and check the learning agent's lessons.

The node never edits knowledge/rules.json. It writes a proposal that only a named human can apply
(`vpg memory apply`). The project's own memory.md is the one control document this node may update.
"""

from __future__ import annotations

from pathlib import Path

from ..context import CheckResult, NodeResult
from ..memory import DEFAULT_RULES, propose_from_run
from ..util import write_json_atomic


def propose(ctx) -> NodeResult:
    rules = Path(ctx.state.knowledge.rules_path) if ctx.state.knowledge.rules_path else DEFAULT_RULES
    proposal = propose_from_run(ctx.state, rules)
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "learning-proposal.json", proposal)
    return NodeResult(outputs={"learning_proposal": "learning-proposal.json"},
                      data={"observations": len(proposal["observations"]), "candidates": len(proposal["candidates"]),
                            "project_specific": len(proposal["project_specific"])})


def check(ctx) -> CheckResult:
    lessons = ctx.load("lessons") or {}
    msgs = []
    items = lessons.get("lessons", [])
    if not items:
        msgs.append("no lessons recorded (a production always teaches something, even 'no change')")
    for x in items:
        if x.get("class") not in ("reusable", "project_specific"):
            msgs.append(f"lesson '{x.get('text', '')[:40]}' must be classed reusable or project_specific")
        if not x.get("evidence"):
            msgs.append(f"lesson '{x.get('text', '')[:40]}' needs evidence (defect, feedback or comparison ids)")
        if x.get("class") == "reusable" and x.get("scope") in ("product_truth", "objective", "brand_constraints", "approved_design_principles"):
            msgs.append("a lesson may not target a protected scope; raise a change request with the project owner instead")
    return CheckResult(not msgs, msgs)
