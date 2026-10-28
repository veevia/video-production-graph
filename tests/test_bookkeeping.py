"""v1.1 regressions from the first real production (the SRA film): defect bookkeeping (#25 #26 #27 #41), `vpg resolve`,
the bound command's code in the fingerprint (#21 #40), the engine in the accepted configuration (#9), on-screen density
and reading time (#36), the submitter's recorded identity (#11) and failed-attempt timing (#30).

Each test fails on the behaviour the production observed.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import unicodedata
from types import SimpleNamespace

import pytest
import yaml

from tests.conftest import PKG, FnExecutor, flag, mini_workflow
from tests.test_hardening import make_project, submit_packet
from tests.test_tools import Ctx, _legibility, layer
from vpg import cli
from vpg.engine import Engine, EngineError, Project
from vpg.evaluation import compare_after_rework, gate, record_findings, signature
from vpg.executors import ScriptedExecutor, TaskPacketExecutor
from vpg.memory import GovernanceError, apply_proposal
from vpg.models import Approval, ArtifactRecord, Defect, EvaluationState, Finding, Policies, Review, RuntimeState
from vpg.models import NodeStatus as S
from vpg.provenance import Ledger
from vpg.tools import pacing
from vpg.tools import privacy
from vpg.tools.legibility import max_simultaneous
from vpg.util import is_human_name, now, sha256_file, sha256_json

ROUTES = "tests.fakes:review_routes"
COMMAND_SCRIPT = ("import json, os, pathlib\n"
                  "ctx = json.load(open(os.environ['VPG_CONTEXT']))\n"
                  "pathlib.Path(ctx['workdir'], 'outputs.json').write_text(json.dumps({'outputs': {}}))\n")


def events(proj, kind):
    return [e["data"] for e in Ledger(proj / ".vpg/ledger.jsonl").events(kind)]


def release_workflow(tmp_path, release_id="n23_final_verification", **kw):
    path = mini_workflow(tmp_path, **kw)
    data = yaml.safe_load(path.read_text())
    for node in data["nodes"]:
        if node["id"] == "z_done":
            node["id"] = release_id
            node["human_review"] = {"required": True, "decision": "release", "approver": "Wei",
                                    "outcomes": ["approved", "changes_requested"]}
            node["outcomes"] = ["approved", "changes_requested"]
            for output in node.get("outputs", []):
                if output.get("key") == "z_done":
                    output["key"] = release_id
            for completion in node.get("completion", []):
                if completion.get("key") == "z_done":
                    completion["key"] = release_id
    for edge in data["edges"]:
        if edge["from"] == "z_done":
            edge["from"] = release_id
        if edge["to"] == "z_done":
            edge["to"] = release_id
        if edge["id"] == "E15":
            edge["when"], edge["transition"] = "approved", "conditional"
    data["edges"].append({"id": "E16R", "from": release_id, "to": "g_gate", "kind": "loop", "transition": "conditional",
                          "when": "changes_requested", "transfers": ["feedback"], "threshold": "release changes requested",
                          "branching": "changes requested", "on_fail": "bounded", "max_traversals": 2})
    data["subgraphs"][0]["nodes"] = [n["id"] for n in data["nodes"]]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def add_verification_artifact(eng, warnings=None, pending=None):
    path = eng.project.root / "work/verification.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"warnings": list(warnings or []), "pending_human": list(pending or [])}))
    eng.state.artifacts[f"verification-{len(eng.state.artifacts)}"] = ArtifactRecord(
        id=f"verification-{len(eng.state.artifacts)}", key="verification", path="work/verification.json",
        sha256=sha256_file(path), bytes=path.stat().st_size, node=eng._release_node() or "n23_final_verification",
        attempt=1, produced_at=now())
    return path


def _state(*defects, failures=None):
    return SimpleNamespace(evaluation=EvaluationState(defects=list(defects)),
                           runtime=RuntimeState(run_id="t", defect_failures=dict(failures or {})))


def _defect(id, dim, trace, check, status="open", severity="P2", source="rev-n16-v1", seen=1):
    return Defect(id=id, signature=signature(dim, trace, check), severity=severity, dimension=dim, description="before",
                  source=source, traced_node=trace, status=status, first_seen_version=seen, last_seen_version=seen)


def _finding(dim, trace, check, severity="P2", element=None, message="after", fix="", level="MUST"):
    return Finding(check=check, dimension=dim, severity=severity, message=message, traced_node=trace, element=element,
                   smallest_fix=fix, level=level)


def _review(node, version, carried=False, defect_ids=()):
    return Review(id=f"rev-{node}-v{version}", node=node, version=version, reviewer="critic", rubric_ids=[], verdict="pass",
                  carried_forward=carried, defect_ids=list(defect_ids), created_at="t")


# ---------------------------------------------------------------------------------------------- #41 the change-request deadlock
def test_a_defect_fixed_outside_rework_is_resolved_and_the_gate_passes(tmp_path):
    """#41: a defect traced to a node no rework loop reaches blocks for a human. The fix lands outside rework (the source is
    invalidated and re-runs, as an approved change request does) and the fresh review no longer reports it. It used to stay
    open for ever, because only the regeneration plan put defects in rework, so the gate re-blocked on every unblock."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect_audio")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    d = eng.state.evaluation.defects[0]
    assert eng.state.runtime.status.value == "BLOCKED" and d.traced_node == "c_audio" and d.needs_human
    flag(proj, "defect_audio", on=False)
    (proj / "flags" / "c_audio.variant").write_text("fixed")      # the human's fix changes c_audio's output
    eng.invalidate_nodes(["c_audio"], reason="approved change request", actor="Wei")
    eng.unblock("g_gate", actor="Wei", note="the fix is in")
    eng.run(max_steps=200)
    d = eng.state.evaluation.defects[0]
    assert eng.state.runtime.status.value == "RUN_PASS", eng.state.evaluation.gate_results[-1].reasons
    assert d.status == "resolved" and not d.needs_human
    assert eng.state.runtime.rework_cycles == 0 and eng.state.runtime.defect_failures == {}
    last = eng.state.evaluation.comparisons[-1]
    assert last.defect_id == d.id and last.resolved and "outside a rework cycle" in last.after


def test_after_a_fix_outside_rework_the_remaining_defects_still_go_through_rework(tmp_path):
    """#41: clearing the fixed defect must not let the others pass: the one rework can reach is routed to the
    regeneration plan (N22), and only it."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect")
    flag(proj, "defect_audio")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.evaluation.gate_results[-1].outcome == "blocked"
    flag(proj, "defect_audio", on=False)
    (proj / "flags" / "c_audio.variant").write_text("fixed")
    eng.invalidate_nodes(["c_audio"], reason="approved change request", actor="Wei")
    eng.unblock("g_gate", actor="Wei", note="the fix is in")
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert [g.outcome for g in eng.state.evaluation.gate_results] == ["blocked", "rework", "pass"]
    by = {d.dimension: d for d in eng.state.evaluation.defects}
    plan = events(proj, "regeneration_plan")
    assert len(plan) == 1 and by["LEG-SIZE"].id in plan[0]["defects"] and by["LEG-CONTRAST"].id not in plan[0]["defects"]
    assert by["LEG-SIZE"].status == by["LEG-CONTRAST"].status == "resolved"
    assert eng.state.runtime.status.value == "RUN_PASS"


def test_an_open_defect_resolves_on_a_fresh_review_without_counting_a_failure():
    """#41 (unit): open and persisting defects no correction was attempted on are resolved by a fresh review that no longer
    reports them. With no fresh review (none, or only a carried-forward one) they stay as they are, and nothing is counted."""
    fixed = _defect("D009", "TEC-FORMAT", "n14", "media:tec-format", source="rev-n15-v2", seen=2)
    persisting = _defect("D003", "CRE-TRANSITIONS", "n13", "review:n16", status="persisting", source="rev-n16-v1", seen=2)
    unseen = _defect("D010", "DES-TOKENS", "n13", "design:pairs", source="rev-n17-v2", seen=2)
    feedback = _defect("D030", "NAR-ARC", "n07", "interpretation:F005", source="feedback:F005", seen=8)
    fixed.needs_human = True
    st = _state(fixed, persisting, unseen, feedback, failures={persisting.signature: 1})
    st.evaluation.reviews += [_review("n15", 8), _review("n16", 8), _review("n17", 8, carried=True)]
    out = compare_after_rework(st, 8, {}, {"D030"})
    assert fixed.status == persisting.status == "resolved" and not fixed.needs_human
    assert unseen.status == "open"                        # only a carried-forward review: no fresh evidence
    assert feedback.status == "open"                      # filed on this very version: the interpreter cannot close it yet
    assert st.runtime.defect_failures == {persisting.signature: 1}
    assert {c.defect_id for c in out} == {"D009", "D003"} and all("outside a rework cycle" in c.after for c in out)
    compare_after_rework(st, 9, {}, {"D030"})
    assert feedback.status == "resolved"


def test_a_retrace_off_a_human_gated_node_waits_for_a_human(tmp_path):
    """A defect traced to a human-gated node (here one whose approved document is locked) needs a human. When a reviewer
    re-traces the same problem to a node rework reaches, it stays the same defect (#27), so it must not route around that
    human: it is held, the gate blocks and says why, and rework routes it only after a named human unblocks the gate."""
    proj = make_project(tmp_path, review_tool=ROUTES, human_b=True, lock_b=("design",))
    flag(proj, "defect_plan")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="the plan is right")
    eng.run(max_steps=200)
    d = eng.state.evaluation.defects[0]
    assert eng.state.evaluation.gate_results[-1].outcome == "blocked" and d.needs_human and d.traced_node == "b_plan"
    flag(proj, "retrace_plan")                                     # the next review traces the problem to d_build
    (proj / "flags" / "c_audio.variant").write_text("new take")    # an unrelated change builds a new version
    eng.invalidate_nodes(["c_audio"], reason="a new take", actor="Wei")
    eng.unblock("g_gate", actor="Wei", note="re-review the new take")
    eng.run(max_steps=200)
    d = eng.state.evaluation.defects[0]
    held = eng.state.evaluation.gate_results[-1]
    assert held.outcome == "blocked" and "on hold" in held.reasons[0] and "b_plan" in held.reasons[0]
    assert d.traced_node == "d_build" and d.human_hold and d.needs_human and len(eng.state.evaluation.defects) == 1
    assert events(proj, "defect_retraced")[0] == {"id": d.id, "from": "b_plan", "to": "d_build", "by": "rev-r1_review-v2"}
    assert events(proj, "defect_held")[0]["id"] == d.id
    with pytest.raises(EngineError, match="named human"):
        eng.unblock("g_gate", actor="agent:critic", note="route it")
    eng.unblock("g_gate", actor="Wei", note="the fix belongs in the build, not the plan")
    assert events(proj, "unblocked")[-1]["released_holds"] == [d.id]
    eng.on_step = lambda e: flag(proj, "defect_plan", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert [g.outcome for g in eng.state.evaluation.gate_results] == ["blocked", "blocked", "rework", "pass"]
    d = eng.state.evaluation.defects[0]
    assert d.status == "resolved" and not d.human_hold and eng.state.runtime.status.value == "RUN_PASS"
    assert events(proj, "regeneration_plan")[0]["targets"] == ["d_build"]


def _interpret_once(defect):
    """A gate that maps each open feedback item to one new defect (and has nothing to map afterwards)."""
    def handler(ctx):
        data = {"defects": [], "feedback": []}
        for f in (f for f in ctx.state.evaluation.feedback if f.status == "open"):
            data["defects"].append({**defect, "feedback": f.id})
            data["feedback"].append({"id": f.id, "defect_ids": []})
        (ctx.workdir / "interpretation.json").write_text(json.dumps(data))
        return {"interpretation": "interpretation.json"}
    return handler


def test_a_named_human_resolves_a_defect_no_review_can_clear(tmp_path, capsys):
    """`vpg resolve`: feedback traced to a node rework cannot reach needs a human. When the human finds it does not apply,
    they close it with their evidence and the gate passes. Agents are refused and the evidence is required."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    handlers = {"g_gate": _interpret_once({"dimension": "VOI-PACE", "severity": "P2", "description": "the voice sounds rushed",
                                           "traced_node": "c_audio"})}
    eng = Engine(proj, executor=FnExecutor(handlers))
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    eng.add_feedback("the voice sounds rushed", actor="Wei")
    eng.run(max_steps=200)
    d = eng.state.evaluation.defects[-1]
    assert d.source == "feedback:F001" and d.needs_human and eng.state.runtime.status.value == "BLOCKED"
    with pytest.raises(EngineError, match="named human"):
        eng.resolve_defect(d.id, actor="agent:critic", evidence="sounds fine")
    with pytest.raises(EngineError, match="evidence"):
        eng.resolve_defect(d.id, actor="Wei", evidence="  ")
    assert cli.main(["resolve", str(proj), d.id, "--actor", "Wei", "--evidence", "played back on headphones: the pace is fine"]) == 0
    out = capsys.readouterr().out
    assert f"{d.id} resolved by Wei" in out and "unblock" in out and "g_gate" in out
    eng = Engine(proj, executor=FnExecutor(handlers))
    assert eng.state.evaluation.defects[-1].status == "resolved"
    assert events(proj, "defect_resolved")[-1] == {"id": d.id, "actor": "Wei", "evidence": "played back on headphones: the pace is fine",
                                                   "was": "open", "version": 1}
    assert "resolved by Wei" in eng.state.evaluation.comparisons[-1].after
    with pytest.raises(EngineError, match="only an open"):
        eng.resolve_defect(d.id, actor="Wei", evidence="again")
    eng.unblock("g_gate", actor="Wei", note="the feedback does not apply")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS", eng.state.evaluation.gate_results[-1].reasons


