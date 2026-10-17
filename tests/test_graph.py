"""The shipped graph, its documentation and its bindings are complete, consistent and free of placeholders."""
from __future__ import annotations

import copy
import glob
import re
from pathlib import Path

import pytest
import yaml

from tests.conftest import PKG
from vpg.docs import generate
from vpg.evaluation import BRIEF_DIMENSIONS, load_rubrics
from vpg.executors import PROMPT_VARS
from vpg.graph import REQUIRED_STAGES, GraphIndex, load_workflow, validate
from vpg.models import EdgeKind, Workflow

WF = load_workflow()
NODE_FIELDS = ["id", "name", "purpose", "inputs", "context", "state_read", "state_write", "agent", "skills", "tools", "instructions",
               "outputs", "completion", "rubric", "failure_conditions", "retry", "escalation", "human_review", "provenance"]


def test_shipped_graph_is_valid():
    assert validate(WF) == []


def test_graph_has_25_brief_stages_in_order():
    assert [n.name for n in sorted(WF.nodes, key=lambda n: n.num)] == REQUIRED_STAGES


def test_every_node_has_19_fields():
    idx = GraphIndex(WF)
    for n in WF.nodes:
        for f in NODE_FIELDS:
            assert getattr(n, f) not in (None, "", []) or f in ("skills", "tools"), f"{n.id}.{f}"
        assert idx.incoming(n.id) and idx.outgoing(n.id), n.id          # upstream and downstream are derived from edges


def test_edges_have_7_fields_and_both_transition_types():
    for e in WF.edges:
        assert e.source and e.target and e.when and e.transfers and e.threshold and e.branching and e.on_fail, e.id
    kinds = {e.transition for e in WF.edges}
    assert kinds == {"deterministic", "conditional"}
    assert {e.kind for e in WF.edges} == {EdgeKind.FLOW, EdgeKind.LOOP, EdgeKind.ESCALATE}


def _mutated(fn):
    data = copy.deepcopy(yaml.safe_load((PKG / "graph/workflow.yaml").read_text()))
    fn(data)
    return Workflow.model_validate(data)


def test_validator_rejects_unbounded_loop():
    def f(d):
        next(e for e in d["edges"] if e["id"] == "E44").pop("max_traversals")
    assert any("max_traversals" in e for e in validate(_mutated(f)))


def test_validator_rejects_join_deadlock():
    def f(d):
        d["edges"] = [e for e in d["edges"] if e["id"] != "E16"]   # n09 'rejected' would leave the n08 join waiting forever
    assert any("waiting forever" in e for e in validate(_mutated(f)))


def test_validator_rejects_empty_field():
    def f(d):
        d["nodes"][6]["failure_conditions"] = []
    assert any("empty failure_conditions" in e for e in validate(_mutated(f)))


def test_validator_rejects_cycle_in_flow():
    def f(d):
        d["edges"].append({"id": "EX", "from": "n14_video_assembly", "to": "n13_motion_scenes", "kind": "flow", "transition": "deterministic",
                           "when": "pass", "transfers": ["x"], "threshold": "t", "branching": "b", "on_fail": "f"})
    assert any("cycle" in e for e in validate(_mutated(f)))


def test_validator_rejects_untyped_state_path():
    def f(d):
        d["nodes"][6]["state_write"] = ["production.not_a_field"]
    assert any("not in the typed state model" in e for e in validate(_mutated(f)))


def test_validator_rejects_unknown_or_missing_gate_hook():
    def typo(d):
        next(n for n in d["nodes"] if n["id"] == "n21_feedback_interpretation")["gate"] = "review-gate"
    errs = validate(_mutated(typo))
    assert any("unknown gate hook 'review-gate'" in e for e in errs)
    assert any("gate hook 'review_gate' must be on exactly one node (found 0)" in e for e in errs)


def _flow(eid, a, b, when="pass", transition="deterministic"):
    return {"id": eid, "from": a, "to": b, "kind": "flow", "transition": transition, "when": when, "transfers": ["x"],
            "threshold": "t", "branching": "b", "on_fail": "f"}


def test_validator_rejects_a_conditional_branch_one_hop_above_a_join():
    """Critic variant V3c: an N06 outcome that feeds N07 but not N09 strands the N08 join."""
    def f(d):
        next(n for n in d["nodes"] if n["id"] == "n06_creative_direction")["outcomes"] = ["approved", "rejected", "script_only"]
        next(e for e in d["edges"] if e["id"] == "E11")["when"] = "approved|script_only"
    assert any("script_only" in e and "waiting forever" in e for e in validate(_mutated(f)))


def test_validator_treats_end_as_a_join():
    """Critic variants V4 and V5: a rework-only node feeding END or N25 would strand the run on every pass path."""
    for target in ("END", "n25_learning_memory"):
        def f(d, target=target):
            d["edges"].append(_flow("EX", "n22_targeted_regeneration", target))
        assert any("waiting forever" in e for e in validate(_mutated(f))), target


def test_validator_rejects_target_edges_as_outcome_cover_outside_regeneration():
    def f(d):
        e = next(e for e in d["edges"] if e["id"] == "E16")          # n09's 'rejected' loop
        e["when"] = "target:n09_visual_system"
    assert any("n09_visual_system: outcome 'rejected' has no outgoing edge" in e for e in validate(_mutated(f)))


