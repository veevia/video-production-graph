"""Shared fixtures: a mini workflow (built at test time) and a function-driven executor, for fast, isolated engine tests."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from vpg.context import NodeResult  # noqa: E402
from vpg.executors import Executor  # noqa: E402


def node(id, num, kind="agent", **kw):
    base = {
        "id": id, "num": num, "name": id.replace("_", " "), "phase": "Test", "subgraph": "main", "kind": kind,
        "purpose": f"test node {id}", "inputs": ["state:inputs.brief"], "context": ["test"], "state_read": ["inputs.brief"],
        "state_write": [f"production.refs.{id}"], "agent": "production-orchestrator", "skills": [], "tools": [],
        "instructions": "test", "prompt": "prompts/n07_script_development.md" if kind in ("agent", "review") else None,
        "outputs": [{"key": id, "kind": "json", "description": "out", "state_path": f"production.refs.{id}"}],
        "completion": [{"type": "artifact", "key": id, "description": "exists"}], "rubric": ["script"],
        "failure_conditions": ["missing output"], "retry": {"max_attempts": 2, "behavior": "retry"},
        "escalation": {"after": "2 failures", "to": "Wei", "report": "what failed"},
        "human_review": {"required": False, "decision": "not required"}, "provenance": ["ledger"],
    }
    base.update(kw)
    return base


def edge(id, a, b, kind="flow", when="pass", transition=None, **kw):
    t = transition or ("deterministic" if when in ("pass", "*") else "conditional")
    return {"id": id, "from": a, "to": b, "kind": kind, "transition": t, "when": when, "transfers": ["x"], "threshold": "t",
            "branching": "b", "on_fail": "f", **kw}


def mini_workflow(tmp: Path, *, human_b=False, review_tool="tests.fakes:review_check", c_resources=("tts",), lock_b=(), c_kind="tool",
                  c_reads_design=False, z_gate=None):
    """a → (b ∥ c) → d → (r1 ∥ r2) → g(gate) → z → END, with g --rework--> x(regen) --target--> d | b."""
    nodes = [
        node("a_intake", 1),
        node("b_plan", 2, controls={"read": ["design"], "write": list(lock_b), "lock_on_approval": list(lock_b)},
             human_review={"required": human_b, "decision": "approve plan", "outcomes": ["approved", "rejected"]} if human_b else
             {"required": False, "decision": "not required"}, outcomes=["approved", "rejected"] if human_b else ["pass"]),
        node("c_audio", 3, kind="tool", run={"tool": "tests.fakes:produce"}, resources=list(c_resources),
             inputs=["state:inputs.brief"] + (["control:design"] if c_reads_design else []), prompt=None,
             controls={"read": ["design"] if c_reads_design else [], "write": [], "lock_on_approval": []}) if c_kind == "tool" else
        node("c_audio", 3, resources=list(c_resources), inputs=["state:inputs.brief"] + (["control:design"] if c_reads_design else []),
             controls={"read": ["design"] if c_reads_design else [], "write": [], "lock_on_approval": []}),
        node("d_build", 4, inputs=["artifact:b_plan", "artifact:c_audio", "control:design"], state_write=["production.master_ref"],
             outputs=[{"key": "d_build", "kind": "json", "description": "the build (defines the version)", "state_path": "production.master_ref"}]),
        node("r1_review", 5, kind="review", run={"tool": review_tool}, rubric=["accessibility"],
             inputs=["artifact:d_build"], state_write=["evaluation.reviews", "evaluation.defects"], outputs=[],
             completion=[{"type": "review_complete", "description": "complete"}]),
        node("r2_review", 6, kind="review", run={"tool": "tests.fakes:review_clean"}, rubric=["technical"],
             inputs=["artifact:c_audio"], state_write=["evaluation.reviews"], outputs=[],
             completion=[{"type": "review_complete", "description": "complete"}]),
        node("g_gate", 7, inputs=["reviews:current", "feedback:open", "defects:open"], gate="review_gate",
             state_write=["evaluation.defects", "evaluation.feedback", "evaluation.gate_results", "evaluation.comparisons"],
             outputs=[{"key": "interpretation", "kind": "json", "description": "interp"}],
             completion=[{"type": "feedback_interpreted", "description": "all feedback mapped"}], outcomes=["pass", "rework", "blocked"]),
        node("x_regen", 8, kind="tool", run={"tool": "regen:plan"}, gate="regeneration_plan", prompt=None, inputs=["defects:open"],
             state_write=["runtime.rework_cycles"], outputs=[{"key": "regeneration_plan", "kind": "json", "description": "plan"}],
             completion=[{"type": "artifact", "key": "regeneration_plan", "description": "plan"}]),
        node("z_done", 9, kind="tool", run={"tool": "tests.fakes:produce"}, prompt=None, gate=z_gate),
    ]
    edges = [
        edge("E1", "START", "a_intake", when="*"), edge("E2", "a_intake", "b_plan"), edge("E3", "a_intake", "c_audio"),
        edge("E4", "b_plan", "d_build", when="approved" if human_b else "pass"), edge("E5", "c_audio", "d_build"),
        edge("E6", "d_build", "r1_review"), edge("E7", "d_build", "r2_review"), edge("E8", "r1_review", "g_gate"),
        edge("E9", "r2_review", "g_gate"), edge("E10", "g_gate", "z_done", when="pass"), edge("E11", "g_gate", "x_regen", when="rework"),
        edge("E12", "g_gate", "@escalate", kind="escalate", when="blocked"),
        edge("E13", "x_regen", "d_build", kind="loop", when="target:d_build", max_traversals=5),
        edge("E14", "x_regen", "b_plan", kind="loop", when="target:b_plan", max_traversals=1),
        edge("E15", "z_done", "END"),
    ]
    if human_b:
        edges.append(edge("E16", "b_plan", "b_plan", kind="loop", when="rejected", max_traversals=1))
    wf = {"id": "mini", "name": "mini", "version": "0.0.1", "description": "test", "nodes": nodes, "edges": edges,
          "subgraphs": [{"id": "main", "name": "main", "pattern": "test", "purpose": "test", "nodes": [n["id"] for n in nodes]}],
          "policies": {"max_rework_cycles": 6, "max_same_defect_failures": 3, "parallel_limit": 4, "single_flight_resources": ["tts"],
                       "blocking_severities": ["P1", "P2", "P3"], "dry_run_actor": "dry-run-auto"}}
    outcomes = {n["id"]: n.get("outcomes", ["pass"]) for n in nodes}
    for e in edges:   # an edge is deterministic only when it fires on every outcome of its source (as the validator requires)
        single = e["from"] == "START" or len(outcomes[e["from"]]) == 1
        e["transition"] = "deterministic" if e["when"] in ("pass", "*") and single else "conditional"
    path = tmp / "mini.yaml"
    path.write_text(yaml.safe_dump(wf, sort_keys=False))
    return path


class FnExecutor(Executor):
    """Agent stand-in: handlers[node_id](ctx) -> dict of outputs (written into the workdir) or a NodeResult."""
    name = "fn"

    def __init__(self, handlers=None):
        self.handlers = handlers or {}
        self.calls: list[tuple[str, int]] = []

    def execute(self, ctx):
        self.calls.append((ctx.node.id, ctx.attempt))
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        h = self.handlers.get(ctx.node.id)
        if h is not None:
            r = h(ctx)
            if isinstance(r, NodeResult):
                return r
            return NodeResult(outputs=r or {})
        outs = {}
        for o in ctx.node.outputs:
            p = ctx.workdir / f"{o.key}.json"
            p.write_text(json.dumps({"node": ctx.node.id, "attempt": ctx.attempt}))
            outs[o.key] = p.name
        if ctx.node.gate == "review_gate":
            (ctx.workdir / "interpretation.json").write_text(json.dumps({"defects": [], "feedback": [
                {"id": f.id, "no_action_reason": "test"} for f in ctx.state.evaluation.feedback if f.status == "open"]}))
            outs["interpretation"] = "interpretation.json"
        review = None
        if ctx.node.kind.value == "review":
            dims = [d for r in ctx.node.rubric for d in ctx.rubrics[r]["dimensions"] if d["check"]["type"] == "review"]
            review = {"reviewer": "test", "scores": [{"dimension": d["id"], "score": 5, "evidence": "test"} for d in dims], "defects": []}
        return NodeResult(outputs=outs, review=review, executor=self.name)


@pytest.fixture
def project(tmp_path):
    """A project wired to the mini workflow; returns (dir, workflow_path)."""
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path)
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({"id": "t", "name": "t", "workflow": str(wf), "executor": "task"}))
    return proj, wf


def flag(proj: Path, name: str, on: bool = True) -> None:
    p = proj / "flags" / name
    p.parent.mkdir(exist_ok=True)
    if on:
        p.write_text("1")
    elif p.exists():
        p.unlink()