def test_tool_defect_cannot_be_resolved_by_command(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    finding = Finding(check="tool:contrast", dimension="ACC-CONTRAST", severity="P2", message="contrast below bar",
                      evidence="ratio 2.1", traced_node="d_build")
    review = Review(id="rev-r1_review-v1", node="r1_review", version=1, reviewer="tool", rubric_ids=[],
                    tool_findings=[finding], verdict="fail", created_at=now())
    eng.state.evaluation.reviews.append(review)
    did = eng._record_findings([finding], review.id, version=1)[0]
    with pytest.raises(EngineError, match="deterministic tool check.*next tool check.*end review"):
        eng.resolve_defect(did, actor="Wei", evidence="looks fixed")
    assert eng.state.evaluation.defects[0].status == "open"


def test_reproduced_closed_defect_reopens_for_routing():
    finding = Finding(check="tool:contrast", dimension="ACC-CONTRAST", severity="P2", message="contrast below bar",
                      evidence="ratio 2.1", traced_node="d_build")
    closed = Defect(id="D001", signature=signature("ACC-CONTRAST", "d_build", "tool:contrast"), severity="P2",
                    dimension="ACC-CONTRAST", description="contrast below bar", source="rev-r1_review-v1",
                    traced_node="d_build", status="waived", first_seen_version=1, last_seen_version=1)
    st = _state(closed)
    ids = record_findings(st, [finding], "rev-r1_review-v2", 2, default_trace="g_gate")
    assert ids == ["D001"]
    assert closed.status == "open" and closed.last_seen_version == 2


def test_independent_review_defect_needs_fresh_pass_or_principal(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    finding = Finding(check="review:r1_review", dimension="CRE-ARC", severity="P2", message="story is unclear",
                      evidence="review notes", traced_node="d_build")
    review = Review(id="rev-r1_review-v1", node="r1_review", version=1, reviewer="critic", rubric_ids=[],
                    verdict="fail", independent=True, created_at=now())
    eng.state.evaluation.reviews.append(review)
    did = eng._record_findings([finding], review.id, version=1)[0]
    with pytest.raises(EngineError, match="independent review.*producer cannot close"):
        eng.resolve_defect(did, actor="Pat Example", evidence="I fixed it")
    eng.resolve_defect(did, actor="Wei", evidence="end-review decision accepts it")
    assert eng.state.evaluation.defects[0].status == "resolved"


def test_relayed_resolve_of_review_defect_refused_mid_run(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.state.runtime.accepted_policy = {"humans": ["Wei"], "autonomy": {"mode": "provisional", "principal": "Wei"}}
    finding = Finding(check="review:r1_review", dimension="CRE-ARC", severity="P2", message="story is unclear",
                      evidence="review notes", traced_node="d_build")
    review = Review(id="rev-r1_review-v1", node="r1_review", version=1, reviewer="critic", rubric_ids=[],
                    verdict="fail", independent=True, created_at=now())
    eng.state.evaluation.reviews.append(review)
    did = eng._record_findings([finding], review.id, version=1)[0]
    with pytest.raises(EngineError, match="release end review"):
        eng.resolve_defect(did, actor="agent:producer", on_behalf_of="Wei", instruction="looks fine", evidence="fixed")


def test_non_independent_fresh_review_does_not_close_defect():
    d = Defect(id="D001", signature=signature("CRE-ARC", "d_build", "review:r1_review"), severity="P2",
               dimension="CRE-ARC", description="story unclear", source="rev-r1_review-v1", traced_node="d_build",
               first_seen_version=1, last_seen_version=1)
    st = _state(d)
    st.evaluation.reviews = [Review(id="rev-r1_review-v1", node="r1_review", version=1, reviewer="critic", rubric_ids=[],
                                    verdict="fail", independent=True, defect_ids=["D001"], created_at=now()),
                             Review(id="rev-r1_review-v2", node="r1_review", version=2, reviewer="producer", rubric_ids=[],
                                    verdict="pass", independent=False, defect_ids=[], created_at=now())]
    compare_after_rework(st, 2, {})
    assert d.status == "open"


def test_failed_review_satisfied_when_all_defects_resolved():
    d = Defect(id="D001", signature=signature("CRE-ARC", "d_build", "review:r1_review"), severity="P2",
               dimension="CRE-ARC", description="story unclear", source="rev-r1_review-v1", traced_node="d_build",
               first_seen_version=1, last_seen_version=1, status="resolved", resolved_by="Wei")
    st = _state(d)
    st.evaluation.reviews = [Review(id="rev-r1_review-v1", node="r1_review", version=1, reviewer="critic", rubric_ids=[],
                                    verdict="fail", independent=True, defect_ids=["D001"], created_at=now())]
    result = gate(st, Policies(max_rework_cycles=3, max_same_defect_failures=3, parallel_limit=1,
                               single_flight_resources=[], blocking_severities=["P1", "P2", "P3"],
                               dry_run_actor="dry-run-auto"),
                  1, ["r1_review"], "g_gate")
    assert result.outcome == "pass"
    assert not any("reviews failed with no blocking defect" in r for r in result.reasons)


# ---------------------------------------------------------------------------------------------- #25 #26 #27 defect identity
def test_a_retraced_problem_keeps_its_identity_and_its_failure_count():
    """#27: the reviewer correctly re-traced a persisting problem (n13 → n08). It became a new defect with a zero count
    while the old one counted as fixed, so the three-corrections escalation could be evaded."""
    d = _defect("D002", "CRE-MOTION", "n13", "review:n16", status="in_rework")
    st = _state(d, failures={d.signature: 1})
    log = []
    ids = record_findings(st, [_finding("CRE-MOTION", "n08", "review:n16")], "rev-n16-v2", 2, default_trace="gate", log=log)
    assert ids == ["D002"] and len(st.evaluation.defects) == 1
    assert d.traced_node == "n08" and d.status == "persisting" and st.runtime.defect_failures[d.signature] == 2
    assert log == [{"event": "defect_retraced", "id": "D002", "from": "n13", "to": "n08", "by": "rev-n16-v2"}]
    st.evaluation.reviews.append(_review("n16", 2, defect_ids=ids))
    compare_after_rework(st, 2, {})
    assert d.status == "persisting"                        # still reported: re-tracing never resolves it
    # The old problem still reported on its own trace, plus a new one elsewhere: two defects, not one.
    d = _defect("D002", "CRE-MOTION", "n13", "review:n16", status="in_rework")
    st = _state(d)
    ids = record_findings(st, [_finding("CRE-MOTION", "n13", "review:n16", message="jitter"),
                               _finding("CRE-MOTION", "n08", "review:n16", message="static")], "rev-n16-v2", 2, default_trace="gate")
    assert ids[0] == "D002" and ids[1] != "D002" and d.traced_node == "n13" and d.description == "jitter"
    assert st.evaluation.defects[1].traced_node == "n08"
    # A new finding on a trace a re-traced defect still names gets its own signature (its own failure count).
    d = _defect("D002", "CRE-MOTION", "n13", "review:n16", status="persisting")
    st = _state(d)
    record_findings(st, [_finding("CRE-MOTION", "n08", "review:n16")], "rev-n16-v2", 2, default_trace="gate")
    record_findings(st, [_finding("CRE-MOTION", "n08", "review:n16"), _finding("CRE-MOTION", "n13", "review:n16")], "rev-n16-v3", 3,
                    default_trace="gate")
    assert len(st.evaluation.defects) == 2 and len({x.signature for x in st.evaluation.defects}) == 2


POLICIES = Policies(max_rework_cycles=6, max_same_defect_failures=3, parallel_limit=4, single_flight_resources=[],
                    blocking_severities=["P1", "P2", "P3"], dry_run_actor="dry-run-auto")


def test_a_problem_alternating_between_traces_still_escalates():
    """Critic finding: a problem once reported on two traces left a resolved twin. Alternating the persisting problem
    between the traces reopened the resolved twin at zero each time and 'resolved' the one in rework, so the
    three-corrections stop never fired. Unresolved defects are matched before a resolved twin is reopened."""
    st = _state()
    seen = {1: ["n13", "n08"], 2: ["n13"], 3: ["n08"], 4: ["n13"]}   # n08's part is fixed at v2; then the problem alternates
    outcomes = []
    for v, traces in seen.items():
        for d in st.evaluation.defects:                 # the regeneration plan routes every blocking defect
            if d.status in ("open", "persisting"):
                d.status = "in_rework"
        ids = record_findings(st, [_finding("CRE-MOTION", t, "review:n16") for t in traces], f"rev-n16-v{v}", v, default_trace="gate")
        st.evaluation.reviews.append(_review("n16", v, defect_ids=ids))
        compare_after_rework(st, v, {})
        st.runtime.rework_cycles = v
        outcomes.append(gate(st, POLICIES, v, ["n16"], "gate").outcome)
    first, twin = st.evaluation.defects
    assert twin.status == "resolved" and first.status == "persisting" and len(st.evaluation.defects) == 2
    assert st.runtime.defect_failures[first.signature] == 3 and outcomes == ["rework", "rework", "rework", "blocked"]


def test_findings_about_several_items_keep_every_description(tmp_path):
    """#26: three LEG-DWELL findings from one review became one defect that kept only the last text, so two of them never
    reached the rework context. Findings of one batch about one problem share a defect that keeps every description and
    names every item (per-item tools set `element`)."""
    res = _legibility(tmp_path, [layer(id=f"L{i}", text=f"A long label {i} with many words to read here", start=0, end=0.6)
                                 for i in range(3)])
    dwell = [f for f in res.findings if f.dimension == "LEG-DWELL"]
    assert sorted(f.element for f in dwell) == ["L0", "L1", "L2"]
    st = _state()
    ids = record_findings(st, dwell, "rev-n18-v2", 2, default_trace="gate")
    d = st.evaluation.defects[0]
    assert len(set(ids)) == 1 and d.elements == ["L0", "L1", "L2"]
    assert all(f"label {i}" in d.description for i in range(3)) and d.description.count(" | ") == 2
    ids = record_findings(st, dwell[1:2], "rev-n18-v3", 3, default_trace="gate")   # two fixed: the defect names what remains
    assert ids == [d.id] and d.elements == ["L1"] and "label 1" in d.description and "label 0" not in d.description


def test_severity_follows_the_latest_review_and_the_latest_fix():
    """#25: a defect re-filed at P2 stayed P3 (a nit re-filed at P3 would have stayed non-blocking), and its smallest fix kept
    the first review's text. Severity follows the latest review, and each change is ledgered."""
    d = _defect("D004", "LEG-LAPTOP", "n13", "review:n18", severity="P3")
    d.smallest_fix = "v1 fix"
    st = _state(d)
    log = []
    record_findings(st, [_finding("LEG-LAPTOP", "n13", "review:n18", severity="P2", fix="v2 fix")], "rev-n18-v2", 2,
                    default_trace="gate", log=log)
    assert d.severity == "P2" and d.smallest_fix == "v2 fix"
    assert {"event": "defect_severity_changed", "id": "D004", "from": "P3", "to": "P2", "by": "rev-n18-v2"} in log
    record_findings(st, [_finding("LEG-LAPTOP", "n13", "review:n18", severity="P3"),
                         _finding("LEG-LAPTOP", "n13", "review:n18", severity="P1")], "rev-n18-v3", 3, default_trace="gate")
    assert d.severity == "P1" and d.smallest_fix == "v2 fix"     # the most severe of one review; no new fix keeps the last
    record_findings(st, [_finding("LEG-LAPTOP", "n13", "review:n18", severity="nit")], "rev-n18-v4", 4, default_trace="gate")
    assert d.severity == "nit"


def test_a_waived_defect_never_absorbs_a_later_finding():
    """Critic nit: with identity per check, a waived defect matched every later finding of its check on its trace, so one
    waiver would silence the check there for good. A later finding is a new defect with its own name."""
    waived = _defect("D007", "DES-COMPOSITION", "n08", "review:n17", status="waived")
    st = _state(waived)
    ids = record_findings(st, [_finding("DES-COMPOSITION", "n08", "review:n17")], "rev-n17-v5", 5, default_trace="gate")
    assert ids != ["D007"] and waived.status == "waived" and st.evaluation.defects[-1].status == "open"
    assert st.evaluation.defects[-1].signature != waived.signature


def test_the_rubric_sets_a_findings_level_not_the_reviewer(project):
    """Critic finding: a reviewer that declared `level: SHOULD` on a MUST dimension made a persisting P1 a nit, while the
    review itself still failed. The rubric (and evaluation.md) set the level; a SHOULD dimension's findings are nits."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    levels = eng._dimension_levels()
    must = next(k for k, v in levels.items() if v == "MUST")
    kept = eng._govern_findings([_finding(must, "d_build", "review:r1", severity="P1", level="SHOULD")])
    assert [(f.level, f.severity) for f in kept] == [("MUST", "P1")]
    should = next((k for k, v in levels.items() if v == "SHOULD"), None)
    if should:
        assert [(f.level, f.severity) for f in eng._govern_findings([_finding(should, "d_build", "review:r1", severity="P1")])] \
            == [("SHOULD", "nit")]


def test_a_failed_gate_attempt_keeps_no_defect_change_and_ledgers_none(tmp_path):
    """A gate attempt that re-files a defect and then fails its checks is rolled back whole: the severity, the failure
    count and the ledger events of that attempt are all discarded."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    arc = {"dimension": "NAR-ARC", "severity": "P3", "description": "the middle sags", "traced_node": "d_build"}
    plans = [{"defects": [arc], "feedback": []},
             {"defects": [{**arc, "severity": "P1", "description": "the middle collapses"}],
              "feedback": [{"id": "F999", "no_action_reason": "unknown feedback id"}]}]

    def g_gate(ctx):
        (ctx.workdir / "interpretation.json").write_text(json.dumps(plans[min(ctx.attempt, 2) - 1]))
        return {"interpretation": "interpretation.json"}
    eng = Engine(proj, executor=FnExecutor({"g_gate": g_gate}))
    eng.run(max_steps=200)
    d = eng.state.evaluation.defects[0]
    rt = eng.rt("g_gate")
    assert rt.status.value == "BLOCKED" and rt.fail_streak == 2 and "F999" in " ".join(rt.failures)
    assert d.severity == "P3" and d.status == "in_rework" and d.description == "the middle sags"
    assert eng.state.runtime.defect_failures == {} and not events(proj, "defect_severity_changed")


def test_a_state_written_before_v1_1_still_loads_and_runs(tmp_path):
    """The in-flight run's state has none of v1.1's fields. It must load, fingerprint and finish (fields default)."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    path = proj / ".vpg/state.json"
    raw = json.loads(path.read_text())
    for d in raw["evaluation"]["defects"]:
        del d["elements"], d["human_hold"]
    for r in raw["evaluation"]["reviews"]:
        for f in r["tool_findings"]:
            del f["element"]
    for rt in raw["runtime"]["nodes"].values():
        del rt["command_code"]
    for f in raw["evaluation"]["feedback"]:
        del f["hold"]
    path.write_text(json.dumps(raw))
    assert '"elements"' not in path.read_text() and '"command_code"' not in path.read_text()
    eng = Engine(proj, executor=FnExecutor())
    assert eng.state.evaluation.defects[0].elements == [] and eng.rt("d_build").command_code == {}
    eng.add_feedback("the title reads well now", actor="Wei")
    eng.run(max_steps=200)                                          # the interpreter closes it with no action: held for Wei
    assert eng.state.runtime.status.value == "BLOCKED" and eng.state.evaluation.feedback[0].hold
    eng.unblock("g_gate", actor="Wei", note="no change needed")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


# ---------------------------------------------------------------------------------------------- #21 #40 command code, #9 engine
def _bind(proj, command, **extra):
    """Bind `command` to c_audio; the call's settings replace every earlier command setting."""
    cfg = {k: v for k, v in yaml.safe_load((proj / "vpg.project.yaml").read_text()).items()
           if k not in ("commands", "command_deps", "command_code_exclude")}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "commands": {"c_audio": command}, **extra}))


