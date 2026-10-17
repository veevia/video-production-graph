"""Changed code reaches the film (triage #99, #97).

`vpg accept-config` kept the pass of a producer whose code changed, and nothing scheduled the re-check its status warning
promised: on the SRA run the take recorder's fix (`sra-take/v2`) sat in the pipeline while N11's registered take stayed
`sra-take/v1`, until an owner invalidated N11 by hand. An accepted configuration now re-runs every command node whose
code changed, and so does every rework cycle, whatever its plan routes. A narrowed code set is not a change (#97), and a
human-gated node is left to a human."""

from __future__ import annotations

import json

import yaml

from tests.conftest import FnExecutor, mini_workflow
from tests.test_bookkeeping import COMMAND_SCRIPT, _bind, events
from tests.test_hardening import make_project
from vpg.context import NodeResult
from vpg.engine import Engine
from vpg.models import NodeStatus as S
from vpg.tools._common import finding


def review_by_version(ctx):
    """Reports a P2 on d_build for the versions flags/script.json lists under "defect", and appends to the file under
    "edit" while that version is reviewed, as a pipeline fix made meanwhile."""
    script = json.loads((ctx.project_dir / "flags" / "script.json").read_text())
    version = ctx.state.runtime.current_version
    path = (script.get("edit") or {}).get(str(version))
    if path and "# fixed while reviewed" not in (ctx.project_dir / path).read_text():
        with open(ctx.project_dir / path, "a") as f:
            f.write("# fixed while reviewed\n")
    if version in script.get("defect", []):
        return NodeResult(findings=[finding("test:size", "LEG-SIZE", "P2", "label is 14px", traced="d_build", key="d_build", fix="enlarge")])
    return NodeResult()


def _project(tmp_path, script, human_b=False):
    proj = make_project(tmp_path, workflow=mini_workflow(tmp_path, human_b=human_b,
                                                         review_tool="tests.test_code_reaches_the_film:review_by_version"))
    (proj / "flags").mkdir(exist_ok=True)
    (proj / "flags" / "script.json").write_text(json.dumps(script))
    (proj / "pipeline").mkdir()
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    return proj


def _run(proj):
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=400)
    return eng


def test_accepting_a_configuration_runs_the_changed_code(tmp_path):
    proj = _project(tmp_path, {})
    _bind(proj, "{python} pipeline/run.py")
    eng = _run(proj)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.rt("c_audio").executions == 1
    with open(proj / "pipeline/run.py", "a") as f:
        f.write("# the fixed recorder\n")
    _bind(proj, "{python} pipeline/run.py", command_timeout_s=900)          # a configuration change to accept
    eng = Engine(proj, executor=FnExecutor())
    [warning] = [w for w in eng.status()["warnings"] if w.startswith("c_audio:")]
    assert "the next accepted configuration or rework cycle runs the new code" in warning
    recorded = dict(eng.rt("c_audio").command_code)
    eng.rt("c_audio").command_code = {**recorded, "pipeline/film/generated.js": "0" * 64}
    assert eng._code_changed_nodes() == ["c_audio"]            # run.py changed; the extra file alone would not count
    eng.rt("c_audio").command_code = {**dict(eng.project.command_code("c_audio")), "pipeline/film/generated.js": "0" * 64}
    assert eng._code_changed_nodes() == []                     # #97: only a file it no longer counts differs
    eng.rt("c_audio").command_code = recorded
    eng.accept_config(actor="Wei", reason="the recorder fix")
    assert events(proj, "config_accepted")[-1]["rerun"] == ["c_audio"] and eng.rt("c_audio").status == S.STALE
    eng.run(max_steps=400)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.rt("c_audio").executions == 2


def test_a_rework_cycle_also_runs_command_nodes_whose_code_changed(tmp_path):
    proj = _project(tmp_path, {"defect": [1], "edit": {"1": "pipeline/run.py"}})
    _bind(proj, "{python} pipeline/run.py")
    eng = _run(proj)
    assert eng.state.runtime.status.value == "RUN_PASS"
    [ride] = events(proj, "code_changed_rerun")
    assert ride["nodes"] == ["c_audio"] and ride["cycle"] == 1
    assert eng.rt("c_audio").executions == 2 and eng.rt("d_build").executions == 2


def test_a_human_gated_command_node_is_left_to_a_human(tmp_path):
    proj = _project(tmp_path, {}, human_b=True)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "commands": {"b_plan": "{python} pipeline/run.py"}}))
    eng = _run(proj)
    eng.decide("b_plan", "approved", actor="Wei", reason="the plan")
    eng.run(max_steps=400)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.rt("b_plan").command_code
    with open(proj / "pipeline/run.py", "a") as f:
        f.write("# edited after Wei approved the plan\n")
    eng = Engine(proj, executor=FnExecutor())
    assert eng._code_changed_nodes() == []
    [warning] = [w for w in eng.status()["warnings"] if w.startswith("b_plan:")]
    assert "it is human-gated, so it re-runs only when a human invalidates it" in warning