def test_validator_rejects_bad_bounds_severities_and_duplicate_flow_edges():
    def bound(d):
        next(e for e in d["edges"] if e["id"] == "E44")["max_traversals"] = -1
    def severities(d):
        d["policies"]["blocking_severities"] = ["P2", "P3"]
    def duplicate(d):
        e = dict(next(e for e in d["edges"] if e["id"] == "E48"))
        e["id"] = "E48b"
        d["edges"].append(e)
    assert any("max_traversals ≥ 1" in e for e in validate(_mutated(bound)))
    assert any("blocking_severities" in e for e in validate(_mutated(severities)))
    assert any("duplicated" in e for e in validate(_mutated(duplicate)))


def test_validator_rejects_an_unscorable_must_dimension():
    """A MUST review dimension on an agent node is never scored (critic J04 finding 9)."""
    def f(d):
        next(n for n in d["nodes"] if n["id"] == "n07_script_development")["rubric"] = ["script", "creative"]
    assert any("would never be scored" in e for e in validate(_mutated(f)))


def test_validator_rejects_a_human_decision_no_edge_handles():
    """Critic variant V15: a gate outcome that is not a node outcome would fire no edge."""
    def f(d):
        next(n for n in d["nodes"] if n["id"] == "n03_objective_audience")["human_review"]["outcomes"] = ["approved", "maybe"]
    assert any("not outcomes of the node" in e for e in validate(_mutated(f)))


def test_shipped_graph_has_no_unscorable_must_dimension():
    from vpg.graph import _rubric_placement_errors
    assert _rubric_placement_errors(WF, PKG) == []


def test_docs_are_up_to_date():
    assert generate(check=True) == []


def test_skill_bindings_resolve_in_index():
    index = (PKG / "docs" / "skill-bindings.md").read_text()
    missing = sorted({s for n in WF.nodes for s in n.skills if not re.search(rf"(`|\[|/){re.escape(s)}(`|\]|/|\b)", index)})
    assert missing == []
    workspace_index = PKG.parents[1] / "03_Agents-and-Skills" / "INDEX.md"
    if workspace_index.is_file():
        index = workspace_index.read_text()
        assert all(re.search(rf"(`|\[|/){re.escape(s)}(`|\]|/|\b)", index)
                   for n in WF.nodes for s in n.skills)


def test_every_rubric_dimension_has_rule_and_brief_dimension():
    import json
    rules = {r["id"] for r in json.loads((PKG / "knowledge/rules.json").read_text())["rules"]}
    for rid, rub in load_rubrics().items():
        for d in rub["dimensions"]:
            assert d["rule"] in rules, (rid, d["id"])
            assert d["brief_dimension"] in BRIEF_DIMENSIONS, (rid, d["id"])
            assert d["check"]["type"] in ("tool", "review", "human")
            assert d["cases"]["never"] or d["cases"]["always"] or d["check"]["type"] == "tool", (rid, d["id"])


def test_rubrics_cover_18_brief_dimensions():
    covered = {d["brief_dimension"] for r in load_rubrics().values() for d in r["dimensions"]}
    assert covered == set(BRIEF_DIMENSIONS) and len(BRIEF_DIMENSIONS) == 18


def test_tool_dimensions_match_what_tools_emit():
    """Every dimension a tool can emit is declared by the rubric of the node that runs the tool."""
    rubrics = load_rubrics()
    for n in WF.nodes:
        if n.kind.value != "review":
            continue
        declared = {d["id"] for r in n.rubric for d in rubrics[r]["dimensions"]}
        src = (PKG / "vpg/tools" / f"{n.run.tool.split(':')[0]}.py").read_text()
        emitted = set(re.findall(r'"((?:TEC|LEG|ACC|DES|PAC)-[A-Z-]+)"', src))
        assert emitted <= declared, (n.id, sorted(emitted - declared))


def test_agents_cover_18_roles():
    names = {yaml.safe_load(Path(p).read_text())["name"] for p in glob.glob(str(PKG / "agents/*.yaml"))}
    expected = {"Context Research Agent", "Product Understanding Agent", "Creative Director Agent", "Narrative and Script Agent", "Storyboard Agent",
                "Visual Design Agent", "Asset Generation Agent", "Motion Design Agent", "Voice and Audio Agent", "Remotion or Composition Agent",
                "Product Accuracy Reviewer", "Accessibility Reviewer", "Design-System Reviewer", "Technical QA Agent", "Creative Critic Agent",
                "Production Orchestrator", "Final Approval Agent", "Learning and Memory Agent"}
    assert names == expected
    for p in glob.glob(str(PKG / "agents/*.yaml")):
        a = yaml.safe_load(Path(p).read_text())
        for f in ("responsibility", "inputs_allowed", "skills", "tools", "outputs", "decision_authority", "constraints", "evaluation", "escalation"):
            assert f in a, (p, f)


def test_prompt_placeholders_known():
    for f in glob.glob(str(PKG / "prompts/*.md")):
        assert set(re.findall(r"\{\{([^}]+)\}\}", Path(f).read_text())) <= PROMPT_VARS, f


def test_no_placeholders_in_code():
    for f in glob.glob(str(PKG / "vpg/**/*.py"), recursive=True) + [str(PKG / "graph/workflow.yaml")]:
        text = Path(f).read_text()
        assert not re.search(r"\bTODO\b|NotImplementedError|\bplaceholder\b(?! sections| or empty)", text), f


def test_no_network_imports_in_engine():
    for f in glob.glob(str(PKG / "vpg/**/*.py"), recursive=True):
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib|http\.client|httpx|aiohttp)\b", Path(f).read_text(), re.M), f
