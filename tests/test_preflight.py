"""v1.6: an optional independent pre-flight holds a rework attempt before the graph spends a review round on it.

The SRA side chat took v16 to v19 through an independent reviewer outside the graph, so each graph pass would be spent
on an already-checked version. `preflight: {nodes: [...], when: rework | always}` in vpg.project.yaml makes that a graph
step: the finished attempt waits (no version cut, nothing downstream started) until someone other than its producer
passes it, or returns it with notes as a failed attempt."""

from __future__ import annotations

import json

import pytest
import yaml

from tests.conftest import FnExecutor, mini_workflow
from tests.test_bookkeeping import events
from tests.test_hardening import make_project
from vpg import cli
from vpg.engine import Engine, EngineError, StaleSubmission
from vpg.models import NodeStatus as S
from vpg.tools import verify

A = ["a", "P2"]


def held(tmp_path, when=None, rounds=([A], []), **preflight):
    tmp_path.mkdir(parents=True, exist_ok=True)
    proj = make_project(tmp_path, workflow=mini_workflow(tmp_path, review_tool="tests.fakes:review_rounds"))
    (proj / "flags").mkdir(exist_ok=True)
    (proj / "flags" / "rounds.json").write_text(json.dumps(list(rounds)))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["preflight"] = {"nodes": ["d_build"], **({"when": when} if when else {}), **preflight}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=400)
    return proj, eng


def test_a_rework_attempt_waits_for_an_independent_verdict_before_any_review_round(tmp_path):
    proj, eng = held(tmp_path)
    rt = eng.rt("d_build")
    assert rt.status == S.WAITING_RUNTIME and rt.preflight and eng.state.runtime.status.value == "WAITING_RUNTIME"
    assert eng.state.runtime.current_version == 1 and eng.state.runtime.rework_cycles == 1   # no version cut yet
    assert [e["attempt"] for e in events(proj, "preflight_requested")] == [2]                # the first build was not held
    [req] = events(proj, "preflight_requested")
    assert req["producer"] == "engine" and set(req["outputs"]) == {"d_build"}
    packet = (proj / rt.task_packet).read_text()
    assert "D001 [P2] LEG-SIZE: problem a" in packet and rt.preflight["token"] in packet and "--verdict revise --notes" in packet
    assert eng.status()["waiting_runtime"] == {"d_build": rt.task_packet}


def test_the_verdict_must_be_independent_bound_to_the_files_and_explained(tmp_path):
    proj, eng = held(tmp_path)
    rt = eng.rt("d_build")
    token = rt.preflight["token"]
    for actor in ("agent:production-orchestrator", "", "cli", "dry-run-auto"):     # the bound agent, or nobody
        with pytest.raises(EngineError):
            eng.preflight("d_build", token, "pass", actor=actor)
    with pytest.raises(StaleSubmission):
        eng.preflight("d_build", token + "x", "pass", actor="agent:critic")
    with pytest.raises(EngineError, match="needs the reviewer's notes"):
        eng.preflight("d_build", token, "revise", actor="agent:critic", notes=" ")
    out = proj / next(a.path for a in eng.state.artifacts.values() if a.node == "d_build" and a.attempt == 2)
    original = out.read_bytes()
    out.write_text('{"edited": "after the packet"}')
    with pytest.raises(EngineError, match="changed since it was requested"):
        eng.preflight("d_build", token, "pass", actor="agent:critic")
    out.write_bytes(original)
    with pytest.raises(EngineError, match="waits for its independent pre-flight"):
        eng.submit("d_build", {"d_build": str(out)}, token=rt.dispatch_token, actor="agent:composer")
    assert rt.preflight and not events(proj, "preflight_passed") and not events(proj, "preflight_revise")


