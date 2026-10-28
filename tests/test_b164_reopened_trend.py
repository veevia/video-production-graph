"""#164: the quality trend marks a version re-opened after its gate passed with none open.

Water r08: an outside review's findings on v7 were fixed by re-opening N13 after N23 had passed v7, so they were never
filed in v7, and the trend read v7 → v8 as "not converging" (0 → 2 open) with no hint why.
"""
from __future__ import annotations

from types import SimpleNamespace

from tests.conftest import FnExecutor, flag
from tests.test_bookkeeping import ROUTES
from tests.test_hardening import make_project
from tests.test_quality_budget import row
from vpg.budget import convergence, trend_lines, trend_rows


def test_a_version_after_a_passed_one_is_marked_and_explained(tmp_path):
    from vpg.engine import Engine
    proj = make_project(tmp_path, review_tool=ROUTES)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.evaluation.gate_results[-1].outcome == "pass"
    flag(proj, "defect")                                   # an outside look finds something in the passed version
    eng.invalidate_nodes(["d_build"], reason="re-open after an outside review", actor="Wei")
    for _ in range(60):
        eng.step()
        if len(eng.state.evaluation.gate_results) == 2:
            break
    rows = eng._trend()
    assert [r["version"] for r in rows] == [1, 2] and rows[-1]["reopened_after"] == 1 and "reopened_after" not in rows[0]
    assert trend_lines(rows)[-1].endswith(" persisting · re-opened after v1 passed")
    assert convergence(rows) == (False, f"v1 → v2: 0 → 1 open P1–P3, 0 resolved, 1 new; v1 {NOTE}")
    event = eng._trend_event()
    assert event["trend"][-1]["reopened_after"] == 1 and "reopened_after" not in event["trend"][0]


def _gate(version, outcome, open_=0, snapshot=True):
    return SimpleNamespace(version=version, outcome=outcome, open_blocking=[f"D{i}" for i in range(open_)], reasons=[],
                           trend={"open": open_, "open_by_severity": None, "cycle": 0, "severities": {}} if snapshot else None)


def _rows(*gates, latest=None):
    st = SimpleNamespace(evaluation=SimpleNamespace(gate_results=list(gates), defects=[], comparisons=[]))
    return trend_rows(st, ["P1", "P2", "P3"], latest)


def test_which_passes_re_open_which_round():
    marked = lambda rows: [r.get("reopened_after") for r in rows]                       # noqa: E731
    assert marked(_rows(_gate(1, "pass"), _gate(2, "rework", 2))) == [None, 1]           # built after v1 passed (invalidate)
    assert marked(_rows(_gate(1, "pass", 1), _gate(2, "rework", 2))) == [None, None]     # a provisional pass left one open
    assert marked(_rows(_gate(1, "pass"), _gate(1, "rework", 3), _gate(2, "rework", 1))) == [1, None]   # feedback re-gated v1
    assert marked(_rows(_gate(1, "rework", 1), _gate(2, "pass"), _gate(2, "rework", 2), _gate(3, "rework", 2))) == \
        [None, 2, None]                                                                  # v3's stall stays unexplained
    assert marked(_rows(_gate(1, "pass", snapshot=False), _gate(2, "rework", 1))) == [None, 1]           # recorded before v1.6
    assert marked(_rows(_gate(1, "rework", 2), _gate(2, "rework", 1))) == [None, None]
    latest = {"version": 2, "open": 0, "open_by_severity": None, "cycle": 1, "severities": {}}
    assert marked(_rows(_gate(1, "rework", 2), latest=latest)) == [None, None]        # the gate being decided passes nothing yet
    regate = {"version": 1, "open": 2, "open_by_severity": None, "cycle": 1, "severities": {}}
    assert marked(_rows(_gate(1, "pass"), latest=regate)) == [1]                        # v1 being re-gated after it passed


NOTE = "had passed its gate with none open and this round re-opened it, so no earlier round of this work compares"


def test_only_a_round_that_is_not_converging_gets_the_note():
    for prev, cur, at in ((row(7, 0, 0, 0), row(8, 2, 0, 2), 7), (row(7, 0, 0, 0), row(8, 0, 2, 0), 7),
                          (row(1, 1, 0, 1), row(2, 3, 1, 3), 2)):                       # the last: v2 re-gated after it passed
        plain = convergence([prev, cur])
        marked = convergence([prev, {**cur, "reopened_after": at}])
        assert marked[0] == plain[0] is False and "persist" not in marked[1]
        assert marked[1] == f"{plain[1].split(';')[0]}; v{at} {NOTE}"
    ok_rows = [row(7, 2, 0, 2), {**row(8, 1, 2, 1), "reopened_after": 7}]
    assert convergence(ok_rows) == convergence([row(7, 2, 0, 2), row(8, 1, 2, 1)])           # converging: no note
    assert "re-opened" not in convergence([row(1, 2, 0, 2), row(2, 1, 1, 0)])[1]
