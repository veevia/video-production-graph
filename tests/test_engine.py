"""Engine semantics on the mini workflow: transitions, retries, resume, memo-skips, gates, rework, controls, parallelism, provenance."""
from __future__ import annotations

import json

import pytest
import yaml

from tests.conftest import FnExecutor, flag, mini_workflow
from vpg.engine import Engine, EngineError, StaleSubmission
from vpg.executors import TaskPacketExecutor
from vpg.models import NodeStatus as S
from vpg.provenance import Ledger


def run(proj, ex=None, **kw):
    eng = Engine(proj, executor=ex or FnExecutor(), **kw)
    eng.run(max_steps=200)
    return eng


def test_happy_path_reaches_end(project):
    proj, _ = project
    eng = run(proj)
    assert eng.state.runtime.status.value == "RUN_PASS"
    assert all(eng.rt(n).status == S.STEP_PASS for n in ("a_intake", "d_build", "g_gate", "z_done"))
    assert eng.rt("x_regen").status == S.PLANNED        # the conditional rework branch was never taken


def test_conditional_branch_follows_outcome(project):
    proj, _ = project
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    for _ in range(60):
        eng.step()
        if eng.rt("x_regen").status == S.STEP_PASS:
            break
    assert eng.state.evaluation.gate_results[0].outcome == "rework"
    assert eng.rt("z_done").status == S.PLANNED         # E10 (pass) not taken while rework is routed


def test_targeted_rework_cycle(project):
    """A defect traced to d_build re-runs only d_build and its dependents; a and c are preserved."""
    proj, _ = project
    flag(proj, "defect")
    ex = FnExecutor()

    def fix(eng):
        if eng.state.runtime.rework_cycles == 1:
            flag(proj, "defect", on=False)             # the correction lands in the re-run of d_build
    eng = Engine(proj, executor=ex)
    eng.on_step = fix
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    assert eng.state.runtime.rework_cycles == 1
    assert eng.rt("a_intake").executions == 1 and eng.rt("c_audio").executions == 1 and eng.rt("b_plan").executions == 1
    assert eng.rt("d_build").executions == 2
    cmp = eng.state.evaluation.comparisons
    assert cmp and cmp[0].resolved and "re-ran" in cmp[0].after
    plan = next(e["data"] for e in Ledger(proj / ".vpg/ledger.jsonl").events("regeneration_plan"))
    assert plan["targets"] == ["d_build"] and "a_intake" in plan["preserved"]


def test_memo_skip_on_unchanged_inputs(project):
    """r2_review reads only c_audio, which the rework did not touch: it is memo-skipped and its review carried forward."""
    proj, _ = project
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert eng.rt("r2_review").memo_hits == 1 and eng.rt("r2_review").executions == 1
    assert any(r.node == "r2_review" and r.carried_forward for r in eng.state.evaluation.reviews)


def test_fix_that_changes_nothing_is_not_resolved(project):
    """Regression (found by the dry run): a rework whose output is byte-identical must not resolve the defect."""
    proj, _ = project
    flag(proj, "defect")
    ex = FnExecutor({"d_build": lambda ctx: (ctx.workdir.joinpath("d_build.json").write_text("{}"), {"d_build": "d_build.json"})[1]})
    eng = run(proj, ex)
    assert eng.state.runtime.status.value == "BLOCKED"
    d = eng.state.evaluation.defects[0]
    assert d.status == "persisting" and not any(c.resolved for c in eng.state.evaluation.comparisons)


def test_persisting_defect_escalates_after_three_failures(project):
    proj, _ = project
    flag(proj, "defect")                                  # never fixed
    eng = run(proj)
    assert eng.state.runtime.status.value == "BLOCKED"
    assert eng.state.evaluation.gate_results[-1].outcome == "blocked"
    assert "persisted through 3 corrections" in " ".join(eng.state.evaluation.gate_results[-1].reasons)
    report = next((proj / ".vpg/escalations").glob("g_gate-*.md")).read_text()
    assert "Rework history" in report and "NOT resolved" in report


def test_persisting_defect_continues_to_end_review_in_provisional_mode(project, monkeypatch):
    import vpg.engine as ve
    proj, wf = project
    monkeypatch.setattr(ve, "autonomy_precondition_problems", lambda config: [])
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({
        "humans": ["Wei"],
        "privacy": {"names": "none", "reason": "synthetic"},
        "loop_budget": {"on_exhaust": "extend_if_converging"},
        "autonomy": {"mode": "provisional", "principal": "Wei"},
    })
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    flag(proj, "defect")
    eng = run(proj)
    assert eng.state.runtime.status.value == "RUN_PASS"
    assert eng.state.evaluation.gate_results[-1].outcome == "pass"
    assert eng.state.runtime.end_review_items and eng.state.runtime.end_review_items[-1]["node"] == "g_gate"
    assert list(Ledger(proj / ".vpg/ledger.jsonl").events("end_review_item"))
    assert not list((proj / ".vpg/escalations").glob("g_gate-*.md"))