def test_a_revision_is_a_failed_attempt_with_the_notes_and_spends_no_version(tmp_path):
    proj, eng = held(tmp_path)
    token = eng.rt("d_build").preflight["token"]
    eng.preflight("d_build", token, "revise", actor="agent:critic", notes="the label is still 14px at 00:03")
    rt = eng.rt("d_build")
    assert rt.status == S.ITERATE and rt.preflight is None and eng.state.runtime.current_version == 1
    assert rt.failures == ["the independent pre-flight by agent:critic returned this attempt before any review round: "
                           "the label is still 14px at 00:03"]
    eng.run(max_steps=400)
    assert eng.rt("d_build").status == S.WAITING_RUNTIME and [e["attempt"] for e in events(proj, "preflight_requested")] == [2, 3]
    eng.preflight("d_build", eng.rt("d_build").preflight["token"], "pass", actor="agent:critic", notes="fixed")
    eng.run(max_steps=400)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.runtime.current_version == 2
    assert eng.rt("d_build").result_data["preflight"]["by"] == "agent:critic"
    assert [e["attempt"] for e in events(proj, "preflight_passed")] == [3] and events(proj, "preflight_revise")[0]["attempt"] == 2
    lines = verify.engine_decisions(eng.ledger.events())
    assert lines[1] == "## Independent pre-flight verdicts"
    assert lines[2].endswith("d_build attempt 2 returned for revision by agent:critic: the label is still 14px at 00:03")
    assert lines[3].endswith("d_build attempt 3 passed by agent:critic: fixed")
    assert verify.engine_decisions([]) == []                   # an older run's release summary is unchanged


def test_when_always_holds_the_first_build_too_and_the_cli_records_the_verdict(tmp_path, capsys):
    proj, eng = held(tmp_path, when="always")
    rt = eng.rt("d_build")
    assert rt.preflight and rt.preflight["attempt"] == 1 and eng.state.runtime.current_version == 0
    notes = tmp_path / "notes.md"
    notes.write_text("checked the build against the brief")
    assert cli.main(["preflight", str(proj), "d_build", "--token", rt.preflight["token"], "--verdict", "pass",
                     "--actor", "agent:critic", "--notes", str(notes)]) == 0
    assert events(proj, "preflight_passed")[0]["notes"] == "checked the build against the brief"
    assert Engine(proj, read_only=True).state.runtime.current_version == 1


def test_a_pending_pre_flight_is_dropped_with_the_attempt_it_judged(tmp_path):
    proj, eng = held(tmp_path)
    eng.invalidate_nodes(["d_build"], reason="the plan changed", actor="Wei")
    rt = eng.rt("d_build")
    assert rt.status == S.STALE and rt.preflight is None and events(proj, "preflight_dropped")[0]["attempt"] == 2


@pytest.mark.parametrize("block, message", [
    ({"nodes": ["nope"]}, "not a node of this workflow"), ({"nodes": []}, "non-empty list"),
    ({"nodes": ["d_build"], "when": "sometimes"}, "rework or always"), ("d_build", "must be a mapping")])
def test_a_malformed_preflight_block_is_refused(tmp_path, block, message):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "preflight": block}))
    with pytest.raises(EngineError, match=message):
        Engine(proj, executor=FnExecutor())


def test_vpg_tasks_prints_the_pre_flight_token(tmp_path, capsys):
    proj, eng = held(tmp_path)
    assert cli.main(["tasks", str(proj)]) == 0
    out = capsys.readouterr().out
    assert eng.rt("d_build").preflight["token"] in out and "pre-flight: `vpg preflight`" in out


# ------------------------------------------------------------------------------------------------ review fixes (v1.6)
def test_a_dropped_hold_never_memo_passes_the_unjudged_attempt(tmp_path):
    """Review I1: the held attempt's files stay registered; an upstream invalidation used to memo-pass the node on them."""
    proj, eng = held(tmp_path, when="always")
    eng.preflight("d_build", eng.rt("d_build").preflight["token"], "pass", actor="agent:critic")
    eng.run(max_steps=400)
    assert eng.rt("d_build").preflight["attempt"] == 2 and eng.state.runtime.current_version == 1
    eng.invalidate_nodes(["c_audio"], reason="the audio source changed", actor="Wei")
    assert eng.rt("d_build").force_rerun and events(proj, "preflight_dropped")[0]["attempt"] == 2
    eng.run(max_steps=400)
    rt = eng.rt("d_build")
    assert rt.status == S.WAITING_RUNTIME and rt.preflight["attempt"] == 3 and eng.state.runtime.current_version == 1
    assert [e["attempt"] for e in events(proj, "preflight_passed")] == [1]


