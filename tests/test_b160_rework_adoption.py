"""#160: a defect routed by the gate survives an invalidation that re-stales the gate and the plan before the plan runs."""
from __future__ import annotations

import pytest

from tests.conftest import FnExecutor, flag
from tests.test_bookkeeping import ROUTES, events
from tests.test_hardening import make_project
from vpg.engine import Engine
from vpg.provenance import Ledger


def _until_the_gate_decides_rework(eng: Engine, proj, n: int = 1) -> None:
    """Step until the gate has decided 'rework' n times and the regeneration plan has not run for the n-th one yet."""
    for _ in range(300):
        eng.step()
        reworks = [g for g in events(proj, "gate") if g["outcome"] == "rework"]
        if len(reworks) == n and len(events(proj, "regeneration_plan")) == n - 1:
            return
    raise AssertionError(f"the gate never decided rework {n} time(s)")


def _seq(proj) -> int:
    return max(e["seq"] for e in Ledger(proj / ".vpg/ledger.jsonl").events())


def _d_build_after(proj, seq: int, node: str = "d_build") -> list[tuple[str, dict]]:
    return [(e["type"], e["data"]) for e in Ledger(proj / ".vpg/ledger.jsonl").events()
            if e["seq"] > seq and e["type"] in ("memo_hit", "node_dispatched", "rework_adopted")
            and e["data"].get("node") == node]


def _step_until(eng: Engine, done, limit: int = 40) -> None:
    for _ in range(limit):
        eng.step()
        if done():
            return
    raise AssertionError("the run never reached the expected point")


def _defect(eng: Engine, did: str):
    return next(d for d in eng.state.evaluation.defects if d.id == did)


def _reported_again(eng: Engine, did: str) -> bool:
    """A reviewer reported the defect again after its correction, and the gate has not decided on that report yet."""
    return _defect(eng, did).status == "persisting" and eng.rt("g_gate").status.value != "STEP_PASS"


def _routed_project(tmp_path):
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect")                                   # a P2 defect traced to d_build, which rework reaches
    return proj, Engine(proj, executor=FnExecutor())


def test_a_cascade_before_the_plan_still_reworks_the_node_the_gate_routed_to(tmp_path):
    proj, eng = _routed_project(tmp_path)
    _until_the_gate_decides_rework(eng, proj)
    defect = next(d for d in eng.state.evaluation.defects if d.traced_node == "d_build")
    eng.invalidate_nodes(["a_intake"], reason="approved change request", actor="Wei")   # re-stales g_gate and x_regen
    seq = _seq(proj)
    for _ in range(30):
        eng.step()
        if any(kind == "node_dispatched" for kind, _ in _d_build_after(proj, seq)):
            break
    seen = _d_build_after(proj, seq)
    assert seen[0] == ("rework_adopted", {**seen[0][1], "defects": [defect.id]})
    kind, data = seen[1]
    assert kind == "node_dispatched" and defect.id in data["rework"]          # not a memo hit on unchanged inputs
    assert next(d for d in eng.state.evaluation.defects if d.id == defect.id).status == "in_rework"   # bookkeeping sees it
    eng.run(max_steps=60)                                  # the flag stays on: the next review finds it again
    assert any(c.defect_id == defect.id and not c.resolved for c in eng.state.evaluation.comparisons)


def test_one_adoption_per_gate_decision_a_second_cascade_memo_passes(tmp_path):
    proj, eng = _routed_project(tmp_path)
    _until_the_gate_decides_rework(eng, proj)
    eng.invalidate_nodes(["a_intake"], reason="approved change request", actor="Wei")
    for _ in range(30):                                    # until the adopted attempt has passed, before the next gate
        eng.step()
        if eng.rt("d_build").status.value == "STEP_PASS" and eng.rt("g_gate").status.value != "STEP_PASS":
            break
    seq = _seq(proj)
    eng.invalidate_nodes(["a_intake"], reason="another change request", actor="Wei")
    for _ in range(30):
        eng.step()
        if _d_build_after(proj, seq):
            break
    assert [kind for kind, _ in _d_build_after(proj, seq)][:1] == ["memo_hit"]     # already carried since that gate
    assert len(events(proj, "rework_adopted")) == 1


def test_a_persisting_defect_routed_again_survives_a_cascade_in_the_next_round(tmp_path):
    proj, eng = _routed_project(tmp_path)
    _until_the_gate_decides_rework(eng, proj, n=2)        # round 1 was routed and reworked; the defect persisted
    defect = next(d for d in eng.state.evaluation.defects if d.traced_node == "d_build")
    assert defect.status == "persisting"
    eng.invalidate_nodes(["a_intake"], reason="approved change request", actor="Wei")
    seq = _seq(proj)
    for _ in range(30):
        eng.step()
        if _d_build_after(proj, seq):
            break
    assert _d_build_after(proj, seq)[0][0] == "rework_adopted"