def test_node_retry_then_escalate(project):
    proj, _ = project
    ex = FnExecutor({"b_plan": lambda ctx: {}})           # never produces its required output
    eng = run(proj, ex)
    rt = eng.rt("b_plan")
    assert rt.status == S.BLOCKED and rt.attempts == 2 and rt.fail_streak == 2
    assert [r.attempt for r in eng.state.history.retries if r.node == "b_plan"] == [1, 2]
    assert eng.state.runtime.status.value == "BLOCKED"


def test_retry_carries_failure_context_and_recovers(project):
    proj, _ = project
    seen = []

    def b(ctx):
        seen.append(list(ctx.failures))
        if ctx.attempt == 1:
            return {}
        ctx.workdir.joinpath("b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = run(proj, FnExecutor({"b_plan": b}))
    assert eng.state.runtime.status.value == "RUN_PASS"
    assert seen[0] == [] and "missing required output" in seen[1][0]


def test_resume_does_not_rerun_approved(project):
    proj, _ = project
    ex = FnExecutor()
    eng = Engine(proj, executor=ex)
    for _ in range(3):
        eng.step()
    done = {n for n, rt in eng.state.runtime.nodes.items() if rt.status == S.STEP_PASS}
    assert done
    ex2 = FnExecutor()
    eng2 = Engine(proj, executor=ex2)                      # fresh process: state, ledger and checkpoints come from disk
    eng2.run(max_steps=200)
    assert eng2.state.runtime.resumes == 1 and eng2.state.runtime.status.value == "RUN_PASS"
    assert not ({n for n, _ in ex2.calls} & done)


def test_interrupted_running_node_is_redispatched_without_consuming_budget(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    rt = eng.rt("b_plan")
    rt.status, rt.attempts = S.RUNNING, 1                  # simulate a crash mid-attempt
    eng._save("crash")
    eng2 = Engine(proj, executor=FnExecutor())
    assert eng2.rt("b_plan").status == S.READY and eng2.rt("b_plan").attempts == 0


def test_conflict_free_batching_and_single_flight(tmp_path, project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()                                            # a_intake
    batch = eng.select_batch(eng.ready_nodes())
    assert set(batch) == {"b_plan", "c_audio"}            # fan-out: disjoint writes and resources
    # two nodes sharing a single-flight resource never run together
    n1, n2 = eng.node("b_plan"), eng.node("c_audio")
    n1.resources = ["tts"]
    assert len(eng.select_batch(["b_plan", "c_audio"])) == 1
    n1.resources = []
    n1.state_write = ["production.refs"]                  # overlaps c_audio's production.refs.c_audio
    assert len(eng.select_batch(["b_plan", "c_audio"])) == 1


def test_fan_in_waits_for_all_reviewers(project):
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    assert eng.rt("a_intake").status == S.WAITING_RUNTIME   # agents get task packets and the run waits
    # finish a_intake, b_plan, d_build by submitting outputs
    for nid in ("a_intake", "b_plan"):
        rt = eng.rt(nid)
        out = proj / "work" / nid / f"a{rt.attempts}" / f"{nid}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}")
        eng.submit(nid, {nid: str(out)}, token=rt.dispatch_token, actor="agent:test")
        eng.run(max_steps=20)
    rt = eng.rt("d_build")
    out = proj / "work/d_build" / f"a{rt.attempts}" / "d_build.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}")
    eng.submit("d_build", {"d_build": str(out)}, token=rt.dispatch_token, actor="agent:test")
    eng.run(max_steps=20)
    assert eng.rt("r2_review").status == S.STEP_PASS       # tool-only review finishes by itself
    assert eng.rt("r1_review").status == S.WAITING_RUNTIME
    assert eng.rt("g_gate").status == S.PLANNED            # join = all: the gate waits for r1


def test_stale_and_duplicate_submission_rejected(project):
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake/a1/a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}")
    with pytest.raises(StaleSubmission):
        eng.submit("a_intake", {"a_intake": str(out)}, token="wrong", actor="agent:test")
    eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:test")
    with pytest.raises(EngineError):
        eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:test")


