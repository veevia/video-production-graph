"""#185: a relayed invalidation is decided by the action, not by the sentence that describes it.

The batch 1 relay allow-list lets an agent relay a declared human's verbatim words for the grantable actions mid-run
(unblock, invalidate, change-request decisions, resolve, restore-control, feedback triage). `invalidate_nodes` describes
the act as "invalidating <targets> re-opens a human's decision (<gates>), which", so the scope check, which matched the
bare word "invalidating", refused every mid-run relayed invalidation that re-opened a human gate. An agent with neither a
relay nor a grant was also told only that "--on-behalf-of must be one of the declared humans", not what would work.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
import yaml

from tests.conftest import FnExecutor
from tests.test_hardening import make_project
from vpg import cli
from vpg.engine import Engine, EngineError
from vpg.models import Defect
from vpg.provenance import Ledger
from vpg.tools import verify

RELAY = {"on_behalf_of": "Wei", "instruction": "re-open the plan; the storyboard changed"}


def project(tmp_path, declared=True, provisional=True):
    proj = make_project(tmp_path, human_b=True)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    if declared:
        cfg["humans"] = ["Wei"]
    if provisional:
        cfg.update({"privacy": {"names": "none", "reason": "synthetic"},
                    "reviews": {"binding": "require", "independence": "require"},
                    "loop_budget": {"on_exhaust": "extend_if_converging"},
                    "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return proj


def passed_gate(tmp_path, **kw):
    """b_plan is a human gate that Wei approved; the run is mid-run (the release node does not wait)."""
    proj = project(tmp_path, **kw)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.rt("b_plan").status.value == "WAITING_HUMAN"
    eng.decide("b_plan", "approved", actor="Wei", reason="ok")
    eng.run(max_steps=200)
    assert eng.rt("b_plan").status.value == "STEP_PASS" and not eng._at_release_wait()
    return proj, eng


@pytest.mark.parametrize("instruction_at", ["2026-10-15T17:00:00-05:00", ""])
def test_relayed_invalidation_reopens_a_passed_gate_mid_run(tmp_path, instruction_at):
    proj, eng = passed_gate(tmp_path)
    changed = eng.invalidate_nodes(["b_plan"], reason="storyboard changed", actor="agent:ops", instruction_at=instruction_at,
                                   **RELAY)
    assert "b_plan" in changed and eng.rt("b_plan").status.value != "STEP_PASS"
    ev = [e for e in Ledger(proj / ".vpg/ledger.jsonl").events("invalidated")][-1]
    assert ev["data"]["relayed"] is True and ev["data"]["principal"] == "Wei" and ev["data"]["instruction"] == RELAY["instruction"]
    assert ev["data"].get("instruction_at", "") == instruction_at
    rows = [r for r in verify.human_record(Ledger(proj / ".vpg/ledger.jsonl").events(), declared_humans=["Wei"])
            if r["action"] == "invalidate"]
    assert len(rows) == 1 and rows[0]["actor"] == "agent:ops" and rows[0]["principal"] == "Wei" and rows[0]["relayed"]


def test_the_stamp_covers_only_the_relayed_act(tmp_path):
    proj, eng = passed_gate(tmp_path)
    eng.invalidate_nodes(["b_plan"], reason="storyboard changed", actor="agent:ops", **RELAY)
    assert eng.ledger.stamp == {}
    eng.invalidate_nodes(["d_build"], reason="code changed", actor="Wei")
    later = [e for e in Ledger(proj / ".vpg/ledger.jsonl").events("invalidated")][-1]
    assert later["data"]["targets"] == ["d_build"] and "relayed" not in later["data"] and "principal" not in later["data"]


def test_a_relayed_human_decision_is_still_refused_mid_run(tmp_path):
    proj = project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    with pytest.raises(EngineError, match="relayed a human decision is refused mid-run"):
        eng.decide("b_plan", "approved", actor="agent:ops", reason="relayed", **RELAY)
    assert eng.rt("b_plan").status.value == "WAITING_HUMAN"


def test_a_relay_still_needs_a_declared_principal_and_their_words(tmp_path):
    proj, eng = passed_gate(tmp_path)
    with pytest.raises(EngineError) as e:
        eng.invalidate_nodes(["b_plan"], reason="r", actor="agent:ops", on_behalf_of="Someone Else", instruction="go")
    assert str(e.value) == ("invalidating b_plan re-opens a human's decision (b_plan): --on-behalf-of must be one of the "
                            "declared humans: Wei")
    with pytest.raises(EngineError, match=r"\(b_plan\): a relayed act needs --instruction"):
        eng.invalidate_nodes(["b_plan"], reason="r", actor="agent:ops", on_behalf_of="Wei")
    assert eng.rt("b_plan").status.value == "STEP_PASS"


def test_an_agent_without_relay_or_grant_is_told_every_option(tmp_path):
    proj, eng = passed_gate(tmp_path)
    with pytest.raises(EngineError) as e:
        eng.invalidate_nodes(["b_plan"], reason="r", actor="agent:ops")
    msg = str(e.value)
    assert msg.startswith("invalidating b_plan re-opens a human's decision (b_plan), which needs one of the declared humans "
                          "(Wei) acting in their own name, or an agent with a live grant for 'invalidate'")
    assert "a live grant for 'invalidate'" in msg and "--on-behalf-of Wei --instruction" in msg and ", which:" not in msg


def test_a_grant_still_covers_invalidation(tmp_path):
    proj, eng = passed_gate(tmp_path)
    g = eng.grant("agent:ops", ["invalidate"], (date.today() + timedelta(days=30)).isoformat(), "routine re-checks", actor="Wei")
    eng.invalidate_nodes(["b_plan"], reason="r", actor="agent:ops")
    ev = [e for e in Ledger(proj / ".vpg/ledger.jsonl").events("invalidated")][-1]
    assert ev["data"]["grant"] == g.id and "relayed" not in ev["data"]


def test_the_relay_option_is_offered_only_where_a_relay_is_accepted(tmp_path):
    proj = project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    with pytest.raises(EngineError) as e:
        eng.decide("b_plan", "approved", actor="agent:ops", reason="no relay")
    assert "a live grant for 'approve:b_plan'" in str(e.value) and "--on-behalf-of" not in str(e.value)


def test_without_declared_humans_no_relay_is_offered(tmp_path):
    proj, eng = passed_gate(tmp_path, declared=False, provisional=False)
    with pytest.raises(EngineError) as e:
        eng.invalidate_nodes(["b_plan"], reason="r", actor="agent:ops")
    assert "a live grant for 'invalidate'" in str(e.value) and "--on-behalf-of" not in str(e.value)


# ------------------------------------------------------------- a refusal never offers a route its own caller refuses

FIX = {"signature": "s", "severity": "P2", "dimension": "TECH-X", "description": "d", "traced_node": "d_build"}


def started(tmp_path):
    proj = project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert not eng._at_release_wait()
    return proj, eng


def _waive(eng, relay):
    eng.unblock("a_intake", actor="agent:ops", waive=["G1"], **relay)


def _evaluation_cr(eng, relay):
    cr = eng.change_request("evaluation", "loosen a threshold", by="agent:writer")
    eng.approve_change_request(cr.id, by="agent:ops", **relay)


def _own_cr(eng, relay):
    cr = eng.change_request("design", "tighten the type scale", by="agent:ops")
    eng.approve_change_request(cr.id, by="agent:ops", **relay)


def _review_defect(eng, relay):
    eng.state.evaluation.defects.append(Defect(id="D900", source="rev-r1_review", **FIX))
    eng.resolve_defect("D900", actor="agent:ops", evidence="fixed in a2", **relay)


@pytest.mark.parametrize("act,plain,relayed", [
    (_waive, "waiving an intake gap is refused mid-run for an agent", "waiving an intake gap is refused mid-run for an agent"),
    (_evaluation_cr, "on evaluation.md is a bar change", "relayed change requests that touch evaluation or thresholds"),
    (_review_defect, "a live grant for 'resolve'", "relayed resolve of review-raised D900 is allowed only at the release"),
    (_own_cr, "an agent never decides its own change request", "an agent never decides its own change request"),
])
def test_a_relay_is_never_offered_where_the_caller_refuses_it(tmp_path, act, plain, relayed):
    """The mid-run refusals that live in their callers (review I1, I1-r2): the refusal names no relay, and a relay that
    follows anyway is still refused."""
    proj, eng = started(tmp_path)
    with pytest.raises(EngineError) as e:
        act(eng, {})
    assert plain in str(e.value) and "--on-behalf-of" not in str(e.value)
    with pytest.raises(EngineError, match=relayed):
        act(eng, RELAY)


def test_a_relay_is_offered_for_a_defect_the_review_did_not_raise(tmp_path):
    proj, eng = started(tmp_path)
    eng.state.evaluation.defects.append(Defect(id="D901", source="tool:n15", **FIX))
    with pytest.raises(EngineError) as e:
        eng.resolve_defect("D901", actor="agent:ops", evidence="fixed")
    assert "--on-behalf-of Wei" in str(e.value) and "--instruction-at" not in str(e.value)


def test_at_the_release_wait_the_hint_includes_the_instruction_time(tmp_path, monkeypatch):
    proj, eng = started(tmp_path)
    with pytest.raises(EngineError) as e:
        eng._authorize("agent:ops", "approve:b_plan", "a human decision")
    assert "--on-behalf-of" not in str(e.value)                       # mid-run, a relayed decision would be refused
    monkeypatch.setattr(Engine, "_at_release_wait", lambda self: True)
    with pytest.raises(EngineError) as e:
        eng._authorize("agent:ops", "approve:b_plan", "a human decision")
    assert "--on-behalf-of Wei" in str(e.value) and "--instruction-at" in str(e.value)


def test_a_relayed_invalidation_may_reopen_frozen_inputs_and_is_recorded(tmp_path):
    proj = make_project(tmp_path, z_gate="freeze_inputs")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Wei"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng._freeze_inputs()
    with pytest.raises(EngineError, match=r"\(z_done \(the frozen inputs\)\), which needs one of the declared humans"):
        eng.invalidate_nodes(["c_audio"], reason="new take", actor="agent:ops")
    eng.invalidate_nodes(["c_audio"], reason="new take", actor="agent:ops", **RELAY)
    ev = [e for e in Ledger(proj / ".vpg/ledger.jsonl").events("inputs_unfrozen")][-1]
    assert ev["data"]["relayed"] is True and ev["data"]["principal"] == "Wei"


@pytest.mark.parametrize("when", ["yesterday-ish", "2026-10-15T17:00:00"])
def test_a_relay_time_must_be_an_iso_timestamp_with_timezone(tmp_path, when):
    proj, eng = passed_gate(tmp_path)
    with pytest.raises(EngineError, match="--instruction-at must be an ISO timestamp with timezone"):
        eng.invalidate_nodes(["b_plan"], reason="r", actor="agent:ops", instruction_at=when, **RELAY)
    assert eng.rt("b_plan").status.value == "STEP_PASS"


def test_an_ungated_invalidation_records_its_relay(tmp_path):
    proj, eng = passed_gate(tmp_path)
    eng.invalidate_nodes(["c_audio"], reason="new take", actor="agent:ops", **RELAY)   # no human gate below c_audio
    ev = [e for e in Ledger(proj / ".vpg/ledger.jsonl").events("invalidated")][-1]
    assert ev["data"]["targets"] == ["c_audio"] and ev["data"]["relayed"] is True and ev["data"]["principal"] == "Wei"
    with pytest.raises(EngineError, match="--on-behalf-of must be one of the declared humans"):
        eng.invalidate_nodes(["c_audio"], reason="r", actor="agent:ops", on_behalf_of="Someone Else", instruction="go")


def test_a_multi_word_principal_is_shell_quoted_in_the_hint(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["humans"] = ["Jane Placeholder"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    eng = Engine(proj, executor=FnExecutor())
    with pytest.raises(EngineError) as e:
        eng._authorize("agent:ops", "invalidate", "invalidating b_plan")
    assert "--on-behalf-of 'Jane Placeholder' --instruction" in str(e.value)


def test_blank_relay_fields_are_no_relay(tmp_path):
    proj, eng = passed_gate(tmp_path)
    eng.invalidate_nodes(["c_audio"], reason="r", actor="agent:ops", on_behalf_of=" ", instruction="  ", instruction_at=" ")
    ev = [e for e in Ledger(proj / ".vpg/ledger.jsonl").events("invalidated")][-1]
    assert ev["data"]["targets"] == ["c_audio"] and "relayed" not in ev["data"]


def test_the_mid_run_deny_list_wins_over_the_action(tmp_path):
    """Review S1: a waiver or a human decision is never relayed mid-run, even if a future caller files it under a
    grantable action."""
    proj, eng = started(tmp_path)
    for what in ("waiving an intake gap", "a human decision"):
        assert eng._relay_scope_ok(what, "unblock") is False
        with pytest.raises(EngineError, match="refused mid-run"):
            eng._check_relay_scope(what, "agent:ops", {"relayed": True}, action="unblock")


def test_a_grant_that_does_not_cover_the_act_is_named_before_the_relay_hint(tmp_path):
    proj, eng = passed_gate(tmp_path)
    g = eng.grant("agent:ops", ["unblock"], (date.today() + timedelta(days=30)).isoformat(), "routine unblocks", actor="Wei")
    with pytest.raises(EngineError) as e:
        eng.invalidate_nodes(["b_plan"], reason="r", actor="agent:ops")
    msg = str(e.value)
    assert f"agent:ops's grants {g.id} don't cover it or have ended" in msg
    assert msg.index("don't cover it") < msg.index("--on-behalf-of Wei")


def test_a_review_raised_defect_at_the_release_wait_is_offered_a_timed_relay(tmp_path, monkeypatch):
    proj, eng = started(tmp_path)
    eng.state.evaluation.defects.append(Defect(id="D902", source="rev-r1_review", **FIX))
    monkeypatch.setattr(Engine, "_at_release_wait", lambda self: True)
    with pytest.raises(EngineError) as e:
        eng.resolve_defect("D902", actor="agent:ops", evidence="fixed")
    assert "--on-behalf-of Wei" in str(e.value) and "--instruction-at" in str(e.value)


def test_no_route_is_offered_for_a_defect_nobody_can_close_by_hand(tmp_path, monkeypatch):
    """A tool-check defect is refused up front for everyone; an independent review's defect for an agent mid-run."""
    proj, eng = started(tmp_path)
    eng.state.evaluation.defects.append(Defect(id="D903", source="rev-r1_review", **FIX))
    monkeypatch.setattr(Engine, "_from_tool_check", lambda self, d: d.id == "D903")
    for actor in ("agent:ops", "Wei"):
        with pytest.raises(EngineError) as e:
            eng.resolve_defect("D903", actor=actor, evidence="fixed")
        assert "cannot close it" in str(e.value) and "--on-behalf-of" not in str(e.value) and "grant" not in str(e.value)
    monkeypatch.setattr(Engine, "_from_tool_check", lambda self, d: False)
    monkeypatch.setattr(Engine, "_independent_review_defect", lambda self, d: object() if d.id == "D903" else None)
    with pytest.raises(EngineError) as e:
        eng.resolve_defect("D903", actor="agent:ops", evidence="fixed")
    assert "came from independent review rev-r1_review; the producer cannot close it" in str(e.value)
    assert "--on-behalf-of" not in str(e.value) and "grant" not in str(e.value)
    with pytest.raises(EngineError, match="relayed resolve of review-raised D903 is allowed only at the release end review"):
        eng.resolve_defect("D903", actor="agent:ops", evidence="fixed", **RELAY)