def test_a_blocked_gate_routes_nothing_to_adopt(tmp_path):
    """#41's flow: the gate blocks on a defect only a human can fix; after the fix and the unblock, the routable defect
    goes through the regeneration plan, never through adoption."""
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
    assert events(proj, "rework_adopted") == []
    assert [g.outcome for g in eng.state.evaluation.gate_results] == ["blocked", "rework", "pass"]


def test_a_normal_rework_round_adopts_nothing(tmp_path):
    proj, eng = _routed_project(tmp_path)
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)                                 # the plan routes the defect as usual
    assert events(proj, "regeneration_plan") and events(proj, "rework_adopted") == []


@pytest.mark.parametrize("stamped", [True, False], ids=["stamped", "routed-before-v1.8"])
def test_a_defect_the_plan_routed_and_a_reviewer_reported_again_waits_for_the_next_gate(tmp_path, stamped):
    """The plan routed it and the reviewer reported it again on the corrected version; a cascade then re-stales its node
    before the gate re-decides. The plan already used that gate decision: no second correction for it. A routing recorded
    before v1.8 carries no stamp, and the newer report alone defers it to the next gate."""
    proj, eng = _routed_project(tmp_path)
    if not stamped:
        plan = eng._apply_regeneration_plan

        def unstamped(data):
            plan(data)
            for d in eng.state.evaluation.defects:
                d.rework_gate = ""
        eng._apply_regeneration_plan = unstamped
    _until_the_gate_decides_rework(eng, proj)
    defect = next(d for d in eng.state.evaluation.defects if d.traced_node == "d_build")
    _step_until(eng, lambda: _reported_again(eng, defect.id))
    assert len(events(proj, "regeneration_plan")) == 1 and _defect(eng, defect.id).rework_gate == ("gate-v1-1" if stamped else "")
    eng.invalidate_nodes(["a_intake"], reason="approved change request", actor="Wei")
    seq = _seq(proj)
    _step_until(eng, lambda: bool(_d_build_after(proj, seq)))
    assert _d_build_after(proj, seq)[0][0] == "memo_hit"
    assert events(proj, "rework_adopted") == []


def test_an_adopted_defect_reported_again_is_not_adopted_twice_for_one_gate_decision(tmp_path):
    proj, eng = _routed_project(tmp_path)
    _until_the_gate_decides_rework(eng, proj)
    defect = next(d for d in eng.state.evaluation.defects if d.traced_node == "d_build")
    eng.invalidate_nodes(["a_intake"], reason="approved change request", actor="Wei")
    _step_until(eng, lambda: _reported_again(eng, defect.id))     # adopted, corrected, and reported again on the new version
    assert eng.state.runtime.defect_failures[defect.signature] == 1
    eng.invalidate_nodes(["a_intake"], reason="another change request", actor="Wei")
    seq = _seq(proj)
    _step_until(eng, lambda: bool(_d_build_after(proj, seq)))
    assert _d_build_after(proj, seq)[0][0] == "memo_hit"
    _step_until(eng, lambda: len(eng.state.evaluation.gate_results) == 2)
    assert [e["gate"] for e in events(proj, "rework_adopted")] == ["gate-v1-1"]
    assert eng.state.runtime.defect_failures[defect.signature] == 1   # one correction and one failure for that decision


def _unchanged_plan(ctx):
    (ctx.workdir / "b_plan.json").write_text('{"plan": "unchanged"}')
    return {"b_plan": "b_plan.json"}


def test_a_report_on_the_same_version_does_not_reopen_the_gate_decision(tmp_path):
    """The correction changed no bytes, so the build memo-passed and the version stayed; a reviewer then re-ran on that
    version and reported the defect again. Only the stamp shows the gate decision was already adopted."""
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect_plan")                              # a P2 defect traced to b_plan, upstream of the build
    eng = Engine(proj, executor=FnExecutor({"b_plan": _unchanged_plan}))
    _until_the_gate_decides_rework(eng, proj)
    defect = next(d for d in eng.state.evaluation.defects if d.traced_node == "b_plan")
    version = eng.state.evaluation.gate_results[-1].version
    eng.invalidate_nodes(["a_intake"], reason="approved change request", actor="Wei")
    _step_until(eng, lambda: _defect(eng, defect.id).status == "in_rework" and eng.rt("b_plan").status.value == "STEP_PASS")
    eng.invalidate_nodes(["r1_review"], reason="its rubric changed", actor="Wei")
    _step_until(eng, lambda: _reported_again(eng, defect.id))
    assert eng.state.runtime.current_version == version == _defect(eng, defect.id).last_seen_version
    eng.invalidate_nodes(["a_intake"], reason="another change request", actor="Wei")
    seq = _seq(proj)
    _step_until(eng, lambda: bool(_d_build_after(proj, seq, "b_plan")))
    assert _d_build_after(proj, seq, "b_plan")[0][0] == "memo_hit"
    assert len(events(proj, "rework_adopted")) == 1
