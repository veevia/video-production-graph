"""A human's change-request decision must be recorded as the human made it (first real run: #81, #82).

#81: `vpg change-request --approve CR-… --reason …` dropped the reason, so the release approver could not see why a
locked document was changed (the SRA run's CR-007 is a calibration call its approver was asked to explain). #82: a change
request could not be declined, so a request the human deferred stayed "open" for good."""

from __future__ import annotations

import pytest

from tests.conftest import FnExecutor
from tests.test_bookkeeping import events
from tests.test_release_record import _human_gated_project
from vpg import cli
from vpg.engine import Engine, EngineError
from vpg.provenance import Ledger
from vpg.tools import verify
from vpg.tools.verify import human_record


def test_a_change_request_decision_keeps_the_humans_reason_and_a_rejection_re_runs_nothing(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.change_request("evaluation", "density limit 18 (R-DEN-001)", by="agent:accessibility-reviewer")
    eng.change_request("design", "blurred -> opaque bars", by="agent:independent-design-critic")
    eng.approve_change_request("CR-001", by="Wei", reason="calibrated on the template's chapter 01")
    before = len(events(proj, "invalidated"))
    state, ledger = (proj / ".vpg/state.json").read_bytes(), (proj / ".vpg/ledger.jsonl").read_bytes()
    for actor in ("agent:critic", "dry-run-auto"):
        with pytest.raises(EngineError, match="named human"):
            eng.reject_change_request("CR-002", by=actor, reason="after release")
    with pytest.raises(EngineError, match="needs the human's reason"):
        eng.reject_change_request("CR-002", by="Wei", reason="  ")
    assert (proj / ".vpg/state.json").read_bytes() == state and (proj / ".vpg/ledger.jsonl").read_bytes() == ledger   # a refusal leaves no trace
    eng.reject_change_request("CR-002", by="Wei", reason="wording only; fix it after release")
    cr = next(c for c in eng.state.history.change_requests if c.id == "CR-002")
    assert cr.status == "rejected" and cr.approved_by is None
    assert len(events(proj, "invalidated")) == before                      # the document stays as approved; nothing re-runs
    with pytest.raises(EngineError, match="only an open or deferred change request can be approved"):
        eng.approve_change_request("CR-002", by="Wei")
    with pytest.raises(EngineError, match="only an open or deferred change request can be rejected"):
        eng.reject_change_request("CR-001", by="Wei", reason="changed my mind")
    rows = {(r["action"], r["subject"]): r for r in human_record(Ledger(proj / ".vpg/ledger.jsonl").events())}
    approved = rows[("change_request_approved", "CR-001 on evaluation.md")]
    assert approved["reason"] == "calibrated on the template's chapter 01" and "agent_filed" in approved["codes"]
    rejected = rows[("change_request_rejected", "CR-002 on design.md")]
    assert rejected["reason"] == "wording only; fix it after release"
    assert "agent_filed" not in rejected["codes"]      # declining an agent's request is not "approving an agent-filed change"


def test_the_change_request_cli_records_reasons_rejects_and_refuses_two_decisions_at_once(project, capsys):
    proj, _ = project
    Engine(proj, executor=FnExecutor()).step()
    for doc in ("evaluation", "design"):
        assert cli.main(["change-request", str(proj), "--doc", doc, "--reason", "from the critic", "--actor", "agent:critic"]) == 0
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--approve", "CR-001", "--reject", "CR-002", "--reason", "x"]) == 4
    assert "exclusive" in capsys.readouterr().err
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--reject", "CR-002"]) == 4
    assert "needs the human's reason" in capsys.readouterr().err
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--approve", "CR-001", "--reason", "calibrated"]) == 0
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--reject", "CR-002", "--reason", "wording only"]) == 0
    assert "CR-002 rejected by Wei" in capsys.readouterr().out
    decided = {e["type"]: e["data"] for e in Ledger(proj / ".vpg/ledger.jsonl").events() if e["type"].startswith("change_request_")}
    assert decided["change_request_approved"]["reason"] == "calibrated" and decided["change_request_rejected"]["reason"] == "wording only"


def test_the_release_summary_shows_a_declined_agent_request_as_declined_not_approved(tmp_path):
    """The SRA case: Wei declines an agent's film-wide limit and approves another agent's request. The release approver
    must read one agent-filed approval, and the declined request with what it asked for, not a second approval."""
    proj = _human_gated_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.change_request("evaluation", "film-wide density limit 18", by="agent:accessibility-reviewer")
    eng.change_request("design", "blurred to opaque bars", by="agent:independent-design-critic")
    eng.reject_change_request("CR-001", by="Wei", reason="one scene's own limit instead")
    eng.approve_change_request("CR-002", by="Wei", reason="the film uses opaque bars")
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    verify.final(ctx)
    summary = (ctx.workdir / "release-summary.md").read_text()
    assert "1 approve an agent-filed change request" in summary
    assert ("- CR-001 on evaluation.md: rejected; filed by agent:accessibility-reviewer; the human record gives who declined it "
            "and why. Request: film-wide density limit 18") in summary
    assert "- CR-002 on design.md: approved; filed by agent:independent-design-critic, approved by Wei: blurred to opaque bars" in summary