def test_a_human_never_carries_relay_fields_on_an_invalidation(tmp_path):
    """Gated or not, a declared human acts in their own name (an ungated invalidation used to drop the fields silently)."""
    proj, eng = passed_gate(tmp_path)
    for target in ("c_audio", "b_plan"):
        with pytest.raises(EngineError, match="--on-behalf-of/--instruction are only for relayed agent acts"):
            eng.invalidate_nodes([target], reason="r", actor="Wei", **RELAY)
    eng.invalidate_nodes(["b_plan"], reason="r", actor="Wei", on_behalf_of=" ", instruction=" ")   # blank fields: no relay


def test_the_cli_refuses_a_relay_flag_given_blank():
    from types import SimpleNamespace
    assert cli._relay(SimpleNamespace(on_behalf_of=None, instruction=None, instruction_at=None)) == {
        "on_behalf_of": "", "instruction": "", "instruction_at": ""}
    with pytest.raises(EngineError, match="--on-behalf-of was given but is blank"):
        cli._relay(SimpleNamespace(on_behalf_of="", instruction="go", instruction_at=None))
    with pytest.raises(EngineError, match="--instruction-at was given but is blank"):
        cli._relay(SimpleNamespace(on_behalf_of="Wei", instruction="go", instruction_at="  "))


