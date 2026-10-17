"""Loop budgets (triage #77, #78).

A regeneration plan was applied before its loop bounds were checked. A plan that needed a spent loop therefore still
spent a rework cycle, put its defects in rework (so the escalation listed "Open defects: none") and charged every loop it
named a traversal, and the unblock reset only the loop that escalated. On the SRA run a sibling loop (E44) was already at
its bound, so a plain unblock would have blocked again on the next plan, spending another cycle."""

from __future__ import annotations

import pytest
import yaml

from tests.conftest import FnExecutor, flag, mini_workflow
from tests.test_bookkeeping import events
from tests.test_hardening import make_project
from vpg import cli
from vpg.engine import Engine, EngineError
from vpg.graph import outcome_matches
from vpg.models import NodeStatus as S
from vpg.tools import verify
from vpg.util import read_json


def _two_loops(tmp_path):
    """Defects on d_build (loop E13, 5 traversals) and b_plan (loop E14, 1 traversal): the second plan needs a spent E14."""
    proj = make_project(tmp_path, workflow=mini_workflow(tmp_path, review_tool="tests.fakes:review_routes"))
    flag(proj, "defect")
    flag(proj, "defect_plan")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=300)
    return proj, eng


def test_a_plan_that_needs_a_spent_loop_blocks_before_anything_moves(tmp_path):
    proj, eng = _two_loops(tmp_path)
    x = eng.rt("x_regen")
    assert eng.state.runtime.status.value == "BLOCKED" and x.status == S.BLOCKED
    [esc] = [e for e in eng.state.runtime.escalations if not e.resolved]
    assert esc.reason == "loop E14 has no traversal left (1 of 1 used)" and esc.edge == "E14" and esc.edges == ["E14"]
    assert eng.state.runtime.rework_cycles == 1 and x.traversals == {"E13": 1, "E14": 1}   # the second plan spent nothing
    assert [p["targets"] for p in events(proj, "regeneration_plan")] == [["b_plan", "d_build"]]
    assert {d.status for d in eng.state.evaluation.defects} == {"persisting"}             # still open, not "in rework"
    assert "loop budgets of x_regen: E13 1/5, E14 1/1 (at its bound)" in esc.report
    report = sorted((proj / ".vpg/escalations").glob("x_regen-*.md"))[-1].read_text()
    assert "D001 [P2] LEG-SIZE" in report and "D002 [P2] LEG-SAFE" in report               # the human sees what is open
    eng.unblock("x_regen", actor="Wei", note="the plan's owner inset the title")
    assert x.traversals == {"E13": 1, "E14": 0} and eng.rt("b_plan").force_rerun
    flag(proj, "defect", on=False)
    flag(proj, "defect_plan", on=False)
    eng.run(max_steps=300)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.runtime.rework_cycles == 2   # the re-run plan is cycle 2


def test_one_escalation_names_every_spent_loop_and_a_plain_unblock_resets_them(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    x = eng.rt("x_regen")
    x.traversals.update({"E13": 5, "E14": 1})
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["b_plan", "d_build"], "defect_ids": []})
    [esc] = [e for e in eng.state.runtime.escalations if not e.resolved]
    assert esc.reason == "loop E13 has no traversal left (5 of 5 used); loop E14 has no traversal left (1 of 1 used)"
    assert esc.edges == ["E13", "E14"] and eng.state.runtime.rework_cycles == 0 and x.traversals == {"E13": 5, "E14": 1}
    eng.unblock("x_regen", actor="Wei", note="both loops")
    assert x.traversals == {"E13": 0, "E14": 0} and events(proj, "unblocked")[-1]["reset_loops"] == ["E13", "E14"]
    assert eng.rt("d_build").force_rerun and eng.rt("b_plan").force_rerun


def test_reset_loops_gives_every_loop_of_the_node_a_fresh_budget(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    x = eng.rt("x_regen")
    x.traversals.update({"E13": 5, "E14": 1})
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["b_plan"], "defect_ids": []})   # only E14 is needed; E13 is spent too
    report = sorted((proj / ".vpg/escalations").glob("x_regen-*.md"))[-1].read_text()
    assert "E13 has no traversal left either, so the next plan that needs it blocks again" in report
    assert "x_regen --actor <name> --reset-loops` gives every loop of x_regen a fresh budget" in report
    eng.unblock("x_regen", actor="Wei", note="E14 only")
    assert x.traversals == {"E13": 5, "E14": 0}                                            # a plain unblock: only what escalated
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["d_build"], "defect_ids": []})
    assert [e.edges for e in eng.state.runtime.escalations if not e.resolved] == [["E13"]]
    x.traversals["E14"] = 1
    eng.unblock("x_regen", actor="Wei", note="every loop", all_loops=True)
    assert x.traversals == {"E13": 0, "E14": 0} and events(proj, "unblocked")[-1]["reset_loops"] == ["E13", "E14"]
    eng._escalate(eng.node("x_regen"), "3 consecutive attempts failed completion", ["x"])      # critic S2: not a loop block
    with pytest.raises(EngineError, match="--reset-loops needs an open loop-bound escalation on x_regen"):
        eng.unblock("x_regen", actor="Wei", all_loops=True)
    eng.unblock("x_regen", actor="Wei", note="the retries")
    rows = [r for r in verify.human_record(eng.ledger.events()) if r["action"] == "unblocked"]
    assert rows[1]["subject"] == "x_regen (reset loops E13, E14)" and rows[-1]["subject"] == "x_regen"