def test_a_bound_commands_code_is_in_its_fingerprint(tmp_path):
    """#21/#40: only the command string was hashed, so editing the scene engine a script loads left the fingerprint
    unchanged and a memo-skip reused the old film. The script's top-level directory is hashed too (caches excluded)."""
    proj = make_project(tmp_path)
    (proj / "pipeline/film").mkdir(parents=True)
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    (proj / "pipeline/film/engine.js").write_text("// v1\n")
    _bind(proj, "{python} pipeline/run.py --fast")
    eng = Engine(proj, executor=FnExecutor())
    assert [p for p, _ in eng.project.command_code("c_audio")] == ["pipeline/film/engine.js", "pipeline/run.py"]
    assert eng.project.command_code("d_build") == []
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.rt("c_audio").executions == 1
    (proj / "pipeline/__pycache__").mkdir()
    (proj / "pipeline/__pycache__/run.cpython-311.pyc").write_bytes(b"cache")
    (proj / "pipeline/.DS_Store").write_bytes(b"finder")
    eng.invalidate(["c_audio"], reason="an upstream re-check")      # STALE, not forced: a memo-skip is allowed
    eng.run(max_steps=200)
    assert eng.rt("c_audio").memo_hits == 1 and eng.rt("c_audio").executions == 1   # caches never count
    (proj / "pipeline/film/engine.js").write_text("// v2: new transitions\n")
    eng.invalidate(["c_audio"], reason="an upstream re-check")
    eng.run(max_steps=200)
    assert eng.rt("c_audio").executions == 2 and eng.rt("c_audio").memo_hits == 1   # the new engine code ran


def test_command_deps_names_exactly_the_code_a_node_depends_on(tmp_path):
    """`command_deps` replaces the directory rule for one node, e.g. so a voice step ignores scene-engine edits."""
    proj = make_project(tmp_path)
    (proj / "pipeline/film").mkdir(parents=True)
    for f in ("pipeline/voice.py", "pipeline/_ctx.py", "pipeline/film/engine.js"):
        (proj / f).write_text("")
    _bind(proj, "{python} pipeline/voice.py", command_deps={"c_audio": ["pipeline/_ctx.py"]})
    eng = Engine(proj, executor=FnExecutor())
    assert [p for p, _ in eng.project.command_code("c_audio")] == ["pipeline/_ctx.py", "pipeline/voice.py"]


def test_the_engine_code_is_part_of_the_configuration_a_run_accepted(tmp_path):
    """#9: only config files were hashed, so an engine change mid-run applied silently. The engine's code is now a named
    part of the accepted configuration: a change blocks the run until a named human accepts it."""
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert {"engine.py", "evaluation.py", "tools/legibility.py"} <= set(eng._config_parts()["engine"])
    rt = eng.state.runtime
    rt.config_parts["engine"]["evaluation.py"] = "0" * 64          # as if this run had accepted another engine version
    rt.config_sha256 = sha256_json(rt.config_parts)
    eng._save("test")
    eng = Engine(proj, executor=FnExecutor())
    assert any("engine/evaluation.py" in p and "accept-config" in p for p in eng._integrity_problems())
    eng.accept_config(actor="Wei", reason="v1.1 engine fixes")
    assert not eng._integrity_problems()


def test_odd_command_lines_never_break_dispatch(tmp_path):
    """Critic finding: a command the shell accepts but shlex cannot parse (an apostrophe in a comment) raised inside
    dispatch, so the run could not advance. Parsing never raises, and a command whose code cannot be found is reported."""
    proj = make_project(tmp_path)
    (proj / "pipeline").mkdir()
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    lines = {"{python} pipeline/run.py  # don't": ["pipeline/run.py"],
             "{python} pipeline/run.py;echo done>log.txt": ["pipeline/run.py"],
             "python 'unclosed pipeline/run.py": ["pipeline/run.py"],
             "cd pipeline && python run.py": []}
    for line, expected in lines.items():
        _bind(proj, line)
        assert [p for p, _ in Project(proj).command_code("c_audio")] == expected, line
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)                                          # c_audio is fingerprinted at every dispatch
    assert eng.state.runtime.status.value == "RUN_PASS"
    assert any("c_audio: its command names no script" in w for w in eng.status()["warnings"])


def test_invalid_command_settings_are_refused_before_any_dispatch(tmp_path, capsys):
    """Critic finding: a string-valued command_deps entry was iterated character by character, and '/' made the glob raise
    inside dispatch. Bad settings are refused when the run opens, with a message and no traceback."""
    proj = make_project(tmp_path)
    for bad, message in [({"command_deps": {"c_audio": "pipeline/x.py"}}, "must be a list"),
                         ({"command_deps": {"c_audio": ["/"]}}, "not a project-relative glob"),
                         ({"command_deps": {"c_audio": ["../elsewhere/*.py"]}}, "not a project-relative glob"),
                         ({"command_deps": {"typo_node": []}}, "not a node of this workflow"),
                         ({"command_code_exclude": [""]}, "not a project-relative glob"),
                         ({"command_code_exclude": "pipeline/film/data.js"}, "must be a list")]:
        _bind(proj, "{python} pipeline/run.py", **bad)
        with pytest.raises(EngineError, match=message):
            Engine(proj, executor=FnExecutor())
    assert cli.main(["run", str(proj)]) == 4
    assert "vpg.project.yaml has invalid command settings" in capsys.readouterr().err
    _bind(proj, "{python} pipeline/run.py")                         # a valid run, then a bad setting added to it
    Engine(proj, executor=FnExecutor()).run(max_steps=5)
    _bind(proj, "{python} pipeline/run.py", command_deps={"c_audio": "/etc/*"})
    assert cli.main(["status", str(proj)]) == 0                     # status names the problem instead of failing on it
    assert "vpg.project.yaml: command_deps.c_audio must be a list" in capsys.readouterr().out


def test_a_file_a_command_generates_is_reported_and_can_be_excluded(tmp_path):
    """Critic finding: a pipeline that writes a generated file into its own tree (the scene step's data.js) changed the
    code fingerprint of every node bound to that tree, so they re-ran on every re-check. The pass that changed it is
    ledgered, status names the file, and command_code_exclude restores memoisation."""
    def setup(root, **extra):
        proj = make_project(root)
        (proj / "pipeline/film").mkdir(parents=True)
        (proj / "pipeline/plan.py").write_text(COMMAND_SCRIPT)
        (proj / "pipeline/build.py").write_text(COMMAND_SCRIPT + "pathlib.Path('pipeline/film/data.js').write_text("
                                                "'window.FILM = ' + json.dumps(ctx['attempt']))\n")
        cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
        (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "privacy": {"names": "none", "reason": "synthetic"},
                                                                "humans": ["Wei"],
                                                                "commands": {"b_plan": "{python} pipeline/plan.py",
                                                                             "d_build": "{python} pipeline/build.py"}, **extra}))
        eng = Engine(proj, executor=FnExecutor())
        eng.run(max_steps=200)
        assert eng.state.runtime.status.value == "RUN_PASS"
        return proj, eng
    proj, eng = setup(tmp_path / "plain")
    assert events(proj, "command_code_changed")[0]["node"] == "d_build"
    assert "pipeline/film/data.js" in events(proj, "command_code_changed")[0]["files"]
    warnings = eng.status()["warnings"]
    assert any(w.startswith("b_plan: its code changed since it last ran (pipeline/film/data.js)") for w in warnings)
    eng.invalidate(["b_plan"], reason="an upstream re-check")      # STALE, not forced: a memo-skip is allowed
    eng.run(max_steps=200)
    assert eng.rt("b_plan").executions == 2 and eng.rt("b_plan").memo_hits == 0   # the generated file re-ran it
    proj, eng = setup(tmp_path / "excluded", command_code_exclude=["pipeline/film/data.js"])
    assert not events(proj, "command_code_changed") and not eng.status()["warnings"]
    eng.invalidate(["b_plan"], reason="an upstream re-check")
    eng.run(max_steps=200)
    assert eng.rt("b_plan").executions == 1 and eng.rt("b_plan").memo_hits == 1
    assert eng.rt("d_build").memo_hits == 1


