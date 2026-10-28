"""v1.8 batch 2.2 (#147): an approved change request's edit belongs inside its writer's dispatched task."""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta

import pytest
import yaml

from tests.conftest import PKG, FnExecutor, edge, mini_workflow, node
from vpg.executors import TaskPacketExecutor
from vpg import cli
from vpg.engine import Engine, EngineError

DOCS = ("product", "creative", "design", "production", "evaluation", "memory")


def _project(tmp_path, wf):
    proj = tmp_path / "p"
    (proj / "controls").mkdir(parents=True)
    for doc in DOCS:
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "h", "workflow": str(wf)}))
    return proj


def _writer_handler(text):
    def handler(ctx):   # the writer's own task makes the edit, then hands in its outputs
        (ctx.project_dir / "controls/design.md").write_text(text)
        outs = {}
        for o in ctx.node.outputs:
            (ctx.workdir / f"{o.key}.json").write_text(json.dumps({"node": ctx.node.id, "attempt": ctx.attempt}))
            outs[o.key] = f"{o.key}.json"
        return outs
    return handler


def _locked_design(tmp_path):
    proj = _project(tmp_path, mini_workflow(tmp_path, human_b=True, lock_b=("design",)))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="looks right")   # locks design.md; b_plan is its writer
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.controls["design"].status == "approved"
    return proj, eng


def _plain_writer_project(tmp_path):
    """The gated a_intake locks design.md; b_plan writes it with no gate and only flow edges out, so it could memo-pass."""
    path = mini_workflow(tmp_path)
    wf = yaml.safe_load(path.read_text())
    for n in wf["nodes"]:
        if n["id"] == "a_intake":
            n.update(human_review={"required": True, "decision": "approve intake", "outcomes": ["approved", "rejected"]},
                     outcomes=["approved", "rejected"], controls={"read": [], "write": [], "lock_on_approval": ["design"]})
        elif n["id"] == "b_plan":
            n["controls"] = {"read": ["design"], "write": ["design"], "lock_on_approval": []}
    for e in wf["edges"]:
        if e["from"] == "a_intake":
            e.update(when="approved", transition="conditional")
    wf["edges"].append(edge("E17", "a_intake", "a_intake", kind="loop", when="rejected", max_traversals=1))
    path.write_text(yaml.safe_dump(wf, sort_keys=False))
    return _project(tmp_path, path)


def _plain_writer(tmp_path):
    proj = _plain_writer_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("a_intake", "approved", actor="Wei", reason="intake looks right")   # locks design.md
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.controls["design"].status == "approved"
    return proj, eng


def _integrity_report(eng):
    return next(e.report for e in eng.state.runtime.escalations if e.node == "integrity" and not e.resolved)


def test_approving_a_change_request_says_the_edit_belongs_inside_the_writers_task(tmp_path, capsys):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    capsys.readouterr()
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--approve", "CR-001", "--reason", "agreed"]) == 0
    out = capsys.readouterr().out
    assert "CR-001 approved by Wei" in out
    assert "b_plan (the writer of design.md)" in out and "inside b_plan's task" in out and "vpg submit" in out
    assert "before the task is dispatched is outside the workflow" in out


