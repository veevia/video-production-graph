"""v1.6: a converging run never blocks on a loop budget; a stalled one still escalates, with the quality trend.

On the first real run (SRA, 2026-09-27) rework loop E43 reached its bound of 3 while cycle 4 had just resolved 15
defects, and the run sat BLOCKED for a day while the film kept improving outside the graph. Budgets are now project
settings (`loop_budget` in vpg.project.yaml). With `on_exhaust: extend_if_converging` a spent rework loop, or the
rework-cycle budget, is extended when the latest review round made progress, and the runaway guards still hold."""

from __future__ import annotations

import json

import pytest
import yaml

from tests.conftest import FnExecutor, mini_workflow
from tests.test_bookkeeping import events
from tests.test_hardening import make_project
from vpg import cli
from vpg.budget import PROFILES, budget_problems, convergence, load_budget, trend_lines
from vpg.engine import Engine, EngineError
from vpg.models import NodeStatus as S
from vpg.tools import verify

A, B, C, D = ["a", "P2"], ["b", "P2"], ["c", "P3"], ["d", "P3"]


def scripted(tmp_path, rounds, e13=None, **budget):
    """A mini run whose reviewer reports `rounds` version by version; `e13` sets the rework loop's own bound."""
    wf = mini_workflow(tmp_path, review_tool="tests.fakes:review_rounds")
    if e13 is not None:
        spec = yaml.safe_load(wf.read_text())
        next(e for e in spec["edges"] if e["id"] == "E13")["max_traversals"] = e13
        wf.write_text(yaml.safe_dump(spec, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    (proj / "flags").mkdir(exist_ok=True)
    (proj / "flags" / "rounds.json").write_text(json.dumps(rounds))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    if budget:
        cfg["loop_budget"] = budget
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=400)
    return proj, eng


def escalation_file(proj, node="x_regen"):
    return sorted((proj / ".vpg/escalations").glob(f"{node}-*.md"))[-1].read_text()


# ------------------------------------------------------------------------------------------------ settings
def test_profiles_and_explicit_settings():
    assert load_budget({}) == load_budget({"loop_budget": None}) and not load_budget({}).declared
    q = load_budget({"loop_budget": {"profile": "quality-first"}})
    assert (q.max_traversals, q.rework_cycles, q.on_exhaust, q.extend_by, q.declared) == (8, 15, "extend_if_converging", 1, True)
    mine = load_budget({"loop_budget": {"profile": "quality-first", "rework_cycles": 20, "max_hours": 96}})
    assert (mine.rework_cycles, mine.max_traversals, mine.max_hours) == (20, 8, 96)
    assert load_budget({"loop_budget": {"on_exhaust": "extend_if_converging"}}).max_traversals is None   # standard bounds
    assert set(PROFILES) == {"standard", "quality-first"}


@pytest.mark.parametrize("block, message", [
    ("fast", "must be a mapping"), ({"profile": "turbo"}, "profile must be one of"), ({"max_traversals": 0}, "whole number"),
    ({"rework_cycles": True}, "whole number"), ({"on_exhaust": "retry"}, "on_exhaust must be one of"),
    ({"max_hours": -1}, "positive number"), ({"budget": 3}, "is not a setting")])
def test_a_malformed_loop_budget_is_refused_before_the_run_starts(tmp_path, block, message):
    assert any(message in p for p in budget_problems({"loop_budget": block}))
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "loop_budget": block}))
    with pytest.raises(EngineError, match="vpg.project.yaml has invalid"):
        Engine(proj, executor=FnExecutor())
    assert load_budget({"loop_budget": block}) == load_budget({})   # an invalid block never loosens anything


# ------------------------------------------------------------------------------------------------ convergence
def row(version, open_, resolved, new, split=None):
    return {"version": version, "open": open_, "resolved": resolved, "new": new, "persisting": 0, "open_by_severity": split}