def test_command_code_ignores_caches_by_their_place_in_the_project(tmp_path):
    """Critic nit: cache directories were matched against absolute path parts, so a project under a folder named venv
    fingerprinted no code at all."""
    proj = make_project(tmp_path / "venv")
    (proj / "pipeline/__pycache__").mkdir(parents=True)
    (proj / "pipeline/film").mkdir()
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    (proj / "pipeline/film/engine.js").write_text("// the scene engine the script loads\n")
    (proj / "pipeline/__pycache__/run.cpython-311.pyc").write_bytes(b"cache")
    _bind(proj, "{python} pipeline/run.py")
    assert [p for p, _ in Project(proj).command_code("c_audio")] == ["pipeline/film/engine.js", "pipeline/run.py"]


# ---------------------------------------------------------------------------------------------- #36 density and reading time
def test_density_counts_the_text_on_screen_at_once(tmp_path):
    """#36: LEG-DENSITY counted every text layer of a scene, but the rule is 'simultaneous', so progressive disclosure (one
    idea replacing the last) was penalised."""
    assert max_simultaneous([(i, i + 1) for i in range(14)]) == (1, 0)
    assert max_simultaneous([(0, 2), (1, 3), (2, 4)]) == (2, 1)
    assert max_simultaneous([(0, 0.1 + 0.2), (0.3, 1)])[0] == 1      # float noise at a hand-off is not an overlap
    reveal = [layer(id=f"L{i}", text=f"label {i}", start=i * 1.5, end=i * 1.5 + 1.5) for i in range(14)]
    assert not [f for f in _legibility(tmp_path, reveal).findings if f.dimension == "LEG-DENSITY"]
    crowd = [layer(id=f"L{i}", text=f"label {i}", bbox=[200, 100 + i * 60, 300, 40]) for i in range(9)]
    dense = [f for f in _legibility(tmp_path, crowd).findings if f.dimension == "LEG-DENSITY"]
    assert len(dense) == 1 and dense[0].element == "B1" and "9 text elements at once" in dense[0].message


def test_reading_time_counts_new_words_and_a_code_as_one_word(tmp_path):
    """#36: PAC-READ counted 'G/TBT/N/GBR/124' as five words and re-counted text still on screen from the previous beat."""
    case = "Case G/TBT/N/GBR/124 notified to all members"   # 6 words to read (10 if the code split into five)
    beats = [{"id": "b1", "duration_s": 2.0, "on_screen_text": [case]},                 # needs 1.44 s (2.4 s split)
             {"id": "b2", "duration_s": 1.0, "on_screen_text": [case, "Answered"]},     # 1 new word: 1.0 s (1.68 s re-counted)
             {"id": "b3", "duration_s": 1.0, "on_screen_text": ["Seven new words appear on screen here"]}]
    ctx = Ctx(tmp_path, loads={"storyboard": {"beats": beats}, "timings": {"units": []}, "brief": {}, "scenes": {"runtime_s": 3.1}})
    read = [f for f in pacing.analyze(ctx).findings if f.dimension == "PAC-READ"]
    assert [f.element for f in read] == ["b3"] and "7 new on-screen words" in read[0].message


# ---------------------------------------------------------------------------------------------- #11 submitter, #30 timing
def test_submit_records_one_agent_prefix_and_names_a_different_agent(project):
    """#11: `--actor agent:x` was recorded as agent:agent:x on the artifacts, and a submitter other than the node's bound
    agent went unremarked."""
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    submit_packet(eng, proj, "a_intake")                   # actor agent:test; the node's bound agent is production-orchestrator
    assert next(a for a in eng.state.artifacts.values() if a.node == "a_intake").tool == "agent:test"
    ev = events(proj, "node_submitted")[-1]
    assert ev["actor"] == "agent:test" and ev["bound_agent"] == "production-orchestrator"