def test_an_edit_made_before_the_writer_is_dispatched_names_the_request_and_the_writer(tmp_path, capsys):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    doc = proj / "controls/design.md"
    doc.write_text(doc.read_text() + "\nopaque bars\n")   # too early: b_plan has not been dispatched yet
    eng.run(max_steps=50)
    assert eng.state.runtime.status.value == "BLOCKED"
    report = _integrity_report(eng)
    assert report.startswith("design.md changed outside its writer's task: b_plan re-runs for approved CR-001")
    assert "vpg restore-control" in report and "inside b_plan's task" in report and "vpg submit" in report
    assert "open `vpg change-request`" not in report       # the request is already approved; a new one would not help
    capsys.readouterr()
    assert cli.main(["status", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "inside b_plan's task" in out and "vpg submit" in out    # status prints the whole message, not its first 240 characters


def test_the_documented_recovery_applies_the_request_and_later_edits_get_the_generic_advice(tmp_path):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    doc = proj / "controls/design.md"
    edited = doc.read_text() + "\nopaque bars\n"
    doc.write_text(edited)
    eng.run(max_steps=50)
    eng.restore_control("design", actor="Wei")
    eng.unblock("integrity", actor="Wei", note="restored; the writer makes the edit in its task")
    eng = Engine(proj, executor=FnExecutor({"b_plan": _writer_handler(edited)}))
    eng.run(max_steps=200)
    cr = next(c for c in eng.state.history.change_requests if c.id == "CR-001")
    assert cr.status == "applied" and doc.read_text() == edited
    eng.decide("b_plan", "approved", actor="Wei", reason="the change is in")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"

    doc.write_text(edited + "\nanother edit\n")             # no approved request is waiting any more
    eng.invalidate(["z_done"], reason="re-check")
    eng.run(max_steps=50)
    reason = _integrity_report(eng)
    assert "design.md changed outside the workflow" in reason and "open `vpg change-request`" in reason


def test_once_the_writer_ran_without_applying_the_request_a_stray_edit_gets_the_generic_advice(tmp_path):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    eng.run(max_steps=200)                                  # b_plan re-runs and leaves design.md as it was
    assert next(c for c in eng.state.history.change_requests if c.id == "CR-001").status == "approved"
    eng.decide("b_plan", "approved", actor="Wei", reason="no change needed after all")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    doc = proj / "controls/design.md"
    doc.write_text(doc.read_text() + "\nlate edit\n")
    eng.invalidate(["z_done"], reason="re-check")
    eng.run(max_steps=50)
    report = _integrity_report(eng)
    assert report.startswith("design.md changed outside the workflow")   # b_plan won't be dispatched again on its own
    assert "re-runs for approved" not in report


def test_approval_re_runs_a_writer_that_is_not_a_gate_so_the_request_is_applied(tmp_path):
    proj, eng = _plain_writer(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    doc = proj / "controls/design.md"
    edited = doc.read_text() + "\nopaque bars\n"
    doc.write_text(edited)                                  # too early: the plain writer gets the same advice
    eng.run(max_steps=50)
    assert _integrity_report(eng).startswith("design.md changed outside its writer's task: b_plan re-runs for approved CR-001")
    eng.restore_control("design", actor="Wei")
    eng.unblock("integrity", actor="Wei", note="restored; the writer makes the edit in its task")
    ex = FnExecutor({"b_plan": _writer_handler(edited)})
    eng = Engine(proj, executor=ex)
    eng.run(max_steps=200)
    assert any(n == "b_plan" for n, _ in ex.calls)          # dispatched, not memo-passed
    assert next(c for c in eng.state.history.change_requests if c.id == "CR-001").status == "applied"
    assert doc.read_text() == edited


def test_with_two_approved_requests_the_message_names_the_one_the_edit_will_apply(tmp_path):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.change_request("design", "wider margins", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    eng.approve_change_request("CR-002", by="Wei", reason="agreed")
    doc = proj / "controls/design.md"
    edited = doc.read_text() + "\nopaque bars\n"
    doc.write_text(edited)
    eng.run(max_steps=50)
    report = _integrity_report(eng)
    assert "for approved CR-001" in report and "CR-002" not in report
    eng.restore_control("design", actor="Wei")
    eng.unblock("integrity", actor="Wei", note="restored")
    eng = Engine(proj, executor=FnExecutor({"b_plan": _writer_handler(edited)}))
    eng.run(max_steps=200)
    status = {c.id: c.status for c in eng.state.history.change_requests}
    assert status == {"CR-001": "applied", "CR-002": "approved"}      # the message named the request the ledger now shows applied


def test_approving_a_request_on_a_document_no_node_writes_says_nothing_re_runs(tmp_path, capsys):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("product", "sharper claim", by="agent:critic")
    capsys.readouterr()
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--approve", "CR-001", "--reason", "agreed"]) == 0
    assert "CR-001 approved by Wei; no node writes product.md, so nothing re-runs" in capsys.readouterr().out


def test_a_change_request_grant_does_not_cover_evaluation_before_the_release_review(tmp_path):
    proj, eng = _locked_design(tmp_path)
    expires = (date.today() + timedelta(days=30)).isoformat()
    eng.grant("agent:producer", ["change-request"], expires=expires, reason="routine requests", actor="Wei")
    eng.change_request("evaluation", "density 14 for one scene", by="agent:critic")
    eng.change_request("design", "opaque bars", by="agent:critic")
    state, ledger = (proj / ".vpg/state.json").read_bytes(), (proj / ".vpg/ledger.jsonl").read_bytes()
    for decide in (lambda: eng.approve_change_request("CR-001", by="agent:producer", reason="needed"),
                   lambda: eng.reject_change_request("CR-001", by="agent:producer", reason="not needed"),
                   lambda: eng.defer_change_request("CR-001", by="agent:producer", until="the end review", reason="later")):
        with pytest.raises(EngineError, match="grant doesn't cover evaluation"):
            decide()
    assert (proj / ".vpg/state.json").read_bytes() == state and (proj / ".vpg/ledger.jsonl").read_bytes() == ledger
    eng.approve_change_request("CR-002", by="agent:producer", reason="routine")   # the grant still covers other documents
    eng.approve_change_request("CR-001", by="Wei", reason="calibrated")            # and a named human decides the bar
    assert {c.id: c.status for c in eng.state.history.change_requests} == {"CR-001": "approved", "CR-002": "approved"}


def test_status_prints_a_node_escalations_reason_not_its_report(tmp_path, capsys):
    proj = _project(tmp_path, mini_workflow(tmp_path, human_b=True))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "rejected", actor="Wei", feedback=["tighter"])
    eng.run(max_steps=10)
    eng.decide("b_plan", "rejected", actor="Wei")          # the loop's bound runs out: b_plan escalates
    esc = next(e for e in eng.state.runtime.escalations if e.node == "b_plan" and not e.resolved)
    capsys.readouterr()
    assert cli.main(["status", str(proj)]) == 0
    lines = [ln.strip() for ln in capsys.readouterr().out.splitlines()]
    assert f"b_plan: {esc.reason}" in lines


def test_a_draft_document_gets_the_generic_advice_because_no_request_is_consumed_there(tmp_path):
    proj = _plain_writer_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)                                  # waits for a_intake's approval; design.md is still a draft
    assert eng.state.controls["design"].status != "approved"
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    doc = proj / "controls/design.md"
    doc.write_text(doc.read_text() + "\nstray edit\n")
    eng.run(max_steps=50)
    assert _integrity_report(eng).startswith("design.md changed outside the workflow")


def test_a_stale_plain_writer_without_a_forced_re_run_gets_the_generic_advice(tmp_path):
    proj, eng = _plain_writer(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    eng.run(max_steps=200)                                  # b_plan re-runs (forced) but leaves design.md as it was
    assert next(c for c in eng.state.history.change_requests if c.id == "CR-001").status == "approved"
    eng.invalidate(["b_plan"], reason="upstream changed")   # STALE again, but nothing forces a re-run: it would memo-pass
    doc = proj / "controls/design.md"
    doc.write_text(doc.read_text() + "\nstray edit\n")
    eng.run(max_steps=50)
    report = _integrity_report(eng)
    assert report.startswith("design.md changed outside the workflow") and "re-runs for approved" not in report


def test_a_later_approval_that_cancels_the_writers_open_task_gets_neutral_wording(tmp_path):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=50)
    assert eng.rt("b_plan").status.value == "WAITING_RUNTIME"      # the writer's task is open
    doc = proj / "controls/design.md"
    doc.write_text(doc.read_text() + "\nopaque bars\n")           # made inside the open task
    eng.change_request("design", "wider margins", by="agent:critic")
    eng.approve_change_request("CR-002", by="Wei", reason="agreed")  # re-runs b_plan: its open task is cancelled
    eng.run(max_steps=50)
    report = _integrity_report(eng)
    assert report.startswith("design.md changed outside its writer's task: b_plan re-runs for approved CR-001")
    assert "before" not in report.split(".")[0]                    # no claim about when the edit happened


def test_a_writer_the_workflow_gained_mid_run_gets_the_generic_advice_not_a_crash(tmp_path):
    proj, eng = _locked_design(tmp_path)
    eng.change_request("design", "opaque bars", by="agent:critic")
    eng.approve_change_request("CR-001", by="Wei", reason="agreed")
    path = proj.parent / "mini.yaml"
    wf = yaml.safe_load(path.read_text())
    wf["nodes"].insert(0, node("b0_writer", 10, controls={"read": [], "write": ["design"], "lock_on_approval": []}))
    wf["edges"] += [edge("E18", "a_intake", "b0_writer"), edge("E19", "b0_writer", "d_build")]
    wf["subgraphs"][0]["nodes"].append("b0_writer")
    path.write_text(yaml.safe_dump(wf, sort_keys=False))   # the first declared writer now has no runtime record
    doc = proj / "controls/design.md"
    doc.write_text(doc.read_text() + "\nstray edit\n")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=50)
    report = _integrity_report(eng)
    assert "design.md changed outside the workflow" in report and "re-runs for approved" not in report