def test_convergence_follows_the_open_count_the_severity_and_new_against_resolved():
    assert convergence([row(10, 14, 9, 11), row(11, 4, 15, 1)]) == (True, "v10 → v11: 14 → 4 open P1–P3, 15 resolved, 1 new")
    assert convergence([row(1, 2, 0, 2), row(2, 2, 0, 0)])[1].endswith("nothing was resolved, so the same defects persist")
    assert not convergence([row(1, 2, 0, 2), row(2, 1, 1, 0)] + [row(3, 3, 1, 3)])[0]          # more new than resolved
    lighter = [row(1, 2, 0, 2, {"P1": 1, "P2": 1, "P3": 0}), row(2, 2, 1, 1, {"P1": 0, "P2": 1, "P3": 1})]
    assert convergence(lighter) == (True, "v1 → v2: 2 → 2 open P1–P3, 1 resolved, 1 new; fewer severe ones open")
    heavier = [row(1, 2, 0, 2, {"P1": 0, "P2": 1, "P3": 1}), row(2, 2, 1, 1, {"P1": 1, "P2": 0, "P3": 1})]
    assert not convergence(heavier)[0]
    assert convergence([row(1, 3, 0, 3)]) == (False, "no earlier review round to compare with")


# ------------------------------------------------------------------------------------------------ rework loops
def test_a_converging_run_passes_a_spent_rework_loop_and_ledgers_why(tmp_path, capsys):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []], e13=1, on_exhaust="extend_if_converging")
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.runtime.rework_cycles == 2
    [ext] = events(proj, "loop_budget_extended")
    assert ext["loops"] == {"E13": [1, 2]} and ext["reason"] == "v1 → v2: 3 → 1 open P1–P3, 2 resolved, 0 new"
    assert eng.state.runtime.loop_extra == {"E13": 1} and not eng.state.runtime.escalations
    assert [(r["version"], r["open"], r["resolved"], r["new"]) for r in eng._trend()] == [(1, 3, 0, 3), (2, 1, 2, 0), (3, 0, 1, 0)]
    assert eng.state.evaluation.gate_results[0].trend["open_by_severity"] == {"P1": 0, "P2": 2, "P3": 1}
    assert cli.main(["status", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "loop budget: standard (each loop's own bound, 6 rework cycles; when spent: extend while converging); granted so far: E13 +1" in out
    assert "quality trend (blocking P1–P3 per review round):" in out and "v2 · cycle 1 · 1 open (P1 0, P2 0, P3 1) · 2 resolved" in out
    assert "latest round: not converging: v2 → v3: 1 → 0 open P1–P3, 1 resolved, 0 new" not in out   # v3 converged too
    assert "latest round: converging: v2 → v3: 1 → 0 open P1–P3, 1 resolved, 0 new" in out
    summary = verify.engine_decisions(eng.ledger.events())
    assert summary[1] == "## Budget extensions (the run was converging, so a spent budget did not stop it)"
    assert summary[2].endswith("loop E13 1 → 2 (standard): v1 → v2: 3 → 1 open P1–P3, 2 resolved, 0 new")


@pytest.mark.parametrize("rounds, why", [
    ([[A, B], [A, B]], "v1 → v2: 2 → 2 open P1–P3, 0 resolved, 0 new; nothing was resolved, so the same defects persist"),
    ([[A, B], [B, C, D]], "v1 → v2: 2 → 3 open P1–P3, 1 resolved, 2 new; more new defects than resolved ones")])
def test_a_stalled_run_still_escalates_with_the_trend(tmp_path, rounds, why):
    proj, eng = scripted(tmp_path, rounds, e13=1, on_exhaust="extend_if_converging")
    assert eng.state.runtime.status.value == "BLOCKED" and not events(proj, "loop_budget_extended")
    [esc] = [e for e in eng.state.runtime.escalations if not e.resolved]
    assert esc.reason == "loop E13 has no traversal left (1 of 1 used)" and f"not extended: {why}" in esc.report
    text = escalation_file(proj)
    assert "## Quality trend (blocking P1–P3 per review round)" in text and f"- **Latest round:** not converging: {why}" in text
    assert events(proj, "escalated")[-1]["converging"] is False and events(proj, "escalated")[-1]["convergence"] == why


def test_without_a_loop_budget_a_spent_loop_blocks_as_before_and_the_escalation_shows_the_trend(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []])
    assert eng.state.runtime.status.value == "RUN_PASS"
    rt = eng.rt("x_regen")
    rt.traversals["E13"] = 5                                   # the workflow's own bound of 5, spent
    eng._pass(eng.node("x_regen"), rt, "pass", {"targets": ["d_build"], "defect_ids": []})
    [esc] = [e for e in eng.state.runtime.escalations if not e.resolved]
    assert esc.reason == "loop E13 has no traversal left (5 of 5 used)" and "not extended" not in esc.report
    assert not events(proj, "loop_budget_extended") and "## Quality trend" in escalation_file(proj)


def test_the_quality_first_profile_widens_only_the_rework_loops(tmp_path):
    proj, eng = scripted(tmp_path, [[A], []], profile="quality-first")
    loops = {e.id: eng._bound(e) for e in eng.wf.edges if e.kind.value == "loop"}
    assert loops == {"E13": 8, "E14": 8} and eng._max_rework_cycles() == 15
    (tmp_path / "h").mkdir()
    human = make_project(tmp_path / "h", workflow=mini_workflow(tmp_path / "h", human_b=True))
    cfg = yaml.safe_load((human / "vpg.project.yaml").read_text())
    (human / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "loop_budget": {"profile": "quality-first"}}))
    h = Engine(human, executor=FnExecutor())
    assert {e.id: h._bound(e) for e in h.wf.edges if e.kind.value == "loop"}["E16"] == 1   # a human's rejection loop keeps its bound