def test_a_failed_attempt_records_when_it_finished(project):
    """#30: a failed attempt kept an older finished_at than its own start (finished 00:41 < started 01:18)."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor({"b_plan": lambda ctx: {}}))   # never produces its required output
    eng.run(max_steps=200)
    rt = eng.rt("b_plan")
    assert rt.fail_streak == 2 and rt.finished_at and rt.finished_at >= rt.started_at


# ---------------------------------------------------------------------------------------------- human boundary (judge, v1.1)
def test_a_real_production_refuses_auto_approval_and_fixtures(tmp_path, capsys):
    """Judge finding: on a real project, `vpg run --auto-approve-dry-run` auto-approved every gate up to the release and
    the run passed with no human; replaying fixtures had the same hole. Both are for dry-run projects only."""
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    with pytest.raises(EngineError, match="dry-run projects only"):
        Engine(proj, executor=FnExecutor(), auto_approve=True)
    with pytest.raises(EngineError, match="dry-run projects only"):
        Engine(proj, executor=ScriptedExecutor(proj / "fixtures"))
    assert cli.main(["run", str(proj), "--auto-approve-dry-run"]) == 4
    assert "auto-approval is for dry-run projects only" in capsys.readouterr().err
    assert cli.main(["run", str(proj), "--fixtures", "fixtures"]) == 4
    assert "fixtures is for dry-run projects only" in capsys.readouterr().err
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.rt("b_plan").status.value == "WAITING_HUMAN" and not eng.state.history.approvals


def test_the_human_check_ignores_case_and_spacing():
    """Judge finding: 'Agent:x' passed the human check (it was case-sensitive)."""
    assert is_human_name("Wei") and is_human_name("Sam Placeholder")
    for fake in ("agent:x", "Agent:x", " AGENT:critic", "dry-run-auto", "DRY-RUN-AUTO", "", "   ", None):
        assert not is_human_name(fake), fake
    with pytest.raises(GovernanceError, match="named human"):
        apply_proposal({"rules": []}, approved_by="Agent:learner")


def test_agents_in_any_case_cannot_act_as_humans(tmp_path):
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect_audio")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    d = eng.state.evaluation.defects[0]   # from the review's deterministic tool check: nobody resolves it by hand (#185)
    manual = Defect(id="D950", signature="s", severity="P2", dimension="TECH-X", description="d", source="manual",
                    traced_node="d_build")
    eng.state.evaluation.defects.append(manual)
    for actor in ("Agent:critic", " AGENT:x", "Dry-Run-Auto"):
        with pytest.raises(EngineError, match="named human"):
            eng.unblock("g_gate", actor=actor, note="route it")
        with pytest.raises(EngineError, match="named human"):
            eng.resolve_defect(manual.id, actor=actor, evidence="it is fine")
        with pytest.raises(EngineError, match="came from a deterministic tool check"):
            eng.resolve_defect(d.id, actor=actor, evidence="it is fine")
    cr = eng.change_request("design", "tighten the plate", by="Wei")
    with pytest.raises(EngineError, match="named human"):
        eng.approve_change_request(cr.id, by="Agent:designer")


def test_a_submission_cannot_replace_a_tool_output(tmp_path):
    """Judge finding: an agent's submission could replace the file its node's deterministic tool had written (e.g. a
    reviewer swapping the tool's report). Keys the tool produced are refused, without using an attempt."""
    wf = mini_workflow(tmp_path)
    data = yaml.safe_load(wf.read_text())
    next(n for n in data["nodes"] if n["id"] == "b_plan")["run"] = {"tool": "tests.fakes:produce"}
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    submit_packet(eng, proj, "a_intake")
    eng.run(max_steps=20)
    rt = eng.rt("b_plan")
    assert rt.status.value == "WAITING_RUNTIME" and "b_plan" in rt.result_data["pre_outputs"]
    forged = proj / "work/b_plan" / f"a{rt.attempts}" / "forged.json"
    forged.write_text("{}")
    attempts = rt.attempts
    with pytest.raises(EngineError, match="written by this node's tool check"):
        eng.submit("b_plan", {"b_plan": str(forged)}, token=rt.dispatch_token, actor="agent:test")
    assert eng.rt("b_plan").status.value == "WAITING_RUNTIME" and eng.rt("b_plan").attempts == attempts
    eng.submit("b_plan", {}, token=rt.dispatch_token, actor="agent:test")
    rec = next(a for a in eng.state.artifacts.values() if a.node == "b_plan" and a.status == "current")
    assert rec.path.endswith("b_plan.json")


def test_approve_needs_the_humans_reason(project):
    """Judge finding (J05): `approve` without --reason recorded an empty reason, though the SOP requires Wei's words."""
    proj, _ = project
    with pytest.raises(SystemExit):
        cli.main(["approve", str(proj), "b_plan", "--actor", "Wei"])


def test_declared_humans_refuse_free_text_and_record_relays(tmp_path, monkeypatch):
    import vpg.engine as ve
    monkeypatch.setattr(ve, "PROVISIONAL_GATES", {"b_plan"})
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
                "reviews": {"binding": "require", "independence": "require"},
                "loop_budget": {"on_exhaust": "extend_if_converging"},
                "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    with pytest.raises(EngineError, match="--actor must be exactly one of the declared humans"):
        eng.decide("b_plan", "approved", actor="Wei (standing instruction)", reason="go")
    st = eng.decide("b_plan", "approved", actor="agent:runner", reason="proceed", on_behalf_of="Wei",
                    instruction="approve the plan and keep going", instruction_at="2026-09-30T12:00:00Z",
                    provisional=True)
    assert st["nodes"]["b_plan"]["status"] == "STEP_PASS"
    approval = eng.state.history.approvals[-1]
    assert approval.actor == "agent:runner" and approval.relayed and approval.principal == "Wei"
    decision = list(Ledger(proj / ".vpg/ledger.jsonl").events("decision"))[-1]["data"]
    assert decision["actor"] == "agent:runner" and decision["principal"] == "Wei" and decision["relayed"] is True


def test_invalid_humans_config_is_refused(tmp_path):
    for idx, (humans, msg) in enumerate((([], "non-empty"), (["agent:runner"], "not agent"), (["dry-run-auto"], "not agent"))):
        proj = make_project(tmp_path / str(idx))
        cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
        cfg["humans"] = humans
        (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        with pytest.raises(EngineError, match=msg):
            Engine(proj, executor=FnExecutor())


def test_unaccepted_policy_config_cannot_authorize_human_actions(tmp_path, monkeypatch):
    import vpg.engine as ve
    monkeypatch.setattr(ve, "PROVISIONAL_GATES", {"b_plan"})
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
                "reviews": {"binding": "require", "independence": "require"},
                "loop_budget": {"on_exhaust": "extend_if_converging"},
                "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError, match="unaccepted configuration change"):
        eng.decide("b_plan", "approved", actor="agent:runner", on_behalf_of="Wei", instruction="go",
                   reason="p", provisional=True)


def test_provisional_approval_requires_policy_and_records_standing_pass(tmp_path, monkeypatch):
    import vpg.engine as ve
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({
        "humans": ["Wei"],
        "privacy": {"names": "none", "reason": "synthetic"},
        "reviews": {"binding": "require", "independence": "require"},
        "loop_budget": {"on_exhaust": "extend_if_converging"},
        "autonomy": {"mode": "provisional", "principal": "Wei"},
    })
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    monkeypatch.setattr(ve, "PROVISIONAL_GATES", {"b_plan"})
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    with pytest.raises(EngineError, match="cannot approve its own output"):
        eng.decide("b_plan", "approved", actor="agent:production-orchestrator", on_behalf_of="Wei", instruction="go",
                   reason="provisional", provisional=True)
    eng.decide("b_plan", "approved", actor="agent:runner", on_behalf_of="Wei", instruction="go", reason="provisional",
               provisional=True)
    approval = eng.state.history.approvals[-1]
    assert approval.provisional and approval.relayed and approval.principal == "Wei"
    assert eng.state.controls["design"].provisional is True
    data = list(Ledger(proj / ".vpg/ledger.jsonl").events("decision"))[-1]["data"]
    assert data["provisional"] is True and data["principal"] == "Wei"


@pytest.mark.parametrize("remove_key, expected", [
    ("principal", "autonomy.principal"),
    ("privacy", "privacy names"),
    ("reviews", "reviews must require"),
    ("loop_budget", "loop_budget must extend"),
])
def test_each_autonomy_precondition_fails_on_its_own(tmp_path, remove_key, expected):
    import vpg.engine as ve
    cfg = {"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
           "reviews": {"binding": "require", "independence": "require"},
           "loop_budget": {"on_exhaust": "extend_if_converging"},
           "autonomy": {"mode": "provisional", "principal": "Wei"}}
    if remove_key == "principal":
        del cfg["autonomy"]["principal"]
    else:
        cfg.pop(remove_key)
    assert any(expected in p for p in ve.autonomy_precondition_problems(cfg))


def test_provisional_refused_at_release_and_review_descendant(tmp_path, monkeypatch):
    import vpg.engine as ve
    wf = release_workflow(tmp_path)
    proj = make_project(tmp_path, workflow=wf)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
                "reviews": {"binding": "require", "independence": "require"},
                "loop_budget": {"on_exhaust": "extend_if_converging"},
                "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    monkeypatch.setattr(ve, "PROVISIONAL_GATES", {"n23_final_verification", "d_build"})
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError, match="release-review side"):
        eng._check_provisional_decision(eng.node("n23_final_verification"), "approved", "agent:ops", "Wei", "go")


def test_release_structure_requires_warning_acks_and_human_signoffs(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    path = proj / "work/verification.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"warnings": ["check this", "and this"], "pending_human": ["VOI-ADULT", "CRE-MUSIC"]}))
    eng.state.artifacts["verification-test"] = ArtifactRecord(
        id="verification-test", key="verification", path="work/verification.json", sha256=sha256_file(path),
        bytes=path.stat().st_size, node="n23_final_verification", attempt=1, produced_at=now())
    with pytest.raises(EngineError, match="acknowledge warning"):
        eng._check_release_structure(["1"], ["VOI-ADULT", "CRE-MUSIC"])
    with pytest.raises(EngineError, match="pending human dimension"):
        eng._check_release_structure(["all"], ["VOI-ADULT"])
    assert eng._check_release_structure(["all"], ["VOI-ADULT,CRE-MUSIC"]) == (["1", "2"], ["CRE-MUSIC", "VOI-ADULT"])


def test_partial_confirm_leaves_standing_and_reject_reopens_node(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei", "Pat Example"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.state.history.approvals += [
        Approval(node="b_plan", decision="approved", actor="agent:a", human=True, at=now(), provisional=True, relayed=True,
                 principal="Wei", instruction="go"),
        Approval(node="c_audio", decision="approved", actor="agent:b", human=True, at=now(), provisional=True, relayed=True,
                 principal="Wei", instruction="go"),
    ]
    eng.rt("c_audio").status = S.STEP_PASS
    eng._handle_release_provisional_controls("b_plan", "", "", "Wei", "", "", "", now())
    assert [a.node for a in eng._standing_provisional()] == ["c_audio"]
    eng._handle_release_provisional_controls("", "c_audio", "redo the mix", "Wei", "", "", "", now())
    assert not eng._standing_provisional()
    assert eng.rt("c_audio").status == S.STALE
    assert eng.rt("c_audio").task_notes_by_attempt[eng.rt("c_audio").attempts + 1][-1].endswith("redo the mix")
    assert eng.state.evaluation.feedback == []


def test_reject_provisional_restores_prelock_and_later_reaches_release(tmp_path, monkeypatch):
    import vpg.engine as ve
    monkeypatch.setattr(ve, "PROVISIONAL_GATES", {"b_plan"})
    monkeypatch.setattr(ve, "autonomy_precondition_problems", lambda config: [])
    proj = make_project(tmp_path, workflow=release_workflow(tmp_path, human_b=True, lock_b=("design",)))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
                "loop_budget": {"on_exhaust": "extend_if_converging"},
                "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    def write_design(ctx):
        p = ctx.project_dir / "controls/design.md"
        p.write_text(p.read_text().rstrip() + f"\n\n<!-- attempt {ctx.attempt} -->\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}

    eng = Engine(proj, executor=FnExecutor({"b_plan": write_design}))
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="agent:runner", on_behalf_of="Wei", instruction="go", reason="p", provisional=True)
    eng.run(max_steps=300)
    assert eng.rt("n23_final_verification").status == S.WAITING_HUMAN
    eng.decide("n23_final_verification", "approved", actor="Wei", reason="reject", reject_provisional="b_plan",
               rejection_feedback="change palette")
    assert eng.state.controls["design"].status == "draft"
    ctx = eng._context(eng.node("b_plan"), eng.rt("b_plan").attempts + 1, eng.rt("b_plan").fingerprint or "fp", "tok")
    packet = TaskPacketExecutor().execute(ctx)
    packet_path = proj / packet.task_packet
    assert packet_path.exists() and "Wei's rejection of the provisional approval (verbatim): change palette" in packet_path.read_text()
    eng = Engine(proj, executor=FnExecutor({"b_plan": write_design}))
    eng.run(max_steps=300)
    assert "attempt 2" in (proj / "controls/design.md").read_text()
    assert not [e for e in Ledger(proj / ".vpg/ledger.jsonl").events() if "without an approved change request" in json.dumps(e["data"])]
    assert not [f for f in eng.state.evaluation.feedback if f.hold or f.status == "open"]
    ctx3 = eng._context(eng.node("b_plan"), eng.rt("b_plan").attempts + 1, eng.rt("b_plan").fingerprint or "fp", "tok3")
    packet3 = TaskPacketExecutor().execute(ctx3)
    assert "rejection of the provisional approval" not in (proj / packet3.task_packet).read_text()
    eng.decide("b_plan", "approved", actor="Wei", reason="ok")
    eng.run(max_steps=300)
    if eng.rt("n23_final_verification").status == S.STALE:
        eng.run(max_steps=300)
    assert eng.rt("n23_final_verification").status == S.WAITING_HUMAN


def test_relayed_signoff_requires_principal_and_instruction(tmp_path):
    proj = make_project(tmp_path, workflow=release_workflow(tmp_path))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError, match="--on-behalf-of must be one of"):
        eng._require_human("agent:release", "release signoff", instruction="sign VOI-ADULT")
    with pytest.raises(EngineError, match="needs --instruction"):
        eng._require_human("agent:release", "release signoff", on_behalf_of="Wei")
    eng.rt(eng._release_node()).status = S.WAITING_HUMAN
    meta = eng._require_human("agent:release", "release signoff", on_behalf_of="Wei", instruction="sign VOI-ADULT")
    assert meta["relayed"] is True and meta["principal"] == "Wei"


def test_relayed_confirm_requires_fresh_instruction_at(tmp_path):
    proj = make_project(tmp_path, workflow=release_workflow(tmp_path))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.state.history.approvals.append(Approval(node="b_plan", decision="approved", actor="agent:a", human=False, at=now(),
                                                provisional=True, relayed=True, principal="Wei", instruction="go"))
    eng.rt("n23_final_verification").status = S.WAITING_HUMAN
    eng.rt("n23_final_verification").started_at = "2026-09-30T12:00:00+00:00"
    with pytest.raises(EngineError, match="instruction-at"):
        eng.decide("n23_final_verification", "approved", actor="agent:a", on_behalf_of="Wei", instruction="ship",
                   reason="ship", confirm_provisional="all")
    with pytest.raises(EngineError, match="ISO timestamp"):
        eng.decide("n23_final_verification", "approved", actor="agent:a", on_behalf_of="Wei", instruction="ship",
                   instruction_at="whenever", reason="ship", confirm_provisional="all")
    with pytest.raises(EngineError, match="later"):
        eng.decide("n23_final_verification", "approved", actor="agent:a", on_behalf_of="Wei", instruction="ship",
                   instruction_at="2026-09-30T11:59:59+00:00", reason="ship", confirm_provisional="all")


def test_superseded_provisional_approval_confirm_all_clears_latest(tmp_path, monkeypatch):
    import vpg.engine as ve
    monkeypatch.setattr(ve, "PROVISIONAL_GATES", {"b_plan"})
    proj = make_project(tmp_path, human_b=True)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
                "reviews": {"binding": "require", "independence": "require"},
                "loop_budget": {"on_exhaust": "extend_if_converging"},
                "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="agent:a", on_behalf_of="Wei", instruction="go", reason="p", provisional=True)
    eng.invalidate_nodes(["b_plan"], reason="again", actor="Wei")
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="agent:a", on_behalf_of="Wei", instruction="go", reason="p2", provisional=True)
    assert len(eng._standing_provisional()) == 1
    eng._handle_release_provisional_controls("all", "", "", "Wei", "", "", "", now())
    assert not eng._standing_provisional()


# The grantable acts as _authorize sees them: (action, the sentence the engine passes). #185: the relay scope is decided by
# the action, so these go through _authorize, the path every engine caller takes, never _require_human with a bare word.
GRANTABLE_ACTS = [("unblock", "unblocking"),
                  ("invalidate", "invalidating b_plan re-opens a human's decision (b_plan), which"),
                  ("change-request", "approving a change request"), ("resolve", "resolving a defect"),
                  ("restore-control", "restoring a control document"), ("feedback-triage", "triaging an agent's note")]


@pytest.mark.parametrize("action,what", GRANTABLE_ACTS + [("", "waiving an intake gap")])
def test_declared_human_actions_refuse_free_text_and_accept_structured_relay(tmp_path, action, what):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError, match="--actor must be exactly one of the declared humans"):
        eng._require_human("Wei (standing instruction)", what)
    if not action:
        with pytest.raises(EngineError, match="refused mid-run"):
            eng._require_human("agent:ops", what, on_behalf_of="Wei", instruction="do this")
    else:
        assert eng._authorize("agent:ops", action, what, on_behalf_of="Wei", instruction="do this") is None
        assert eng.ledger.stamp["principal"] == "Wei" and eng.ledger.stamp["relayed"] is True


@pytest.mark.parametrize("action", ["granting a delegation", "accepting a configuration change", "a rollback", "accepting a ledger break",
                                    "a human decision", "waiving an intake gap"])
def test_mid_run_relays_are_refused_for_non_grantable_human_actions(tmp_path, action):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError, match="release|accept-config|refused mid-run"):
        eng._require_human("agent:ops", action, on_behalf_of="Wei", instruction="do this")


def test_waiver_path_refuses_free_text_declared_human(tmp_path):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError, match="declared humans"):
        eng._require_human("Wei (standing instruction)", "waiving an intake gap")


