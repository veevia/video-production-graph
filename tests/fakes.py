"""Deterministic fake tools for the mini workflow."""
from __future__ import annotations

import json

from vpg.context import NodeResult
from vpg.evaluation import review_bindings
from vpg.tools._common import finding


def produce(ctx):
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    marker = ctx.project_dir / "flags" / f"{ctx.node.id}.variant"
    payload = {"node": ctx.node.id, "variant": marker.read_text() if marker.exists() else "base"}
    (ctx.workdir / f"{ctx.node.id}.json").write_text(json.dumps(payload))
    return NodeResult(outputs={ctx.node.id: f"{ctx.node.id}.json"})


def review_check(ctx):
    """Reports a text-size defect traced to d_build while flags/defect exists."""
    if (ctx.project_dir / "flags" / "defect").exists():
        return NodeResult(findings=[finding("test:size", "LEG-SIZE", "P2", "label is 14px", traced="d_build", key="d_build", fix="enlarge")])
    return NodeResult()


def review_check_bound(ctx):
    res = review_check(ctx)
    current = review_bindings(ctx.node, ctx.current)
    if "d_build" in current:
        res.review = {"reviewed": {"d_build": current["d_build"][:16]}}
    else:
        res.review = {"reviewed": {k: v[:16] for k, v in current.items()}}
    return res


def review_routes(ctx):
    """flags/defect: a defect traced to d_build, which rework reaches. flags/defect_audio: one traced to c_audio, which
    no rework loop reaches (like product truth), so only a human's fix outside rework can clear it. flags/defect_plan:
    one traced to b_plan, which owns a document an approval may lock."""
    flags, out = ctx.project_dir / "flags", []
    if (flags / "defect").exists():
        out.append(finding("test:size", "LEG-SIZE", "P2", "label is 14px", traced="d_build", key="d_build", fix="enlarge"))
    if (flags / "defect_audio").exists():
        out.append(finding("test:contrast", "LEG-CONTRAST", "P2", "caption plate 2.1:1", traced="c_audio", key="c_audio", fix="darken"))
    if (flags / "defect_plan").exists():   # flags/retrace_plan: the reviewer traces the same problem to d_build instead
        trace = "d_build" if (flags / "retrace_plan").exists() else "b_plan"
        out.append(finding("test:safe", "LEG-SAFE", "P2", "title leaves the safe area", traced=trace, key=trace, fix="inset"))
    return NodeResult(findings=out)


def review_clean(ctx):
    return NodeResult()


def kill_process(ctx):
    """A completion check that takes the process down while the node is judged (not an Exception, so nothing catches it)."""
    raise SystemExit("killed while judging")


def review_rounds(ctx):
    """flags/rounds.json: one list of [check, severity] per version, the last repeated; each check is a separate problem
    traced to d_build. Scripts a run that converges or stalls, version by version (v1.6 budgets)."""
    path = ctx.project_dir / "flags" / "rounds.json"
    rounds = json.loads(path.read_text()) if path.exists() else [[]]
    found = rounds[min(max(ctx.state.runtime.current_version, 1), len(rounds)) - 1]
    return NodeResult(findings=[finding(f"test:{check}", "LEG-SIZE", severity, f"problem {check}", traced="d_build",
                                        key="d_build", fix=f"fix {check}") for check, severity in found])