# ------------------------------------------------------------------------------------------------ rework cycles
def test_a_converging_run_gets_more_rework_cycles(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []], rework_cycles=1, on_exhaust="extend_if_converging")
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.runtime.rework_extra == 1
    [ext] = events(proj, "rework_budget_extended")
    assert (ext["before"], ext["after"]) == (1, 2) and ext["reason"] == "v1 → v2: 3 → 1 open P1–P3, 2 resolved, 0 new"
    assert any("rework cycles 1 → 2 (standard)" in line for line in verify.engine_decisions(eng.ledger.events()))
    gate_v2 = next(g for g in eng.state.evaluation.gate_results if g.version == 2)
    assert gate_v2.outcome == "rework" and gate_v2.reasons[0].startswith("rework budget extended from 1 to 2 cycles")


def test_a_stalled_run_blocks_at_the_rework_budget_and_says_why(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B], [A, B]], rework_cycles=1, on_exhaust="extend_if_converging")
    g = eng.state.evaluation.gate_results[-1]
    assert g.outcome == "blocked" and "rework budget of 1 cycles is spent" in g.reasons
    assert "not extended: v1 → v2: 2 → 2 open P1–P3, 0 resolved, 0 new; nothing was resolved, so the same defects persist" in g.reasons
    assert not events(proj, "rework_budget_extended")


def test_the_same_defect_guard_still_stops_a_converging_run(tmp_path):
    rounds = [[A, B, C, D, ["e", "P3"]], [A, C, D, ["e", "P3"]], [A, D, ["e", "P3"]], [A, ["e", "P3"]]]
    proj, eng = scripted(tmp_path, rounds, profile="quality-first")
    g = eng.state.evaluation.gate_results[-1]
    assert g.outcome == "blocked" and "a defect persisted through 3 corrections; stop and report instead of looping" in g.reasons
    assert eng.state.runtime.status.value == "BLOCKED" and eng._trend()[-1]["resolved"] == 1   # it was still converging


def test_the_wall_clock_guard_refuses_every_extension(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []], e13=1, on_exhaust="extend_if_converging", max_hours=1e-9)
    [esc] = [e for e in eng.state.runtime.escalations if not e.resolved]
    assert "not extended: the wall-clock guard of 1e-09 h is spent" in esc.report and not events(proj, "loop_budget_extended")


