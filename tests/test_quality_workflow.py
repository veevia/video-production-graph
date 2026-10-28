"""P4: the quality workflow (graph/workflow-quality.yaml): look and animatic gates before any asset is built, the spec
family as control documents, the spec's build gates and ratchet at technical QA, and spec proposals at N21.

Acceptance: a production whose style frames fail the approved look gates is stopped at N26 and never reaches asset
generation; one whose frames pass runs end to end. The default workflow and its runs are untouched.
"""
from __future__ import annotations

import copy
import json
import shlex
import shutil
from types import SimpleNamespace

import pytest
import yaml

from tests import quality_fixtures as qf
from tests.conftest import PKG
from vpg import quality
from vpg.controls import CONTROL_DOCS, KNOWN_DOCS, SPEC_CONTROL_DOCS, ControlStore, docs_for, rel_path
from vpg.dryrun import REVIEWERS
from vpg.engine import Engine, EngineError
from vpg.executors import ScriptedExecutor
from vpg.graph import OPTIONAL_STAGES, REQUIRED_STAGES, load_workflow, resolve_workflow, validate
from vpg.models import NodeStatus, Workflow
from vpg.provenance import Ledger
from vpg.spec import family_hash, load_family
from vpg.tools import qualitygates

QUALITY = load_workflow(resolve_workflow("quality"))
DEFAULT = load_workflow()


@pytest.fixture(scope="module")
def measured_registry(tmp_path_factory):
    """The benchmark registry is the slow part: measure it once and copy it into each project."""
    d = tmp_path_factory.mktemp("bench") / "benchmarks"
    qf.registry(d)
    return d


def _project(tmp_path, measured_registry, look="good"):
    return qf.quality_project(tmp_path, look=look, registry_dir=measured_registry)


def _ledger(proj):
    return list(Ledger(proj / ".vpg" / "ledger.jsonl").events())


def _first(events, kind, node):
    return next((i for i, e in enumerate(events) if e["type"] == kind and e["data"].get("node") == node), None)


def _hook():
    injected = {"done": False}

    def hook(e):   # the sample's scripted N21 interprets this feedback, as in `vpg dry-run`
        if not injected["done"] and e.state.runtime.current_version == 1 and all(
                e.rt(n).status == NodeStatus.STEP_PASS for n in REVIEWERS):
            e.add_feedback("The captions appear after the words are spoken, right from the first line.",
                           actor="simulated reviewer (dry run, not Wei)", at="00:00–00:05", severity="P2")
            injected["done"] = True
    return hook


# ------------------------------------------------------------------------------------------------ the graph
def test_quality_workflow_is_generated_valid_and_opt_in():
    assert validate(QUALITY) == [] and validate(DEFAULT) == []
    assert (PKG / "graph" / "workflow-quality.yaml").read_text() == quality.text()   # `vpg docs --check` guards this too
    names = [n.name for n in sorted(QUALITY.nodes, key=lambda n: n.num)]
    assert names[:25] == REQUIRED_STAGES and names[25:] == OPTIONAL_STAGES
    assert [n.name for n in sorted(DEFAULT.nodes, key=lambda n: n.num)] == REQUIRED_STAGES
    assert QUALITY.id != DEFAULT.id and resolve_workflow(None) == resolve_workflow("default")


def test_quality_workflow_differs_from_the_default_only_where_declared():
    """Drift guard: every default node the overlay does not extend, and every default edge it does not remove, is
    identical in the quality workflow; an extended node keeps every default value it does not set."""
    q_nodes = {n.id: n.model_dump() for n in QUALITY.nodes}
    for n in DEFAULT.nodes:
        d, q = n.model_dump(), q_nodes[n.id]
        patch = quality.NODES.get(n.id)
        if patch is None:
            assert d == q, n.id
            continue
        touched = {k.split(".")[0] for k in list(patch.get("append", {})) + list(patch.get("set", {}))} | {"instructions"}
        assert {k: v for k, v in d.items() if k not in touched} == {k: v for k, v in q.items() if k not in touched}, n.id
        for dotted in patch.get("append", {}):
            head, *rest = dotted.split(".")
            dv, qv = d[head], q[head]
            for part in rest:
                dv, qv = dv[part], qv[part]
            assert qv[:len(dv)] == dv, (n.id, dotted)   # the default's items first, unchanged
        assert q["instructions"].startswith(d["instructions"].rstrip()), n.id
    q_edges = {e.id: e.model_dump() for e in QUALITY.edges}
    for e in DEFAULT.edges:
        assert (e.id in quality.REMOVED_EDGES) != (q_edges.get(e.id) == e.model_dump()), e.id
    assert QUALITY.policies == DEFAULT.policies


def test_validator_accepts_only_the_known_optional_stages():
    data = yaml.safe_load(quality.text())
    bad = copy.deepcopy(data)
    next(n for n in bad["nodes"] if n["id"] == "n27_animatic")["name"] = "Teaser cut"
    assert any("optional stages" in e for e in validate(Workflow.model_validate(bad)))
    dup = copy.deepcopy(data)
    next(n for n in dup["nodes"] if n["id"] == "n27_animatic")["name"] = "Look development"
    assert any("optional stages" in e for e in validate(Workflow.model_validate(dup)))


def test_control_document_registry_is_exact():
    assert docs_for(DEFAULT) == CONTROL_DOCS
    assert docs_for(QUALITY) == CONTROL_DOCS + list(SPEC_CONTROL_DOCS)
    assert rel_path("design") == "controls/design.md" and rel_path("spec-design") == "spec/DESIGN.md"
    assert ControlStore.suffix("spec-gates") == ".yaml" and ControlStore.suffix("evaluation") == ".md"
    data = yaml.safe_load(quality.text())
    next(n for n in data["nodes"] if n["id"] == "n26_look_development")["controls"]["read"].append("spec-palette")
    assert any("unknown control document spec-palette" in e for e in validate(Workflow.model_validate(data)))
    assert "spec-palette" not in KNOWN_DOCS