def test_v17_project_gets_clear_new_release_refusals(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    path = proj / "work/verification.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"warnings": ["check"], "pending_human": ["VOI-ADULT"]}))
    eng.state.artifacts["verification-test"] = ArtifactRecord(
        id="verification-test", key="verification", path="work/verification.json", sha256=sha256_file(path),
        bytes=path.stat().st_size, node="n23_final_verification", attempt=1, produced_at=now())
    with pytest.raises(EngineError, match="--ack-warning"):
        eng._check_release_structure([], ["VOI-ADULT"])
    with pytest.raises(EngineError, match="--signoff"):
        eng._check_release_structure(["all"], [])
    from vpg.tools.privacy import release_problem
    ctx = SimpleNamespace(project_dir=proj, config={}, workdir=proj / "work")
    assert "privacy.names" in release_problem(ctx, path, sha256_file(path))


def test_custom_release_node_enforces_provisional_and_structured_release(tmp_path):
    proj = make_project(tmp_path, workflow=release_workflow(tmp_path, release_id="ship_release"))
    eng = Engine(proj, executor=FnExecutor())
    eng.state.history.approvals.append(Approval(node="b_plan", decision="approved", actor="agent:a", human=False, at=now(),
                                                provisional=True, relayed=True, principal="Wei", instruction="go"))
    eng.rt("ship_release").status = S.WAITING_HUMAN
    add_verification_artifact(eng, warnings=["check"], pending=["VOI-ADULT"])
    with pytest.raises(EngineError, match="standing provisional"):
        eng.decide("ship_release", "approved", actor="Wei", reason="ship", ack_warnings=["all"], signoffs=["VOI-ADULT"])
    eng._handle_release_provisional_controls("all", "", "", "Wei", "", "", "", now())
    with pytest.raises(EngineError, match="--ack-warning"):
        eng.decide("ship_release", "approved", actor="Wei", reason="ship", signoffs=["VOI-ADULT"])
    with pytest.raises(EngineError, match="--signoff"):
        eng.decide("ship_release", "approved", actor="Wei", reason="ship", ack_warnings=["all"])


def test_cli_release_approve_with_ack_warning_and_signoff_on_default_workflow(tmp_path):
    proj = tmp_path / "film"
    assert cli.main(["init", str(proj), "--name", "film"]) == 0
    eng = Engine(proj, executor=FnExecutor())
    eng.rt("n23_final_verification").status = S.WAITING_HUMAN
    add_verification_artifact(eng, warnings=["review warning"], pending=["VOI-ADULT", "CRE-MUSIC"])
    eng._save("test")
    assert cli.main(["approve", str(proj), "n23_final_verification", "--actor", "Wei", "--reason", "ship",
                     "--ack-warning", "all", "--signoff", "VOI-ADULT,CRE-MUSIC"]) == 0
    eng = Engine(proj, read_only=True)
    approval = eng.state.history.approvals[-1]
    assert approval.node == "n23_final_verification" and approval.warning_acks == ["1"]
    assert approval.signoffs == ["CRE-MUSIC", "VOI-ADULT"]


def test_pre_v18_state_loads_accepts_config_and_steps(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei (standing instruction)", reason="go")
    raw = json.loads((proj / ".vpg/state.json").read_text())
    raw["runtime"].pop("accepted_policy", None)
    raw["runtime"].pop("accepted_config", None)
    for approval in raw["history"]["approvals"]:
        for key in ("provisional", "confirmed_at", "superseded_at", "pre_lock_controls", "warning_acks", "signoffs"):
            approval.pop(key, None)
    (proj / ".vpg/state.json").write_text(json.dumps(raw))
    eng = Engine(proj, executor=FnExecutor())
    assert eng.state.history.approvals[0].actor == "Wei (standing instruction)"
    eng.accept_config(actor="Wei", reason="adopt")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value in ("RUN_PASS", "RUNNING", "WAITING_RUNTIME", "WAITING_HUMAN")


def test_release_structure_fails_closed_on_missing_or_drifted_verification(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    path = proj / "work/verification.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"warnings": ["check"], "pending_human": []}))
    eng.state.artifacts["verification-test"] = ArtifactRecord(
        id="verification-test", key="verification", path="work/verification.json", sha256=sha256_file(path),
        bytes=path.stat().st_size, node="n23_final_verification", attempt=1, produced_at=now())
    path.write_text(json.dumps({"warnings": [], "pending_human": []}))
    with pytest.raises(EngineError, match="changed on disk"):
        eng._check_release_structure([], [])
    path.unlink()
    with pytest.raises(EngineError, match="missing"):
        eng._check_release_structure([], [])


def test_feedback_declared_humans_and_relays(tmp_path):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError, match="declared humans"):
        eng.add_feedback("make it bluer", actor="Wei (standing instruction)")
    fb = eng.add_feedback("make it shorter", actor="agent:runner", on_behalf_of="Wei", instruction="shorter")
    ev = list(Ledger(proj / ".vpg/ledger.jsonl").events("feedback"))[-1]["data"]
    assert fb.actor == "Wei" and fb.triage == "" and ev["relayed"] is True and ev["relay_actor"] == "agent:runner"


@pytest.mark.parametrize("action", [
    "granting a delegation", "accepting a configuration change", "unblocking", "invalidating",
    "approving a change request", "resolving a defect", "restoring a control document", "triaging an agent's note",
    "waiving an intake gap", "a rollback", "accepting a ledger break",
])
def test_relays_allowed_for_human_actions_at_waiting_release(tmp_path, action):
    proj = make_project(tmp_path, workflow=release_workflow(tmp_path))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.rt("n23_final_verification").status = S.WAITING_HUMAN
    meta = eng._require_human("agent:ops", action, on_behalf_of="Wei", instruction="do it")
    assert meta["relayed"] is True and eng.ledger.stamp["principal"] == "Wei"


@pytest.mark.parametrize("action,what", GRANTABLE_ACTS)
def test_grantable_relays_allowed_mid_run(tmp_path, action, what):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    assert not eng._at_release_wait()
    assert eng._authorize("agent:ops", action, what, on_behalf_of="Wei", instruction="do it") is None
    assert eng.ledger.stamp["principal"] == "Wei"


def test_relayed_safety_accept_config_allowed_but_broad_config_refused(tmp_path):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.accept_config(actor="Wei", reason="declare")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["loop_budget"] = {"on_exhaust": "extend_if_converging"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    assert eng._require_human("agent:ops", "accepting a configuration change", on_behalf_of="Wei", instruction="safety")["principal"] == "Wei"
    eng = Engine(proj, executor=FnExecutor())
    eng.accept_config(actor="Wei", reason="accept safety")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["commands"] = {"b_plan": "echo x"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    with pytest.raises(EngineError, match="safety-key-only"):
        eng._require_human("agent:ops", "accepting a configuration change", on_behalf_of="Wei", instruction="broad")


def test_relayed_threshold_change_request_refused_mid_run(tmp_path):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    cr = eng.change_request("evaluation", "lower threshold", by="agent:critic")
    with pytest.raises(EngineError, match="bar changes"):
        eng.approve_change_request(cr.id, by="agent:ops", on_behalf_of="Wei", instruction="approve")
    with pytest.raises(EngineError, match="bar changes"):
        eng.reject_change_request(cr.id, by="agent:ops", reason="reject", on_behalf_of="Wei", instruction="reject")
    with pytest.raises(EngineError, match="bar changes"):
        eng.defer_change_request(cr.id, by="agent:ops", until="later", reason="defer", on_behalf_of="Wei", instruction="defer")


def test_adoption_relay_instruction_at_validation(tmp_path):
    proj = make_project(tmp_path, workflow=release_workflow(tmp_path, human_b=True))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="ok")
    eng.run(max_steps=300)
    cfg.update({"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
                "reviews": {"binding": "require", "independence": "require"},
                "loop_budget": {"on_exhaust": "extend_if_converging"},
                "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    with pytest.raises(EngineError, match="ISO timestamp"):
        Engine(proj, executor=FnExecutor()).accept_config(actor="agent:ops", reason="adopt", on_behalf_of="Wei",
                                                          instruction="adopt", instruction_at="whenever")
    with pytest.raises(EngineError, match="timezone"):
        Engine(proj, executor=FnExecutor()).accept_config(actor="agent:ops", reason="adopt", on_behalf_of="Wei",
                                                          instruction="adopt", instruction_at="2099-01-01T10:00:00")
    with pytest.raises(EngineError, match="later"):
        Engine(proj, executor=FnExecutor()).accept_config(actor="agent:ops", reason="adopt", on_behalf_of="Wei",
                                                          instruction="adopt", instruction_at="2000-01-01T10:00:00+00:00")
    later_at = "2099-01-01T10:00:00+00:00"
    Engine(proj, executor=FnExecutor()).accept_config(actor="agent:ops", reason="adopt", on_behalf_of="Wei",
                                                      instruction="adopt", instruction_at=later_at)
    assert Engine(proj, read_only=True).state.runtime.accepted_policy["humans"] == ["Wei"]


# ---------------------------------------------------------------------------------------------- printed commands run as written (judge, J05)
def test_the_printed_gate_commands_run_as_written(tmp_path, monkeypatch):
    """Judge regression: once `approve` required --reason, the reject line `vpg tasks` printed failed with exit 2."""
    monkeypatch.chdir(PKG)                                          # humans and agents run vpg from the package root
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)

    def printed(kind):
        line = next(ln.strip() for ln in cli._human_gate_lines(Engine(proj, read_only=True), "b_plan") if ln.strip().startswith(kind + ":"))
        cmd = re.sub(r"\s+\(records '[^']*'\)$", "", line.split(":", 1)[1].strip())
        return shlex.split(cmd.replace("<human>", "Wei").replace("<their words>", "too long").replace("<what to change>", "cut it"))[3:]
    assert cli.main(printed("reject")) == 0
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)                                          # the rejection loops back and the gate asks again
    assert cli.main(printed("approve")) == 0
    decisions = [e["data"] for e in Ledger(proj / ".vpg/ledger.jsonl").events("decision")]
    assert [(d["decision"], d["reason"]) for d in decisions] == [("rejected", "too long"), ("approved", "too long")]


def test_a_packet_never_asks_for_a_tool_written_output(tmp_path, monkeypatch):
    """Judge regression: review and learning packets still told the agent to submit the key its tool had written, which
    submit now refuses. The packet marks it and its submit line leaves it out, so the line runs as written."""
    monkeypatch.chdir(PKG)
    wf = mini_workflow(tmp_path)
    data = yaml.safe_load(wf.read_text())
    next(n for n in data["nodes"] if n["id"] == "b_plan")["run"] = {"tool": "tests.fakes:produce"}
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    submit_packet(eng, proj, "a_intake")
    eng.run(max_steps=20)
    packet = (proj / eng.rt("b_plan").task_packet).read_text()
    line = next(ln for ln in packet.splitlines() if ln.startswith("python -m vpg submit"))
    assert "--output b_plan=" not in line and "already written by this node's tool check" in packet
    assert cli.main(shlex.split(line)[3:]) == 0
    assert Engine(proj, read_only=True).rt("b_plan").status.value == "STEP_PASS"


def test_a_relative_review_path_is_found_in_the_work_directory(tmp_path, monkeypatch):
    """Judge finding: `--review review.json` was read from the current directory, while `--output` paths resolve against
    the packet's work directory, so a review written where the packet says was not found."""
    monkeypatch.chdir(PKG)
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    for nid in ("a_intake", "b_plan", "d_build"):
        submit_packet(eng, proj, nid)
        eng.run(max_steps=20)
    rt = eng.rt("r1_review")
    assert rt.status.value == "WAITING_RUNTIME"
    dims = [d["id"] for r in eng.node("r1_review").rubric for d in eng.rubrics[r]["dimensions"] if d["check"]["type"] == "review"]
    workdir = proj / "work/r1_review" / f"a{rt.attempts}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "review.json").write_text(json.dumps({"reviewer": "critic", "scores": [
        {"dimension": d, "score": 5, "evidence": "checked every frame"} for d in dims]}))
    assert not (PKG / "review.json").exists()
    assert cli.main(["submit", str(proj), "r1_review", "--token", rt.dispatch_token, "--actor", "agent:test",
                     "--review", "review.json"]) == 0
    assert Engine(proj, read_only=True).rt("r1_review").status.value == "STEP_PASS"


# ---------------------------------------------------------------------------------------------- v1.2: human feedback is never dismissed silently
def test_human_feedback_closed_with_no_action_waits_for_its_author(tmp_path):
    """Monitor finding (the SRA run): the interpreter could close Wei's 'v3 is even worse' with a no-action reason, and the
    gate then passed that version and opened the release. A human's feedback closed with no action is held: no version
    passes until its author, or the gate's owner, accepts the dismissal by unblocking the gate."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    eng.add_feedback("no improvement from v1; this version is even worse", actor="Maria")
    eng.run(max_steps=200)
    fb, g = eng.state.evaluation.feedback[0], eng.state.evaluation.gate_results[-1]
    assert eng.state.runtime.status.value == "BLOCKED" and g.outcome == "blocked" and fb.hold
    assert "leads to no open blocking defect" in g.reasons[0] and any("on hold" in r and "F001" in r for r in g.reasons)
    assert any(h.startswith("F001: ") for h in eng.status()["holds"])
    assert events(proj, "feedback_held")[0] == {"id": "F001", "actor": "Maria", "reason": "test"}
    with pytest.raises(EngineError, match="F001.*Wei|Wei.*F001"):
        eng.unblock("g_gate", actor="Sam Placeholder", note="looks fine to me")   # neither the author nor the gate's owner
    assert eng.state.evaluation.feedback[0].hold
    eng.run(max_steps=200)
    assert eng.state.evaluation.gate_results[-1].outcome == "blocked"
    eng.unblock("g_gate", actor="Wei", note="as the owner I accept: no change")   # the gate's owner (escalation.to)
    assert events(proj, "unblocked")[-1]["released_holds"] == ["F001"]
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and not eng.state.evaluation.feedback[0].hold


def test_a_held_feedback_blocks_only_the_pass_not_the_rework(tmp_path):
    """The hold stops a pass, not the loop: while other defects are open, rework still routes."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.add_feedback("the music is too loud", actor="Maria")
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert [g.outcome for g in eng.state.evaluation.gate_results] == ["rework", "blocked"]
    assert eng.state.evaluation.defects[0].status == "resolved" and eng.state.evaluation.feedback[0].hold
    eng.unblock("g_gate", actor="Maria", note="the mix is fine as it is")   # the author accepts it
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