def test_granted_budget_survives_a_rollback(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []], e13=1, on_exhaust="extend_if_converging")
    first = eng.state.history.checkpoints[0].seq
    eng.rollback(first, actor="Wei")
    assert eng.state.runtime.loop_extra == {"E13": 1}


def test_trend_lines_mark_rounds_recorded_before_v1_6():
    lines = trend_lines([{"version": 10, "cycle": None, "open": 14, "open_by_severity": None, "resolved": 9, "new": 11,
                          "persisting": 0, "estimated": True}])
    assert lines == ["v10 · 14 open · 9 resolved · 11 new · 0 persisting (estimated: recorded before v1.6)"]


def test_the_release_summary_lists_each_extension(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []], e13=1, on_exhaust="extend_if_converging")
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    verify.final(ctx)
    summary = (ctx.workdir / "release-summary.md").read_text()
    assert "## Budget extensions (the run was converging, so a spent budget did not stop it)" in summary
    assert "loop E13 1 → 2 (standard): v1 → v2: 3 → 1 open P1–P3, 2 resolved, 0 new" in summary


# ------------------------------------------------------------------------------------------------ review fixes (v1.6)
def test_a_second_gate_on_the_same_version_credits_nothing_and_extends_nothing(tmp_path):
    """Review I2: resolved counts were per version, so re-gating a version re-credited the earlier round's fixes and
    granted a second extension for the same progress."""
    proj, eng = scripted(tmp_path, [[A, B, C], [C], [C]], rework_cycles=1, on_exhaust="extend_if_converging")
    [ext] = events(proj, "rework_budget_extended")
    assert ext["version"] == 2 and eng.state.runtime.grant_versions == {"rework": 2}
    last = eng.state.evaluation.gate_results[-1]
    assert (last.version, last.outcome, last.trend["round"]["resolved"]) == (3, "blocked", [])   # C persisted: a stall
    eng._review_gate(eng.node("g_gate"))                              # the same version judged again (accept-config, unblock)
    again = eng.state.evaluation.gate_results[-1]
    assert again.version == 3 and again.trend["round"] == {"resolved": [], "persisting": ["D003"], "new": []}
    assert len(events(proj, "rework_budget_extended")) == 1
    eng.state.runtime.grant_versions["rework"] = eng.state.runtime.current_version
    ok, why = eng._extension_verdict("rework")
    assert not ok and why.startswith("rework was already extended at version 3")


def test_a_versions_gates_share_one_trend_row(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []])
    assert eng._trend()[-1]["resolved"] == 1                           # version 3's gate resolved C
    eng._review_gate(eng.node("g_gate"))                              # a second gate on version 3
    assert [r["version"] for r in eng._trend()] == [1, 2, 3]
    assert eng._trend()[-1]["resolved"] == 1                           # the version's progress, counted once
    assert eng.state.evaluation.gate_results[-1].trend["round"]["resolved"] == []   # the re-gate's own round: nothing