def test_a_revision_forces_the_next_dispatch_to_re_run(tmp_path):
    proj, eng = held(tmp_path)
    eng.preflight("d_build", eng.rt("d_build").preflight["token"], "revise", actor="agent:critic", notes="the label is 14px")
    assert eng.rt("d_build").force_rerun


@pytest.mark.parametrize("producer, actor", [("composer", "agent:composer"), ("agent:composer", "Composer"),
                                             ("engine", "Agent:Production-Orchestrator"), ("engine", "production-orchestrator")])
def test_the_same_actor_under_another_spelling_is_not_independent(tmp_path, producer, actor):
    """Review I3: `submit` records a bare name as given and runs it as agent:<name>; case never makes two actors."""
    proj, eng = held(tmp_path)
    eng.rt("d_build").preflight["producer"] = producer
    with pytest.raises(EngineError, match="independent"):
        eng.preflight("d_build", eng.rt("d_build").preflight["token"], "pass", actor=actor)


@pytest.mark.parametrize("node, why", [("r1_review", "a review node"), ("g_gate", "a review_gate node"),
                                       ("x_regen", "a regeneration_plan node")])
def test_only_a_producer_can_be_held(tmp_path, node, why):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "preflight": {"nodes": [node]}}))
    with pytest.raises(EngineError, match=f"preflight.nodes names {node}, {why}"):
        Engine(proj, executor=FnExecutor())


def test_a_verdict_that_fails_part_way_records_nothing(tmp_path, monkeypatch):
    """Review S2: the verdict is one durable action; a failure after its ledger event leaves state and ledger as they were."""
    proj, eng = held(tmp_path)
    token = eng.rt("d_build").preflight["token"]

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(eng, "_pass", boom)
    with pytest.raises(RuntimeError):
        eng.preflight("d_build", token, "pass", actor="agent:critic")
    assert eng.rt("d_build").preflight["token"] == token and not events(proj, "preflight_passed")


@pytest.mark.parametrize("actor", ["agent:", "CLI", "Dry-Run-Auto", "agent:dry-run-auto", "  "])
def test_a_placeholder_reviewer_is_refused_however_it_is_spelled(tmp_path, actor):
    """Review I3: the "nobody" check compared raw strings, so `agent:` or `CLI` passed."""
    proj, eng = held(tmp_path)
    with pytest.raises(EngineError, match="needs the reviewer's identity"):
        eng.preflight("d_build", eng.rt("d_build").preflight["token"], "pass", actor=actor)


def test_the_revisions_checkpoint_already_forces_the_re_run(tmp_path):
    """Review I1: `_fail` checkpointed before force_rerun was set, so a rollback to that checkpoint could memo-pass the
    returned attempt."""
    proj, eng = held(tmp_path)
    eng.preflight("d_build", eng.rt("d_build").preflight["token"], "revise", actor="agent:critic", notes="still 14px")
    ref = next(c for c in reversed(eng.state.history.checkpoints) if c.event == "node_failed" and c.node == "d_build")
    saved = json.loads((proj / ref.path).read_text())
    assert saved["runtime"]["nodes"]["d_build"]["force_rerun"] is True


def test_max_revisions_lets_a_project_iterate_longer_than_its_retry_budget(tmp_path):
    proj, eng = held(tmp_path / "a", max_revisions=4)
    for n in range(1, 5):                                  # d_build's own retry budget is 2
        eng.run(max_steps=400)
        rt = eng.rt("d_build")
        assert rt.preflight, f"revision {n}"
        eng.preflight("d_build", rt.preflight["token"], "revise", actor="agent:critic", notes=f"revision {n}")
    rt = eng.rt("d_build")   # #106: revisions count apart from technical failures
    assert rt.status == S.BLOCKED and rt.preflight_revisions == 4 and rt.fail_streak == 0   # the fourth asks a human
    assert eng.state.runtime.escalations[-1].reason == ("the independent pre-flight returned 4 attempts without a pass "
                                                        "(preflight.max_revisions 4)")
    with pytest.raises(EngineError, match="max_revisions must be a whole number"):
        held(tmp_path / "b", max_revisions=0)
