"""v1.1 regressions from the first real production (the SRA film): defect bookkeeping (#25 #26 #27 #41), `vpg resolve`,
the bound command's code in the fingerprint (#21 #40), the engine in the accepted configuration (#9), on-screen density
and reading time (#36), the submitter's recorded identity (#11) and failed-attempt timing (#30).

Each test fails on the behaviour the production observed.
"""
from __future__ import annotations

import json
import re
import shlex
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
from vpg.models import Defect, EvaluationState, Finding, Policies, Review, RuntimeState
from vpg.provenance import Ledger
from vpg.tools import pacing
from vpg.tools.legibility import max_simultaneous
from vpg.util import is_human_name, sha256_json

ROUTES = "tests.fakes:review_routes"
COMMAND_SCRIPT = ("import json, os, pathlib\n"
                  "ctx = json.load(open(os.environ['VPG_CONTEXT']))\n"
                  "pathlib.Path(ctx['workdir'], 'outputs.json').write_text(json.dumps({'outputs': {}}))\n")


def events(proj, kind):
    return [e["data"] for e in Ledger(proj / ".vpg/ledger.jsonl").events(kind)]


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
        (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "commands": {"b_plan": "{python} pipeline/plan.py",
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
    proj = make_project(tmp_path, human_b=True)
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
    d = eng.state.evaluation.defects[0]
    for actor in ("Agent:critic", " AGENT:x", "Dry-Run-Auto"):
        with pytest.raises(EngineError, match="named human"):
            eng.unblock("g_gate", actor=actor, note="route it")
        with pytest.raises(EngineError, match="named human"):
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
    eng.unblock("g_gate", actor="Sam Placeholder", note="looks fine to me")   # neither the author nor the gate's owner
    assert events(proj, "unblocked")[-1]["released_holds"] == [] and eng.state.evaluation.feedback[0].hold
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
    eng.unblock("g_gate", actor="Sam Placeholder", note="not the author")     # releases nothing: not the author or the owner
    plan["now"] = "broken"                                              # the gate now fails its own checks twice …
    eng.run(max_steps=200)
    esc = [e for e in eng.state.runtime.escalations if not e.resolved][-1]
    assert "unknown defect" in esc.report and "F001" in esc.report and esc.holds == []   # it mentions F001, but not as a hold
    eng.unblock("g_gate", actor="Wei", note="fixed the interpreter")
    assert events(proj, "unblocked")[-1]["released_holds"] == [] and eng.state.evaluation.feedback[0].hold
    plan["now"] = "good"
    eng.run(max_steps=200)
    g = eng.state.evaluation.gate_results[-1]
    assert g.outcome == "blocked" and any("on hold" in r and "F001" in r for r in g.reasons)
    eng.unblock("g_gate", actor="Wei", note="the ending stays as it is")
    assert events(proj, "unblocked")[-1]["released_holds"] == ["F001"]
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