def test_feedback_hold_unblock_accepts_relay_principal_or_exact_author(tmp_path):
    proj = make_project(tmp_path, review_tool=ROUTES)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei", "Pat Example"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))

    def no_action(ctx):
        fb = [f.id for f in ctx.state.evaluation.feedback if f.status == "open"]
        (ctx.workdir / "interpretation.json").write_text(json.dumps(
            {"defects": [], "feedback": [{"id": f, "no_action_reason": "already fixed"} for f in fb]}))
        return {"interpretation": "interpretation.json"}

    eng = Engine(proj, executor=FnExecutor({"g_gate": no_action}))
    eng.run(max_steps=200)
    eng.add_feedback("the proof point is now clear", actor="Wei")
    eng.run(max_steps=200)
    gate = eng._gate_node()
    with pytest.raises(EngineError, match="--actor must be one of Wei"):
        eng.unblock(gate, actor="agent:relay", on_behalf_of="Pat Example", instruction="continue")
    eng.unblock(gate, actor="agent:relay", on_behalf_of="Wei", instruction="continue", note="relayed")
    assert events(proj, "unblocked")[-1]["released_holds"] == ["F001"]

    proj2 = make_project(tmp_path / "exact", review_tool=ROUTES)
    eng2 = Engine(proj2, executor=FnExecutor({"g_gate": no_action}))
    eng2.run(max_steps=200)
    eng2.add_feedback("the proof point is now clear", actor="Wei")
    eng2.run(max_steps=200)
    eng2.unblock(eng2._gate_node(), actor="Wei", note="exact author")
    assert events(proj2, "unblocked")[-1]["released_holds"] == ["F001"]


def test_feedback_mapped_to_a_defect_is_not_held(tmp_path):
    """Only a dismissal waits for the human; feedback that became a defect routes as usual."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    handlers = {"g_gate": _interpret_once({"dimension": "CRE-ARC", "severity": "P2", "description": "the middle sags",
                                           "traced_node": "d_build"})}
    eng = Engine(proj, executor=FnExecutor(handlers))
    eng.run(max_steps=200)
    eng.add_feedback("the middle sags", actor="Wei")
    eng.run(max_steps=10)
    assert not eng.state.evaluation.feedback[0].hold and eng.state.evaluation.feedback[0].defect_ids
    assert eng.state.evaluation.gate_results[-1].outcome == "rework"


def test_human_feedback_cannot_be_closed_on_a_closed_defect_or_a_nit(tmp_path):
    """Reviewer question: mapping a human's feedback to an already-resolved defect, to a new nit, or to an unknown id
    would also have dismissed it. Feedback that leads to no open blocking defect is held; an unknown id fails."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    fixed = eng.state.evaluation.defects[0]
    assert fixed.status == "resolved" and eng.state.runtime.status.value == "RUN_PASS"
    plans = {"closed": {"defects": [], "feedback": [{"id": "F001", "defect_ids": [fixed.id]}]},
             "nit": {"defects": [{"dimension": "CRE-ARC", "severity": "nit", "description": "a small thing",
                                  "traced_node": "d_build", "feedback": "F002"}], "feedback": [{"id": "F002"}]},
             "unknown": {"defects": [], "feedback": [{"id": "F003", "defect_ids": ["D999"]}]}}
    for fid, plan in (("F001", "closed"), ("F002", "nit"), ("F003", "unknown")):
        def g_gate(ctx, plan=plan):
            (ctx.workdir / "interpretation.json").write_text(json.dumps(plans[plan]))
            return {"interpretation": "interpretation.json"}
        eng = Engine(proj, executor=FnExecutor({"g_gate": g_gate}))
        eng.add_feedback(f"feedback for the {plan} case", actor="Wei")
        eng.run(max_steps=200)
        fb = next(f for f in eng.state.evaluation.feedback if f.id == fid)
        if plan == "unknown":
            assert fb.status == "open" and any("unknown defect(s) ['D999']" in x for x in eng.rt("g_gate").failures)
        else:
            assert fb.hold and eng.state.evaluation.gate_results[-1].outcome == "blocked", plan
            eng.unblock("g_gate", actor="Wei", note=f"accept the {plan} mapping")


def test_principal_feedback_resolved_under_provisional_does_not_hold_gate(tmp_path):
    def no_action(ctx):
        fb = [f.id for f in ctx.state.evaluation.feedback if f.status == "open"]
        (ctx.workdir / "interpretation.json").write_text(json.dumps(
            {"defects": [], "feedback": [{"id": f, "no_action_reason": "fixed with evidence"} for f in fb]}))
        return {"interpretation": "interpretation.json"}

    proj = make_project(tmp_path, review_tool=ROUTES)
    eng = Engine(proj, executor=FnExecutor({"g_gate": no_action}))
    eng.state.runtime.accepted_policy = {"autonomy": {"mode": "provisional", "principal": "Wei"}, "humans": ["Wei"]}
    eng.run(max_steps=200)
    eng.add_feedback("the evidence label is wrong", actor="Wei")
    eng.run(max_steps=200)
    fb = eng.state.evaluation.feedback[0]
    assert (fb.status, fb.hold, fb.no_action_reason) == ("resolved_pending_confirmation", "", "fixed with evidence")
    assert eng.state.runtime.status.value == "RUN_PASS"
    assert events(proj, "feedback_pending_confirmation")[-1]["id"] == "F001"

    proj2 = make_project(tmp_path / "gated", review_tool=ROUTES)
    eng2 = Engine(proj2, executor=FnExecutor({"g_gate": no_action}))
    eng2.run(max_steps=200)
    eng2.add_feedback("the evidence label is wrong", actor="Wei")
    eng2.run(max_steps=200)
    assert eng2.state.evaluation.feedback[0].hold
    assert eng2.state.runtime.status.value == "BLOCKED"


def test_non_principal_feedback_still_holds_under_provisional(tmp_path):
    def no_action(ctx):
        fb = [f.id for f in ctx.state.evaluation.feedback if f.status == "open"]
        (ctx.workdir / "interpretation.json").write_text(json.dumps(
            {"defects": [], "feedback": [{"id": f, "no_action_reason": "already fine"} for f in fb]}))
        return {"interpretation": "interpretation.json"}

    proj = make_project(tmp_path, review_tool=ROUTES)
    eng = Engine(proj, executor=FnExecutor({"g_gate": no_action}))
    eng.state.runtime.accepted_policy = {"autonomy": {"mode": "provisional", "principal": "Wei"}, "humans": ["Wei", "Pat Example"]}
    eng.run(max_steps=200)
    eng.add_feedback("labels are wrong", actor="Pat Example")
    eng.run(max_steps=200)
    fb = eng.state.evaluation.feedback[0]
    assert fb.status == "interpreted" and fb.hold
    assert eng.state.runtime.status.value == "BLOCKED"


def _declare_privacy_name(proj, name="Jordan Sample"):
    (proj / "names.json").write_text(json.dumps({"names": [name]}))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    hold = proj.parent / "hold"
    hold.mkdir(exist_ok=True)
    cfg["privacy"] = {"names": "names.json", "text_scan": "on", "n14_hold": "on", "hold_dir": str(hold.resolve())}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    return name


def test_submit_refuses_text_output_with_listed_name_before_attempt_used(tmp_path):
    proj = make_project(tmp_path)
    listed = _declare_privacy_name(proj)
    eng = Engine(proj)
    eng.run(max_steps=1)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake/a1/a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"speaker": listed}))
    with pytest.raises(EngineError, match=r"no attempt used: privacy text scan.*\[list entry #1\]"):
        eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:test")
    assert eng.rt("a_intake").status == S.WAITING_RUNTIME and eng.rt("a_intake").attempts == 1


def test_text_scan_folds_json_escapes_nfd_and_ascii_spelling(tmp_path):
    listed = "Zoë Núñez"
    for idx, text in enumerate((json.dumps({"speaker": listed}), json.dumps({"speaker": unicodedata.normalize('NFD', listed)}),
                                json.dumps({"speaker": "Zoe Nunez"}))):
        proj = make_project(tmp_path / str(idx))
        _declare_privacy_name(proj, listed)
        eng = Engine(proj)
        eng.run(max_steps=1)
        rt = eng.rt("a_intake")
        out = proj / "work/a_intake/a1/a_intake.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        with pytest.raises(EngineError, match=r"\[list entry #1\]"):
            eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:test")


def test_text_scan_uses_frame_matcher_variants():
    cases = [("Jordan Sample", "Sample, Jordan"), ("Jordan Sample", "JordanSample"),
             ("Jordan Sample", "jordan_sample"), ("Ana Smith-Jones", "Ana Smith-Jones"),
             ("Ana O'Brien", "Ana O'Brien")]
    for name, text in cases:
        full, part = privacy.text_privacy_hits(text, [name])
        assert full == [1] and part == []


def test_text_scan_part_only_hit_becomes_end_review_warning(tmp_path):
    proj = make_project(tmp_path)
    _declare_privacy_name(proj, "Jordan Sample")
    eng = Engine(proj)
    eng.run(max_steps=1)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake/a1/a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"note": "attach a sample report"}))
    eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:test")
    assert eng.rt("a_intake").status == S.STEP_PASS
    assert events(proj, "privacy_text_warning")[-1]["warning"].endswith("[list entry #1]")


def test_text_scan_refuses_undecodable_text_and_names_file_output(tmp_path):
    proj = make_project(tmp_path)
    _declare_privacy_name(proj)
    eng = Engine(proj)
    eng.run(max_steps=1)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake/a1/a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\xff\xfe")
    with pytest.raises(EngineError, match="not valid JSON|not UTF-8"):
        eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:test")
    with pytest.raises(EngineError, match="cannot register the privacy name list"):
        eng.submit("a_intake", {"a_intake": str(proj / "names.json")}, token=rt.dispatch_token, actor="agent:test")


def test_submit_allows_preexisting_control_doc_with_listed_name(tmp_path):
    proj = make_project(tmp_path, lock_b=("design",))
    listed = _declare_privacy_name(proj)
    (proj / "controls/design.md").write_text(f"# Design\n\nOwner: {listed}\n")
    eng = Engine(proj)
    eng.run(max_steps=10)
    submit_packet(eng, proj, "a_intake")
    eng.run(max_steps=10)
    assert eng.rt("b_plan").status == S.WAITING_RUNTIME
    submit_packet(eng, proj, "b_plan")
    assert eng.rt("b_plan").status == S.STEP_PASS


def test_submit_allows_clean_text_when_names_declared(tmp_path):
    proj = make_project(tmp_path)
    _declare_privacy_name(proj)
    eng = Engine(proj)
    eng.run(max_steps=1)
    submit_packet(eng, proj, "a_intake")
    assert eng.rt("a_intake").status == S.STEP_PASS


def test_command_written_text_output_is_scanned(tmp_path):
    wf_dir = tmp_path / "wf"
    wf_dir.mkdir()
    wf = mini_workflow(wf_dir)
    data = yaml.safe_load(wf.read_text())
    for node in data["nodes"]:
        if node["id"] == "a_intake":
            node["outputs"].append({"key": "notes", "kind": "markdown", "description": "notes", "required": False})
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    listed = _declare_privacy_name(proj)
    (proj / "tools").mkdir()
    (proj / "tools/cmd.py").write_text(
        "import json, os, pathlib\n"
        "w = pathlib.Path(json.load(open(os.environ['VPG_CONTEXT']))['workdir'])\n"
        f"(w / 'notes.md').write_text('Speaker: {listed}\\n')\n"
        "(w / 'outputs.json').write_text(json.dumps({'outputs': {'notes': 'notes.md'}}))\n")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["commands"] = {"a_intake": "{python} tools/cmd.py"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj)
    eng.run(max_steps=1)
    submit_packet(eng, proj, "a_intake")
    assert eng.rt("a_intake").status == S.ITERATE
    assert any("[list entry #1]" in f for f in eng.rt("a_intake").failures)