def test_each_spec_document_has_one_writer_that_locks_it():
    writers = {d: [n.id for n in QUALITY.nodes if d in n.controls.write] for d in SPEC_CONTROL_DOCS}
    lockers = {d: [n.id for n in QUALITY.nodes if d in n.controls.lock_on_approval] for d in SPEC_CONTROL_DOCS}
    assert writers == lockers and all(len(v) == 1 for v in writers.values()), writers
    assert writers["spec-gates"] == ["n03_objective_audience"] and writers["spec-story"] == ["n06_creative_direction"]
    assert writers["spec-design"] == ["n26_look_development"] and writers["spec-voice"] == ["n09_visual_system"]
    n09 = QUALITY.node("n09_visual_system")
    assert "design" in n09.controls.write and "design" not in n09.controls.lock_on_approval
    assert "design" in QUALITY.node("n26_look_development").controls.lock_on_approval


def test_quality_tool_dimensions_match_what_tools_emit():
    from vpg.evaluation import load_rubrics
    rubrics = load_rubrics()
    n15 = QUALITY.node("n15_technical_qa")
    declared = {d["id"] for r in n15.rubric for d in rubrics[r]["dimensions"]}
    src = (PKG / "vpg/tools/qualitygates.py").read_text() + (PKG / "vpg/tools/media.py").read_text()
    import re
    assert set(re.findall(r'"((?:TEC|GAT)-[A-Z-]+)"', src)) <= declared
    assert {d["rule"] for d in rubrics["quality-gates"]["dimensions"]} == {"R-REG-001"}   # advisory until adopted


# ------------------------------------------------------------------------------------------------ acceptance
def test_bad_look_is_stopped_before_any_asset_is_built(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry, look="bad")
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    st = eng.run(max_steps=400)
    assert st["status"] == "BLOCKED"
    rt = eng.rt("n26_look_development")
    assert rt.status == NodeStatus.BLOCKED and rt.attempts == 3
    text = " ".join(rt.failures)
    for role in qf.ROLES:
        assert f"GT-GROUND-001 (DS-GROUND-001) for the {role} frame" in text
    assert "is above the envelope" in text
    events = _ledger(proj)
    for later in ("n08_storyboard_shots", "n27_animatic", "n10_asset_planning", "n11_asset_generation", "n12_voice_audio"):
        assert _first(events, "node_dispatched", later) is None, later
    assert (proj / ".vpg" / "escalations" / "n26_look_development-1.md").is_file()
    report = json.loads(next((proj / "work" / "n26_look_development").glob("a3/look-report.json")).read_text())
    assert report["passed"] is False and {r["role"] for r in report["gates"]} == set(qf.ROLES)


