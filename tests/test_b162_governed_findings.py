"""#162: a reviewer's P1–P3 that the rubric's levels demote or drop is never silent.

Water v6, N16: a P3 (an empty card) and a nit, both on CRE-POLISH, merged into one nit defect, and the P3 vanished.
CRE-POLISH is SHOULD while its rule R-UI-001 is only `validated`, so the rubric demoted the P3, yet the review still
read "fail" and nothing recorded what the reviewer had filed. These tests use a fixture rule base, so they hold whatever
the live rules' states become.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from tests.conftest import PKG, FnExecutor, mini_workflow
from tests.test_bookkeeping import _finding, events
from tests.test_hardening import make_project
from vpg.context import NodeResult
from vpg.engine import Engine
from vpg.tools import verify

# all in the creative rubric: CRE-POLISH is declared MUST (its rule decides), REG-VERDICT is declared SHOULD
RULES = {"R-UI-001": "validated",   # CRE-POLISH: advisory, two promote steps
         "R-UI-003": "approved",    # CRE-FOCUS: advisory, one promote step
         "R-NAR-002": "candidate",  # CRE-ARC: inactive, so the dimension judges nothing
         "R-DES-001": "adopted"}    # CRE-HOOK: enforced (MUST)


def _rules(tmp_path, states=RULES):
    data = json.loads((PKG / "knowledge/rules.json").read_text())
    for r in data["rules"]:
        if r["id"] in states:
            r["lifecycle"]["state"] = states[r["id"]]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data))
    return path


def _project(tmp_path):
    (tmp_path / "wf").mkdir()
    wf = mini_workflow(tmp_path / "wf", review_tool="tests.fakes:review_clean")
    data = yaml.safe_load(wf.read_text())
    next(n for n in data["nodes"] if n["id"] == "r1_review")["rubric"] = ["creative"]
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    return make_project(tmp_path, workflow=wf)


def _review_with(defects):
    """A reviewer that scores the whole rubric and files `defects` (or `defects(attempt)`)."""
    def handler(ctx):
        dims = [d for r in ctx.node.rubric for d in ctx.rubrics[r]["dimensions"] if d["check"]["type"] == "review"]
        return NodeResult(review={"reviewer": "critic", "defects": defects(ctx.attempt) if callable(defects) else defects,
                                  "scores": [{"dimension": d["id"], "score": 5, "evidence": "ok"} for d in dims]})
    return handler


def _filed(dim, severity, text):
    return {"dimension": dim, "severity": severity, "description": text, "traced_node": "d_build"}


def _run(tmp_path, defects, handlers=None):
    proj = _project(tmp_path)
    eng = Engine(proj, executor=FnExecutor({"r1_review": _review_with(defects), **(handlers or {})}), rules_path=_rules(tmp_path))
    eng.run(max_steps=200)
    return proj, eng


def _warnings(eng, principal=None):
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    if principal:
        ctx.config = {**ctx.config, "autonomy": {"principal": principal}}
    verify.final(ctx)
    return json.loads((ctx.workdir / "verification.json").read_text())["warnings"], ctx


WATER = [_filed("CRE-POLISH", "P3", "the proposal card is empty"), _filed("CRE-POLISH", "nit", "privacy bars over ordinary words")]


def test_a_reviewers_p3_on_an_advisory_dimension_is_a_nit_but_never_silent(tmp_path):
    proj, eng = _run(tmp_path, WATER)
    review = next(r for r in eng.state.evaluation.reviews if r.node == "r1_review")
    [defect] = [d for d in eng.state.evaluation.defects if d.id in review.defect_ids]
    assert defect.severity == "nit" and review.verdict == "pass"            # the verdict agrees with what the gate routes
    assert "the proposal card is empty [filed P3; not blocking: rule R-UI-001 is not yet adopted]" in defect.description
    assert "privacy bars over ordinary words" in defect.description and defect.description.count("[filed") == 1
    [event] = events(proj, "finding_governed")
    assert {k: event[k] for k in ("defect", "dimension", "filed", "to", "rule", "review", "version", "attempt")} == {
        "defect": defect.id, "dimension": "CRE-POLISH", "filed": "P3", "to": "nit", "rule": "R-UI-001", "review": review.id,
        "version": review.version, "attempt": 1}
    assert eng.state.runtime.status.value == "RUN_PASS"                     # a nit never holds the gate


def test_a_must_dimension_keeps_the_filed_severity_and_a_quoted_note_goes(tmp_path):
    quoted = "the hook lands late [filed P3; not blocking: rule R-DES-001 is not yet adopted]"
    proj = _project(tmp_path)
    eng = Engine(proj, executor=FnExecutor({"r1_review": _review_with([_filed("CRE-HOOK", "P3", quoted)])}),
                 rules_path=_rules(tmp_path))
    for _ in range(60):
        eng.step()
        if any(r.node == "r1_review" for r in eng.state.evaluation.reviews):
            break
    review = next(r for r in eng.state.evaluation.reviews if r.node == "r1_review")
    [defect] = [d for d in eng.state.evaluation.defects if d.id in review.defect_ids]
    assert (review.verdict, defect.severity, defect.description) == ("fail", "P3", "the hook lands late")
    assert events(proj, "finding_governed") == []


def test_n23_gives_the_steps_each_cause_allows(tmp_path):
    filed = [_filed("CRE-POLISH", "P3", "card empty"), _filed("CRE-FOCUS", "P2", "pointer wanders"),
             _filed("REG-VERDICT", "P3", "worse than the release"), _filed("CRE-ARC", "P2", "the middle sags")]
    proj, eng = _run(tmp_path, filed)
    review = next(r for r in eng.state.evaluation.reviews if r.node == "r1_review")
    assert review.verdict == "pass" and eng.state.runtime.status.value == "RUN_PASS"
    dropped = [e for e in events(proj, "finding_governed") if e["to"] == "dropped"]
    assert [(e["dimension"], e["defect"], e["rule"]) for e in dropped] == [("CRE-ARC", "", "R-NAR-002")]
    warnings, ctx = _warnings(eng, principal="Jane Placeholder")
    by_dim = {w.split(" ", 1)[0]: w for w in warnings if "reviewer-filed blocking findings" in w}
    assert set(by_dim) == {"CRE-POLISH", "CRE-FOCUS", "REG-VERDICT", "CRE-ARC"}
    polish = by_dim["CRE-POLISH"]
    assert "promote rule R-UI-001 to approved, then adopted" in polish and "--by 'Jane Placeholder'" in polish
    assert "--adopted-in rubrics/creative.yaml" in polish and "`vpg accept-config`" in polish
    assert "promote rule R-UI-003 to adopted:" in by_dim["CRE-FOCUS"] and "--to approved" not in by_dim["CRE-FOCUS"]
    assert by_dim["REG-VERDICT"].endswith("making REG-VERDICT block is a rubric change")         # its rubric says SHOULD
    assert by_dim["CRE-ARC"].startswith("CRE-ARC dropped reviewer-filed blocking findings (rule R-NAR-002 is candidate")
    assert "not recorded (filed P2 by rev-r1_review-v1)" in by_dim["CRE-ARC"] and "needs evidence and validation" in by_dim["CRE-ARC"]
    digest = (ctx.workdir / "decision-digest.md").read_text()
    assert sum(line.startswith("- K") and "reviewer-filed blocking findings" in line for line in digest.splitlines()) == 4


def _same_build(ctx):
    (ctx.workdir / "d_build.json").write_text('{"build": "unchanged"}')
    return {"d_build": "d_build.json"}


def test_a_carried_forward_review_still_reports_its_demotions(tmp_path):
    proj, eng = _run(tmp_path, WATER, handlers={"d_build": _same_build})
    eng.invalidate_nodes(["d_build"], reason="re-render", actor="Wei")      # a new version the reviewer doesn't re-judge
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.runtime.current_version == 2
    assert next(r for r in reversed(eng.state.evaluation.reviews) if r.node == "r1_review").carried_forward
    warnings, _ = _warnings(eng)
    assert any(w.startswith("CRE-POLISH counted as nits") and "(filed P3 by rev-r1_review-v1)" in w for w in warnings)


@pytest.mark.parametrize("second, left", [([_filed("CRE-FOCUS", "P2", "pointer wanders")], ["CRE-FOCUS"]), ([], [])],
                         ids=["re-filed elsewhere", "nothing filed"])
def test_a_re_review_of_the_same_version_replaces_its_earlier_attempt(tmp_path, second, left):
    proj, eng = _run(tmp_path, lambda attempt: WATER if attempt == 1 else second)
    eng.invalidate_nodes(["r1_review"], reason="its rubric changed", actor="Wei")
    eng.run(max_steps=200)
    warnings, _ = _warnings(eng)
    governed = [w for w in warnings if "reviewer-filed blocking findings" in w]
    assert [w.split(" ", 1)[0] for w in governed] == left                    # attempt 1's CRE-POLISH is no longer judged


def test_governed_warnings_read_the_current_judgment_only():
    reviews = [SimpleNamespace(node="r1", id="rev-r1-v1", version=1, carried_forward=False),
               SimpleNamespace(node="r1", id="rev-r1-v2", version=2, carried_forward=False),
               SimpleNamespace(node="r2", id="rev-r2-v1", version=1, carried_forward=False),
               SimpleNamespace(node="r2", id="rev-r2-v2", version=2, carried_forward=True)]
    st = SimpleNamespace(evaluation=SimpleNamespace(reviews=reviews, defects=[SimpleNamespace(id="D9", status="resolved")]))

    def ev(review, dim, defect, rule="R-1", attempt=1):
        return {"type": "finding_governed", "data": {"review": review, "dimension": dim, "defect": defect, "filed": "P2",
                                                     "to": "nit", "rule": rule, "reason": "why", "attempt": attempt}}
    out = verify._governed_warnings(
        [ev("rev-r1-v1", "CRE-X", "D1"),                      # superseded by r1's fresh v2 review
         ev("rev-r1-v2", "CRE-X", "D2"), ev("rev-r1-v2", "CRE-X", "D2"),
         ev("rev-r2-v1", "OTH-Y", "D3", rule=""),             # r2's v2 review is a carried copy of this one
         ev("rev-r2-v1", "OTH-Y", "D9", rule=""),             # resolved since
         {"type": "finding_governed"}, "junk"],
        st, 2, {"creative": {"dimensions": [{"id": "CRE-X"}]}}, {"R-1": "approved"}, "Wei")
    assert len(out) == 2
    assert "D2 (filed P2 by rev-r1-v2)" in out[0] and "D1" not in out[0] and out[0].count("D2") == 1
    assert "promote rule R-1 to adopted:" in out[0] and "rubrics/creative.yaml" in out[0]
    assert "D3" in out[1] and "D9" not in out[1] and out[1].endswith("making OTH-Y block is a rubric change")
    assert "is already adopted; then accept" in verify._block_steps("CRE-X", "R-1", {"R-1": "adopted"}, {}, "Wei", "rev")
    for state in ("retired", None):   # retired is terminal; a rule missing from the base can't be promoted either
        steps = verify._block_steps("CRE-X", "R-1", {"R-1": state} if state else {}, {}, "Wei", "rev")
        assert steps.endswith("making CRE-X block needs a new rule or a rubric change") and "evidence" not in steps


def test_every_quoted_note_is_stripped_from_reviewer_findings(tmp_path):
    eng = Engine(_project(tmp_path), executor=FnExecutor(), rules_path=_rules(tmp_path))
    note = " [filed P3; not blocking: rule R-UI-001 is not yet adopted]"
    merged = f"card empty{note} | bars{note}"
    [polish] = eng._govern_findings([_finding("CRE-POLISH", "d_build", "review:r1_review", severity="P2", message=merged)])
    assert polish.message == "card empty | bars [filed P2; not blocking: rule R-UI-001 is not yet adopted]"
    [nit] = eng._govern_findings([_finding("CRE-POLISH", "d_build", "review:r1_review", severity="nit", message=f"bars{note}")])
    assert nit.message == "bars"
    [tool] = eng._govern_findings([_finding("CRE-POLISH", "d_build", "tool:check", severity="P2", message="measured")])
    assert (tool.severity, tool.message) == ("nit", "measured")             # tool rows are reported by the harness instead