def test_review_payload_is_scanned_before_state(tmp_path):
    proj = make_project(tmp_path)
    listed = _declare_privacy_name(proj)
    eng = Engine(proj)
    eng.run(max_steps=10)
    for nid in ("a_intake", "b_plan", "d_build"):
        submit_packet(eng, proj, nid)
        eng.run(max_steps=10)
    rt = eng.rt("r1_review")
    dims = [x for r in eng.node("r1_review").rubric for x in eng.rubrics[r]["dimensions"] if x["check"]["type"] == "review"]
    review = {"reviewer": "critic", "scores": [{"dimension": x["id"], "score": 5, "evidence": "ok"} for x in dims],
              "defects": [{"dimension": dims[0]["id"], "severity": "P2", "traced_node": "d_build",
                           "description": f"frame shows {listed}"}]}
    with pytest.raises(EngineError, match=r"review payload.*\[list entry #1\]"):
        eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:critic", review=review)
    assert listed not in (proj / ".vpg/state.json").read_text()


def _master_project(tmp_path):
    (tmp_path / "wf").mkdir(parents=True)
    wf = mini_workflow(tmp_path / "wf")
    data = yaml.safe_load(wf.read_text())
    for node in data["nodes"]:
        if node["id"] == "d_build":
            node["outputs"] = [{"key": "master", "kind": "video", "description": "Master",
                                "state_path": "production.master_ref"}]
            node["completion"] = [{"type": "artifact", "key": "master", "description": "exists"}]
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    (proj / "names.json").write_text(json.dumps({"names": ["Jordan Sample"]}))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    hold = tmp_path / "hold"
    hold.mkdir(exist_ok=True)
    cfg["privacy"] = {"names": "names.json", "n14_hold": "on", "hold_dir": str(hold.resolve())}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    return proj


def _master_project_with_silent(tmp_path):
    (tmp_path / "wf").mkdir(parents=True)
    wf = mini_workflow(tmp_path / "wf")
    data = yaml.safe_load(wf.read_text())
    for node in data["nodes"]:
        if node["id"] == "d_build":
            node["outputs"] = [{"key": "master", "kind": "video", "description": "Master",
                                "state_path": "production.master_ref"},
                               {"key": "silent", "kind": "video", "description": "Silent",
                                "state_path": "production.refs.silent"}]
            node["completion"] = [{"type": "artifact", "key": "master", "description": "exists"}]
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    (proj / "names.json").write_text(json.dumps({"names": ["Jordan Sample"]}))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    hold = tmp_path / "hold"
    hold.mkdir(exist_ok=True)
    cfg["privacy"] = {"names": "names.json", "n14_hold": "on", "hold_dir": str(hold.resolve())}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    return proj


def _drive_to_master_submit(eng, proj):
    eng.run(max_steps=10)
    submit_packet(eng, proj, "a_intake")
    eng.run(max_steps=10)
    submit_packet(eng, proj, "b_plan")
    eng.run(max_steps=10)
    assert eng.rt("d_build").status == S.WAITING_RUNTIME
    rt = eng.rt("d_build")
    master = proj / "work/d_build/a1/master.mp4"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"synthetic")
    return rt, master


def test_privacy_failing_master_is_held_outside_repo_before_registration(tmp_path, monkeypatch):
    proj = _master_project(tmp_path)
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: {"frames": 1, "fps": 30, "ocr_calls": 1,
                                                                "windows": [{"entry": 1, "start_s": 0, "end_s": 1,
                                                                             "reads": 1, "box": [1, 2, 3, 4]}],
                                                                "live_windows": []})
    eng = Engine(proj)
    rt, master = _drive_to_master_submit(eng, proj)
    eng.submit("d_build", {"master": str(master)}, token=rt.dispatch_token, actor="agent:test")
    hold = tmp_path / "hold"
    held = list(hold.glob("*.mp4"))
    assert held and held[0].is_file() and not held[0].is_relative_to(proj)
    assert not master.exists()
    assert not eng.current("master")
    assert oct(hold.stat().st_mode & 0o777) == "0o700"
    assert any("privacy scan failed before video registration" in f for f in eng.rt("d_build").failures)


def test_privacy_passing_master_registers_current(tmp_path, monkeypatch):
    proj = _master_project(tmp_path)
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: {"frames": 1, "fps": 30, "ocr_calls": 1,
                                                                "windows": [], "live_windows": []})
    eng = Engine(proj)
    rt, master = _drive_to_master_submit(eng, proj)
    eng.submit("d_build", {"master": str(master)}, token=rt.dispatch_token, actor="agent:test")
    assert eng.current("master")
    assert master.exists()


def test_privacy_scan_cache_is_ledgered_but_n23_still_scans(tmp_path, monkeypatch):
    proj = _master_project(tmp_path)
    calls = {"n": 0}
    def clean(*a, **k):
        calls["n"] += 1
        return {"frames": 1, "fps": 30, "ocr_calls": 1, "windows": [], "live_windows": []}
    monkeypatch.setattr(privacy, "scan_video", clean)
    eng = Engine(proj)
    rt, master = _drive_to_master_submit(eng, proj)
    eng.submit("d_build", {"master": str(master)}, token=rt.dispatch_token, actor="agent:test")
    rec = eng.current("master")[0]
    ctx = SimpleNamespace(config=eng.project.config, project_dir=proj, workdir=proj / "work/n23", load=lambda k: None)
    assert privacy.release_problem(ctx, proj / rec.path, rec.sha256) is None
    assert calls["n"] == 3
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: {"frames": 1, "fps": 30, "ocr_calls": 1,
                                                                "windows": [{"entry": 1, "start_s": 0, "end_s": 1,
                                                                             "reads": 1, "box": [1, 2, 3, 4]}],
                                                                "live_windows": []})
    ctx.config = {**eng.project.config, "privacy": {**eng.project.config["privacy"], "scan_fps": 60}}
    assert privacy.release_problem(ctx, proj / rec.path, rec.sha256)
    ctx.config = eng.project.config
    ctx.load = lambda k: {"live_windows": [{"start": 0, "end": 1, "what": "ui"}]} if k == "text_layers" else None
    assert privacy.release_problem(ctx, proj / rec.path, rec.sha256)
    (proj / "names.json").write_text(json.dumps({"names": ["Jordan Sample", "Riley Example"]}))
    ctx.load = lambda k: None
    assert privacy.release_problem(ctx, proj / rec.path, rec.sha256)


def test_privacy_hold_dir_must_be_absolute_and_outside_git(tmp_path):
    proj = _master_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["privacy"]["hold_dir"] = "relative"
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(EngineError, match="privacy.hold_dir"):
        Engine(proj)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    proj2 = _master_project(tmp_path / "other")
    cfg = yaml.safe_load((proj2 / "vpg.project.yaml").read_text())
    cfg["privacy"]["hold_dir"] = str((repo / "held").resolve())
    (proj2 / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(EngineError, match="git working tree"):
        Engine(proj2)


def test_privacy_holds_silent_video_and_never_moves_sources(tmp_path, monkeypatch):
    proj = _master_project_with_silent(tmp_path)
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: {"frames": 1, "fps": 30, "ocr_calls": 1,
                                                                "windows": [{"entry": 1, "start_s": 0, "end_s": 1,
                                                                             "reads": 1, "box": [1, 2, 3, 4]}],
                                                                "live_windows": []})
    eng = Engine(proj)
    rt, master = _drive_to_master_submit(eng, proj)
    silent = master.with_name("silent.mp4")
    silent.write_bytes(b"synthetic")
    source = proj / "assets/source-recording.mp4"
    source.parent.mkdir()
    source.write_bytes(b"source")
    eng.submit("d_build", {"master": str(source), "silent": str(silent)}, token=rt.dispatch_token, actor="agent:test")
    assert source.exists()
    assert not silent.exists()
    assert len(list((tmp_path / "hold").glob("*.mp4"))) == 1


def test_privacy_ocr_unavailable_does_not_move_or_register(tmp_path, monkeypatch):
    proj = _master_project(tmp_path)
    def no_ocr(*a, **k):
        raise privacy.OcrUnavailable("missing")
    monkeypatch.setattr(privacy, "scan_video", no_ocr)
    eng = Engine(proj)
    rt, master = _drive_to_master_submit(eng, proj)
    before = eng.rt("d_build").attempts
    eng.submit("d_build", {"master": str(master)}, token=rt.dispatch_token, actor="agent:test")
    assert eng.rt("d_build").attempts == before and eng.rt("d_build").status == S.WAITING_RUNTIME
    assert master.exists()
    assert not list((tmp_path / "hold").glob("*.mp4"))
    assert not eng.current("master")
    assert any("no attempt used" in f for f in eng.rt("d_build").failures)


def test_symlinked_workdir_does_not_move_video_target(tmp_path, monkeypatch):
    proj = _master_project(tmp_path)
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: {"frames": 1, "fps": 30, "ocr_calls": 1,
                                                                "windows": [{"entry": 1, "start_s": 0, "end_s": 1,
                                                                             "reads": 1, "box": [1, 2, 3, 4]}],
                                                                "live_windows": []})
    eng = Engine(proj)
    rt, master = _drive_to_master_submit(eng, proj)
    source = proj / "assets/source.mp4"
    source.parent.mkdir()
    source.write_bytes(b"source")
    master.unlink()
    master.parent.rmdir()
    master.parent.symlink_to(source.parent, target_is_directory=True)
    eng.submit("d_build", {"master": str(master.parent / "source.mp4")}, token=rt.dispatch_token, actor="agent:test")
    assert source.exists()


def test_a_feedback_mapping_that_closes_in_the_same_gate_is_held(tmp_path):
    """Reviewer (round 3): the interpreter mapped Wei's complaint to a defect in rework that the same gate then resolved,
    so the feedback led to nothing and the version passed. The hold is checked again after the gate's comparison."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect")

    def g_gate(ctx):
        fb = [f.id for f in ctx.state.evaluation.feedback if f.status == "open"]
        live = [d.id for d in ctx.state.evaluation.defects if d.status == "in_rework"]
        (ctx.workdir / "interpretation.json").write_text(json.dumps(
            {"defects": [], "feedback": [{"id": f, "defect_ids": live} for f in fb]}))
        return {"interpretation": "interpretation.json"}

    def step(e):
        if e.state.runtime.rework_cycles and not e.state.evaluation.feedback:
            flag(proj, "defect", on=False)                        # the correction lands in the rework …
            e.add_feedback("this version is even worse", actor="Wei")   # … and Wei rejects the version anyway
    eng = Engine(proj, executor=FnExecutor({"g_gate": g_gate}))
    eng.on_step = step
    eng.run(max_steps=200)
    fb, d = eng.state.evaluation.feedback[0], eng.state.evaluation.defects[0]
    assert fb.defect_ids == [d.id] and d.status == "resolved"
    assert fb.hold and "closed at the gate that mapped them" in fb.hold
    assert [g.outcome for g in eng.state.evaluation.gate_results] == ["rework", "blocked"]
    assert events(proj, "feedback_held")[-1]["id"] == "F001"


def test_an_unblock_releases_only_the_holds_its_human_was_shown(tmp_path):
    """Reviewer (round 3): a hold was released by an unblock whose escalation never mentioned it. Every gate result names
    every hold, and an unblock releases only holds its escalation cited."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    good = {"defects": [], "feedback": []}
    plan = {"now": "noaction"}

    def g_gate(ctx):
        fb = [f.id for f in ctx.state.evaluation.feedback if f.status == "open"]
        data = ({"defects": [], "feedback": [{"id": f, "no_action_reason": "needs no change"} for f in fb]} if plan["now"] == "noaction"
                else {"defects": [], "feedback": [{"id": "F001", "defect_ids": ["D999"]}]} if plan["now"] == "broken" else good)
        (ctx.workdir / "interpretation.json").write_text(json.dumps(data))
        return {"interpretation": "interpretation.json"}
    eng = Engine(proj, executor=FnExecutor({"g_gate": g_gate}))
    eng.run(max_steps=200)
    eng.add_feedback("the ending is flat", actor="Wei")
    eng.run(max_steps=200)
    assert eng.state.evaluation.feedback[0].hold and eng.state.runtime.status.value == "BLOCKED"
    with pytest.raises(EngineError, match="F001.*Wei|Wei.*F001"):
        eng.unblock("g_gate", actor="Sam Placeholder", note="not the author")     # not the author or the owner
    assert eng.state.evaluation.feedback[0].hold and not events(proj, "unblocked")
    eng.unblock("g_gate", actor="Wei", note="the ending stays as it is")
    assert events(proj, "unblocked")[-1]["released_holds"] == ["F001"]
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