def test_submission_outside_project_rejected(project, tmp_path):
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}")
    attempts = eng.rt("a_intake").attempts
    with pytest.raises(EngineError, match="outside the project"):
        eng.submit("a_intake", {"a_intake": str(outside)}, token=eng.rt("a_intake").dispatch_token, actor="agent:test")
    assert eng.rt("a_intake").status == S.WAITING_RUNTIME and eng.rt("a_intake").attempts == attempts   # no attempt used


def test_tool_finding_overrides_agent_pass(project):
    """The agent scores every dimension 5, but a tool MUST finding on LEG-SIZE still fails the review."""
    proj, _ = project
    flag(proj, "defect")

    def r1(ctx):
        dims = [d["id"] for d in ctx.rubrics["accessibility"]["dimensions"]]
        from vpg.context import NodeResult
        return NodeResult(review={"reviewer": "t", "scores": [{"dimension": d, "score": 5, "evidence": "x"} for d in dims], "defects": []})
    eng = Engine(proj, executor=FnExecutor({"r1_review": r1}))
    for _ in range(40):
        eng.step()
        if any(r.node == "r1_review" for r in eng.state.evaluation.reviews):
            break
    rev = next(r for r in eng.state.evaluation.reviews if r.node == "r1_review")
    assert rev.verdict == "fail" and "LEG-SIZE" in rev.overridden_agent_passes


def test_unscored_must_dimension_fails_review(project):
    proj, _ = project
    from vpg.context import NodeResult
    eng = run(proj, FnExecutor({"r1_review": lambda ctx: NodeResult(review={"reviewer": "t", "scores": [], "defects": []})}))
    assert eng.rt("r1_review").status == S.BLOCKED
    assert "unscored" in eng.rt("r1_review").failures[0]


def test_gate_blocks_without_current_reviews(project):
    proj, _ = project
    eng = run(proj)
    from vpg.evaluation import gate
    g = gate(eng.state, eng.wf.policies, eng.state.runtime.current_version + 1, eng.reviewer_nodes(), "g_gate")
    assert g.outcome == "blocked" and "missing reviews" in g.reasons[0]


def test_human_gate_requires_named_human(tmp_path):
    proj = tmp_path / "p"
    (proj / "controls").mkdir(parents=True)
    import shutil
    from tests.conftest import PKG
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path, human_b=True, lock_b=("design",))
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "h", "workflow": str(wf)}))
    eng = run(proj)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN
    for bad in ("agent:critic", "dry-run-auto", ""):
        with pytest.raises(EngineError):
            eng.decide("b_plan", "approved", actor=bad)
    eng.decide("b_plan", "approved", actor="Wei", reason="looks right")
    assert eng.state.history.approvals[-1].human and eng.state.controls["design"].status == "approved"
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


def test_dry_run_auto_approval_is_labelled_not_human(tmp_path):
    proj = tmp_path / "p"
    (proj / "controls").mkdir(parents=True)
    import shutil
    from tests.conftest import PKG
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path, human_b=True)
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "h", "workflow": str(wf), "dry_run": True}))
    eng = run(proj, auto_approve=True)
    a = eng.state.history.approvals[0]
    assert a.actor == "dry-run-auto" and a.human is False and "NOT a human" in a.reason


def test_rejection_loops_back_within_bound(tmp_path):
    proj = tmp_path / "p"
    (proj / "controls").mkdir(parents=True)
    import shutil
    from tests.conftest import PKG
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path, human_b=True)
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "h", "workflow": str(wf)}))
    eng = run(proj)
    eng.decide("b_plan", "rejected", actor="Wei", feedback=["tighter"])
    eng.run(max_steps=10)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN and eng.rt("b_plan").executions == 2
    eng.decide("b_plan", "rejected", actor="Wei")
    assert eng.rt("b_plan").status == S.BLOCKED           # E16 max_traversals = 1: the second rejection escalates


def test_control_doc_unauthorized_edit_reverted(project):
    proj, _ = project
    original = (proj / "controls/product.md").read_text()

    def a(ctx):
        (ctx.project_dir / "controls/product.md").write_text(original + "\n## Sneaky\ninvented claim\n")
        ctx.workdir.joinpath("a_intake.json").write_text("{}")
        return {"a_intake": "a_intake.json"}
    eng = Engine(proj, executor=FnExecutor({"a_intake": a}))
    eng.step()
    assert (proj / "controls/product.md").read_text() == original
    assert eng.rt("a_intake").status == S.ITERATE and "unauthorized control change" in eng.rt("a_intake").failures[0]


