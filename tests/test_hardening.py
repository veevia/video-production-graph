"""Regression tests for the independent engineering critique of J04 (architecture) and J05 (runnable by another agent).

Each test reproduces one finding and fails on the behaviour the critic observed.
"""
from __future__ import annotations

import glob
import json
import shutil

import jsonschema
import pytest
import yaml

from tests.conftest import PKG, FnExecutor, flag, mini_workflow
from vpg import cli
from vpg.context import NodeResult
from vpg.engine import Engine, EngineError
from vpg.evaluation import gate
from vpg.executors import TaskPacketExecutor, minimal_example
from vpg.models import ArtifactRecord
from vpg.models import NodeStatus as S
from vpg.util import now

CONTROLS = ("product", "creative", "design", "production", "evaluation", "memory")


def make_project(tmp_path, workflow=None, **wf_kw):
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in CONTROLS:
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = workflow or mini_workflow(tmp_path, **wf_kw)
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({"id": "t", "name": "t", "workflow": str(wf), "executor": "task"}))
    return proj


def submit_packet(eng, proj, nid):
    rt = eng.rt(nid)
    out = proj / "work" / nid / f"a{rt.attempts}" / f"{nid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}")
    return eng.submit(nid, {nid: str(out)}, token=rt.dispatch_token, actor="agent:test")


# ---------------------------------------------------------------------------------------------- J04 blocking
def test_stranded_join_blocks_instead_of_running_forever(project):
    """A join whose other branch never ran is BLOCKED with a report, not left RUNNING with nothing ready."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    eng.step()                                           # a_intake, then b_plan ∥ c_audio
    assert eng.rt("b_plan").status == S.STEP_PASS and eng.rt("c_audio").status == S.STEP_PASS
    eng.rt("b_plan").outcome = "unexpected"              # its edge into the d_build join can no longer be satisfied
    assert eng.step() == []
    assert eng.state.runtime.status.value == "BLOCKED"
    esc = eng.state.runtime.escalations[-1]
    assert esc.node == "no-progress" and "d_build waits for b_plan" in esc.report
    assert "b_plan passed with outcome 'unexpected', which no edge handles" in esc.report
    assert list((proj / ".vpg/escalations").glob("run-no-progress-*.md"))


def test_engine_refuses_a_graph_that_fails_validation(tmp_path):
    wf = mini_workflow(tmp_path)
    data = yaml.safe_load(wf.read_text())
    next(e for e in data["edges"] if e["id"] == "E13").pop("max_traversals")
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    with pytest.raises(EngineError, match="fails validation"):
        Engine(proj, executor=FnExecutor())
    assert not (proj / ".vpg/state.json").exists()


# ---------------------------------------------------------------------------------------------- J04 major
def test_loop_without_a_bound_escalates_at_runtime(project):
    """Defence in depth: even a bound removed after validation is treated as spent, never as unlimited."""
    proj, _ = project
    flag(proj, "defect")                                 # r1 reports a defect traced to d_build → rework loop E13
    eng = Engine(proj, executor=FnExecutor())
    next(e for e in eng.wf.edges if e.id == "E13").max_traversals = None
    eng.run(max_steps=200)
    assert eng.rt("x_regen").status == S.BLOCKED and eng.rt("x_regen").traversals.get("E13", 0) == 0   # #77: a spent loop is never counted
    assert any(e.edge == "E13" for e in eng.state.runtime.escalations) and eng.state.runtime.rework_cycles == 0


def test_rollback_needs_a_human_and_never_restores_spent_budgets(project):
    proj, _ = project
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "BLOCKED"
    spent, loops = eng.state.runtime.rework_cycles, dict(eng.rt("x_regen").traversals)
    early = eng.state.history.checkpoints[1]
    with pytest.raises(EngineError, match="named human"):
        eng.rollback(early.seq, actor="agent:rogue")
    eng.rollback(early.seq, actor="Wei")
    assert eng.state.runtime.rework_cycles == spent and eng.rt("x_regen").traversals == loops
    assert eng.state.runtime.status.value == "BLOCKED"   # open escalations survive; a human must still unblock


def test_config_change_blocks_until_a_human_accepts(project):
    proj, _ = project
    Engine(proj, executor=FnExecutor()).step()
    cfg = proj / "vpg.project.yaml"
    cfg.write_text(cfg.read_text() + "note: edited mid-run\n")
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    assert eng.state.runtime.status.value == "BLOCKED"
    assert any("changed since this run started" in e.report for e in eng.state.runtime.escalations)
    with pytest.raises(EngineError, match="named human"):
        eng.accept_config("agent:x", "because")
    eng.accept_config("Wei", "added a note")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


def test_rule_base_swap_cannot_erase_a_tool_failure(project):
    """The critic's attack: point the project at a rule base with R-LEG-001 retired, so LEG-SIZE disappears."""
    proj, _ = project
    flag(proj, "defect")
    Engine(proj, executor=FnExecutor()).step()
    rules = json.loads((PKG / "knowledge/rules.json").read_text())
    for r in rules["rules"]:
        if r["id"] == "R-LEG-001":
            r["lifecycle"]["state"] = "retired"
    (proj / "my-rules.json").write_text(json.dumps(rules))
    cfg = proj / "vpg.project.yaml"
    cfg.write_text(cfg.read_text() + "rules: my-rules.json\n")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "BLOCKED"
    assert not any(g.outcome == "pass" for g in eng.state.evaluation.gate_results)


def test_control_writer_runs_alone(tmp_path):
    proj = make_project(tmp_path, lock_b=("design",), c_kind="agent")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=3)
    submit_packet(eng, proj, "a_intake")
    eng.run(max_steps=3)
    assert eng.rt("b_plan").status == S.WAITING_RUNTIME
    assert eng.rt("c_audio").status in (S.PLANNED, S.READY)          # nothing runs beside a control writer
    assert eng.state.runtime.batches[-1] == ["b_plan"]