def test_status_shows_the_loop_budgets_and_the_rework_budget(tmp_path, capsys):
    proj, eng = _two_loops(tmp_path)
    st = Engine(proj, read_only=True).status()
    assert st["max_rework_cycles"] == 6 and st["loops"] == [
        {"edge": "E13", "from": "x_regen", "to": "d_build", "used": 1, "max": 5},
        {"edge": "E14", "from": "x_regen", "to": "b_plan", "used": 1, "max": 1}]
    assert cli.main(["status", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "rework cycles 1 of 6" in out and "loop budgets: E13 1/5, E14 1/1 (at its bound)" in out
    assert cli.main(["unblock", str(proj), "x_regen", "--actor", "Wei", "--note", "fresh loops", "--reset-loops"]) == 0
    assert events(proj, "unblocked")[-1]["reset_loops"] == ["E14", "E13"]
    assert Engine(proj, read_only=True).status()["loops"] == []


def test_an_unblock_says_why_the_node_was_no_longer_blocked(project):
    """#78: an unblock after `accept-config` was logged as "unblocked after a rollback"."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    clean_end = eng.state.history.checkpoints[-1].seq                                     # x_regen never ran in the clean run
    x = eng.rt("x_regen")
    x.traversals["E14"] = 1
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["b_plan"], "defect_ids": []})
    eng.invalidate_nodes(["d_build"], reason="the brand guide changed", actor="Wei")      # x_regen is downstream: BLOCKED → STALE
    assert x.status == S.STALE
    eng.unblock("x_regen", actor="Wei", note="go on")
    assert events(proj, "invalidated")[-1]["reason"] == (
        "x_regen was unblocked; while it was blocked it was sent to re-check (the brand guide changed (requested by Wei)); "
        "it and its dependents re-check")
    x = eng.rt("x_regen")
    x.traversals["E14"] = 1
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["b_plan"], "defect_ids": []})
    eng.rollback(clean_end, actor="Wei")                                                   # its escalation stays open
    assert eng.rt("x_regen").status != S.BLOCKED
    eng.unblock("x_regen", actor="Wei", note="after the rollback")
    assert events(proj, "invalidated")[-1]["reason"] == (
        "x_regen was unblocked; a rollback had restored a checkpoint at which it was not blocked; it and its dependents re-check")


def test_a_plan_with_no_routable_target_spends_no_rework_cycle(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng._pass(eng.node("x_regen"), eng.rt("x_regen"), "pass", {"targets": [], "defect_ids": ["D9"], "unroutable": ["D9"]})
    assert eng.state.runtime.escalations[-1].reason.startswith("the regeneration plan has no routable target")
    assert eng.state.runtime.rework_cycles == 0 and not events(proj, "regeneration_plan")


def _block_like_v15(eng, node_id, data):
    """What an engine before v1.5.1 wrote when a plan needed a spent loop: the plan applied, every loop it named charged,
    then one escalation per exceeded loop, which recorded no `edges`."""
    node, rt = eng.node(node_id), eng.rt(node_id)
    eng._apply_regeneration_plan(data)
    rt.status, rt.outcome, rt.result_data = S.STEP_PASS, "pass", data
    eng.ledger.append("node_passed", node=node_id, attempt=rt.attempts, outcome="pass")
    for e in eng._loop_edges(node_id):
        if outcome_matches(e.when, "pass", data):
            rt.traversals[e.id] = rt.traversals.get(e.id, 0) + 1
            if rt.traversals[e.id] > e.max_traversals:
                eng._escalate(node, f"loop {e.id} exceeded its {e.max_traversals} allowed traversals", [e.on_fail], edge=e.id)
                eng.state.runtime.escalations[-1].edges = []
    eng._checkpoint("node_passed", node_id)


def _v15_block(tmp_path, monkeypatch):
    """The two-loop scenario, whose second plan an engine before v1.5.1 applied although E14 was spent."""
    proj = make_project(tmp_path, workflow=mini_workflow(tmp_path, review_tool="tests.fakes:review_routes"))
    flag(proj, "defect")
    flag(proj, "defect_plan")
    eng = Engine(proj, executor=FnExecutor())
    real = eng._pass
    monkeypatch.setattr(eng, "_pass", lambda node, rt, outcome, data: _block_like_v15(eng, node.id, data)
                        if node.id == "x_regen" and eng.state.runtime.rework_cycles == 1 else real(node, rt, outcome, data))
    eng.run(max_steps=300)
    monkeypatch.undo()
    return proj, eng


def test_unblocking_a_block_an_older_engine_wrote_reverts_the_plan_it_applied(tmp_path, monkeypatch, capsys):
    """Critic I1: before v1.5.1 a blocked plan was applied anyway. On SRA that spent rework cycle 4, left four defects in
    rework and charged loop E44. Re-judging the same version after `accept-config` then counted each of those defects as
    a failed correction, so the gate would stop on the repeated-defect limit after fewer real corrections."""
    proj, eng = _v15_block(tmp_path, monkeypatch)
    assert eng.state.runtime.rework_cycles == 2 and eng.rt("x_regen").traversals == {"E13": 2, "E14": 2}
    assert {d.status for d in eng.state.evaluation.defects} == {"in_rework"}
    assert [e.edges for e in eng.state.runtime.escalations if not e.resolved] == [[]]
    before = dict(eng.state.runtime.defect_failures)
    assert sorted(before.values()) == [1, 1]
    cfg = proj / "vpg.project.yaml"
    cfg.write_text(cfg.read_text() + "note: the v1.5.1 engine\n")
    eng = Engine(proj, executor=FnExecutor())
    eng.accept_config(actor="Wei", reason="the v1.5.1 engine")
    # critic r2 S1: Wei chooses --reset-loops before the unblock, so status shows what the unblock will revert
    assert eng.status()["pending_reverts"] == [
        {"node": "x_regen", "cycle": 2, "defects": {"D001": "persisting", "D002": "persisting"}, "loops": {"E13": 1}}]
    assert cli.main(["status", str(proj)]) == 0
    assert ("unblocking x_regen reverts the plan an older engine applied although blocked: rework cycles 2 → 1; E13 2 → 1; "
            "D001 → persisting; D002 → persisting") in capsys.readouterr().out
    eng.unblock("x_regen", actor="Wei", note="go on")
    assert eng.status()["pending_reverts"] == []
    [ev] = events(proj, "legacy_plan_reverted")
    assert ev["cycle"] == 2 and ev["defects"] == {"D001": "persisting", "D002": "persisting"} and ev["loops"] == {"E13": 1}
    assert eng.state.runtime.rework_cycles == 1 and eng.rt("x_regen").traversals == {"E13": 1, "E14": 0}
    assert events(proj, "invalidated")[-1]["reason"] == ("x_regen was unblocked; while it was blocked it was sent to re-check "
                                                         "(configuration change accepted by Wei); it and its dependents re-check")
    [row] = [r for r in verify.human_record(eng.ledger.events()) if r["action"] == "unblocked"]
    assert row["subject"] == "x_regen (reset loops E14; reverted a plan an older engine applied although blocked)"
    eng.run(max_steps=300)                  # the reviews re-judge the same version, then one real correction fails
    assert {k: v - before[k] for k, v in eng.state.runtime.defect_failures.items()} == {k: 1 for k in before}
    assert [e.reason for e in eng.state.runtime.escalations if not e.resolved] == ["loop E14 has no traversal left (1 of 1 used)"]
    assert eng.state.runtime.rework_cycles == 2


def test_a_rollback_is_named_as_the_cause_even_after_a_later_invalidation(project):
    """Critic S1: the latest invalidation in the ledger was named, although the rollback before it had lifted the block."""
    proj, _ = project
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)                                                                 # one rework cycle, then clean
    stale = [c.seq for c in eng.state.history.checkpoints
             if read_json(proj / c.path)["runtime"]["nodes"]["x_regen"]["status"] == "STALE"]
    x = eng.rt("x_regen")
    x.traversals["E14"] = 1
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["b_plan"], "defect_ids": []})
    eng.rollback(stale[-1], actor="Wei")                                                   # x_regen is STALE there
    assert eng.rt("x_regen").status == S.STALE
    eng.invalidate_nodes(["d_build"], reason="re-render the build after the rollback", actor="Wei")   # x_regen is in it
    eng.unblock("x_regen", actor="Wei", note="go on")
    assert events(proj, "invalidated")[-1]["reason"].startswith(
        "x_regen was unblocked; a rollback had restored a checkpoint at which it was not blocked")


def test_a_human_decision_on_a_regeneration_node_applies_its_plan_once(tmp_path):
    """Critic S5: while the plan waits for the human, nothing is spent; the decision applies it once."""
    wf = mini_workflow(tmp_path)
    data = yaml.safe_load(wf.read_text())
    regen = next(n for n in data["nodes"] if n["id"] == "x_regen")
    regen["human_review"] = {"required": True, "decision": "approve the regeneration plan", "outcomes": ["pass"]}
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=300)
    assert eng.rt("x_regen").status == S.WAITING_HUMAN and eng.state.runtime.rework_cycles == 0
    assert {d.status for d in eng.state.evaluation.defects} == {"open"} and not events(proj, "regeneration_plan")
    eng.decide("x_regen", "pass", actor="Wei", reason="route it")
    assert eng.state.runtime.rework_cycles == 1 and {d.status for d in eng.state.evaluation.defects} == {"in_rework"}
    assert len(events(proj, "regeneration_plan")) == 1 and eng.rt("d_build").status == S.STALE


def test_an_escalate_edge_firing_with_loops_moves_nothing(project, monkeypatch):
    """Critic S5: before v1.5.1 the loops were counted (and a plan applied) although the node blocked."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    hold = next(e for e in eng.wf.edges if e.id == "E12").model_copy(update={"source": "x_regen"})
    real = eng.index.outgoing
    monkeypatch.setattr(eng.index, "outgoing", lambda nid: real(nid) + ([hold] if nid == "x_regen" else []))
    eng._pass(eng.node("x_regen"), eng.rt("x_regen"), "blocked", {"targets": ["d_build"], "defect_ids": [], "reasons": ["hold"]})
    assert eng.rt("x_regen").status == S.BLOCKED and eng.state.runtime.escalations[-1].reason.startswith("E12")
    assert eng.state.runtime.rework_cycles == 0 and eng.rt("x_regen").traversals.get("E13", 0) == 0
    assert not events(proj, "regeneration_plan") and eng.rt("d_build").status == S.STEP_PASS


def test_a_rollback_after_an_older_engines_block_turns_the_repair_off(tmp_path, monkeypatch):
    """The rollback restores its own state (defects as at its checkpoint; the spent cycle stays), so nothing is reverted
    twice. Critic r2 S2: nothing pinned this guard."""
    proj, eng = _v15_block(tmp_path, monkeypatch)
    eng.rollback(eng.state.history.checkpoints[-2].seq, actor="Wei")
    assert eng.status()["pending_reverts"] == []
    eng.unblock("x_regen", actor="Wei", note="after the rollback")
    assert not events(proj, "legacy_plan_reverted")


def test_the_repair_restores_each_defect_as_the_checkpoint_before_the_plan_had_it(tmp_path, monkeypatch):
    """A regressed defect (resolved, then seen again) is open with a rework count, which the fallback would call persisting."""
    proj, eng = _v15_block(tmp_path, monkeypatch)
    legacy = eng._legacy_blocked_plan("x_regen", [e for e in eng.state.runtime.escalations if not e.resolved])
    ref = next(c for c in eng.state.history.checkpoints if c.seq == legacy["checkpoint"])
    cp = read_json(proj / ref.path)
    next(d for d in cp["evaluation"]["defects"] if d["id"] == "D002")["status"] = "open"
    (proj / ref.path).write_text(__import__("json").dumps(cp))
    eng.unblock("x_regen", actor="Wei", note="go on")
    assert events(proj, "legacy_plan_reverted")[-1]["defects"] == {"D001": "persisting", "D002": "open"}


def test_an_escalation_the_ledger_does_not_match_is_not_repaired(tmp_path, monkeypatch):
    """Only the exact sequence an older engine wrote proves a legacy plan; an escalation it does not name is left alone."""
    proj, eng = _v15_block(tmp_path, monkeypatch)
    [esc] = [e for e in eng.state.runtime.escalations if not e.resolved]
    esc.reason = "loop E14 ran out (reworded)"
    assert eng._legacy_blocked_plan("x_regen", [esc]) is None
    eng.unblock("x_regen", actor="Wei", note="go on")
    assert not events(proj, "legacy_plan_reverted") and eng.state.runtime.rework_cycles == 2


def test_status_survives_a_damaged_ledger_while_an_older_block_is_open(tmp_path, monkeypatch, capsys):
    """Critic r3 N1: the revert preview reads the ledger, so one damaged line made `vpg status` refuse."""
    proj, eng = _v15_block(tmp_path, monkeypatch)
    path = proj / ".vpg/ledger.jsonl"
    lines = path.read_text().splitlines()
    lines[4] = "{damaged"
    path.write_text("\n".join(lines) + "\n")
    [preview] = Engine(proj, read_only=True).status()["pending_reverts"]
    assert preview["node"] == "x_regen" and preview["unavailable"].startswith("ledger line 5 is not a valid event")
    assert cli.main(["status", str(proj)]) == 0
    assert "unblocking x_regen may revert a plan an older engine applied; no preview: ledger line 5" in capsys.readouterr().out