def test_approved_control_needs_change_request(tmp_path):
    proj = tmp_path / "p"
    (proj / "controls").mkdir(parents=True)
    import shutil
    from tests.conftest import PKG
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path, human_b=True, lock_b=("design",))
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "h", "workflow": str(wf)}))
    eng = run(proj)
    eng.decide("b_plan", "approved", actor="Wei")
    # an edit outside the workflow is detected and blocks the run
    locked = (proj / "controls/design.md").read_text()
    (proj / "controls/design.md").write_text(locked + "\nhand edit\n")
    eng.step()
    assert eng.state.runtime.status.value == "BLOCKED"
    assert "outside the workflow" in eng.state.runtime.escalations[-1].report


def test_change_request_allows_edit(tmp_path):
    proj = tmp_path / "p"
    (proj / "controls").mkdir(parents=True)
    import shutil
    from tests.conftest import PKG
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path, human_b=True, lock_b=("design",))
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "h", "workflow": str(wf)}))
    edits = {"n": 0}

    def b(ctx):
        edits["n"] += 1
        if edits["n"] == 2:
            p = ctx.project_dir / "controls/design.md"
            p.write_text(p.read_text() + "\n<!-- CR-001 applied -->\n")
        ctx.workdir.joinpath("b_plan.json").write_text(json.dumps({"n": edits["n"]}))
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.run(max_steps=20)
    eng.decide("b_plan", "approved", actor="Wei")
    cr = eng.change_request("design", "add a token", by="agent:visual-design")
    with pytest.raises(EngineError):
        eng.approve_change_request(cr.id, by="agent:visual-design")
    eng.approve_change_request(cr.id, by="Wei")
    eng.run(max_steps=20)
    st = eng.state.controls["design"]
    assert "CR-001 applied" in (proj / "controls/design.md").read_text() and st.version == 2 and st.status == "draft"


def test_frozen_inputs_are_immutable(project):
    proj, _ = project
    eng = run(proj)
    eng._freeze_inputs()
    eng.state.inputs.constraints.append("sneaky")
    assert eng._integrity_problems() == ["immutable project inputs changed after they were frozen"]


def test_lineage_and_superseded_artifacts(project):
    proj, _ = project
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    builds = sorted((a for a in eng.state.artifacts.values() if a.key == "d_build"), key=lambda a: a.attempt)
    assert [a.status for a in builds] == ["superseded", "current"]
    cur = next(a for a in builds if a.status == "current")
    lineage = eng.lineage(cur.id)
    assert {x["node"] for x in lineage} >= {"d_build", "b_plan", "c_audio"}


def test_feedback_reopens_gate_after_run_pass(project):
    proj, _ = project
    eng = run(proj)
    assert eng.state.runtime.status.value == "RUN_PASS"
    eng.add_feedback("the label is too small", actor="Wei")
    assert eng.rt("g_gate").status == S.STALE and eng.state.runtime.status.value == "RUNNING"
    eng.run(max_steps=50)
    assert eng.state.evaluation.feedback[0].status == "interpreted" and eng.rt("g_gate").executions == 2


def test_loop_traversal_bound_escalates(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    x = eng.rt("x_regen")
    x.traversals["E14"] = 1
    eng._pass(eng.node("x_regen"), x, "pass", {"targets": ["b_plan"]})
    assert x.status == S.BLOCKED and eng.state.runtime.escalations[-1].reason == "loop E14 has no traversal left (1 of 1 used)"


def test_ledger_detects_tampering(project):
    proj, _ = project
    run(proj)
    path = proj / ".vpg/ledger.jsonl"
    ok, n, _ = Ledger(path).verify()
    assert ok and n > 20
    lines = path.read_text().splitlines()
    ev = json.loads(lines[5])
    ev["data"]["node"] = "forged"
    lines[5] = json.dumps(ev)
    path.write_text("\n".join(lines) + "\n")
    ok, _, msg = Ledger(path).verify()
    assert not ok and "mismatch" in msg


def test_rollback_restores_checkpoint(project):
    proj, _ = project
    eng = run(proj)
    first = eng.state.history.checkpoints[0]
    eng.rollback(first.seq, actor="Wei")
    assert eng.rt("z_done").status != S.STEP_PASS
    assert any(ev["type"] == "rollback" for ev in Ledger(proj / ".vpg/ledger.jsonl").events())


def test_task_packet_mode_writes_complete_packet(project):
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    packet = (proj / eng.rt("a_intake").task_packet).read_text()
    for part in ("Dispatch token", "## Purpose", "## Inputs", "## Produce", "## Done when", "## Rubric", "## Never", "python -m vpg submit"):
        assert part in packet
    assert "{{" not in packet