def test_good_look_and_animatic_run_end_to_end(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    eng.on_step = _hook()
    st = eng.run(max_steps=600)
    assert st["status"] == "RUN_PASS", [(n, r.failures) for n, r in eng.state.runtime.nodes.items() if r.failures]
    events = _ledger(proj)
    look, ani = _first(events, "decision", "n26_look_development"), _first(events, "decision", "n27_animatic")
    assert look < _first(events, "node_dispatched", "n08_storyboard_shots") < ani
    for later in ("n10_asset_planning", "n12_voice_audio", "n11_asset_generation", "n13_motion_scenes"):
        assert ani < _first(events, "node_dispatched", later), later
    locked = {(e["data"]["doc"]) for e in events if e["type"] == "control_locked"}
    assert set(SPEC_CONTROL_DOCS) | {"design", "production", "creative", "evaluation"} <= locked
    assert all(eng.state.controls[d].status == "approved" for d in SPEC_CONTROL_DOCS)
    # N15: the spec's build gates and the ratchet ran on the master; R-REG-001 is advisory, so they never block
    rep = json.loads(sorted((proj / "work" / "n15_technical_qa").glob("a*/quality-gates.json"))[-1].read_text())
    assert {r["gate"] for r in rep["gates"]} == {"GT-GROUND-002", "GT-MOTION-001", "GT-OVERLAP-001", "GT-VOM-004"}
    assert rep["baseline"] == "BEST-1" and rep["registry_problem"] == ""
    gat = [d for d in eng.state.evaluation.defects if d.dimension.startswith("GAT-")]
    assert gat and all(d.severity == "nit" for d in gat)
    assert {"GAT-SPEC", "GAT-RATCHET"} <= {d.dimension for d in gat}
    # observability: every node that ran has evidence, N26 and N27 included
    from vpg.telemetry import coverage, derive
    tel = derive(proj)
    cov = coverage(tel, events)
    assert cov["ok"], cov["missing"]
    assert any(s["attributes"].get("vpg.node.id") == "n26_look_development" for s in tel["spans"])
    # a dry-run auto-approval is no human's decision, and is never tagged as one
    auto = [a for a in eng.state.history.approvals if a.actor == "dry-run-auto"]
    assert auto and all(a.evidence == "assumed" for a in auto)
    decided = [e for e in tel["evaluations"] if e["name"] == "vpg.human_decision"]
    assert decided and all(e["evidence_tag"] == "assumed" and e["vpg.human"] is False for e in decided)
    assert all("not a human's decision" in e["gen_ai.evaluation.explanation"] for e in decided)
    # the package carries the approved spec beside the control documents
    import zipfile
    names = zipfile.ZipFile(next((proj / "work" / "n24_packaging_delivery").glob("a*/*.zip"))).namelist()
    assert all(any(n.endswith(f"spec/{f}") for n in names) for f in ("GATES.yaml", "DESIGN.tokens.json", "STORY.md"))


def test_rejected_animatic_rewrites_the_script_and_comes_back(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"))
    rejected = False
    for _ in range(40):
        eng.run(max_steps=200)
        waiting = [n for n, r in eng.state.runtime.nodes.items() if r.status == NodeStatus.WAITING_HUMAN]
        if not waiting:
            break
        nid = waiting[0]
        if nid == "n27_animatic" and not rejected:
            before = eng.rt("n07_script_development").executions
            eng.decide(nid, "rejected", actor="Wei", reason="the middle beat drags",
                       feedback=["The middle beat drags; tighten the signal chapter"])
            rejected = True
            continue
        eng.decide(nid, "approved", actor="Wei", reason="ok")
        if nid == "n27_animatic":
            break
    assert rejected
    assert eng.rt("n07_script_development").executions > before
    eng.run(max_steps=50)
    events = _ledger(proj)
    decisions = [i for i, e in enumerate(events) if e["type"] == "decision" and e["data"].get("node") == "n27_animatic"]
    assert len(decisions) == 2   # rejected, then approved
    rej, ok = decisions

    def after(kinds, node):
        return [i for i, e in enumerate(events) if e["type"] in kinds and e["data"].get("node") == node and rej < i < ok]
    assert after(("node_dispatched",), "n07_script_development")              # the script is rewritten with the feedback
    assert after(("node_dispatched", "memo_hit"), "n08_storyboard_shots")     # the storyboard re-plans (or memo-passes)
    assert after(("node_dispatched",), "n27_animatic")                        # and the animatic is made again
    assert not after(("node_dispatched",), "n10_asset_planning")              # nothing is built on the rejected timing
    assert _first(events, "node_dispatched", "n10_asset_planning") > ok
    fb = [f for f in eng.state.evaluation.feedback if "middle beat drags" in f.text]
    assert fb and fb[0].actor == "Wei"


def test_rejected_look_reruns_and_reapproval_locks_once(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"))
    decided = []
    for _ in range(40):
        eng.run(max_steps=200)
        waiting = [n for n, r in eng.state.runtime.nodes.items() if r.status == NodeStatus.WAITING_HUMAN]
        if not waiting or "n08_storyboard_shots" in [n for n, r in eng.state.runtime.nodes.items()
                                                     if r.status == NodeStatus.STEP_PASS]:
            break
        nid = waiting[0]
        if nid == "n26_look_development" and "n26:rejected" not in decided:
            eng.decide(nid, "rejected", actor="Wei", reason="too dark", feedback=["Lift the ground one step"])
            decided.append("n26:rejected")
            continue
        eng.decide(nid, "approved", actor="Wei", reason="ok")
        decided.append(f"{nid[:3]}:approved")
    assert decided.count("n26:rejected") == 1 and "n26:approved" in decided
    assert eng.rt("n26_look_development").executions == 2
    st = eng.state.controls["spec-design"]
    assert st.status == "approved" and st.version == 1 and st.snapshot.endswith("spec-design@v1.md")
    events = _ledger(proj)
    assert [e["data"]["doc"] for e in events if e["type"] == "control_locked"].count("spec-design") == 1


# ------------------------------------------------------------------------------------------------ ownership and integrity
class _EditingExecutor(ScriptedExecutor):
    """N26's first attempt also edits GATES.yaml, which N03 owns."""

    def execute(self, ctx):
        if ctx.node.id == "n26_look_development" and ctx.attempt == 1:
            p = ctx.project_dir / "spec" / "GATES.yaml"
            p.write_text(p.read_text().replace("severity: P2, stage: look", "severity: nit, stage: look"))
        return super().execute(ctx)


def test_a_writer_cannot_edit_a_spec_document_it_does_not_own(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    before = (proj / "spec" / "GATES.yaml").read_bytes()
    eng = Engine(proj, executor=_EditingExecutor(proj / "fixtures"), auto_approve=True)
    for _ in range(400):
        eng.step()
        if eng.rt("n26_look_development").status == NodeStatus.STEP_PASS or eng.state.runtime.status.value == "BLOCKED":
            break
    assert eng.rt("n26_look_development").status == NodeStatus.STEP_PASS
    assert (proj / "spec" / "GATES.yaml").read_bytes() == before
    failed = [e for e in _ledger(proj) if e["type"] == "node_failed" and e["data"].get("node") == "n26_look_development"]
    assert failed and "may not edit spec/GATES.yaml (reverted)" in json.dumps(failed[0]["data"])


def test_editing_a_locked_spec_document_outside_the_workflow_blocks_until_restored(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    for _ in range(400):
        eng.step()
        if eng.state.controls["spec-story"].status == "approved":
            break
    story = proj / "spec" / "STORY.md"
    story.write_text(story.read_text().replace("runtime_s: {target: 18, max: 20}", "runtime_s: {target: 18, max: 40}"))
    st = eng.run(max_steps=5)
    assert st["status"] == "BLOCKED" and "spec/STORY.md changed outside the workflow" in json.dumps(st)
    eng.restore_control("spec-story", actor="Wei")
    assert "max: 20" in story.read_text()
    kept = list((proj / ".vpg" / "controls").glob("spec-story.discarded*.md"))
    assert kept and "max: 40" in kept[0].read_text()


def test_a_change_to_the_gates_is_a_bar_change(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    for _ in range(400):
        eng.step()
        if eng.state.controls["spec-gates"].status == "approved":
            break
    cr = eng.change_request("spec-gates", "loosen the ground envelope", by="Pat Example")
    with pytest.raises(EngineError, match="bar change"):
        eng.approve_change_request(cr.id, by="agent:runner", reason="ok")
    with pytest.raises(EngineError, match="bar changes"):
        eng.approve_change_request(cr.id, by="agent:runner", on_behalf_of="Wei", instruction="approve it",
                                   instruction_at="2026-10-26T10:00:00+00:00")


def test_a_run_cannot_switch_workflows(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True).step()
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["workflow"] = "default"
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    with pytest.raises(EngineError, match="cannot switch workflows"):
        Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    with pytest.raises(EngineError, match="cannot switch workflows"):
        Engine(proj, read_only=True)


def test_provisional_look_and_animatic_need_an_explicit_opt_in(tmp_path, measured_registry):
    base = {"humans": ["Wei"], "reviews": {"binding": "require", "independence": "require"},
            "loop_budget": {"on_exhaust": "extend_if_converging"}}
    for i, (extra, refused) in enumerate(((None, True), (["n26_look_development"], False))):
        proj = _project(tmp_path / str(i), measured_registry)
        cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
        cfg.update(base, autonomy={"mode": "provisional", "principal": "Wei",
                                   **({"provisional_gates_extra": extra} if extra else {})})
        (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"))
        node = eng.node("n26_look_development")
        if refused:
            with pytest.raises(EngineError, match="provisional_gates_extra"):
                eng._check_provisional_decision(node, "approved", "agent:runner", "Wei", "go")
        else:
            try:
                eng._check_provisional_decision(node, "approved", "agent:runner", "Wei", "go")
            except EngineError as exc:
                assert "allowed only at" not in str(exc)
    proj = _project(tmp_path / "bad", measured_registry)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update(base, autonomy={"mode": "provisional", "principal": "Wei", "provisional_gates_extra": ["n23_final_verification"]})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    with pytest.raises(EngineError, match="provisional_gates_extra lists only"):
        Engine(proj, executor=ScriptedExecutor(proj / "fixtures"))


# ------------------------------------------------------------------------------------------------ checks
def _ctx(proj, state=None, **artifacts):
    loaded = {k: json.loads(v.read_text()) if hasattr(v, "read_text") else v for k, v in artifacts.items()}
    return SimpleNamespace(project_dir=proj, control_path=lambda d: proj / rel_path(d), workdir=proj / "work" / "t",
                           state=state or SimpleNamespace(controls={}), load=lambda k: loaded.get(k),
                           fm=lambda d: (load_family(proj / "spec").fm(d.removeprefix("spec-"))))


def test_the_bar_check_needs_a_pinned_unchanged_registry_with_every_role(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    assert qualitygates.check_bar(_ctx(proj)).ok
    gates = proj / "spec" / "GATES.yaml"
    pinned = gates.read_text()
    gates.write_text(pinned.replace(pinned.split("benchmarks: ")[1].split("\n")[0], "../benchmarks/registry.json"))
    assert any("does not pin" in m for m in qualitygates.check_bar(_ctx(proj)).messages)
    gates.write_text(pinned)
    reg = proj / "benchmarks" / "registry.json"
    data = json.loads(reg.read_text())
    data["entries"][0]["style_frames"] = [sf for sf in data["entries"][0]["style_frames"] if sf["role"] != "closing"]
    reg.write_text(json.dumps(data, indent=1))
    msgs = qualitygates.check_bar(_ctx(proj)).messages
    assert any("changed since GATES.yaml pinned it" in m for m in msgs)
    gates.write_text(pinned.replace(pinned.split("sha256: ")[1].split("}")[0], __import__("vpg.util").util.sha256_file(reg)))
    msgs = qualitygates.check_bar(_ctx(proj)).messages
    assert any("REF-A has no style frame for closing" in m for m in msgs)


def test_the_bar_check_says_why_a_gate_has_no_envelope_and_what_fixes_it(tmp_path, measured_registry):
    from vpg.metrics import not_measured
    from vpg.util import sha256_file
    proj = _project(tmp_path, measured_registry)
    gates, reg = proj / "spec" / "GATES.yaml", proj / "benchmarks" / "registry.json"
    data = json.loads(reg.read_text())
    for e in data["entries"]:
        e["measurements"]["audio.pace_wpm"] = not_measured("audio.pace_wpm", "no narration text was given")
    reg.write_text(json.dumps(data, indent=1))
    text = gates.read_text().replace("envelope: {lo: 30, hi: 600}", "envelope: benchmark").replace(
        "  - {id: GT-PAUSE-001,", "  - {id: GT-UI-002, metric: text.ocr_word_px.dense_min, version: 1, envelope: benchmark, "
        "severity: P2, stage: build, rules: [DS-UI-001]}\n  - {id: GT-PAUSE-001,")
    gates.write_text(text.replace(text.split("sha256: ")[1].split("}")[0], sha256_file(reg)))
    msgs = qualitygates.check_bar(_ctx(proj)).messages
    ui = next(m for m in msgs if m.startswith("GT-UI-002: no benchmark envelope"))
    assert "REF-A: needs a master and --ocr" in ui and "`vpg benchmark measure --ocr`" in ui   # added without --ocr
    pace = next(m for m in msgs if m.startswith("GT-PACE-001: no benchmark envelope"))
    assert "no narration text was given" in pace and "--narration" in pace and "new registry" in pace


def test_the_bar_check_reports_a_registry_with_no_reference_and_an_unknown_metric_without_crashing(tmp_path,
                                                                                                   measured_registry):
    from vpg.util import sha256_file
    proj = _project(tmp_path, measured_registry)
    gates, reg = proj / "spec" / "GATES.yaml", proj / "benchmarks" / "registry.json"
    data = json.loads(reg.read_text())
    data["entries"] = [e for e in data["entries"] if e["kind"] != "reference"]    # only the accepted baseline
    reg.write_text(json.dumps(data, indent=1))
    text = gates.read_text().replace("metric: audio.pause_share,", "metric: audio.pause_shares,")   # a typo
    gates.write_text(text.replace(text.split("sha256: ")[1].split("}")[0], sha256_file(reg)))
    msgs = qualitygates.check_bar(_ctx(proj)).messages                     # no exception: every reason is kept
    assert any("unknown metric audio.pause_shares" in m for m in msgs)
    assert any(m.startswith("GT-PAUSE-001: no benchmark envelope for audio.pause_shares (not a known metric") for m in msgs)
    assert any(m.startswith("GT-VOM-001: no benchmark envelope") and "no reference film yet" in m for m in msgs)


def test_the_bar_check_judges_a_still_gate_on_its_own_roles_and_says_which_frame_lacks_text(tmp_path, measured_registry):
    from vpg.metrics import measured
    from vpg.util import sha256_file
    proj = _project(tmp_path, measured_registry)
    gates, reg = proj / "spec" / "GATES.yaml", proj / "benchmarks" / "registry.json"
    ui = "still.ocr_word_px.median"
    data = json.loads(reg.read_text())
    for e in data["entries"]:     # OCR read the product UI frames, but too little text on the others
        e["measurements"][ui] = measured(ui, 5.0, {"per_role": {"product_ui": 5.0}})
    reg.write_text(json.dumps(data, indent=1))
    text = gates.read_text().replace("  - {id: GT-PAUSE-001,", (
        "  - {id: GT-UI-003, metric: still.ocr_word_px.median, version: 1, envelope: benchmark, severity: P2, stage: look, "
        "rules: [DS-UI-001], roles: [product_ui]}\n"
        "  - {id: GT-UI-004, metric: still.ocr_word_px.median, version: 1, envelope: benchmark, severity: P2, stage: look, "
        "rules: [DS-UI-001]}\n  - {id: GT-PAUSE-001,"))
    gates.write_text(text.replace(text.split("sha256: ")[1].split("}")[0], sha256_file(reg)))
    msgs = qualitygates.check_bar(_ctx(proj)).messages
    assert not any(m.startswith("GT-UI-003") for m in msgs)                # its one role has an envelope
    every = next(m for m in msgs if m.startswith("GT-UI-004"))              # judged on every role, as before
    assert "on a title, data_callout, closing style frame" in every
    assert "REF-A: its closing frame gives no value (too little confidently read text)" in every
    assert "`roles:` in GATES.yaml" in every and "measure --ocr" not in every   # OCR ran: re-running it cannot help


def test_a_changed_approved_spec_document_must_bump_its_version(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    snap = proj / ".vpg" / "controls" / "spec-story@v1.md"
    snap.parent.mkdir(parents=True)
    shutil.copy2(proj / "spec" / "STORY.md", snap)
    state = SimpleNamespace(controls={"spec-story": SimpleNamespace(snapshot=".vpg/controls/spec-story@v1.md")})
    assert qualitygates.check_spec(_ctx(proj, state), docs=["story"]).ok
    story = proj / "spec" / "STORY.md"
    story.write_text(story.read_text().replace('"game-changing"', '"game-changing", "next-gen"'))
    res = qualitygates.check_spec(_ctx(proj, state), docs=["story"])
    assert not res.ok and any("bump it" in m for m in res.messages)
    story.write_text(story.read_text().replace("doc: story, version: 1", "doc: story, version: 2"))
    assert qualitygates.check_spec(_ctx(proj, state), docs=["story"]).ok


def test_story_beats_must_add_up_and_the_storyboard_must_honour_them(tmp_path, measured_registry):
    from vpg.spec import lint
    proj = _project(tmp_path, measured_registry)
    board = PKG / "examples/sample-demo/fixtures/n08_storyboard_shots/attempt-1/storyboard.json"
    assert qualitygates.check_beats(_ctx(proj, storyboard=board)).ok
    data = json.loads(board.read_text())
    data["beats"][0]["duration_s"] = 12.0
    msgs = qualitygates.check_beats(_ctx(proj, storyboard=data)).messages
    assert any("story beat problem gets" in m for m in msgs)
    data["beats"][0]["chapter"] = "ch9"
    assert any("chapter ch9 belongs to no STORY.md beat" in m for m in qualitygates.check_beats(_ctx(proj, storyboard=data)).messages)
    story = proj / "spec" / "STORY.md"
    story.write_text(story.read_text().replace("share: 0.34", "share: 0.54"))
    assert any("add up to 1.20" in i.message for i in lint(load_family(proj / "spec"), only=("story",)))


def test_spec_proposals_are_validated_and_listed_for_a_person_to_file(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    fam = load_family(proj / "spec")
    good = {"doc": "story", "change": "modify", "rule": "ST-TERMS-001", "proposal": "say 'own range' instead",
            "because": "F001"}
    assert qualitygates.proposal_problems([good], fam) == []
    bad = [{"doc": "story", "change": "retire", "rule": "ST-NOPE-001", "proposal": "x", "because": "y"},
           {"doc": "palette", "change": "rewrite", "proposal": "", "because": ""},
           {**good, "proposal": "something else"}]
    msgs = qualitygates.proposal_problems([good] + bad, fam)
    assert any("ST-NOPE-001, which the spec does not define" in m for m in msgs)
    assert any("doc is one of" in m for m in msgs) and any("change is add, modify or retire" in m for m in msgs)
    assert any("conflicts with an earlier proposal on ST-TERMS-001" in m for m in msgs)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    eng.step()
    path = proj / "work" / "interp.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"defects": [], "feedback": [], "spec_proposals": [good]}))
    from vpg.models import ArtifactRecord
    from vpg.util import now, sha256_file
    eng.state.artifacts["interp-1"] = ArtifactRecord(id="interp-1", key="interpretation", path="work/interp.json",
                                                     sha256=sha256_file(path), bytes=path.stat().st_size,
                                                     node="n21_feedback_interpretation", attempt=1, produced_at=now())
    lines = eng._spec_proposal_lines()
    assert len(lines) == 1 and "re-opens the animatic gate" in lines[0] and "--doc spec-story" in lines[0]
    argv = shlex.split(lines[0].split("`")[1])
    reason = argv[argv.index("--reason") + 1]
    assert reason == "modify ST-TERMS-001: say 'own range' instead (because F001)"
    eng.change_request("spec-story", reason, by="Wei")
    assert eng._spec_proposal_lines() == []


def test_a_spec_proposal_cannot_smuggle_a_command_into_the_suggested_one(tmp_path, measured_registry):
    """An agent writes the proposal; a person pastes the command. Nothing in the agent's text may run."""
    import subprocess
    from vpg.models import ArtifactRecord
    from vpg.util import now, sha256_file
    proj = _project(tmp_path, measured_registry)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    eng.step()
    evil = {"doc": "story", "change": "modify", "rule": "ST-TERMS-001", "because": "F001 \u2014 `touch PWNED2`",
            "proposal": "say \u2019own range\u2019 $(touch PWNED)\n and 'quoted'"}
    path = proj / "work" / "interp.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"defects": [], "feedback": [], "spec_proposals": [evil]}))
    eng.state.artifacts["interp-1"] = ArtifactRecord(id="interp-1", key="interpretation", path="work/interp.json",
                                                     sha256=sha256_file(path), bytes=path.stat().st_size,
                                                     node="n21_feedback_interpretation", attempt=1, produced_at=now())
    (line,) = eng._spec_proposal_lines()
    cmd = line.split("`vpg change-request ")[1].rsplit("`", 1)[0]
    argv = shlex.split(cmd)
    reason = argv[argv.index("--reason") + 1]
    assert "$(touch PWNED)" in reason and "\u2019own range\u2019" in reason and "\n" not in reason
    shown = subprocess.run(["bash", "-c", "printf %s " + cmd.split("--reason ", 1)[1].rsplit(" --actor", 1)[0]],
                           cwd=tmp_path, capture_output=True, text=True)
    assert shown.stdout == reason and not (tmp_path / "PWNED").exists() and not (tmp_path / "PWNED2").exists()
    eng.change_request("spec-story", reason, by="Wei")
    assert eng._spec_proposal_lines() == []


def test_the_quality_run_state_and_spec_hash_are_stable(tmp_path, measured_registry):
    """The look and animatic manifests carry the spec hash; nothing in the run rewrites spec/ before N26."""
    proj = _project(tmp_path, measured_registry)
    h = family_hash(load_family(proj / "spec"))
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    for _ in range(400):
        eng.step()
        if eng.rt("n26_look_development").status == NodeStatus.STEP_PASS:
            break
    assert family_hash(load_family(proj / "spec")) == h
    assert sorted(eng.state.controls) == sorted(CONTROL_DOCS + list(SPEC_CONTROL_DOCS))
    assert eng.state.project.workflow_id == quality.QUALITY_ID


# ------------------------------------------------------------------------------------------------ the gates' own evidence
def _gate_ctx(proj, node, key, **loads):
    """A completion-check context for N26 or N27, with the scripted attempt copied into its work folder."""
    work = proj / "work" / node / "a1"
    if not work.exists():
        shutil.copytree(proj / "fixtures" / node / "attempt-1", work)
    name = {"look_frames": "look-frames.json", "animatic": "animatic.json"}[key]
    return SimpleNamespace(project_dir=proj, workdir=work, control_path=lambda d: proj / rel_path(d),
                           artifact_path=lambda k: work / name if k == key else None, load=lambda k: loads.get(k),
                           state=SimpleNamespace(controls={}))


def _manifest_edit(path, fn):
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


def test_the_look_gate_judges_only_frames_rendered_for_this_attempt(tmp_path, measured_registry):
    from vpg.tools import look
    proj = _project(tmp_path, measured_registry)
    ctx = _gate_ctx(proj, "n26_look_development", "look_frames")
    assert look.check(ctx).ok, look.check(ctx).messages
    manifest = ctx.workdir / "look-frames.json"
    original = manifest.read_text()
    refs = proj / "benchmarks" / "REF-A"

    def point(how):
        def fn(d):
            for f in d["frames"]:
                f["file"] = how(f["role"])
                f.pop("sha256", None)
        return fn
    _manifest_edit(manifest, point(lambda r: f"../../../benchmarks/REF-A/{r}.png"))   # the reviewer's escape
    msgs = look.check(ctx).messages
    assert sum("outside this attempt's work folder" in m for m in msgs) == 4
    manifest.write_text(original)
    _manifest_edit(manifest, point(lambda r: str(refs / f"{r}.png")))                  # an absolute path
    assert sum("outside this attempt's work folder" in m for m in look.check(ctx).messages) == 4
    manifest.write_text(original)
    for r in qf.ROLES:                                                                  # a byte copy inside the folder
        shutil.copy2(refs / f"{r}.png", ctx.workdir / f"copied-{r}.png")
    _manifest_edit(manifest, point(lambda r: f"copied-{r}.png"))
    msgs = look.check(ctx).messages
    assert sum("is a copy of the benchmark file REF-A." in m for m in msgs) == 4
    from PIL import Image
    for r in qf.ROLES:                                                                  # re-saved: new bytes, same picture
        with Image.open(refs / f"{r}.png") as im:
            im.save(ctx.workdir / f"copied-{r}.png", optimize=True, compress_level=1)
        assert (ctx.workdir / f"copied-{r}.png").read_bytes() != (refs / f"{r}.png").read_bytes()
    assert sum("is a copy of the benchmark file REF-A." in m for m in look.check(ctx).messages) == 4
    from vpg.benchmarks import load
    with Image.open(refs / "title.png") as im:                                          # same ground, another title
        im = im.convert("RGB")
        im.paste((40, 44, 50), (120, 160, 1100, 220))
        im.paste((225, 228, 232), (700, 400, 1150, 436))
        im.save(ctx.workdir / "new-title.png")
    reg = load(proj / "benchmarks" / "registry.json")
    assert look.copies({"style frame title": ctx.workdir / "new-title.png"}, reg, proj / "benchmarks") == []
    manifest.write_text(original)
    outside = proj / "benchmarks" / "look-frames.json"                                  # the manifest itself elsewhere
    shutil.copy2(manifest, outside)
    ctx2 = SimpleNamespace(**{**vars(ctx), "artifact_path": lambda k: outside})
    assert look.check(ctx2).messages == ["the look_frames manifest must be in this attempt's work folder (a1), not look-frames.json"]


def test_the_animatic_measures_its_runtime_and_hit_points(tmp_path, measured_registry):
    from vpg.tools import animatic
    proj = _project(tmp_path, measured_registry)
    script = json.loads((PKG / "examples/sample-demo/fixtures/n07_script_development/attempt-2/script.json").read_text())
    board = json.loads((PKG / "examples/sample-demo/fixtures/n08_storyboard_shots/attempt-1/storyboard.json").read_text())
    ctx = _gate_ctx(proj, "n27_animatic", "animatic", script=script, storyboard=board)
    res = animatic.check(ctx)
    assert res.ok, res.messages
    report = json.loads((ctx.workdir / "animatic-report.json").read_text())
    assert report["hits"][0]["status"] == "pass" and abs(report["hits"][0]["landed"] - 11.0) <= 0.15
    assert report["runtime_s"] == pytest.approx(qf.cf.SECONDS, abs=0.05) and report["loudness"]["limiting_db"] <= 6
    manifest = ctx.workdir / "animatic.json"
    original = manifest.read_text()
    _manifest_edit(manifest, lambda d: [b.update(start=b["start"] / 2.03, end=b["end"] / 2.03) for b in d["beats"]])
    msgs = animatic.check(ctx).messages   # the reviewer's 5.9 s claim against a measured 12 s read
    assert any("the beats end at 5.91 s, but the measured scratch read runs 12.00 s" in m for m in msgs), msgs
    manifest.write_text(original)
    qf.cf.music(ctx.workdir / "music.wav", gain_db=-28.5)                 # the same pad with no hit in it
    assert any("hit point at 11.00 s" in m and "not landed" in m for m in animatic.check(ctx).messages)
    _manifest_edit(manifest, lambda d: d.pop("music"))
    assert any("hit point at 11.00 s" in m and "no music sketch" in m for m in animatic.check(ctx).messages)
    manifest.write_text(original)
    _manifest_edit(manifest, lambda d: d.update(voice="../../../benchmarks/REF-A/voice.wav"))
    assert any("outside this attempt's work folder" in m for m in animatic.check(ctx).messages)
    manifest.write_text(original)
    shutil.copy2(proj / "benchmarks" / "BEST-1" / "music.wav", ctx.workdir / "music.wav")   # the accepted version's bed
    assert not any("copy of the benchmark" in m for m in animatic.check(ctx).messages)
    shutil.copy2(proj / "benchmarks" / "REF-A" / "voice.wav", ctx.workdir / "scratch.wav")
    assert any("the voice track is a copy of the benchmark file REF-A.voice" in m for m in animatic.check(ctx).messages)


def test_the_loudness_pre_check(tmp_path):
    import wave
    import numpy as np
    from vpg.tools.animatic import loudness_precheck
    v = qf.cf.voice(tmp_path / "v.wav", seed=5)
    m = qf.music_with_hits(tmp_path / "m.wav", hits=[11.0])
    mix = {"loudness": {"integrated_lufs": -16.0, "true_peak_max_dbtp": -1.0}}
    msgs, info = loudness_precheck(v, m, mix, tmp_path)
    assert msgs == [] and info["limiting_db"] <= 6.0 and info["true_peak_dbtp"] < 0
    peaky = qf.music_with_hits(tmp_path / "peaky.wav", hits=[11.0], hit_db=-12.0)   # a sting: needs some limiting
    msgs, info = loudness_precheck(v, peaky, mix, tmp_path)
    assert msgs == [] and 0 < info["limiting_db"] <= 6.0 and info["true_peak_dbtp"] < 0
    msgs, _ = loudness_precheck(v, peaky, {"loudness": {**mix["loudness"], "precheck_max_limiting_db": 1.0}}, tmp_path)
    assert any("more limiting than the pre-check allows (1.0 dB)" in x for x in msgs)
    with wave.open(str(v)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    loud = qf.cf._write_wav(tmp_path / "loud.wav", np.clip(x * 12.0, -1, 1))
    assert any("clips" in x for x in loudness_precheck(loud, None, mix, tmp_path)[0])
    assert "cannot run" in loudness_precheck(v, m, {}, tmp_path)[0][0]
    rate, n = 48000, 48000 * 12
    tone = 0.9 * np.sin(2 * np.pi * 100 * np.arange(n) / rate).astype(np.float32)
    a_, b_ = qf.cf._write_wav(tmp_path / "a.wav", tone), qf.cf._write_wav(tmp_path / "b.wav", tone)
    msgs, info = loudness_precheck(a_, b_, mix, tmp_path)                 # two in-phase stems sum to +5 dBFS
    assert info["true_peak_dbtp"] > 4.0 and any("clips" in x for x in msgs)
    import subprocess
    stereo = tmp_path / "v-stereo.wav"                                    # the same read as dual-mono stereo
    subprocess.run([qf.cf.FFMPEG, "-v", "error", "-y", "-i", str(v), "-af", "pan=stereo|c0=c0|c1=c0", str(stereo)], check=True)
    mono_i = loudness_precheck(v, None, mix, tmp_path)[1]["integrated_lufs"]
    assert abs(mono_i - loudness_precheck(stereo, None, mix, tmp_path)[1]["integrated_lufs"]) <= 0.2
    long_bed = qf.cf.music(tmp_path / "long.wav", gain_db=-28.5, seconds=60.0)   # the tail after the read is not metered
    short_bed = qf.cf.music(tmp_path / "short.wav", gain_db=-28.5)
    assert abs(loudness_precheck(v, long_bed, mix, tmp_path)[1]["limiting_db"]
               - loudness_precheck(v, short_bed, mix, tmp_path)[1]["limiting_db"]) <= 0.3


def test_a_hit_point_lands_on_an_accent_not_on_a_steady_pulse(tmp_path):
    import numpy as np
    from vpg.tools.animatic import hit_points, onsets
    rate, n = 48000, int(48000 * qf.cf.SECONDS)
    pad = qf.cf.music(tmp_path / "pad.wav", gain_db=-28.5)
    import wave
    with wave.open(str(pad)) as w:
        base = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    rng = np.random.default_rng(3)

    def burst(x, at, db, ms=60):
        k = int(ms / 1000 * rate)
        a = int(at * rate)
        x[a:a + k] += (rng.standard_normal(k) * np.exp(-np.arange(k) / (0.015 * rate)) * 10 ** (db / 20))[:max(0, len(x) - a)]
    pulse = base.copy()
    for i in range(int(qf.cf.SECONDS / 0.28)):
        burst(pulse, 0.1 + i * 0.28, -20)
    spec = {"hit_points": [{"at_s": s, "what": "x"} for s in (3.33, 7.77, 11.0)]}
    on = onsets(qf.cf._write_wav(tmp_path / "pulse.wav", pulse))
    assert len(on) > 30 and all(h["status"] == "fail" for h in hit_points(spec, on, 12.0))
    sting = pulse.copy()
    burst(sting, 11.0, -6, ms=120)
    rows = hit_points(spec, onsets(qf.cf._write_wav(tmp_path / "sting.wav", sting)), 12.0)
    assert [h["status"] for h in rows] == ["fail", "fail", "pass"]
    opener = base.copy() * 0.05
    burst(opener, 0.0, -6, ms=120)                                        # a hit on the very first frame
    rows = hit_points({"hit_points": [{"at_s": 0.0, "what": "open"}]}, onsets(qf.cf._write_wav(tmp_path / "open.wav", opener)), 12.0)
    assert rows[0]["status"] == "pass"

    def lands(name, x, hits):
        on = onsets(qf.cf._write_wav(tmp_path / f"{name}.wav", x))
        return [h["status"] for h in hit_points({"hit_points": [{"at_s": s, "what": name} for s in hits]}, on, 12.0)]
    for second in (-12, -8):                                              # two designed hits 0.4 s apart each land
        two = base.copy()
        burst(two, 11.0, -12, ms=120)
        burst(two, 11.4, second, ms=120)
        assert lands(f"two{second}", two, [11.0, 11.4]) == ["pass", "pass"]
    early = base.copy()                                                   # the music coming in is not an accent
    burst(early, 0.3, -12, ms=120)
    assert lands("early", early, [0.3]) == ["pass"]
    late = base.copy()
    late[:int(10.7 * rate)] = 0
    burst(late, 11.0, -12, ms=120)
    assert lands("late", late, [11.0]) == ["pass"]


def test_mix_loudness_must_match_the_approved_bar(tmp_path, measured_registry):
    proj = _project(tmp_path, measured_registry)
    ctx = _ctx(proj)
    assert qualitygates.check_spec(ctx, docs=["mix"]).ok
    mix = proj / "spec" / "MIX.md"
    mix.write_text(mix.read_text().replace("integrated_lufs: -16.0", "integrated_lufs: -14.0"))
    msgs = qualitygates.check_spec(ctx, docs=["mix"]).messages
    assert any("loudness.integrated_lufs is -14.0, but the approved bar" in m and "-16" in m for m in msgs)


def test_quality_packets_name_the_spec_files_where_they_are(tmp_path, measured_registry):
    from vpg.executors import prompt_values
    proj = _project(tmp_path, measured_registry)
    eng = Engine(proj, executor=ScriptedExecutor(proj / "fixtures"), auto_approve=True)
    for nid, doc in (("n03_objective_audience", "spec/GATES.yaml"), ("n26_look_development", "spec/DESIGN.tokens.json"),
                     ("n27_animatic", "spec/MIX.md")):
        text = prompt_values(eng._context(eng.node(nid), 1, "fp", "token"))["inputs"]
        assert doc in text and "controls/spec-" not in text, (nid, text)