def test_an_independent_review_defect_at_the_release_wait_is_offered_a_relay(tmp_path, monkeypatch):
    """At the release wait the principal's relay is the route (review N3-r3); the up-front refusal is mid-run only."""
    proj, eng = started(tmp_path)
    eng.state.evaluation.defects.append(Defect(id="D904", source="rev-r1_review", **FIX))
    monkeypatch.setattr(Engine, "_independent_review_defect", lambda self, d: object() if d.id == "D904" else None)
    monkeypatch.setattr(Engine, "_at_release_wait", lambda self: True)
    with pytest.raises(EngineError) as e:
        eng.resolve_defect("D904", actor="agent:ops", evidence="fixed")
    assert "--on-behalf-of Wei" in str(e.value) and "the producer cannot close it" not in str(e.value)


def test_the_cli_exits_4_on_a_blank_relay_flag(tmp_path):
    proj, eng = passed_gate(tmp_path)
    assert cli.main(["invalidate", str(proj), "b_plan", "--reason", "r", "--actor", "agent:ops",
                     "--on-behalf-of", "", "--instruction", "go"]) == 4
    assert eng.rt("b_plan").status.value == "STEP_PASS"


def test_an_adoption_relay_needs_words_and_a_time_that_are_not_blank(tmp_path):
    """Review S2-r3: blank words were accepted at the release wait and recorded as an empty instruction."""
    from tests.test_bookkeeping import release_workflow
    proj = make_project(tmp_path, workflow=release_workflow(tmp_path, human_b=True))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="ok")
    eng.run(max_steps=300)
    assert eng._at_release_wait()
    cfg.update({"humans": ["Wei"], "privacy": {"names": "none", "reason": "synthetic"},
                "reviews": {"binding": "require", "independence": "require"},
                "loop_budget": {"on_exhaust": "extend_if_converging"},
                "autonomy": {"mode": "provisional", "principal": "Wei"}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    later = "2099-01-01T10:00:00+00:00"
    for words, when in (("   ", later), ("adopt", "  ")):
        with pytest.raises(EngineError, match="adoption accept-config must use an exact pending human, or a release-node relay"):
            Engine(proj, executor=FnExecutor()).accept_config(actor="agent:ops", reason="adopt", on_behalf_of="Wei",
                                                              instruction=words, instruction_at=when)
    Engine(proj, executor=FnExecutor()).accept_config(actor="agent:ops", reason="adopt", on_behalf_of="Wei",
                                                      instruction="adopt", instruction_at=later)