def test_a_version_re_gated_after_a_humans_unblock_keeps_its_credit(tmp_path):
    """Review I2: one row per gate lost v2's progress when a hold blocked its gate and a human's unblock re-gated it,
    so the run blocked although it was converging."""
    proj = make_project(tmp_path, workflow=mini_workflow(tmp_path, review_tool="tests.fakes:review_rounds"))
    (proj / "flags").mkdir(exist_ok=True)
    (proj / "flags" / "rounds.json").write_text(json.dumps([[A, B, C], [C], []]))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["loop_budget"] = {"rework_cycles": 1, "on_exhaust": "extend_if_converging"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    seen = []

    def gate(ctx):   # at version 2's first gate, C is held for a human (as a re-trace off a human-gated node does)
        seen.append(ctx.state.runtime.current_version)
        (ctx.workdir / "interpretation.json").write_text(json.dumps({"defects": [], "feedback": []}))
        if seen.count(2) == 1 and ctx.state.runtime.current_version == 2:
            next(d for d in ctx.state.evaluation.defects if d.description == "problem c").human_hold = "held for Wei (test)"
        return {"interpretation": "interpretation.json"}
    eng = Engine(proj, executor=FnExecutor({"g_gate": gate}))
    eng.run(max_steps=400)
    assert eng.state.runtime.status.value == "BLOCKED" and eng.state.evaluation.gate_results[-1].version == 2
    eng.unblock("g_gate", actor="Wei", note="the re-trace is right")
    eng.run(max_steps=400)
    assert eng.state.runtime.status.value == "RUN_PASS"
    [ext] = events(proj, "rework_budget_extended")
    assert ext["version"] == 2 and ext["reason"] == "v1 → v2: 3 → 1 open P1–P3, 2 resolved, 0 new"
    assert [r["version"] for r in eng._trend()] == [1, 2, 3]


def test_a_human_reset_clears_what_converging_rounds_were_granted(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []], e13=1, on_exhaust="extend_if_converging")
    assert eng.state.runtime.loop_extra == {"E13": 1}
    x = eng.rt("x_regen")
    x.traversals["E13"] = 2
    eng.state.runtime.grant_versions["E13"] = eng.state.runtime.current_version   # granted at this version already
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["d_build"], "defect_ids": []})
    [esc] = [e for e in eng.state.runtime.escalations if not e.resolved]
    assert "not extended: E13 was already extended at version 3" in esc.report
    eng.unblock("x_regen", actor="Wei", note="fresh budget")
    assert events(proj, "unblocked")[-1]["cleared_grants"] == {"E13": 1} and eng.state.runtime.loop_extra == {}
    assert "E13" not in eng.state.runtime.grant_versions


def test_a_profile_never_narrows_a_wider_workflow_bound(tmp_path):
    proj, eng = scripted(tmp_path, [[A], []], e13=12, profile="quality-first")
    assert eng._bound(next(e for e in eng.wf.edges if e.id == "E13")) == 12


def test_a_null_extend_by_is_refused():
    assert any("extend_by must be a whole number" in p for p in budget_problems({"loop_budget": {"extend_by": None}}))


def test_a_pre_v1_6_rework_gate_gives_its_severity_split_from_its_reason(tmp_path):
    """A gate recorded before v1.6 has no snapshot, but its reason lists each open defect's severity at the time."""
    from vpg.budget import _reason_split
    g = type("G", (), {"reasons": ["blocking defects open: D043 P2 LEG-DENSITY→n13, D045 P3 ACC-CAPTIONS→n14"],
                       "open_blocking": ["D043", "D045"]})()
    assert _reason_split(g, ["P1", "P2", "P3"]) == {"P1": 0, "P2": 1, "P3": 1}
    g.open_blocking = ["D043", "D045", "D050"]                              # one it doesn't list: no split
    assert _reason_split(g, ["P1", "P2", "P3"]) is None


def test_a_rounds_severities_are_its_own_even_after_a_downgrade(tmp_path):
    """A defect's severity follows its latest review (SRA's D002 was a P2 at v1 and is a nit now). The trend counts each
    round by the severities its gate recorded, so a later downgrade can't rewrite it."""
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []])
    before = [(r["version"], r["resolved"], r["new"]) for r in eng._trend()]
    for d in eng.state.evaluation.defects:
        d.severity = "nit"                                                   # every defect downgraded later
    assert [(r["version"], r["resolved"], r["new"]) for r in eng._trend()] == before == [(1, 0, 3), (2, 2, 0), (3, 1, 0)]


def test_a_pre_v1_6_row_takes_its_severity_split_from_the_gates_reason(tmp_path):
    proj, eng = scripted(tmp_path, [[A, B, C], [C], []])
    for g in eng.state.evaluation.gate_results:
        g.trend = None                                                       # as recorded before v1.6
    rows = eng._trend()
    assert [r["estimated"] for r in rows] == [True, True, True]
    assert rows[0]["open_by_severity"] == {"P1": 0, "P2": 2, "P3": 1}           # from "blocking defects open: D001 P2 …"
    assert rows[2]["open_by_severity"] is None                                 # a passing gate lists none