def test_unattributable_control_edit_escalates_instead_of_blaming_a_sibling(project):
    proj, _ = project
    design = proj / "controls/design.md"
    before = design.read_text()

    def b(ctx):   # b_plan may not write design.md; c_audio runs in the same batch
        design.write_text(before + "\ntampered\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.step()
    eng.step()
    assert design.read_text() == before
    assert eng.state.runtime.status.value == "BLOCKED"
    assert any(e.node == "control-integrity" for e in eng.state.runtime.escalations)
    assert not any("unauthorized control change" in f for n in ("b_plan", "c_audio") for f in eng.rt(n).failures)


def test_failed_must_dimension_is_never_a_nit(project):
    """The reviewer fails LEG-COLOR-CUE (a MUST review dimension) but files its own defect as 'nit'."""
    proj, _ = project

    def r1(ctx):
        dims = [d for d in ctx.rubrics["accessibility"]["dimensions"] if d["check"]["type"] == "review"]
        return NodeResult(review={"reviewer": "t", "scores": [
            {"dimension": d["id"], "score": 1 if d["id"] == "LEG-COLOR-CUE" else 5, "evidence": "x"} for d in dims],
            "defects": [{"dimension": "LEG-COLOR-CUE", "severity": "nit", "description": "status shown by colour only", "traced_node": "d_build"}]})
    eng = Engine(proj, executor=FnExecutor({"r1_review": r1}))
    eng.run(max_steps=200)
    assert next(d for d in eng.state.evaluation.defects if d.dimension == "LEG-COLOR-CUE").severity == "P3"
    assert not any(g.outcome == "pass" for g in eng.state.evaluation.gate_results)
    assert eng.state.runtime.status.value == "BLOCKED"


def test_gate_never_passes_a_failing_review(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    v = eng.state.runtime.current_version
    next(r for r in eng.state.evaluation.reviews if r.version == v and r.node == "r1_review").verdict = "fail"
    g = gate(eng.state, eng.wf.policies, v, eng.reviewer_nodes(), "g_gate")
    assert g.outcome == "blocked" and "a human must decide" in g.reasons[0]


def test_intake_loop_brings_the_gap_report_back_to_intake(tmp_path):
    """E04 re-asks N01 with N02's gaps, instead of memo-skipping it until the loop budget runs out."""
    proj = tmp_path / "film"
    assert cli.main(["init", str(proj), "--name", "t"]) == 0
    eng = Engine(proj, executor=TaskPacketExecutor())
    n01 = eng.node("n01_context_intake")
    assert "artifact:gaps" in n01.inputs
    before = eng.fingerprint(n01)
    eng.state.artifacts["gaps-1"] = ArtifactRecord(id="gaps-1", key="gaps", path="work/gaps.json", sha256="ab" * 32, bytes=2,
                                                   node="n02_source_validation", attempt=1, produced_at=now())
    with_gaps = eng.fingerprint(n01)
    assert with_gaps != before
    (proj / "sources" / "spec.md").write_text("# spec\n")                   # a new source file alone changes it too
    assert eng.fingerprint(n01) != with_gaps


def test_init_from_copies_safety_keys_and_reports_omissions(tmp_path, capsys):
    src = tmp_path / "source"
    dst = tmp_path / "child"
    assert cli.main(["init", str(src), "--name", "source"]) == 0
    source_cfg = yaml.safe_load((src / "vpg.project.yaml").read_text())
    source_cfg.update({
        "privacy": {"names": "names.json"},
        "reviews": {"binding": "require", "independence": "require"},
        "loop_budget": {"profile": "quality-first"},
        "command_deps": {"n13_video_render": ["pipeline/**"]},
        "humans": ["Wei"],
        "autonomy": {"mode": "gated", "principal": "Wei"},
    })
    (src / "names.json").write_text(json.dumps(["Jane Placeholder"]))
    (src / "vpg.project.yaml").write_text(yaml.safe_dump(source_cfg, sort_keys=False))
    assert cli.main(["init", str(dst), "--name", "child", "--from", str(src)]) == 0
    out = capsys.readouterr().out
    cfg = yaml.safe_load((dst / "vpg.project.yaml").read_text())
    assert cfg["privacy"]["names"] == str((src / "names.json").resolve())
    for key in ("reviews", "loop_budget", "command_deps", "humans", "autonomy"):
        assert cfg[key] == source_cfg[key]
    assert "safety keys copied: privacy, reviews, loop_budget, command_deps, humans, autonomy" in out
    assert "preflight (not in source)" in out
    before = (dst / "vpg.project.yaml").read_text()
    assert cli.main(["init", str(dst), "--from", str(src)]) == 1
    assert (dst / "vpg.project.yaml").read_text() == before
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "vpg.project.yaml").write_text("id: [unclosed\n")
    with pytest.raises(SystemExit, match="cannot parse"):
        cli.main(["init", str(tmp_path / "bad-child"), "--from", str(bad)])


def test_status_warns_when_privacy_names_are_not_declared(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    assert any("no privacy name list is declared" in w for w in eng.status()["warnings"])
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["privacy"] = {"names": "none", "reason": "synthetic sample"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    assert not any("privacy:" in w for w in Engine(proj, executor=FnExecutor()).status()["warnings"])


def test_unblock_grants_the_spent_loop_a_fresh_budget(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=20)
    eng.decide("b_plan", "rejected", actor="Wei")
    eng.run(max_steps=20)
    eng.decide("b_plan", "rejected", actor="Wei")                    # E16 allows one traversal
    assert eng.rt("b_plan").status == S.BLOCKED
    assert any(e.edge == "E16" and not e.resolved for e in eng.state.runtime.escalations)
    with pytest.raises(EngineError):
        eng.unblock("b_plan", actor="agent:x")
    eng.unblock("b_plan", actor="Wei", note="new direction supplied")
    assert eng.rt("b_plan").traversals["E16"] == 0
    eng.run(max_steps=20)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN                # asked again, not re-escalated


def test_invalidate_from_the_cli_is_saved(project):
    proj, _ = project
    Engine(proj, executor=FnExecutor()).run(max_steps=200)
    assert cli.main(["invalidate", str(proj), "d_build", "--reason", "re-check", "--actor", "Wei"]) == 0
    state = json.loads((proj / ".vpg/state.json").read_text())
    assert state["runtime"]["nodes"]["d_build"]["status"] == "STALE"


# ---------------------------------------------------------------------------------------------- J04 minor
def test_regeneration_plan_without_a_target_escalates(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    node, rt = eng.node("x_regen"), eng.rt("x_regen")
    eng._pass(node, rt, "pass", {"targets": [], "unroutable": ["D009"]})
    assert rt.status == S.BLOCKED and "no routable target" in eng.state.runtime.escalations[-1].reason


def test_repeated_interruptions_escalate(project):
    """A node that kills the process every time is escalated after retry.max_attempts, not re-dispatched forever."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    for n in (1, 2):
        rt = eng.rt("a_intake")
        rt.status, rt.attempts = S.RUNNING, rt.attempts + 1
        eng._save("simulated crash")
        eng = Engine(proj, executor=FnExecutor())
        assert eng.rt("a_intake").interruptions == n
    assert eng.rt("a_intake").status == S.BLOCKED
    assert "stopped 2 times" in eng.state.runtime.escalations[-1].reason


def test_dispatch_is_saved_before_the_node_runs(project):
    proj, _ = project
    seen = {}

    def a(ctx):
        seen["on_disk"] = json.loads((proj / ".vpg/state.json").read_text())["runtime"]["nodes"]["a_intake"]["status"]
        (ctx.workdir / "a_intake.json").write_text("{}")
        return {"a_intake": "a_intake.json"}
    Engine(proj, executor=FnExecutor({"a_intake": a})).step()
    assert seen["on_disk"] == "RUNNING"


def test_tampered_input_artifact_blocks_the_run(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    eng.step()                                           # b_plan and c_audio registered; d_build consumes them next
    art = next(a for a in eng.state.artifacts.values() if a.key == "b_plan" and a.status == "current")
    (proj / art.path).write_text('{"edited": "after registration"}')
    eng.step()
    assert eng.state.runtime.status.value == "BLOCKED" and eng.rt("d_build").status != S.STEP_PASS
    assert any("changed on disk" in e.report for e in eng.state.runtime.escalations)


# ---------------------------------------------------------------------------------------------- J05
def test_relative_output_path_resolves_against_the_work_directory(project, monkeypatch):
    proj, _ = project
    monkeypatch.chdir(PKG)                               # the SOP runs every command from the package root
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake" / f"a{rt.attempts}" / "a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}")
    assert cli.main(["submit", str(proj), "a_intake", "--token", rt.dispatch_token, "--actor", "agent:t",
                     "--output", "a_intake=a_intake.json"]) == 0
    assert json.loads((proj / ".vpg/state.json").read_text())["runtime"]["nodes"]["a_intake"]["status"] == "STEP_PASS"


def test_argument_errors_are_refused_without_using_an_attempt(project):
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    rt = eng.rt("a_intake")
    for outputs, message in (({"a_intak": "a_intake.json"}, "unknown output key"), ({"a_intake": "missing.json"}, "file not found"),
                             ({}, "missing required output")):
        with pytest.raises(EngineError, match=message):
            eng.submit("a_intake", outputs, token=rt.dispatch_token, actor="agent:t")
    assert eng.rt("a_intake").status == S.WAITING_RUNTIME and eng.rt("a_intake").attempts == 1


def test_packet_states_the_output_contract_and_the_budget(tmp_path):
    proj = tmp_path / "film"
    assert cli.main(["init", str(proj), "--name", "t"]) == 0
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    text = (proj / eng.rt("n01_context_intake").task_packet).read_text()
    for part in ("## Output contracts", "sources.schema.json", "minimal valid shape", "escalates to a human after 3 consecutive",
                 "Relative `--output` and `--review` paths are resolved against the work directory"):
        assert part in text, part


def test_packet_examples_validate_against_their_schemas():
    for f in glob.glob(str(PKG / "schemas/artifacts/*.schema.json")):
        schema = json.loads(open(f).read())
        assert not list(jsonschema.validators.validator_for(schema)(schema).iter_errors(minimal_example(schema))), f


def test_tasks_explains_a_waiting_human_gate(tmp_path, capsys):
    proj = make_project(tmp_path, human_b=True)
    Engine(proj, executor=FnExecutor()).run(max_steps=20)
    capsys.readouterr()
    assert cli.main(["tasks", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "decision: approve plan" in out and "--actor <human> --reason" in out and "--reject --reason" in out and "--feedback" in out


# ---------------------------------------------------------------------------------------------- engineering re-review
BRIEF = {"id": "S1", "path": "sources/brief.md", "kind": "brief", "evidence": "observed"}
SPEC = {"id": "S2", "path": "sources/spec.md", "kind": "spec", "evidence": "observed"}


def _init_film(tmp_path):
    proj = tmp_path / "film"
    assert cli.main(["init", str(proj), "--name", "t"]) == 0
    (proj / "sources/brief.md").write_text("# Brief\nA 60-second platform film for plant managers.\n")
    return proj


def _submit_sources(eng, proj, sources, questions=()):
    rt = eng.rt("n01_context_intake")
    out = proj / "work/n01_context_intake" / f"a{rt.attempts}" / "sources.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"sources": sources, "open_questions": list(questions)}))
    eng.submit("n01_context_intake", {"sources": str(out)}, token=rt.dispatch_token, actor="agent:intake")


def test_documented_intake_recovery_works(tmp_path):
    """Brief-only intake loops until E04 is spent; adding sources and unblocking N02 re-asks N01, and N02 then passes."""
    proj = _init_film(tmp_path)
    eng = Engine(proj, executor=TaskPacketExecutor())
    for _ in range(4):
        eng.run(max_steps=20)
        if eng.state.runtime.status.value == "BLOCKED":
            break
        _submit_sources(eng, proj, [BRIEF])
    assert eng.state.runtime.status.value == "BLOCKED"
    assert any(e.edge == "E04" and not e.resolved for e in eng.state.runtime.escalations)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    (proj / "sources/brand.md").write_text("# Brand guide\n")
    eng.unblock("n02_source_validation", actor="Wei", note="product and brand sources added")
    eng.run(max_steps=20)
    assert eng.rt("n01_context_intake").status == S.WAITING_RUNTIME          # intake is asked again
    _submit_sources(eng, proj, [BRIEF, {"id": "S2", "path": "sources/spec.md", "kind": "spec", "evidence": "observed"},
                                {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "pass"
    assert eng.rt("n03_objective_audience").status == S.WAITING_RUNTIME


def _intake_without_brand(tmp_path):
    """Spec but no brand source; the agent keeps answering the gap with an open question until the intake loop runs out."""
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    for _ in range(4):
        eng.run(max_steps=20)
        if eng.state.runtime.status.value == "BLOCKED":
            break
        _submit_sources(eng, proj, [BRIEF, SPEC], [{"id": "Q1", "text": "Which brand guide applies?", "blocking": False}])
    return proj, eng


def test_an_agent_question_never_clears_a_coverage_gap(tmp_path):
    """Critic J04 (round 3): the intake agent cannot turn a MUST coverage gap into a note. The old acknowledgement field
    is refused by the schema; a plain question leaves the gap open, so the intake loop runs out and escalates to Wei."""
    from pydantic import ValidationError
    from vpg.models import Question
    with pytest.raises(ValidationError):
        Question(id="Q1", text="Which brand guide applies?", gap="G-BRAND")
    _, eng = _intake_without_brand(tmp_path)
    assert eng.rt("n02_source_validation").outcome == "gaps_resolvable" and not eng.state.inputs.frozen
    assert any(e.edge == "E04" and not e.resolved for e in eng.state.runtime.escalations)


def _ledger_types(proj):
    return [json.loads(x)["type"] for x in (proj / ".vpg/ledger.jsonl").read_text().splitlines()]


def test_only_a_named_human_can_waive_a_gap(tmp_path):
    proj, eng = _intake_without_brand(tmp_path)
    with pytest.raises(EngineError, match="named human"):
        eng.unblock("n02_source_validation", actor="agent:intake", waive=["G-BRAND"], note="no guide")
    with pytest.raises(EngineError, match="not a resolvable gap"):
        eng.unblock("n02_source_validation", actor="Wei", waive=["G-BRAND", "G-BRIEF"], note="no guide")
    with pytest.raises(EngineError, match="reason"):
        eng.unblock("n02_source_validation", actor="Wei", waive=["G-BRAND"])
    with pytest.raises(EngineError, match="source-validation|n02_source_validation"):
        eng.unblock("n03_objective_audience", actor="Wei", waive=["G-BRAND"], note="no guide")
    assert not eng.state.inputs.waivers                                     # a refused waiver leaves no trace …
    assert "gap_waived" not in _ledger_types(proj)
    assert cli.main(["unblock", str(proj), "n02_source_validation", "--actor", "Wei", "--waive", "G-BRAND",
                     "--note", "the brand guide arrives with the design phase"]) == 0
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "pass" and eng.state.inputs.frozen
    assert [(w.gap, w.by) for w in eng.state.inputs.waivers] == [("G-BRAND", "Wei")]   # … an accepted one is frozen with the inputs
    gaps = json.loads((proj / next(a.path for a in eng.state.artifacts.values() if a.key == "gaps" and a.status == "current")).read_text())
    assert any(g["id"] == "G-BRAND" and g["severity"] == "note" and "waived by Wei" in g["text"] for g in gaps["gaps"])
    assert "gap_waived" in _ledger_types(proj)
    assert any("G-BRAND was waived by Wei" in line for line in cli._human_gate_lines(eng, "n03_objective_audience"))
    with pytest.raises(EngineError, match="frozen"):
        eng.unblock("n02_source_validation", actor="Wei", waive=["G-PRODUCT"], note="late")


def test_invalidate_regenerates_a_changed_artifact(project):
    """The documented recovery for a changed artifact: invalidate its producer, unblock, and the producer re-runs."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    eng.step()
    art = next(a for a in eng.state.artifacts.values() if a.key == "b_plan" and a.status == "current")
    (proj / art.path).write_text('{"edited": true}')
    eng.step()
    assert eng.state.runtime.status.value == "BLOCKED"
    eng.invalidate_nodes(["b_plan"], reason="regenerate the edited artifact", actor="Wei")
    eng.unblock("integrity", actor="Wei")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.rt("b_plan").executions == 2


def test_tampered_deliverable_blocks_packaging(tmp_path):
    from vpg.controls import split_front_matter
    proj = make_project(tmp_path, z_gate="package")
    prod = proj / "controls/production.md"
    fm, body = split_front_matter(prod.read_text())
    fm["deliverables"] = [{"key": "d_build", "name": "build.json"}]
    prod.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n" + body)
    eng = Engine(proj, executor=FnExecutor())
    for _ in range(40):
        eng.step()
        if eng.rt("g_gate").status == S.STEP_PASS:
            break
    art = next(a for a in eng.state.artifacts.values() if a.key == "d_build" and a.status == "current")
    (proj / art.path).write_text('{"edited": "after review"}')
    eng.step()
    assert eng.state.runtime.status.value == "BLOCKED" and eng.rt("z_done").status != S.STEP_PASS
    assert any("changed on disk" in e.report for e in eng.state.runtime.escalations)


def test_readers_of_a_rewritten_control_document_are_rechecked(tmp_path):
    """Critic E12: c_audio read design.md while its writer waited at a human gate; the rewrite must re-check it."""
    proj = make_project(tmp_path, human_b=True, lock_b=("design",), c_reads_design=True)
    design = proj / "controls/design.md"
    base = design.read_text()

    def b(ctx):
        design.write_text(base + f"\n<!-- draft {ctx.attempt} -->\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.run(max_steps=20)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN and eng.rt("c_audio").status == S.STEP_PASS
    eng.decide("b_plan", "rejected", actor="Wei")
    eng.run(max_steps=20)
    assert eng.rt("c_audio").executions == 2


def test_engine_refuses_unscorable_must_dimensions(tmp_path):
    data = yaml.safe_load((PKG / "graph/workflow.yaml").read_text())
    next(n for n in data["nodes"] if n["id"] == "n07_script_development")["rubric"] = ["script", "creative"]
    wf = tmp_path / "v18.yaml"
    wf.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    proj = _init_film(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["workflow"] = str(wf)
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    with pytest.raises(EngineError, match="would never be scored"):
        Engine(proj, executor=TaskPacketExecutor())


def test_config_fingerprint_covers_artifact_schemas(project, tmp_path, monkeypatch):
    import vpg.engine as engine_module
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    fake = tmp_path / "pkg"
    for d in ("rubrics", "config", "schemas/artifacts"):
        shutil.copytree(PKG / d, fake / d)
    monkeypatch.setattr(engine_module, "PKG_ROOT", fake)
    before = eng._config_fingerprint()
    (fake / "schemas/artifacts/brief.schema.json").write_text("{}")
    assert eng._config_fingerprint() != before


def test_interruption_is_charged_to_nodes_executing_or_being_judged(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    cases = {"b_plan": (S.RUNNING, False),       # cut short mid-execution → charged
             "c_audio": (S.RUNNING, True),       # its execution had returned; the crash hit another node's fan-in → not charged
             "a_intake": (S.VALIDATING, True)}   # the process died while this node was judged → charged
    for nid, (status, executed) in cases.items():
        eng.rt(nid).status, eng.rt(nid).attempts, eng.rt(nid).executed = status, 1, executed
    eng._save("simulated crash during fan-in")
    eng = Engine(proj, executor=FnExecutor())
    assert [eng.rt(n).interruptions for n in cases] == [1, 0, 1]
    assert all(eng.rt(n).status == S.READY and not eng.rt(n).executed for n in cases)


def test_a_crash_while_judging_is_charged_and_escalates(tmp_path):
    """Critic J04 (round 3): a completion check that kills the process was re-dispatched forever with 0 interruptions."""
    wf = mini_workflow(tmp_path)
    data = yaml.safe_load(wf.read_text())
    next(n for n in data["nodes"] if n["id"] == "a_intake")["completion"].append(
        {"type": "tool", "fn": "tests.fakes:kill_process", "description": "a check that takes the process down"})
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    for n in (1, 2):
        with pytest.raises(SystemExit):
            Engine(proj, executor=FnExecutor()).step()
        assert json.loads((proj / ".vpg/state.json").read_text())["runtime"]["nodes"]["a_intake"]["status"] == "VALIDATING"
        eng = Engine(proj, executor=FnExecutor())                     # resume
        assert eng.rt("a_intake").interruptions == n
    assert eng.rt("a_intake").status == S.BLOCKED and eng.state.runtime.status.value == "BLOCKED"
    assert "being judged" in eng.state.runtime.escalations[-1].reason


def _mutual_writers(tmp_path):
    """b_plan and c_audio are unordered, and each writes a control document the other reads."""
    wf = mini_workflow(tmp_path, c_kind="agent")
    data = yaml.safe_load(wf.read_text())
    nodes = {n["id"]: n for n in data["nodes"]}
    for nid, reads, writes in (("b_plan", "production", "design"), ("c_audio", "design", "production")):
        nodes[nid]["controls"] = {"read": [reads], "write": [writes], "lock_on_approval": []}
        nodes[nid]["inputs"] = ["state:inputs.brief", f"control:{reads}"]
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    return wf


def test_validator_rejects_a_control_document_recheck_cycle(tmp_path):
    """Critic J04 (round 3): two unordered writers that read each other's document re-ran each other ~150 times."""
    from vpg.graph import load_workflow, validate
    wf = _mutual_writers(tmp_path)
    errs = validate(load_workflow(wf), check_stages=False)
    assert any("re-check cycle" in e and "b_plan" in e and "c_audio" in e for e in errs), errs
    with pytest.raises(EngineError, match="fails validation"):
        Engine(make_project(tmp_path, workflow=wf), executor=FnExecutor())
    assert not any("re-check cycle" in e for e in validate(load_workflow(), check_stages=True))


def test_a_recheck_cycle_that_slips_past_validation_is_bounded(tmp_path, monkeypatch):
    import vpg.graph
    monkeypatch.setattr(vpg.graph, "_recheck_cycle_errors", lambda wf, index: [])   # simulate a validator gap
    proj = make_project(tmp_path, workflow=_mutual_writers(tmp_path))
    runs = {"b_plan": 0, "c_audio": 0}

    def writer(nid, doc):
        def h(ctx):
            runs[nid] += 1
            p = proj / "controls" / f"{doc}.md"
            p.write_text(p.read_text() + f"\n<!-- {nid} run {runs[nid]} -->\n")
            (ctx.workdir / f"{nid}.json").write_text("{}")
            return {nid: f"{nid}.json"}
        return h
    eng = Engine(proj, executor=FnExecutor({"b_plan": writer("b_plan", "design"), "c_audio": writer("c_audio", "production")}))
    eng.run(max_steps=300)
    assert eng.state.runtime.status.value == "BLOCKED"
    esc = eng.state.runtime.escalations[-1]
    assert "re-check cycle" in esc.reason and max(runs.values()) <= 3, runs      # bounded by the 2 control writers
    blocked = esc.node
    eng.unblock(blocked, actor="Wei", note="granting a fresh chain to confirm the bound restarts")
    assert all(rt.rechecks == 0 for rt in eng.state.runtime.nodes.values())
    eng.run(max_steps=300)
    assert eng.state.runtime.status.value == "BLOCKED" and max(runs.values()) <= 6, runs


def test_unparseable_json_output_is_refused_without_using_an_attempt(project):
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake" / f"a{rt.attempts}" / "a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{not json")
    with pytest.raises(EngineError, match="not valid JSON"):
        eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:t")
    assert eng.rt("a_intake").attempts == 1 and eng.rt("a_intake").status == S.WAITING_RUNTIME


def test_errors_while_judging_are_failures_not_tracebacks(project):
    proj, _ = project
    bad = FnExecutor({"r1_review": lambda ctx: NodeResult(review={"reviewer": "t", "scores": [], "defects": [{"dimension": "LEG-SIZE"}]})})
    eng = Engine(proj, executor=bad)
    eng.run(max_steps=100)
    assert any("judging this submission raised KeyError" in f for f in eng.rt("r1_review").failures)


def test_packet_lists_nested_fields_and_optional_outputs(tmp_path):
    proj = _init_film(tmp_path)
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    text = (proj / eng.rt("n01_context_intake").task_packet).read_text()
    assert "each `open_questions` item: id*, text*, blocking, owner, status, answer (* required; no other keys)" in text
    assert "`intake_notes` (markdown, optional)" in text
    assert "`files:sources/**` → 1 file(s): sources/brief.md" in text


def test_gate_lines_name_what_the_approval_locks_and_init_names_the_sources(tmp_path, capsys):
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    Engine(proj, executor=FnExecutor()).run(max_steps=20)
    capsys.readouterr()
    assert cli.main(["tasks", str(proj)]) == 0
    assert "approving locks: controls/design.md" in capsys.readouterr().out
    assert cli.main(["init", str(tmp_path / "other"), "--name", "t"]) == 0
    assert "product evidence" in capsys.readouterr().out


def test_resume_rolls_back_an_interrupted_writers_partial_edit(tmp_path):
    """A control writer that died mid-edit re-runs from scratch: resume restores the accepted document and keeps the text."""
    proj = make_project(tmp_path, lock_b=("design",))
    design = proj / "controls/design.md"
    base = design.read_text()
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    rt = eng.rt("b_plan")
    rt.status, rt.attempts = S.RUNNING, 1
    eng._save("dispatch")
    design.write_text(base + "\n<!-- half-written -->\n")
    eng = Engine(proj, executor=FnExecutor())
    assert design.read_text() == base and eng._integrity_problems() == []
    kept = list((proj / ".vpg/controls").glob("design.interrupted-b_plan-*.md"))
    assert len(kept) == 1 and "half-written" in kept[0].read_text()
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


def test_a_writer_killed_while_judged_leaves_no_integrity_dead_end(tmp_path):
    """The crash lands after the edit was accepted on disk but before the state was saved; resume still reconciles."""
    wf = mini_workflow(tmp_path, lock_b=("design",))
    data = yaml.safe_load(wf.read_text())
    next(n for n in data["nodes"] if n["id"] == "b_plan")["completion"].append(
        {"type": "tool", "fn": "tests.fakes:kill_process", "description": "a check that takes the process down"})
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    proj = make_project(tmp_path, workflow=wf)
    design = proj / "controls/design.md"
    base = design.read_text()

    def b(ctx):
        design.write_text(base + f"\n<!-- edit {ctx.attempt} -->\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.step()
    with pytest.raises(SystemExit):
        eng.step()                                            # b_plan runs alone; its check kills the process
    assert (proj / ".vpg/controls/design.accepted.md").read_text() != base   # the latest copy is ahead of the saved state
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    assert design.read_text() == base and eng._integrity_problems() == []
    assert eng.rt("b_plan").interruptions == 1 and eng.rt("b_plan").status == S.READY


# ---------------------------------------------------------------------------------------------- round 6 (own probes)
def test_unblocking_one_escalation_keeps_the_run_blocked_while_another_is_open(project):
    """Unblocking a node used to clear the run's BLOCKED status while a run-level escalation was still open."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    eng._escalate_run("control-integrity", "unattributable edit to design.md; reverted; a human must review")
    eng._escalate(eng.node("b_plan"), "simulated node escalation", ["x"])
    eng.unblock("b_plan", actor="Wei", note="only the node escalation")
    assert eng.state.runtime.status.value == "BLOCKED" and eng.step() == []
    eng.unblock("control-integrity", actor="Wei", note="reviewed")
    assert eng.state.runtime.status.value == "RUNNING" and eng.step()


def test_unblock_refuses_a_node_without_an_open_escalation(project):
    """Resetting a passed node to READY would re-run it without re-checking its dependents."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    before = (proj / ".vpg/ledger.jsonl").read_text()
    with pytest.raises(EngineError, match="no open escalation"):
        eng.unblock("a_intake", actor="Wei", note="nothing is blocked")
    assert eng.rt("a_intake").status == S.STEP_PASS and (proj / ".vpg/ledger.jsonl").read_text() == before
    assert cli.main(["unblock", str(proj), "a_intake", "--actor", "Wei"]) == 4


def test_a_waiver_can_be_recorded_before_n02_is_blocked(tmp_path):
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC])
    eng.run(max_steps=20)                                   # gaps_resolvable → E04 → the intake is asked again
    token, status = eng.rt("n01_context_intake").dispatch_token, eng.rt("n02_source_validation").status
    eng.unblock("n02_source_validation", actor="Wei", waive=["G-BRAND"], note="the brand guide arrives with the design phase")
    assert eng.rt("n01_context_intake").dispatch_token == token and eng.rt("n02_source_validation").status == status
    _submit_sources(eng, proj, [BRIEF, SPEC])
    eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "pass" and eng.state.inputs.frozen


def test_a_waiver_is_shown_only_while_its_gap_is_open(tmp_path):
    proj, eng = _intake_without_brand(tmp_path)
    eng.unblock("n02_source_validation", actor="Wei", waive=["G-BRAND"], note="the brand guide arrives with the design phase")
    eng.run(max_steps=20)
    packet = (proj / eng.rt("n03_objective_audience").task_packet).read_text()
    assert "Waived intake gaps:** G-BRAND, waived by Wei" in packet and "Never fill one by invention" in packet
    assert any("G-BRAND was waived" in x for x in cli._human_gate_lines(eng, "n03_objective_audience"))
    (proj / "sources/brand.md").write_text("# Brand guide\n")                       # the source arrives after all
    eng.invalidate_nodes(["n01_context_intake"], reason="brand guide arrived", actor="Wei")
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "pass"
    assert not any("waived" in x for x in cli._human_gate_lines(eng, "n03_objective_audience"))
    assert "Waived intake gaps" not in (proj / eng.rt("n03_objective_audience").task_packet).read_text()
    assert [w.gap for w in eng.state.inputs.waivers] == ["G-BRAND"]           # history is kept in the frozen inputs


def test_rollback_restores_the_control_documents(tmp_path):
    """A rollback used to leave the control documents ahead of the restored state, so the run was integrity-blocked."""
    proj = make_project(tmp_path, lock_b=("design",))
    design = proj / "controls/design.md"
    base = design.read_text()

    def b(ctx):
        design.write_text(base + f"\n<!-- plan {ctx.attempt} -->\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.step()
    first = eng.state.history.checkpoints[-1].seq
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and design.read_text() != base
    eng.rollback(first, actor="Wei")
    assert design.read_text() == base and eng._integrity_problems() == []
    kept = list((proj / ".vpg/controls").glob("design.before-rollback-*.md"))
    assert len(kept) == 1 and "<!-- plan" in kept[0].read_text()
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


# ---------------------------------------------------------------------------------------------- engineering critic, round 4
def test_a_crash_in_a_submit_time_command_is_charged_and_escalates(tmp_path, monkeypatch):
    """A command bound at submit (renderer, TTS, assembly) that takes the process down was never counted: the task stayed
    open under the same token and crashed on every resubmission. It is now charged on resume and escalates."""
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "commands": {"a_intake": "render --fast"}}))

    def boom(*_a, **_k):
        raise SystemExit("the bound command took the process down")
    monkeypatch.setattr("vpg.engine.run_command", boom)
    for n in (1, 2):
        eng = Engine(proj, executor=TaskPacketExecutor())
        eng.run(max_steps=5)
        assert eng.rt("a_intake").status == S.WAITING_RUNTIME
        with pytest.raises(SystemExit):
            submit_packet(eng, proj, "a_intake")
        assert json.loads((proj / ".vpg/state.json").read_text())["runtime"]["nodes"]["a_intake"]["status"] == "RUNNING"
        eng = Engine(proj, executor=TaskPacketExecutor())             # resume
        assert eng.rt("a_intake").interruptions == n
    assert eng.rt("a_intake").status == S.BLOCKED and eng.state.runtime.status.value == "BLOCKED"


def test_the_next_attempt_is_told_why_it_restarts(tmp_path):
    """Critic J05 (round 4): after resume rolled back a writer's edit, the new packet said 'failed because: none'."""
    proj = make_project(tmp_path, lock_b=("design",))
    design = proj / "controls/design.md"
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    rt = eng.rt("b_plan")
    rt.status, rt.attempts = S.RUNNING, 1
    eng._save("dispatch")
    design.write_text(design.read_text() + "\n<!-- half-written -->\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    assert eng.step() == ["b_plan"]
    packet = (proj / eng.rt("b_plan").task_packet).read_text()
    assert "the previous attempt was interrupted" in packet and "interruption 1 of 2" in packet
    assert "controls/design.md was rolled back" in packet and ".vpg/controls/design.interrupted-b_plan-r1.md" in packet


# ---------------------------------------------------------------------------------------------- engineering critic, round 5
def _stale_lineage(eng):
    """Current artifacts of passed nodes that were built from an input artifact which is no longer current."""
    arts = eng.state.artifacts
    return [f"{a.id} ← {i}" for a in arts.values() if a.status == "current" and eng.rt(a.node).status == S.STEP_PASS
            for i in a.inputs if i in arts and arts[i].status != "current"]


def test_unblock_after_rollback_never_leaves_dependents_on_stale_work(project):
    """Critic H2: rollback carries an open escalation onto a node that passed at the checkpoint; unblocking it used to
    reset it to READY, so it re-ran while d_build stayed passed on the old plan and the run still reached RUN_PASS."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    checkpoint = next(c.seq for c in eng.state.history.checkpoints if c.node == "d_build" and c.event == "node_passed")   # mid-run
    eng._escalate(eng.node("b_plan"), "a later loop ran out of budget", ["x"])
    eng._save("escalated after the checkpoint")
    eng.rollback(checkpoint, actor="Wei")
    assert eng.state.runtime.status.value == "BLOCKED" and eng.rt("b_plan").status == eng.rt("d_build").status == S.STEP_PASS
    eng.unblock("b_plan", actor="Wei", note="continue from the checkpoint")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and _stale_lineage(eng) == []


def test_unblocking_a_blocked_node_rechecks_its_passed_dependents(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng._escalate(eng.node("b_plan"), "simulated escalation", ["x"])
    eng.unblock("b_plan", actor="Wei", note="re-run the plan")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and _stale_lineage(eng) == []
    assert eng.rt("b_plan").executions == 2 and eng.rt("d_build").executions == 2   # new plan → the build re-ran on it


def _latest_gaps(eng, proj):
    return json.loads((proj / next(a.path for a in eng.state.artifacts.values() if a.key == "gaps" and a.status == "current")).read_text())["gaps"]


def test_relabelling_the_brief_closes_no_coverage_gap(tmp_path):
    """Critic H6: registering the brief as spec and brand cleared G-PRODUCT and G-BRAND with no human involved."""
    proj = _init_film(tmp_path)
    (proj / "sources/spec-copy.md").write_bytes((proj / "sources/brief.md").read_bytes())   # a byte copy of the brief
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, {"id": "S2", "path": "sources/spec-copy.md", "kind": "spec", "evidence": "observed"},
                                {"id": "S3", "path": "sources/brief.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "gaps_resolvable" and not eng.state.inputs.frozen
    gaps = {g["id"]: g for g in _latest_gaps(eng, proj)}
    assert gaps["G-PRODUCT"]["severity"] == gaps["G-BRAND"]["severity"] == "resolvable"
    assert "S2 not counted: the same file is registered under another kind too" in gaps["G-PRODUCT"]["text"]
    relabel = [g for gid, g in gaps.items() if gid.startswith("G-RELABEL-")]
    assert len(relabel) == 1 and "(it is the brief)" in relabel[0]["text"] and relabel[0]["severity"] == "note"


def test_an_assumed_source_is_not_product_evidence(tmp_path):
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    (proj / "sources/brand.md").write_text("# Brand guide\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, {**SPEC, "evidence": "assumed"},
                                {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    gaps = {g["id"]: g for g in _latest_gaps(eng, proj)}
    assert "S2 not counted: assumed evidence" in gaps["G-PRODUCT"]["text"] and "G-BRAND" not in gaps


def test_product_and_design_gates_list_their_grounding_sources(tmp_path):
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    (proj / "sources/brand.md").write_text("# Brand guide\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "pass"
    lines = {nid: "\n".join(cli._human_gate_lines(eng, nid)) for nid in ("n03_objective_audience", "n04_product_understanding", "n09_visual_system")}
    assert "product evidence: S2 sources/spec.md (spec, observed)" in lines["n04_product_understanding"]
    assert "brand and design authority: S3 sources/brand.md (brand, observed)" in lines["n09_visual_system"]
    assert "product evidence" not in lines["n03_objective_audience"]


def test_workflow_files_are_not_coverage_evidence(tmp_path):
    """Critic P1 (round 6): registering the unfilled control templates as spec and brand cleared both gaps."""
    proj = _init_film(tmp_path)
    shutil.copy2(proj / "controls/product.md", proj / "sources/spec.md")            # a copy of the template, inside sources/
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, {"id": "S2", "path": "controls/product.md", "kind": "spec", "evidence": "observed"},
                                {"id": "S3", "path": "controls/design.md", "kind": "brand", "evidence": "observed"},
                                {"id": "S4", "path": "sources/spec.md", "kind": "product", "evidence": "observed"}])
    eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "gaps_resolvable" and not eng.state.inputs.frozen
    gaps = {g["id"]: g for g in _latest_gaps(eng, proj)}
    assert "S2 not counted: outside sources/" in gaps["G-PRODUCT"]["text"]
    assert "S4 not counted: a copy of a control document or template" in gaps["G-PRODUCT"]["text"]
    assert "S3 not counted: outside sources/" in gaps["G-BRAND"]["text"]


# ---------------------------------------------------------------------------------------------- engineering critic, round 7
import contextlib  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo") or not hasattr(signal, "SIGALRM"), reason="named pipes are POSIX-only")


@contextlib.contextmanager
def _no_hang(seconds=10):
    """A regression that blocks on a special file fails the test instead of hanging the suite."""
    def boom(*_):
        raise TimeoutError("blocked on a special file")
    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@needs_fifo
def test_hashing_never_blocks_on_a_pipe_or_a_directory(tmp_path):
    from vpg.util import NotRegularFile, sha256_file
    os.mkfifo(tmp_path / "pipe")
    with _no_hang():
        for special in (tmp_path / "pipe", tmp_path):
            with pytest.raises(NotRegularFile):
                sha256_file(special)


@needs_fifo
def test_non_regular_sources_are_refused_at_intake(tmp_path):
    """Critic (round 7): a registered directory crashed N02 with IsADirectoryError; a named pipe hung N02 holding the lock."""
    proj = _init_film(tmp_path)
    (proj / "sources/ui").mkdir()
    os.mkfifo(proj / "sources/pipe")
    eng = Engine(proj, executor=TaskPacketExecutor())
    with _no_hang():
        eng.run(max_steps=20)
        _submit_sources(eng, proj, [BRIEF, {"id": "S2", "path": "sources/ui", "kind": "prototype", "evidence": "observed"},
                                    {"id": "S3", "path": "sources/pipe", "kind": "brand", "evidence": "observed"}])
    rt = eng.rt("n01_context_intake")
    assert rt.status == S.ITERATE and sum("is not a regular file" in f for f in rt.failures) == 2


@needs_fifo
def test_a_source_swapped_for_a_pipe_after_intake_is_a_blocking_gap(tmp_path):
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    (proj / "sources/brand.md").write_text("# Brand guide\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    (proj / "sources/spec.md").unlink()
    os.mkfifo(proj / "sources/spec.md")                    # swapped after N01 passed, before N02 hashes it
    with _no_hang():
        eng.run(max_steps=20)
    assert eng.rt("n02_source_validation").outcome == "gaps_blocking" and eng.state.runtime.status.value == "BLOCKED"
    assert any("no longer a regular file" in g["text"] for g in _latest_gaps(eng, proj))


@needs_fifo
def test_special_files_in_the_run_block_with_a_message(project):
    """A control document or an output swapped for a pipe is refused or blocks the run with a reason; nothing hangs."""
    proj, _ = project
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake" / f"a{rt.attempts}" / "a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(out)
    with _no_hang():
        with pytest.raises(EngineError, match="not a regular file"):
            eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:t")
    assert eng.rt("a_intake").attempts == 1 and eng.rt("a_intake").status == S.WAITING_RUNTIME
    (proj / "controls/design.md").unlink()
    os.mkfifo(proj / "controls/design.md")
    with _no_hang():
        assert eng.step() == []
    assert eng.state.runtime.status.value == "BLOCKED"
    assert "controls/design.md is not a regular file" in eng.state.runtime.escalations[-1].report


def test_a_symlinked_sources_directory_counts_nothing(tmp_path):
    """Critic (round 7): with sources/ symlinked to the project root, every file in the project counted as evidence."""
    proj = _init_film(tmp_path)
    shared = tmp_path / "shared-sources"
    shutil.move(str(proj / "sources"), shared)
    (proj / "sources").symlink_to(shared, target_is_directory=True)
    (shared / "spec.md").write_text("# Product spec\n")
    (shared / "brand.md").write_text("# Brand guide\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    gaps = {g["id"]: g for g in _latest_gaps(eng, proj)}
    assert eng.rt("n02_source_validation").outcome == "gaps_resolvable"
    assert "S2 not counted: sources/ is itself a symlink" in gaps["G-PRODUCT"]["text"]
    assert "S3 not counted: sources/ is itself a symlink" in gaps["G-BRAND"]["text"]


# ---------------------------------------------------------------------------------------------- engineering critic, round 8
@needs_fifo
@pytest.mark.parametrize("target", ["vpg.project.yaml", ".vpg/ledger.jsonl", ".vpg/lock"])
@pytest.mark.parametrize("special", ["pipe", "directory"])
def test_a_special_project_file_is_refused_with_exit_4(project, capsys, target, special):
    """Critic (round 8): a pipe in place of vpg.project.yaml hung every command, holding the project lock for a mutating
    one; a directory crashed with a traceback. Every project-controlled read and write now refuses special files."""
    proj, _ = project
    Engine(proj, executor=FnExecutor()).step()
    path = proj / target
    if path.exists():
        path.unlink()
    os.mkfifo(path) if special == "pipe" else path.mkdir()
    with _no_hang():
        assert cli.main(["status", str(proj)]) == 4
        assert cli.main(["run", str(proj), "--lock-timeout", "0"]) == 4
    assert "is not a regular file" in capsys.readouterr().err


@needs_fifo
def test_tasks_never_hangs_on_a_special_gap_report(tmp_path, capsys):
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    (proj / "sources/brand.md").write_text("# Brand guide\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}])
    eng.run(max_steps=20)
    gaps = proj / next(a.path for a in eng.state.artifacts.values() if a.key == "gaps" and a.status == "current")
    eng.rt("n03_objective_audience").status = S.WAITING_HUMAN          # the run waits at a human gate
    eng._save("waiting at the N03 gate")
    gaps.unlink()
    os.mkfifo(gaps)
    with _no_hang():
        assert cli.main(["tasks", str(proj)]) == 4
    assert "is not a regular file" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------- engineering critic, round 9
@needs_fifo
def test_a_refused_approval_leaves_no_phantom_decision(tmp_path):
    """Critic (round 9): approve exited 4 when the document it locks was a pipe, but the ledger already held the decision."""
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=20)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN
    design = proj / "controls/design.md"
    text = design.read_text()
    design.unlink()
    os.mkfifo(design)
    with _no_hang():
        with pytest.raises(EngineError, match="cannot lock controls/design.md"):
            eng.decide("b_plan", "approved", actor="Wei", reason="looks right")
        assert cli.main(["approve", str(proj), "b_plan", "--actor", "Wei", "--reason", "looks right"]) == 4
    decisions = lambda: [x for x in _ledger_types(proj) if x in ("decision", "control_locked")]   # noqa: E731
    assert decisions() == [] and not eng.state.history.approvals and eng.rt("b_plan").status == S.WAITING_HUMAN
    design.unlink()
    design.write_text(text)                                                       # restored: the approval now goes through
    eng = Engine(proj, executor=FnExecutor())
    eng.decide("b_plan", "approved", actor="Wei", reason="looks right")
    assert decisions() == ["decision", "control_locked"] and eng.state.controls["design"].status == "approved"


@needs_fifo
def test_a_fixture_linked_to_a_special_file_fails_the_attempt(tmp_path):
    """Critic nit (round 9): the scripted executor followed a fixture symlink to a device (a disk-filling copy)."""
    from vpg.executors import ScriptedExecutor
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "dry_run": True}))   # fixtures replay only in dry runs
    fx = proj / "fixtures/a_intake/attempt-1"
    fx.mkdir(parents=True)
    (fx / "outputs.json").write_text(json.dumps({"outputs": {"a_intake": "a_intake.json"}}))
    os.mkfifo(tmp_path / "pipe")
    (fx / "a_intake.json").symlink_to(tmp_path / "pipe")
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"))
    with _no_hang():
        eng.step()
    assert eng.rt("a_intake").status == S.ITERATE and any("not a regular file" in f for f in eng.rt("a_intake").failures)


# ---------------------------------------------------------------------------------------------- engineering critic, round 10
def test_a_human_decision_is_all_or_nothing(tmp_path, monkeypatch, capsys):
    """Critic (round 10): a rejection with feedback half-saved (feedback saved, node still waiting) when the checkpoint write
    failed, and the retry recorded two decisions, two approvals and two feedback notes for one human decision."""
    import vpg.engine
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=20)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN
    before = len(_ledger_types(proj))
    real = vpg.engine.write_json_atomic

    def failing(path, obj):
        if "checkpoints" in str(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, obj)
    monkeypatch.setattr(vpg.engine, "write_json_atomic", failing)
    with pytest.raises(PermissionError):
        eng.decide("b_plan", "rejected", actor="Wei", reason="too long", feedback=["cut the intro"])
    assert cli.main(["approve", str(proj), "b_plan", "--actor", "Wei", "--reject", "--reason", "too long",
                     "--feedback", "cut the intro"]) == 4
    assert "could not read or write" in capsys.readouterr().err
    on_disk = json.loads((proj / ".vpg/state.json").read_text())
    assert on_disk["runtime"]["nodes"]["b_plan"]["status"] == "WAITING_HUMAN"
    assert not on_disk["history"]["approvals"] and not on_disk["evaluation"]["feedback"]
    assert set(_ledger_types(proj)[before:]) <= {"resumed"}                  # only the CLI's own resume bookkeeping
    assert eng.rt("b_plan").status == S.WAITING_HUMAN and not eng.state.history.approvals and not eng.state.evaluation.feedback
    monkeypatch.setattr(vpg.engine, "write_json_atomic", real)
    eng = Engine(proj, executor=FnExecutor())   # the CLI resumed the run meanwhile: reopen it (a stale engine refuses to write)
    eng.decide("b_plan", "rejected", actor="Wei", reason="too long", feedback=["cut the intro"])
    types = _ledger_types(proj)
    assert types.count("decision") == 1 and types.count("feedback") == 1
    assert len(eng.state.history.approvals) == 1 and len(eng.state.evaluation.feedback) == 1
    reloaded = Engine(proj, executor=FnExecutor())                         # what reached the disk is the whole decision
    assert len(reloaded.state.history.approvals) == 1 and reloaded.rt("b_plan").status != S.WAITING_HUMAN
    assert cli.main(["ledger-verify", str(proj)]) == 0


def test_change_request_errors_are_refusals(project, capsys):
    """Critic (round 10): an unknown CR id crashed with a StopIteration traceback; re-approving a CR re-opened it."""
    proj, _ = project
    Engine(proj, executor=FnExecutor()).step()
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--approve", "CR-999"]) == 4
    assert "no change request CR-999" in capsys.readouterr().err
    assert cli.main(["change-request", str(proj), "--doc", "design", "--reason", "fix the palette", "--actor", "Wei"]) == 0
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--approve", "CR-001"]) == 0
    assert cli.main(["change-request", str(proj), "--actor", "Wei", "--approve", "CR-001"]) == 4
    assert "only an open or deferred change request can be approved" in capsys.readouterr().err


def test_a_stale_engine_refuses_instead_of_overwriting_the_run(project):
    """Two engines on one run: the one that fell behind refuses to write, so no update is lost and the chain stays whole."""
    from vpg.util import ConcurrentChange
    proj, _ = project
    stale = Engine(proj, executor=FnExecutor())
    stale.step()
    fresh = Engine(proj, executor=FnExecutor())   # another process resumes and writes
    fresh.step()
    with pytest.raises(ConcurrentChange, match="another vpg process"):
        stale.run(max_steps=5)
    assert cli.main(["ledger-verify", str(proj)]) == 0
    fresh.run(max_steps=200)
    assert fresh.state.runtime.status.value == "RUN_PASS" and cli.main(["ledger-verify", str(proj)]) == 0


# ---------------------------------------------------------------------------------------------- engineering critic, round 11
def _writer_waiting(tmp_path):
    """b_plan writes design.md, which the workflow accepts at its completion; it then waits for the human to approve."""
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    design = proj / "controls/design.md"
    base = design.read_text()

    def b(ctx):
        design.write_text(base + "\n<!-- the plan's design -->\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.run(max_steps=20)
    assert eng.rt("b_plan").status == S.WAITING_HUMAN
    return proj, eng, design


def test_an_approval_never_locks_an_unaccepted_edit(tmp_path):
    """Critic (round 11, major): an edit made while the writer waited was locked by the approval, unlogged."""
    proj, eng, design = _writer_waiting(tmp_path)
    accepted = design.read_text()
    design.write_text(accepted + "\n<!-- an edit outside the workflow -->\n")
    with pytest.raises(EngineError, match="changed since the workflow accepted it"):
        eng.decide("b_plan", "approved", actor="Wei", reason="the plan is right")
    assert "decision" not in _ledger_types(proj) and eng.state.controls["design"].status != "approved"
    design.write_text(accepted)
    eng.decide("b_plan", "approved", actor="Wei", reason="the plan is right")
    assert eng.state.controls["design"].approved_sha256 == eng.state.controls["design"].sha256
    assert (proj / eng.state.controls["design"].snapshot).read_text() == accepted       # the version the human reviewed


def test_a_failed_rollback_puts_the_documents_back(tmp_path, monkeypatch):
    """Critic (round 11): a rollback that failed at its save left the documents rolled back while the state was not."""
    from vpg.util import ConcurrentChange
    proj = make_project(tmp_path, lock_b=("design",))
    design = proj / "controls/design.md"
    base = design.read_text()

    def b(ctx):
        design.write_text(base + f"\n<!-- plan {ctx.attempt} -->\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.step()
    first = eng.state.history.checkpoints[-1].seq
    eng.run(max_steps=200)
    current, events = design.read_text(), len(_ledger_types(proj))

    def fail(self):
        raise ConcurrentChange("simulated failure at the rollback's commit")
    monkeypatch.setattr(Engine, "_write_state", fail)
    with pytest.raises(ConcurrentChange):
        eng.rollback(first, actor="Wei")
    monkeypatch.undo()
    assert design.read_text() == current and len(_ledger_types(proj)) == events            # nothing happened
    fresh = Engine(proj, executor=FnExecutor())
    assert fresh._integrity_problems() == [] and fresh.state.runtime.status.value == "RUN_PASS"


def test_a_ledger_write_failure_after_commit_catches_up_on_reopen(tmp_path, monkeypatch, capsys):
    """Critic (round 11): the state was saved but the decision never reached the ledger, and a retry was refused."""
    from vpg.provenance import Ledger, LedgerBehind
    proj, eng, design = _writer_waiting(tmp_path)

    def fail(self, lines):
        self._behind = True
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Ledger, "write_lines", fail)
    with pytest.raises(LedgerBehind, match="was saved"):
        eng.decide("b_plan", "approved", actor="Wei", reason="the plan is right")
    monkeypatch.undo()
    on_disk = json.loads((proj / ".vpg/state.json").read_text())
    assert on_disk["history"]["approvals"] and on_disk["runtime"]["pending_ledger"]   # committed, events kept write-ahead
    assert "decision" not in _ledger_types(proj)
    with pytest.raises(LedgerBehind):
        eng.step()                                   # this engine stops appending: the chain can't fork
    assert cli.main(["status", str(proj)]) == 0 and "decision" not in _ledger_types(proj)   # read-only: never writes
    assert cli.main(["run", str(proj)]) == 0         # the next command that changes the run writes the pending events
    assert _ledger_types(proj).count("decision") == 1 and _ledger_types(proj).count("control_locked") == 1
    reopened = Engine(proj, executor=FnExecutor())
    assert reopened.state.runtime.pending_ledger == [] and cli.main(["ledger-verify", str(proj)]) == 0
    assert "events: ok" in capsys.readouterr().out


def test_a_torn_ledger_line_is_set_aside(project, capsys):
    """Critic (round 11): after a cut-short write, the next event was glued onto the fragment and every command crashed."""
    proj, _ = project
    Engine(proj, executor=FnExecutor()).step()
    with open(proj / ".vpg/ledger.jsonl", "a") as f:
        f.write('{"seq": 999, "ts": "2026')                 # a write cut short: no newline
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    torn = list((proj / ".vpg").glob("ledger.jsonl.torn-*"))
    assert len(torn) == 1 and torn[0].read_text() == '{"seq": 999, "ts": "2026'
    assert cli.main(["ledger-verify", str(proj)]) == 0 and cli.main(["status", str(proj)]) == 0


def test_a_corrupt_ledger_line_is_reported_not_a_traceback(project, capsys):
    proj, _ = project
    Engine(proj, executor=FnExecutor()).step()
    with open(proj / ".vpg/ledger.jsonl", "a") as f:
        f.write("this is not an event\n")
    assert cli.main(["status", str(proj)]) == 0      # read-only: the state stays readable
    assert cli.main(["run", str(proj)]) == 4         # a command that would append refuses, with the reason
    assert cli.main(["ledger-verify", str(proj)]) == 1
    captured = capsys.readouterr()
    assert "is not a valid event" in captured.err and "is not a valid event" in captured.out


# ---------------------------------------------------------------------------------------------- engineering critic, round 12
def test_a_rollback_failing_partway_puts_back_what_it_restored(tmp_path):
    """Critic (round 12): with production.md read-only, rollback restored design.md, failed on production.md, and left
    design.md (and its accepted copy) at the checkpoint's text while the state was not rolled back."""
    proj = make_project(tmp_path, lock_b=("design", "production"))
    docs = {d: proj / f"controls/{d}.md" for d in ("design", "production")}
    base = {d: p.read_text() for d, p in docs.items()}

    def b(ctx):
        for d, p in docs.items():
            p.write_text(base[d] + f"\n<!-- plan {ctx.attempt} -->\n")
        (ctx.workdir / "b_plan.json").write_text("{}")
        return {"b_plan": "b_plan.json"}
    eng = Engine(proj, executor=FnExecutor({"b_plan": b}))
    eng.step()
    first = eng.state.history.checkpoints[-1].seq
    eng.run(max_steps=200)
    current = {d: p.read_text() for d, p in docs.items()}
    docs["production"].chmod(0o444)
    try:
        with pytest.raises(OSError) as failed:
            eng.rollback(first, actor="Wei")
    finally:
        docs["production"].chmod(0o644)
    assert any("could not undo part of this action" in n for n in getattr(failed.value, "__notes__", []))   # shown, not hidden
    assert {d: p.read_text() for d, p in docs.items()} == current                        # design.md was put back
    fresh = Engine(proj, executor=FnExecutor())
    assert fresh._integrity_problems() == [] and fresh.state.runtime.status.value == "RUN_PASS"
    from vpg.util import sha256_file
    assert sha256_file(proj / ".vpg/controls/design.accepted.md") == fresh.state.controls["design"].sha256
    fresh.rollback(first, actor="Wei")                                                   # repeating it completes it
    assert {d: p.read_text() for d, p in docs.items()} == base


def test_restore_control_is_the_working_remedy_for_an_unaccepted_edit(tmp_path, capsys):
    """Critic (round 12): 'reject with feedback' and a change request did not clear an edit made outside the workflow."""
    proj, eng, design = _writer_waiting(tmp_path)
    accepted = design.read_text()
    design.write_text(accepted + "\n<!-- an edit outside the workflow -->\n")
    assert cli.main(["approve", str(proj), "b_plan", "--actor", "Wei", "--reason", "ok"]) == 4
    assert "vpg restore-control" in capsys.readouterr().err
    assert cli.main(["restore-control", str(proj), "--doc", "design", "--actor", "agent:x"]) == 4    # humans only
    assert cli.main(["restore-control", str(proj), "--doc", "design", "--actor", "Wei"]) == 0
    assert design.read_text() == accepted
    kept = list((proj / ".vpg/controls").glob("design.discarded*.md"))
    assert len(kept) == 1 and "an edit outside the workflow" in kept[0].read_text()
    assert cli.main(["restore-control", str(proj), "--doc", "design", "--actor", "Wei"]) == 4       # nothing left to restore
    assert cli.main(["approve", str(proj), "b_plan", "--actor", "Wei", "--reason", "ok"]) == 0
    assert "control_restored" in _ledger_types(proj) and cli.main(["ledger-verify", str(proj)]) == 0


# ---------------------------------------------------------------------------------------------- engineering critic, round 13
def test_a_node_that_deletes_a_control_document_fails_and_the_document_returns(project):
    """Critic (round 13, major): deleting a control document went unnoticed; later checks read an empty document and passed."""
    proj, _ = project
    design = proj / "controls/design.md"
    accepted = design.read_text()

    def a(ctx):
        design.unlink()                                   # a_intake may not write design.md
        (ctx.workdir / "a_intake.json").write_text("{}")
        return {"a_intake": "a_intake.json"}
    eng = Engine(proj, executor=FnExecutor({"a_intake": a}))
    eng.step()
    rt = eng.rt("a_intake")
    assert rt.status == S.ITERATE and any("deleted controls/design.md" in f and "(restored)" in f for f in rt.failures)
    assert design.read_text() == accepted


def test_a_document_deleted_outside_the_workflow_blocks_until_restored(project, capsys):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    production = proj / "controls/production.md"
    accepted = production.read_text()
    production.unlink()
    assert eng.step() == [] and eng.state.runtime.status.value == "BLOCKED"
    assert "production.md was deleted outside the workflow" in eng.state.runtime.escalations[-1].report
    assert cli.main(["restore-control", str(proj), "--doc", "production", "--actor", "Wei"]) == 0
    assert production.read_text() == accepted and "none (the document was missing" in capsys.readouterr().out
    assert cli.main(["unblock", str(proj), "integrity", "--actor", "Wei"]) == 0
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"


@needs_fifo
def test_restore_control_recreates_a_document_replaced_by_a_pipe(project):
    """Critic (round 13, minor): restore-control said 'nothing to restore' exactly when every message told Wei to restore."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    design = proj / "controls/design.md"
    accepted = design.read_text()
    design.unlink()
    os.mkfifo(design)
    with _no_hang():
        assert cli.main(["restore-control", str(proj), "--doc", "design", "--actor", "Wei"]) == 0
    assert design.is_file() and design.read_text() == accepted
    (proj / "controls/creative.md").unlink()
    (proj / "controls/creative.md").mkdir()               # a directory is never deleted: the refusal says what to do
    assert cli.main(["restore-control", str(proj), "--doc", "creative", "--actor", "Wei"]) == 4


def test_release_refuses_an_empty_deliverables_list(project):
    """N23 recomputes the release: a production spec that lists no deliverables would ship nothing."""
    from vpg.controls import split_front_matter
    from vpg.tools import verify
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    assert not any("lists no deliverables" in p for p in verify.final(ctx).data["problems"])
    prod = proj / "controls/production.md"
    fm, body = split_front_matter(prod.read_text())
    fm["deliverables"] = []
    prod.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n" + body)
    assert any("lists no deliverables" in p for p in verify.final(ctx).data["problems"])


# ---------------------------------------------------------------------------------------------- engineering critic, round 14
def test_a_truncated_deleted_or_rewritten_ledger_is_refused(project, capsys):
    """Critic (round 14): deleting the ledger, or cutting off its last events, still verified and shipped at N23."""
    from vpg.provenance import Ledger
    from vpg.tools import verify
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    led = proj / ".vpg/ledger.jsonl"
    full = led.read_text()
    assert cli.main(["ledger-verify", str(proj)]) == 0
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    assert not any("ledger" in p for p in verify.final(ctx).data["problems"])
    led.write_text("".join(full.splitlines(keepends=True)[:-5]))              # the last five events cut off
    assert cli.main(["ledger-verify", str(proj)]) == 1
    assert "ends before event" in capsys.readouterr().out
    assert any("ends before event" in p for p in verify.final(ctx).data["problems"])
    forged = Ledger(led)                                                     # a valid chain re-grown past the recorded event
    for _ in range(8):
        forged.append("note", text="rewritten")
    assert cli.main(["ledger-verify", str(proj)]) == 1
    assert "is not the one the state recorded" in capsys.readouterr().out
    led.unlink()                                                             # deleted outright
    assert cli.main(["ledger-verify", str(proj)]) == 1
    led.write_text(full)
    assert cli.main(["ledger-verify", str(proj)]) == 0


@pytest.mark.parametrize("change", ["rubric", "tool"])
def test_a_graph_with_a_missing_reference_does_not_start(tmp_path, change):
    """Critic (round 14): with a node's rubric file missing, a run started and that node's MUST checks disappeared."""
    wf = mini_workflow(tmp_path)
    data = yaml.safe_load(wf.read_text())
    r2 = next(n for n in data["nodes"] if n["id"] == "r2_review")
    if change == "rubric":
        r2["rubric"] = ["no-such-rubric"]
    else:
        r2["run"] = {"tool": "tests.fakes:no_such_tool"}
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(EngineError, match="no-such-rubric missing" if change == "rubric" else "does not resolve"):
        Engine(make_project(tmp_path, workflow=wf), executor=FnExecutor())


def test_n19_scores_untagged_claims():
    """Critic (round 14): claims checks saw only declared claim ids; the N19 reviewer now scores untagged claims (MUST)."""
    from vpg.evaluation import load_rubrics
    from vpg.graph import load_workflow
    n19 = next(n for n in load_workflow().nodes if n.id == "n19_product_accuracy")
    dims = {d["id"]: d for rid in n19.rubric for d in load_rubrics(PKG)[rid]["dimensions"]}
    assert dims["ACC-UNTAGGED"]["level"] == "MUST" and dims["ACC-UNTAGGED"]["check"]["type"] == "review"


def test_blocks_name_what_changed_and_how_to_restore(tmp_path):
    proj, eng, design = _writer_waiting(tmp_path)
    design.unlink()
    with pytest.raises(EngineError, match="vpg restore-control"):
        eng.decide("b_plan", "approved", actor="Wei", reason="ok")
    eng.restore_control("design", actor="Wei")
    cfg = proj / "vpg.project.yaml"
    cfg.write_text(cfg.read_text() + "note: a mid-run edit\n")
    eng.step()
    assert eng.state.runtime.status.value == "BLOCKED"
    assert "configuration changed since this run started (project)" in eng.state.runtime.escalations[-1].report


# ---------------------------------------------------------------------------------------------- engineering critic, round 15 (final)
def test_the_next_command_refuses_a_damaged_ledger_until_restored_or_accepted(project, capsys):
    """Critic (round 15): after a deletion or truncation, the next `vpg run` appended and re-anchored, so N23 passed."""
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.step()
    eng.step()
    led = proj / ".vpg/ledger.jsonl"
    full = led.read_text()
    led.write_text("".join(full.splitlines(keepends=True)[:-3]))
    damaged = led.read_text()
    assert cli.main(["run", str(proj)]) == 4
    assert "ends before event" in capsys.readouterr().err and led.read_text() == damaged   # nothing was written
    assert cli.main(["status", str(proj)]) == 0                                             # still readable
    led.write_text(full)                                                                    # a backup restores it
    assert cli.main(["run", str(proj)]) == 0
    led.unlink()                                                                            # lost, with no backup
    assert cli.main(["run", str(proj)]) == 4
    assert cli.main(["accept-ledger-break", str(proj), "--actor", "agent:x", "--reason", "gone"]) == 4
    assert cli.main(["accept-ledger-break", str(proj), "--actor", "Wei", "--reason", "disk failure; no backup"]) == 0
    assert "ledger_break_accepted" in _ledger_types(proj)
    assert cli.main(["run", str(proj)]) == 0 and cli.main(["ledger-verify", str(proj)]) == 0
    assert "break accepted by Wei" in capsys.readouterr().out
    assert cli.main(["accept-ledger-break", str(proj), "--actor", "Wei", "--reason", "again"]) == 4   # nothing to accept now


def test_ledger_verify_reports_pending_events_not_a_loss(tmp_path, monkeypatch, capsys):
    """Critic (round 15): with events pending after a failed ledger write, ledger-verify advised restoring a backup."""
    from vpg.provenance import Ledger, LedgerBehind
    proj, eng, _ = _writer_waiting(tmp_path)

    def fail(self, lines):
        self._behind = True
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Ledger, "write_lines", fail)
    with pytest.raises(LedgerBehind):
        eng.decide("b_plan", "approved", actor="Wei", reason="the plan is right")
    monkeypatch.undo()
    assert cli.main(["ledger-verify", str(proj)]) == 0
    assert "pending in the state" in capsys.readouterr().out
    assert cli.main(["run", str(proj)]) == 0 and cli.main(["ledger-verify", str(proj)]) == 0
