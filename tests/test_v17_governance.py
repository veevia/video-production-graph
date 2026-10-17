"""v1.7 governance: who may act for a human, and what a review is bound to.

On the first real run (SRA) agents recorded human decisions in a human's name (#18), an agent's invalidation or note re-opened
approved gates (#13, #14), and a review submitted for version 10 judged a different build (#62/#67). v1.7 makes delegation
explicit (a grant, ledgered on every act), keeps listening, taste and the release with the human, holds an agent's note for
a human's triage, and binds each review to the bytes it judged. Also: deferred change requests with an impact preview
(#102, #80), what changed since a gate's last approval (#43), and the smaller fixes #22, #27, #100, #101, #105 and #106."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import jsonschema
import pytest
import yaml

from tests.conftest import PKG, FnExecutor, flag, mini_workflow
from tests.test_bookkeeping import COMMAND_SCRIPT, _bind, events
from tests.test_hardening import BRIEF, SPEC, _init_film, _submit_sources, make_project
from tests.test_preflight import held
from vpg import cli
from vpg.context import NodeResult
from vpg.engine import Engine, EngineError, Project
from vpg.executors import TaskPacketExecutor
from vpg.models import Defect, Feedback
from vpg.models import NodeStatus as S
from vpg.provenance import Ledger
from vpg.tools import accuracy
from vpg.tools.verify import _source_byte_warnings, human_record
from vpg.util import now


def _in(days: int) -> str:
    return (datetime.strptime(now()[:10], "%Y-%m-%d").date() + timedelta(days=days)).isoformat()


def _ledger(proj) -> list[dict]:
    return list(Ledger(proj / ".vpg/ledger.jsonl").events())


def _config(proj, **extra) -> None:
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, **extra}))


# ---------------------------------------------------------------------------------------------- #18 grants
def test_only_a_named_human_grants_and_only_delegable_actions(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    ok = dict(grantee="agent:ops", actions=["unblock"], expires=_in(30), reason="Wei: keep the loop going")
    with pytest.raises(EngineError, match="granting a delegation needs a named human"):
        eng.grant(**ok, actor="agent:boss")
    for bad, why in ((dict(grantee="Wei"), "names one agent"), (dict(grantee="agent:cli"), "names one agent"),
                     (dict(actions=[]), "lists the actions"), (dict(actions=["rollback"]), "not a delegable action"),
                     (dict(actions=["grant"]), "not a delegable action"), (dict(actions=["approve:nope"]), "not a node"),
                     (dict(actions=["accept-config"]), "not a delegable action"),
                     (dict(actions=["approve:d_build"]), "has no human gate"), (dict(expires="2026-13-40"), "end date"),
                     (dict(expires=_in(91)), "90 days"), (dict(expires=_in(-1)), "90 days"), (dict(reason=" "), "reason")):
        with pytest.raises(EngineError, match=why):
            eng.grant(**{**ok, **bad}, actor="Wei")
    assert not eng.state.history.grants and not events(proj, "grant_issued")   # every refusal left no trace
    g = eng.grant(**{**ok, "actions": ["unblock", "unblock", "resolve"]}, actor="Wei")
    assert (g.id, g.grantee, g.actions, g.grantor) == ("G-001", "agent:ops", ["unblock", "resolve"], "Wei")
    assert events(proj, "grant_issued")[-1]["actions"] == ["unblock", "resolve"]
    assert eng.status()["grants"] == [f"G-001 agent:ops may unblock, resolve for Wei until {_in(30)}"]


def test_listening_taste_and_the_release_stay_with_the_human(tmp_path):
    eng = Engine(_init_film(tmp_path), executor=TaskPacketExecutor())
    assert "covers BRF-LENSES" in eng._grantable("approve:n03_objective_audience")
    assert "covers PT-NOT-INVENTED" in eng._grantable("approve:n04_product_understanding")
    assert "the release" in eng._grantable("approve:n23_final_verification")
    with pytest.raises(EngineError, match="cannot delegate approve:n23_final_verification"):
        eng.grant("agent:ops", ["approve:n23_final_verification"], _in(10), "ship it for me", actor="Wei")


def _blocked_by_errors(tmp_path):
    """c_audio fails its 2 attempts, so it escalates and the run blocks."""
    proj = make_project(tmp_path, c_kind="agent")
    eng = Engine(proj, executor=FnExecutor({"c_audio": lambda ctx: NodeResult(outcome="__error__", data={"error": "tts down"})}))
    eng.run(max_steps=50)
    assert eng.rt("c_audio").status == S.BLOCKED and eng.state.runtime.status.value == "BLOCKED"
    return proj, eng


def test_a_granted_agent_acts_under_the_grant_and_every_event_names_it(tmp_path):
    proj, eng = _blocked_by_errors(tmp_path)
    with pytest.raises(EngineError, match="or an agent with a live grant for 'unblock'"):
        eng.unblock("c_audio", actor="agent:ops", note="the TTS is back")
    eng.grant("agent:ops", ["unblock"], _in(30), "Wei, 20:51: keep the loop going", actor="Wei")
    eng.unblock("c_audio", actor="agent:ops", note="the TTS is back (G-001)")
    [unblocked] = [e for e in _ledger(proj) if e["type"] == "unblocked"]
    assert unblocked["data"]["actor"] == "agent:ops" and unblocked["data"]["grant"] == "G-001" and unblocked["data"]["grantor"] == "Wei"
    eng.executor = FnExecutor()   # the TTS is back; the same engine carries on, so a leaked stamp would show
    eng.run(max_steps=200)
    evs = _ledger(proj)
    after = evs[[e["type"] for e in evs].index("unblocked") + 1:]
    assert after and not any("grant" in e["data"] for e in after)   # the stamp covers only the action it authorized
    [row] = [r for r in human_record(_ledger(proj)) if r["action"] == "unblocked"]
    assert row["actor"] == "agent:ops" and "granted" in row["codes"]
    assert "an agent acting under grant G-001 from Wei, not the human" in row["flags"]
    with pytest.raises(EngineError, match="accepting a configuration change needs a named human"):
        eng.accept_config("agent:ops", "a v1.7 engine")   # never delegable: it could rewrite this boundary


def test_a_revoked_or_lapsed_grant_covers_nothing_and_a_rollback_never_revives_it(tmp_path):
    proj, eng = _blocked_by_errors(tmp_path)
    eng.grant("agent:ops", ["unblock"], _in(30), "keep the loop going", actor="Wei")
    seq = eng.state.history.checkpoints[-1].seq
    eng.revoke_grant("G-001", actor="Wei", reason="done")
    with pytest.raises(EngineError, match="grants G-001 don't cover it or have ended"):
        eng.unblock("c_audio", actor="agent:ops", note="again")
    eng.rollback(seq, actor="Wei")                           # a checkpoint taken while G-001 was live
    assert Engine(proj, read_only=True).state.history.grants[0].revoked_at
    with pytest.raises(EngineError, match="don't cover it or have ended"):
        eng.unblock("c_audio", actor="agent:ops", note="again")
    eng.rollback(eng.state.history.checkpoints[0].seq, actor="Wei")   # before any grant was issued
    eng.state.history.grants = []   # even a state that lost its grants draws the next id from the ledger
    assert eng.grant("agent:ops", ["unblock"], _in(1), "one more", actor="Wei").id == "G-002"   # ids never repeat
    eng.state.history.grants[-1].expires = "2000-01-01"
    with pytest.raises(EngineError, match="don't cover it or have ended"):
        eng.unblock("c_audio", actor="agent:ops", note="again")


def test_a_delegable_gate_records_the_grant_and_not_a_human_decision(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=100)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN
    with pytest.raises(EngineError, match="live grant for 'approve:b_plan'"):
        eng.decide("b_plan", "approved", actor="agent:ops", reason="fine")
    eng.grant("agent:ops", ["approve:b_plan"], _in(5), "plans are routine here", actor="Wei")
    eng.decide("b_plan", "approved", actor="agent:ops", reason="matches the brief")
    [a] = eng.state.history.approvals
    assert (a.actor, a.human, a.grant) == ("agent:ops", False, "G-001") and set(a.outputs) == {"b_plan"}
    [row] = [r for r in human_record(_ledger(proj)) if r["action"] == "decision"]
    assert row["actor"] == "agent:ops" and "granted" in row["codes"]


# ---------------------------------------------------------------------------------------------- #13 invalidate
def test_only_a_named_human_or_a_grant_may_invalidate_into_a_human_decision(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=100)
    eng.decide("b_plan", "approved", actor="Wei", reason="the plan is right")
    eng.run(max_steps=200)
    assert eng.rt("b_plan").status == S.STEP_PASS
    eng.invalidate_nodes(["d_build"], reason="the agent re-checks its own build", actor="agent:builder")   # no gate below it
    with pytest.raises(EngineError, match=r"re-opens a human's decision \(b_plan\)"):
        eng.invalidate_nodes(["a_intake"], reason="re-read the brief", actor="agent:builder")
    assert eng.rt("b_plan").status == S.STEP_PASS                                   # refused: nothing changed
    for anonymous in ("", "cli", "agent:"):
        with pytest.raises(EngineError, match="needs the requester's identity"):
            eng.invalidate_nodes(["d_build"], reason="a re-check", actor=anonymous)
    eng.grant("agent:builder", ["invalidate"], _in(3), "re-read the brief when it changes", actor="Wei")
    eng.invalidate_nodes(["a_intake"], reason="re-read the brief", actor="agent:builder")
    assert eng.rt("b_plan").status == S.STALE
    assert events(proj, "invalidated")[-1]["grant"] == "G-001"
    with pytest.raises(SystemExit):                                                  # the CLI requires --actor
        cli.main(["invalidate", str(proj), "d_build", "--reason", "a re-check"])


def test_re_opening_the_frozen_inputs_needs_a_named_human(tmp_path):
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    (proj / "sources/brand.md").write_text("# Brand guide\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    assert eng.state.inputs.frozen
    with pytest.raises(EngineError, match=r"n02_source_validation \(the frozen inputs\)"):
        eng.invalidate_nodes(["n01_context_intake"], reason="a late source", actor="agent:intake")
    assert eng.state.inputs.frozen
    # #100: a node that reads the sources records the bytes it was given
    [dispatched] = [e for e in events(proj, "node_dispatched") if e["node"] == "n02_source_validation"]
    assert set(dispatched["sources"]) == {"S1", "S2", "S3"} and all(len(v) == 16 for v in dispatched["sources"].values())
    eng.invalidate_nodes(["n01_context_intake"], reason="a late source", actor="Wei")
    assert not eng.state.inputs.frozen


# ---------------------------------------------------------------------------------------------- #14 agents' notes
def test_an_agents_note_re_opens_nothing_and_waits_for_a_humans_triage(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    note = eng.add_feedback("the second chapter drags", actor="agent:critic")
    assert (note.status, note.triage) == ("triage", "pending") and "awaits a human's triage" in note.hold
    assert eng.rt("g_gate").status == S.STEP_PASS and eng.state.runtime.status.value == "RUN_PASS"   # nothing re-opened
    assert eng.status()["triage"] == ["F001 by agent:critic (v1): the second chapter drags"]
    with pytest.raises(EngineError, match="names its author"):
        eng.add_feedback("anonymous", actor="cli")
    with pytest.raises(EngineError, match="own note"):
        eng.triage_feedback("F001", accept=True, actor="agent:critic", reason="agreed")
    with pytest.raises(EngineError, match="live grant for 'feedback-triage'"):
        eng.triage_feedback("F001", accept=True, actor="agent:other", reason="agreed")
    with pytest.raises(EngineError, match="needs the reason"):
        eng.triage_feedback("F001", accept=True, actor="Wei", reason="")
    eng.triage_feedback("F001", accept=True, actor="Wei", reason="it does drag")
    note = eng.state.evaluation.feedback[0]   # a refused action restores the state, so earlier references are stale
    assert (note.status, note.triage, note.hold) == ("open", "accepted", "") and eng.rt("g_gate").status == S.STALE
    assert events(proj, "feedback_triaged")[-1] == {"id": "F001", "outcome": "accepted", "by": "Wei", "author": "agent:critic",
                                                    "reason": "it does drag"}
    eng.run(max_steps=200)   # N21 maps it to no action: accepted, it counts as the human's, so an agent can't close it
    note = eng.state.evaluation.feedback[0]
    assert note.status == "interpreted" and note.hold and eng.rt("g_gate").status == S.BLOCKED
    with pytest.raises(EngineError, match="not awaiting triage"):
        eng.triage_feedback("F001", accept=False, actor="Wei", reason="again")


def test_a_pending_note_holds_the_gate_and_only_its_triage_releases_it(project, capsys):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.add_feedback("the logo is soft", actor="agent:critic")
    eng.invalidate_nodes(["g_gate"], reason="re-check the version", actor="agent:ops")   # no human gate below it
    eng.run(max_steps=200)
    gate = eng.state.evaluation.gate_results[-1]
    assert gate.outcome == "blocked" and any("an agent's note awaits a human's triage (F001" in r for r in gate.reasons)
    eng.grant("agent:ops", ["unblock", "feedback-triage"], _in(3), "routine unblocks", actor="Wei")
    with pytest.raises(EngineError, match="holds F001 for a named human; a grant can't release them"):
        eng.unblock("g_gate", actor="agent:ops", note="go on")
    eng.unblock("g_gate", actor="Wei", note="go on")
    assert eng.state.evaluation.feedback[0].hold      # an unblock never releases a note awaiting triage
    eng.run(max_steps=200)
    assert eng.rt("g_gate").status == S.BLOCKED
    assert cli.main(["feedback", str(proj), "--triage", "F001", "--dismiss", "--reason", "the logo is a vector",
                     "--actor", "Wei"]) == 0
    assert "The gate has an open escalation" in capsys.readouterr().out
    fb = Engine(proj, executor=FnExecutor()).state.evaluation.feedback[0]
    assert (fb.status, fb.triage, fb.hold, fb.no_action_reason) == ("dismissed", "dismissed", "", "the logo is a vector")
    eng = Engine(proj, executor=FnExecutor())
    eng.unblock("g_gate", actor="agent:ops", note="Wei dismissed the note")   # its hold is cleared, so the grant covers it
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


def test_the_interpreter_may_not_map_a_note_awaiting_triage(project):
    proj, _ = project

    def interpret(ctx):
        (ctx.workdir / "interpretation.json").write_text(json.dumps({"defects": [], "feedback": [
            {"id": f.id, "no_action_reason": "fine"} for f in ctx.state.evaluation.feedback]}))
        return {"interpretation": "interpretation.json"}
    eng = Engine(proj, executor=FnExecutor({"g_gate": interpret}))
    eng.run(max_steps=200)
    eng.add_feedback("too loud", actor="agent:mixer")
    eng.invalidate_nodes(["g_gate"], reason="re-check", actor="agent:ops")
    eng.run(max_steps=1)
    assert any("maps F001, an agent's note that awaits a human's triage" in f for f in eng.rt("g_gate").failures)


# ---------------------------------------------------------------------------------------------- #62/#67 review binding
def _to_reviews(proj):
    eng = Engine(proj, executor=FnExecutor())
    while eng.rt("d_build").status != S.STEP_PASS:
        eng.step()
    return Engine(proj, executor=TaskPacketExecutor())


def _review(eng, **extra) -> dict:
    dims = [d for r in eng.node("r1_review").rubric for d in eng.rubrics[r]["dimensions"] if d["check"]["type"] == "review"]
    return {"reviewer": "critic", "scores": [{"dimension": d["id"], "score": 5, "evidence": "checked"} for d in dims], **extra}


def test_a_review_names_the_build_it_judged_and_a_different_build_is_refused(project):
    proj, _ = project
    eng = _to_reviews(proj)
    eng.run(max_steps=5)
    rt = eng.rt("r1_review")
    assert rt.status == S.WAITING_RUNTIME
    sha = eng.current("d_build")[0].sha256
    packet = (proj / rt.task_packet).read_text()
    assert "## Judge exactly this build" in packet and f'"reviewed": {{"d_build": "{sha[:16]}"}}' in packet
    attempts = rt.attempts
    with pytest.raises(EngineError, match=r"no attempt used: reviewed.d_build is 000000000000, but this version's d_build is"):
        eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:critic", review=_review(eng, reviewed={"d_build": "0" * 12}))
    with pytest.raises(EngineError, match="at least 12 hex"):
        eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:critic", review=_review(eng, reviewed={"d_build": sha[:8]}))
    assert eng.rt("r1_review").attempts == attempts
    eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:critic", review=_review(eng, reviewed={"d_build": sha[:12]}))
    [r] = [r for r in eng.state.evaluation.reviews if r.node == "r1_review"]
    assert (r.actor, r.inputs, r.reviewed, r.independent) == ("agent:critic", {"d_build": sha[:16]}, {"d_build": sha[:12]}, True)


def test_a_project_can_require_bound_and_independent_reviews(project):
    proj, _ = project
    _config(proj, reviews={"binding": "require", "independence": "require"})
    eng = _to_reviews(proj)
    eng.run(max_steps=5)
    rt = eng.rt("r1_review")
    sha = eng.current("d_build")[0].sha256[:16]
    with pytest.raises(EngineError, match=r'must echo the build it judged: "reviewed": \{"d_build"'):
        eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:critic", review=_review(eng))
    # d_build's bound agent judging its own build, under any spelling
    for spelling in ("agent:production-orchestrator", "AGENT:Production-Orchestrator", "production-orchestrator"):
        with pytest.raises(EngineError, match="agent:production-orchestrator produced d_build, which this review judges"):
            eng.submit("r1_review", {}, token=rt.dispatch_token, actor=spelling, review=_review(eng, reviewed={"d_build": sha}))
    for anonymous in ("", "agent:", "cli", "dry-run-auto", "Dry-Run-x"):   # no identity is never independent
        with pytest.raises(EngineError, match="a review names its reviewer"):
            eng.submit("r1_review", {}, token=rt.dispatch_token, actor=anonymous, review=_review(eng, reviewed={"d_build": sha}))
    eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:critic", review=_review(eng, reviewed={"d_build": sha}))
    assert eng.rt("r1_review").status == S.STEP_PASS
    # a review the engine's own tools produce (r2's rubric is all tool checks, like N15) has nothing to echo
    [r2] = [r for r in eng.state.evaluation.reviews if r.node == "r2_review"]
    assert eng.rt("r2_review").status == S.STEP_PASS and r2.reviewed == {} and r2.independent is None


def test_the_reviews_policy_is_validated(project):
    proj, _ = project
    _config(proj, reviews={"binding": "always", "strict": True})
    probs = Project(proj).config_problems(set())
    assert "reviews.binding must be warn or require, not 'always'" in probs and any("reviews.strict is not a setting" in p for p in probs)


def test_an_engine_run_review_is_judged_by_the_same_rules(project):
    proj, _ = project
    _config(proj, reviews={"binding": "require"})

    def review(ctx):
        return NodeResult(review=_review(Engine(proj, read_only=True), reviewed={"d_build": "f" * 16}), executor="agent:critic")
    eng = Engine(proj, executor=FnExecutor({"r1_review": review}))
    eng.run(max_steps=30)
    assert any("the review judged a different build" in f for f in eng.rt("r1_review").failures)


def test_a_self_review_through_a_bound_command_is_recorded_as_not_independent(project):
    proj, _ = project
    (proj / "pipeline").mkdir()
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    _config(proj, commands={"r1_review": "{python} pipeline/run.py"})   # run_command renames the executor "<actor>+command"
    eng = _to_reviews(proj)
    eng.run(max_steps=5)
    rt = eng.rt("r1_review")
    sha = eng.current("d_build")[0].sha256[:16]
    eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:production-orchestrator",
               review=_review(eng, reviewed={"d_build": sha}))
    [r] = [r for r in eng.state.evaluation.reviews if r.node == "r1_review"]
    assert (r.actor, r.independent) == ("agent:production-orchestrator", False)


def test_a_grant_never_resolves_a_defect_that_needs_a_human(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.grant("agent:ops", ["resolve"], _in(3), "close stale nits", actor="Wei")
    base = dict(signature="s", severity="P3", description="before", source="rev-x", traced_node="d_build", status="open",
                first_seen_version=1, last_seen_version=1)
    eng.state.evaluation.defects += [Defect(id="D901", dimension="VOI-ADULT", **base),
                                     Defect(id="D902", dimension="LEG-SIZE", human_hold="Wei's call", **base),
                                     Defect(id="D903", dimension="LEG-SIZE", needs_human=True, **base),
                                     Defect(id="D904", dimension="LEG-SIZE", **base),
                                     Defect(id="D905", dimension="LEG-SIZE", **base)]   # a reviewer's, that Wei's note maps onto
    eng.state.evaluation.feedback.append(Feedback(id="F009", text="the label is tiny", actor="Wei", created_at=now(),
                                                  status="interpreted", defect_ids=["D905"]))
    for did, why in (("D901", "is on a human-judged dimension"), ("D902", "is held for a named human"),
                     ("D903", "needs a human decision"), ("D905", "comes from Wei's feedback")):
        with pytest.raises(EngineError, match=f"{did} {why}, so only a named human resolves it"):
            eng.resolve_defect(did, actor="agent:ops", evidence="fixed")
    assert eng.resolve_defect("D904", actor="agent:ops", evidence="fixed in v2").status == "resolved"
    assert eng.resolve_defect("D901", actor="Wei", evidence="I listened").status == "resolved"


def test_a_developer_edit_during_a_pre_flight_hold_is_not_taken_for_the_nodes_own_write(tmp_path):
    proj = make_project(tmp_path, workflow=mini_workflow(tmp_path, review_tool="tests.fakes:review_rounds"))
    (proj / "flags").mkdir(exist_ok=True)
    (proj / "flags" / "rounds.json").write_text(json.dumps([[]]))
    (proj / "pipeline").mkdir()
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    _config(proj, commands={"d_build": "{python} pipeline/run.py"}, preflight={"nodes": ["d_build"], "when": "always"})
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=50)
    rt = eng.rt("d_build")
    assert rt.preflight and rt.self_written_code == {}
    with open(proj / "pipeline/run.py", "a") as f:   # a fix made while the attempt waits for its verdict
        f.write("# fixed during the hold\n")
    eng.preflight("d_build", rt.preflight["token"], "pass", actor="agent:critic")
    assert eng.rt("d_build").status == S.STEP_PASS and eng._code_changed_nodes() == ["d_build"]


def test_the_release_judges_the_review_the_gate_used(tmp_path):
    from vpg.tools.verify import release_warnings
    old = SimpleNamespace(node="a", version=2, actor="", reviewed={}, independent=None)
    new = SimpleNamespace(node="a", version=2, actor="agent:critic", reviewed={"x": "0" * 16}, independent=True)
    st = SimpleNamespace(inputs=SimpleNamespace(frozen=False, sources=[]), runtime=SimpleNamespace(nodes={}, current_version=2),
                         history=SimpleNamespace(versions=[]), evaluation=SimpleNamespace(reviews=[old, new]), artifacts={})
    wf = SimpleNamespace(nodes=[SimpleNamespace(id="g", gate="review_gate")],
                         edges=[SimpleNamespace(source="a", target="g", kind=SimpleNamespace(value="flow"))])
    assert release_warnings(SimpleNamespace(state=st, project_dir=tmp_path), wf, []) == []
    st.evaluation.reviews = [new, old]
    assert any("recorded before v1.7" in w for w in release_warnings(SimpleNamespace(state=st, project_dir=tmp_path), wf, []))
    nobody = SimpleNamespace(node="a", version=2, actor="agent:", reviewed={"x": "0" * 16}, independent=None)
    st.evaluation.reviews = [nobody]
    assert any("without a reviewer identity" in w for w in release_warnings(SimpleNamespace(state=st, project_dir=tmp_path), wf, []))


# ---------------------------------------------------------------------------------------------- #102/#80/#43 change requests
def test_a_change_request_is_previewed_deferred_and_never_decided_by_its_own_agent(tmp_path, capsys):
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=100)
    eng.decide("b_plan", "approved", actor="Wei", reason="the plan is right")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    with pytest.raises(EngineError, match="unknown control document 'desgin'"):
        eng.change_request("desgin", "a typo", by="Wei")
    assert not eng.state.history.change_requests
    cr = eng.change_request("design", "a warmer palette", by="agent:designer")
    impact = eng.change_request_impact(cr.id)
    assert impact["writer"] == "b_plan" and "d_build" in impact["readers"] and impact["human_gates"] == ["b_plan"]
    for kw, why in ((dict(until="", reason="later"), "revisit trigger"), (dict(until="after v1", reason=""), "the reason")):
        with pytest.raises(EngineError, match=why):
            eng.defer_change_request(cr.id, by="Wei", **kw)
    eng.defer_change_request(cr.id, by="Wei", until="after the v1 release", reason="not before the release")
    cr = eng.state.history.change_requests[0]
    assert (cr.status, cr.revisit) == ("deferred", "after the v1 release") and eng.rt("b_plan").status == S.STEP_PASS
    assert eng.status()["deferred_change_requests"] == ["CR-001 on design.md until: after the v1 release"]
    [row] = [r for r in human_record(_ledger(proj)) if r["action"] == "change request deferred"]
    assert row["actor"] == "Wei" and "until: after the v1 release" in row["subject"]
    eng.grant("agent:designer", ["change-request"], _in(7), "routine palette work", actor="Wei")
    with pytest.raises(EngineError, match="an agent never decides its own change request"):
        eng.approve_change_request(cr.id, by="agent:designer")
    with pytest.raises(EngineError, match="an agent never decides its own change request"):
        eng.defer_change_request(cr.id, by="agent:designer", until="later", reason="mine")
    assert cli.main(["change-request", str(proj), "--impact", cr.id]) == 0      # read-only, no --actor
    assert "human decisions that would re-open: b_plan" in capsys.readouterr().out
    eng = Engine(proj, executor=FnExecutor())
    eng.approve_change_request(cr.id, by="Wei", reason="after all, now")        # a deferred request can still be approved
    eng.run(max_steps=100)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN
    assert any("changed: b_plan" in line for line in cli._since_last_approval(eng, "b_plan"))   # #43


# ---------------------------------------------------------------------------------------------- #101 #105 #106 #100 #27 #22
def test_a_malformed_scene_limit_is_named_by_status(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    doc = proj / "controls/evaluation.md"
    text = doc.read_text()
    head, body = text.split("\n---\n", 1) if text.startswith("---\n") else ("---\n", text)
    fm = yaml.safe_load(head.removeprefix("---\n")) or {}
    fm.setdefault("thresholds", {}).setdefault("density", {})["scene_limits"] = {"B02": "many"}
    doc.write_text("---\n" + yaml.safe_dump(fm) + "---\n" + body)
    assert any("scene_limits needs a whole number" in w and "N18 fails every attempt" in w for w in eng.status()["warnings"])


SELF_WRITING = COMMAND_SCRIPT + ("import time\n"
                                 "pathlib.Path(ctx['project_dir'], 'pipeline', 'data.js').write_text(f'// {time.time_ns()}\\n')\n")


def test_a_command_that_rewrites_its_own_code_is_not_re_run_for_it(tmp_path):
    proj = make_project(tmp_path, c_kind="agent")   # an agent node runs its bound command after the agent's work
    (proj / "pipeline").mkdir()
    (proj / "pipeline/run.py").write_text(SELF_WRITING)
    (proj / "pipeline/data.js").write_text("// seed\n")
    _bind(proj, "{python} pipeline/run.py")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    rt = eng.rt("c_audio")
    assert eng.state.runtime.status.value == "RUN_PASS" and set(rt.self_written_code) == {"pipeline/data.js"}
    assert eng._code_changed_nodes() == []
    assert any("rewrote 1 file(s)" in w and "command_code_exclude" in w for w in eng.status()["warnings"])
    (proj / "pipeline/data.js").write_text("// a human edit\n")
    assert eng._code_changed_nodes() == ["c_audio"]

def test_an_agents_edit_before_its_command_runs_is_reported_not_taken_for_its_own_write(tmp_path):
    proj = make_project(tmp_path, c_kind="agent")
    (proj / "pipeline").mkdir()
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    _bind(proj, "{python} pipeline/run.py")
    eng = Engine(proj, executor=FnExecutor({"c_audio": lambda ctx: NodeResult(pending=True, task_packet="packet.md")}))
    eng.run(max_steps=20)
    rt = eng.rt("c_audio")
    assert rt.status == S.WAITING_RUNTIME
    with open(proj / "pipeline/run.py", "a") as f:   # the agent fixes the script, then submits
        f.write("# the agent's fix\n")
    out = proj / "work/c_audio" / f"a{rt.attempts}" / "c_audio.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}")
    eng.submit("c_audio", {"c_audio": str(out)}, token=rt.dispatch_token, actor="agent:tts")
    rt = eng.rt("c_audio")
    assert rt.status == S.STEP_PASS and rt.self_written_code == {} and eng._code_changed_nodes() == ["c_audio"]
    assert any(w.startswith("c_audio: its code changed since it last ran") for w in eng.status()["warnings"])



def test_a_technical_failure_after_revisions_uses_the_retry_budget_not_the_revisions(tmp_path):
    proj, eng = held(tmp_path, max_revisions=4)
    for _ in range(2):
        rt = eng.rt("d_build")
        eng.preflight("d_build", rt.preflight["token"], "revise", actor="agent:critic", notes="again")
        eng.run(max_steps=400)
    assert eng.rt("d_build").preflight_revisions == 2 and eng.rt("d_build").preflight
    eng.invalidate_nodes(["d_build"], reason="re-run", actor="agent:ops")
    eng = Engine(proj, executor=FnExecutor({"d_build": lambda ctx: NodeResult(outcome="__error__", data={"error": "disk full"})}))
    eng.step()
    rt = eng.rt("d_build")
    assert rt.fail_streak == 1 and rt.status == S.ITERATE and rt.preflight_revisions == 2   # 1 of 2 retries, not 3 of 2


def test_a_pass_that_read_drifted_source_bytes_is_named(tmp_path):
    st = SimpleNamespace(inputs=SimpleNamespace(sources=[SimpleNamespace(id="S21", sha256="a" * 64)]))
    evs = [{"type": "node_dispatched", "data": {"node": "n04", "attempt": 3, "sources": {"S21": "b" * 16}}},
           {"type": "node_passed", "data": {"node": "n04", "attempt": 3}}]
    [w] = _source_byte_warnings(st, evs)
    assert w.startswith("n04 attempt 3 read S21 with other bytes than registered")
    assert _source_byte_warnings(st, evs[:1]) == []   # only a passing attempt counts


def test_submit_takes_every_output_from_one_file(tmp_path, capsys):
    proj = _init_film(tmp_path)
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    rt = eng.rt("n01_context_intake")
    out = proj / "work/n01_context_intake" / f"a{rt.attempts}" / "sources.json"
    out.write_text(json.dumps({"sources": [BRIEF], "open_questions": []}))
    (tmp_path / "outputs.json").write_text(json.dumps({"sources": "sources.json"}))
    args = ["submit", str(proj), "n01_context_intake", "--token", rt.dispatch_token, "--actor", "agent:intake"]
    with pytest.raises(SystemExit, match="given twice"):
        cli.main(args + ["--outputs", str(tmp_path / "outputs.json"), "--output", "sources=sources.json"])
    assert cli.main(args + ["--outputs", str(tmp_path / "outputs.json")]) == 0
    assert events(proj, "node_submitted")[-1]["outputs"] == ["sources"]


def test_a_declared_redaction_must_be_an_opaque_bar(tmp_path):
    schema = json.loads((PKG / "schemas/artifacts/text-layers.schema.json").read_text())
    layers = [{"id": "R1", "scene": "B11", "kind": "redaction", "method": "bar", "bbox": [0, 0, 10, 10], "start": 1, "end": 2},
              {"id": "R2", "scene": "B12", "kind": "redaction", "method": "blur", "bbox": [0, 0, 10, 10], "start": 3, "end": 4},
              {"id": "R3", "scene": "B13", "kind": "redaction", "bbox": [0, 0, 10, 10], "start": 5, "end": 6, "opacity": 0.6}]
    jsonschema.validate({"frame": {"w": 1920, "h": 1080}, "layers": layers}, schema)
    ctx = SimpleNamespace(workdir=tmp_path, fm=lambda doc: {}, state=SimpleNamespace(production=SimpleNamespace(asset_plan=[])),
                          load=lambda key: {"layers": layers} if key == "text_layers" else None)
    res = accuracy.check_film(ctx)
    bad = {f.element: f for f in res.findings if f.check == "accuracy:redaction"}
    assert set(bad) == {"R2", "R3"} and all(f.severity == "P1" and f.dimension == "ACC-UI" for f in bad.values())
    assert "is a blur" in bad["R2"].message and "60% opaque" in bad["R3"].message
    assert json.loads((tmp_path / "accuracy-report.json").read_text())["redactions"] == 3
