from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
import yaml
from PIL import Image

from tests.conftest import FnExecutor, PKG, mini_workflow
from tests.test_release_record import _human_gated_project
from tests.test_tools import Ctx
from vpg.engine import Engine
from vpg.executors import TaskPacketExecutor
from vpg import cli
from vpg.models import ArtifactRecord, ChangeRequest, Feedback
from vpg.memory import RuleBase
from vpg.tools import audio, layout, regress, review_prep, verify

FFMPEG = shutil.which("ffmpeg")


def _artifact(key: str, path: str, node: str = "n13_motion_scenes", version: int = 1) -> ArtifactRecord:
    return ArtifactRecord(id=f"a-{key}", key=key, path=path, sha256="0" * 64, bytes=1, node=node, attempt=1,
                          produced_at="2026-10-02T00:00:00", version=version)


class HarnessCtx(Ctx):
    def __init__(self, tmp_path: Path, *, loads=None, paths=None, config=None, artifacts=None, feedback=None, changes=None, fm=None):
        super().__init__(tmp_path, loads=loads, paths=paths, fm=fm)
        self.project_dir = tmp_path
        self.pkg_root = PKG
        self.config = config or {}
        self.state = NS(
            runtime=NS(current_version=1),
            artifacts={a.id: a for a in artifacts or []},
            evaluation=NS(feedback=feedback or [], defects=[], reviews=[], gate_results=[]),
            history=NS(change_requests=changes or [], approvals=[], versions=[]),
            production=NS(),
        )

    def current(self, key: str):
        return sorted([a for a in self.state.artifacts.values() if a.key == key and a.status == "current"], key=lambda a: a.id)

    def artifact_path(self, key: str):
        if key in self._paths:
            return self._paths[key]
        recs = self.current(key)
        return self.project_dir / recs[-1].path if recs else None


def _inventory(chapters, samples, fps=2.0):
    return {"film": "synthetic.mp4", "fps": fps, "chapters": chapters, "samples": samples}


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _regression_ctx(tmp_path: Path, baseline: dict, candidate: dict, departures: dict | None = None, baseline_layout: dict | None = None,
                    candidate_layout: dict | None = None, **cfg):
    base = _write_json(tmp_path / "baseline-inventory.json", baseline)
    cand = _write_json(tmp_path / "candidate-inventory.json", candidate)
    deps = _write_json(tmp_path / "departures.json", departures or {"removals": []})
    baseline_cfg = {"label": "released", "inventory": str(base)}
    if baseline_layout is not None:
        baseline_cfg["layout_audit"] = str(_write_json(tmp_path / "baseline-layout.json", baseline_layout))
    config = {"regression": {"baseline": baseline_cfg, "departures": str(deps),
                             "demo_chapters": cfg.pop("demo_chapters", []), **cfg}}
    loads = {"inventory": candidate}
    if candidate_layout is not None:
        loads["layout_audit"] = candidate_layout
    return HarnessCtx(tmp_path, loads=loads, config=config,
                      artifacts=[_artifact("inventory", cand.relative_to(tmp_path).as_posix())],
                      feedback=[Feedback(id="F012", text="keep only signal", actor="Wei", created_at="2026-10-02T00:00:00")],
                      changes=[ChangeRequest(id="CR-004", doc="evaluation", requested_by="Wei", reason="threshold",
                                             at="2026-10-02T00:00:00", status="approved", approved_by="Wei")])


def _demo_samples(n, *, cap_prefix="frame", pointer=True, zoom=1.0):
    return [[i / 2, {"texts": ["DEMO"], "cap": f"{cap_prefix}{i}", "zoom": zoom, "pointer": pointer, "ring": False}]
            for i in range(n)]


def _finding_codes(result):
    return [f.check.rsplit(":", 1)[-1] for f in result.findings]


def test_regression_baseline_detects_removals_departures_scope_and_content_loss(tmp_path):
    chapters = [["c1", 0, 4]]
    baseline = _inventory(chapters, [[0, {"texts": ["WATER DATA", "SIGNAL 3"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    candidate = _inventory(chapters, [[0, {"texts": ["WATER DATA"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    ctx = _regression_ctx(tmp_path, baseline, candidate, {"removals": [{"chapter": "c1", "match": "SIGNAL", "why": "requested", "cites": ["F012"]}]})
    assert not regress.check(ctx).findings

    undeclared = _regression_ctx(tmp_path / "u", baseline, candidate, {"removals": []})
    codes = _finding_codes(regress.check(undeclared))
    assert {"UNDECLARED_REMOVAL", "CONTENT_LOSS"} <= set(codes)

    untraced = _regression_ctx(tmp_path / "t", baseline, candidate, {"removals": [{"chapter": "c1", "match": "SIGNAL", "why": "silent", "cites": []}]})
    assert "UNTRACED_DEPARTURE" in _finding_codes(regress.check(untraced))


def test_departures_are_safe_and_absorb_texts(tmp_path):
    chapters = [["c1", 0, 4]]
    baseline = _inventory(chapters, [[0, {"texts": ["WATER DATA", "SIGNAL 3"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    candidate = _inventory(chapters, [[0, {"texts": ["WATER DATA"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    invalid = _regression_ctx(tmp_path / "bad", baseline, candidate, {"removals": [{"match": "[", "regex": True, "why": "x", "cites": ["F012"]}]})
    assert "BAD_DEPARTURE" in _finding_codes(regress.check(invalid))
    broad = _regression_ctx(tmp_path / "broad", baseline, candidate, {"removals": [{"match": ".", "why": "x", "cites": ["F012"]}]})
    assert "BAD_DEPARTURE" in _finding_codes(regress.check(broad))
    ok = _regression_ctx(tmp_path / "ok", baseline, candidate, {"removals": [{"match": "SIGNAL", "why": "scoped", "cites": ["F012"]}]})
    res = regress.check(ok)
    assert not res.findings
    dep = json.loads((tmp_path / "ok/work/regression-report.json").read_text())["declared_departures"][0]
    assert dep["absorbed"] == ["SIGNAL 3"]


@pytest.mark.parametrize("departure", [
    {"match": "a", "why": "too short", "cites": ["F012"]},
    {"match": "^(?!probe).+", "regex": True, "why": "regex unsupported", "cites": ["F012"]},
    {"match": r"([a-z]+\\s?)+:", "regex": True, "why": "regex unsupported", "cites": ["F012"]},
    {"match": r"((a+))+$", "regex": True, "why": "regex unsupported", "cites": ["F012"]},
    {"match": r"(a{1,9})+$", "regex": True, "why": "regex unsupported", "cites": ["F012"]},
    {"match": r"\\w*\\w*\\w*\\w*\\w*:", "regex": True, "why": "regex unsupported", "cites": ["F012"]},
    {"match": r"(a|aa)+$", "regex": True, "why": "regex unsupported", "cites": ["F012"]},
])
def test_departure_rejects_short_literals_and_unsafe_regex(tmp_path, departure):
    chapters = [["c1", 0, 4]]
    baseline = _inventory(chapters, [[0, {"texts": ["ALPHA", "BRAVO"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    candidate = _inventory(chapters, [[0, {"texts": [], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    found = _finding_codes(regress.check(_regression_ctx(tmp_path, baseline, candidate, {"removals": [departure]})))
    assert "BAD_DEPARTURE" in found


def test_overbroad_departure_stays_undeclared(tmp_path):
    chapters = [["c1", 0, 4]]
    baseline = _inventory(chapters, [[0, {"texts": ["SIGNAL A", "SIGNAL B", "SIGNAL C", "OTHER"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    candidate = _inventory(chapters, [[0, {"texts": [], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    found = _finding_codes(regress.check(_regression_ctx(tmp_path, baseline, candidate, {"removals": [
        {"match": "SIGNAL", "why": "too broad", "cites": ["F012"]}]})))
    assert "OVERBROAD_DEPARTURE" in found and "UNDECLARED_REMOVAL" in found


def test_departure_breadth_is_chapter_total_and_exact_match_exempt(tmp_path):
    chapters = [["c1", 0, 4], ["c2", 4, 8]]
    baseline = _inventory(chapters, [
        [0, {"texts": ["ALPHA SIGNAL", "BRAVO SIGNAL", "CHARLIE MAPPING", "DELTA MAPPING"], "cap": None, "zoom": None, "pointer": False, "ring": False}],
        [5, {"texts": ["EXACT TEXT"], "cap": None, "zoom": None, "pointer": False, "ring": False}],
    ])
    candidate = _inventory(chapters, [[0, {"texts": [], "cap": None, "zoom": None, "pointer": False, "ring": False}],
                                      [5, {"texts": [], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    found = _finding_codes(regress.check(_regression_ctx(tmp_path / "broad", baseline, candidate, {"removals": [
        {"chapter": "c1", "match": "SIGNAL", "why": "one half", "cites": ["F012"]},
        {"chapter": "c1", "match": "MAPPING", "why": "other half", "cites": ["F012"]},
        {"chapter": "c2", "match": "EXACT TEXT", "why": "exact", "cites": ["F012"]},
    ]})))
    assert "OVERBROAD_DEPARTURE" in found and "UNDECLARED_REMOVAL" in found
    exact_baseline = _inventory([["c2", 4, 8]], [[5, {"texts": ["EXACT TEXT"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    exact_candidate = _inventory([["c2", 4, 8]], [[5, {"texts": [], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    exact_only = _finding_codes(regress.check(_regression_ctx(tmp_path / "exact", exact_baseline, exact_candidate, {"removals": [
        {"chapter": "c2", "match": "EXACT TEXT", "why": "exact", "cites": ["F012"]},
    ]})))
    assert "OVERBROAD_DEPARTURE" not in exact_only and not any(c == "UNDECLARED_REMOVAL" for c in exact_only)


@pytest.mark.parametrize("candidate_samples, expected", [
    (_demo_samples(3), "cast frames"),
    (_demo_samples(10, pointer=False), "pointer"),
    (_demo_samples(10, zoom=1.4), "mean zoom"),
    ([[i / 2, {"texts": ["DEMO"], "cap": "same", "zoom": 1.0, "pointer": True, "ring": False}] for i in range(10)], "static hold"),
])
def test_regression_baseline_detects_each_demo_metric(tmp_path, candidate_samples, expected):
    chapters = [["demo", 0, 5]]
    baseline = _inventory(chapters, _demo_samples(10), fps=2.0)
    candidate = _inventory(chapters, candidate_samples, fps=2.0)
    ctx = _regression_ctx(tmp_path, baseline, candidate, demo_chapters=["demo"])
    messages = " ".join(f.message for f in regress.check(ctx).findings)
    assert expected in messages


def test_regression_not_applicable_without_block_and_fails_when_inventory_missing(tmp_path):
    none = regress.check(HarnessCtx(tmp_path, config={}))
    assert not none.findings and "no released baseline" in (tmp_path / "work/regression-report.json").read_text()
    configured = HarnessCtx(tmp_path / "missing", config={"regression": {"baseline": {"label": "released", "inventory": "missing.json"}}})
    found = regress.check(configured).findings
    assert [f.dimension for f in found] == ["REG-BASELINE"] and "candidate inventory" in found[0].message


def test_regression_is_safety_only_only_when_added_mid_run(tmp_path):
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path)
    cfg = {"id": "t", "name": "t", "workflow": str(wf), "executor": "task", "humans": ["Wei"]}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=1)
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "regression": {"baseline": {"label": "released", "inventory": "inv.json"}}}))
    assert Engine(proj)._safety_only_config_change()
    Engine(proj).accept_config("Wei", "add baseline safety check")
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "regression": {"baseline": {"label": "released", "inventory": "other.json"}}}))
    assert not Engine(proj)._safety_only_config_change()


def test_init_from_rebases_regression_paths_and_drops_departures(tmp_path):
    src = tmp_path / "src"
    cli.cmd_init(NS(project=src, id="src", name="src", force=False, from_project=None))
    cfg = yaml.safe_load((src / "vpg.project.yaml").read_text())
    cfg["regression"] = {"baseline": {"label": "released", "inventory": "evidence/inv.json", "master": "media/master.mp4",
                                      "layout_audit": "evidence/layout.json"}, "departures": "departures.json",
                         "demo_chapters": ["demo"]}
    (src / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    dest = tmp_path / "dest"
    cli.cmd_init(NS(project=dest, id="dest", name="dest", force=False, from_project=str(src)))
    reg = yaml.safe_load((dest / "vpg.project.yaml").read_text())["regression"]
    assert "departures" not in reg
    assert reg["baseline"]["inventory"] == str((src / "evidence/inv.json").resolve())
    assert reg["baseline"]["master"] == str((src / "media/master.mp4").resolve())
    assert reg["baseline"]["layout_audit"] == str((src / "evidence/layout.json").resolve())


def test_baseline_swap_plus_other_safety_key_is_not_safety_only(tmp_path):
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    inv = _write_json(proj / "base.json", {"film": "a", "fps": 1, "chapters": [], "samples": []})
    cfg = {"id": "t", "name": "t", "workflow": str(mini_workflow(tmp_path)), "executor": "task", "humans": ["Wei"],
           "regression": {"baseline": {"label": "released", "inventory": str(inv)}}}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=1)
    inv.write_text(json.dumps({"film": "swapped", "fps": 1, "chapters": [], "samples": []}))
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({**cfg, "reviews": {"binding": "require"}}))
    assert not Engine(proj)._safety_only_config_change()


def _n12_workflow(tmp_path):
    wf = yaml.safe_load(mini_workflow(tmp_path).read_text())
    for n in wf["nodes"]:
        if n["id"] == "b_plan":
            n["id"] = "n12_voice_audio"
            n["outputs"] = [
                {"key": "voice_direction", "kind": "json", "schema": "voice-direction", "description": "dir"},
                {"key": "voice", "kind": "audio", "description": "voice"},
                {"key": "timings", "kind": "json", "description": "timings"},
                {"key": "voice_seams", "kind": "json", "description": "seams", "required": False},
            ]
            n["completion"] = []
            n["rubric"] = ["voice-audio"]
            n["state_write"] = ["production.voice_ref", "production.timings_ref"]
    for e in wf["edges"]:
        if e["from"] == "b_plan":
            e["from"] = "n12_voice_audio"
        if e["to"] == "b_plan":
            e["to"] = "n12_voice_audio"
    wf["subgraphs"][0]["nodes"] = ["n12_voice_audio" if n == "b_plan" else n for n in wf["subgraphs"][0]["nodes"]]
    path = tmp_path / "n12.yaml"
    path.write_text(yaml.safe_dump(wf, sort_keys=False))
    return path


def _n12_project(tmp_path, rules_path=None):
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({"id": "t", "name": "t", "workflow": str(_n12_workflow(tmp_path)), "executor": "task"}))

    def n12(ctx):
        wav = _write_wav(ctx.workdir / "voice.wav", [(0, 1.0, 0.2)])
        (ctx.workdir / "timings.json").write_text(json.dumps({"units": [{"unit": "u1", "text": "water data", "start": 0, "end": 1.0}]}))
        (ctx.workdir / "voice_direction.json").write_text(json.dumps({"age": "adult", "delivery": "professional", "human_listening": "pending"}))
        return {"voice_direction": "voice_direction.json", "voice": "voice.wav", "timings": "timings.json"}
    return proj, Engine(proj, executor=FnExecutor({"n12_voice_audio": n12}), rules_path=rules_path)


def _submit_waiting(eng: Engine, node_id: str, outputs: dict[str, str], actor="agent:test"):
    rt = eng.rt(node_id)
    assert rt.status.value == "WAITING_RUNTIME", (node_id, rt.status, rt.failures)
    return eng.submit(node_id, outputs, token=rt.dispatch_token, actor=actor)


def _task_n12_project(tmp_path, rules_path=None):
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({"id": "t", "name": "t", "workflow": str(_n12_workflow(tmp_path)),
                                                           "executor": "task"}))
    return proj, Engine(proj, rules_path=rules_path)


def _submit_n12_voice(eng: Engine, proj: Path, loud=True):
    sub = proj / "submit"
    sub.mkdir(exist_ok=True)
    _write_wav(sub / "voice.wav", [(0, 1.0, 0.2 if loud else 0.03)])
    (sub / "timings.json").write_text(json.dumps({"units": [{"unit": "u1", "text": "water data", "start": 0, "end": 1.0}]}))
    (sub / "vd.json").write_text(json.dumps({"age": "adult", "delivery": "professional", "human_listening": "pending"}))
    return _submit_waiting(eng, "n12_voice_audio", {"voice_direction": str(sub / "vd.json"), "voice": str(sub / "voice.wav"),
                                                    "timings": str(sub / "timings.json")})


def test_n12_seams_are_shadow_until_rule_adopted(tmp_path):
    proj, eng = _n12_project(tmp_path)
    eng.run(max_steps=20)
    assert eng.rt("n12_voice_audio").status.value == "STEP_PASS"
    assert eng.current("voice_seams")
    rules = tmp_path / "rules.json"
    shutil.copy2(PKG / "knowledge/rules.json", rules)
    rb = RuleBase(rules)
    rb.transition("R-VOI-004", "approved", by="Wei", evidence=["review"], human=True)
    rb.transition("R-VOI-004", "adopted", by="Wei", evidence=["review"], human=True, adopted_in="rubrics/voice-audio.yaml")
    _proj2, eng2 = _n12_project(tmp_path / "adopted", rules_path=rules)
    eng2.run(max_steps=20)
    assert eng2.rt("n12_voice_audio").status.value in {"ITERATE", "BLOCKED"}
    assert any("VOI-SEAMS" in f for f in eng2.rt("n12_voice_audio").failures)


def test_n12_rerun_audits_submitted_attempt_files(tmp_path):
    for adopted in (False, True):
        rules = tmp_path / ("rules-adopted.json" if adopted else "rules-validated.json")
        shutil.copy2(PKG / "knowledge/rules.json", rules)
        if adopted:
            rb = RuleBase(rules)
            rb.transition("R-VOI-004", "approved", by="Wei", evidence=["review"], human=True)
            rb.transition("R-VOI-004", "adopted", by="Wei", evidence=["review"], human=True, adopted_in="rubrics/voice-audio.yaml")
        proj, eng = _task_n12_project(tmp_path / ("adopted" if adopted else "validated"), rules_path=rules)
        eng.run(max_steps=20)
        _submit_waiting(eng, "a_intake", {"a_intake": str(_write_json(proj / "submit-a.json", {}))})
        eng.run(max_steps=20)
        sub = proj / "submit1"; sub.mkdir(exist_ok=True)
        _write_wav(sub / "voice.wav", [(0.08, 0.60, 0.03)])
        (sub / "timings.json").write_text(json.dumps({"units": [{"unit": "clean-attempt", "text": "water data", "start": 0, "end": 1.0}]}))
        (sub / "vd.json").write_text(json.dumps({"age": "adult", "delivery": "professional", "human_listening": "pending"}))
        _submit_waiting(eng, "n12_voice_audio", {"voice_direction": str(sub / "vd.json"), "voice": str(sub / "voice.wav"),
                                                 "timings": str(sub / "timings.json")})
        assert eng.rt("n12_voice_audio").status.value == "STEP_PASS"
        eng.invalidate(["n12_voice_audio"], reason="retry voice")
        eng.rt("n12_voice_audio").force_rerun = True
        eng.run(max_steps=20)
        sub2 = proj / "submit2"; sub2.mkdir(exist_ok=True)
        _write_wav(sub2 / "voice.wav", [(0, 1.0, 0.2)])
        (sub2 / "timings.json").write_text(json.dumps({"units": [{"unit": "hardcut-attempt", "text": "water data", "start": 0, "end": 1.0}]}))
        (sub2 / "vd.json").write_text(json.dumps({"age": "adult", "delivery": "professional", "human_listening": "pending"}))
        _submit_waiting(eng, "n12_voice_audio", {"voice_direction": str(sub2 / "vd.json"), "voice": str(sub2 / "voice.wav"),
                                                 "timings": str(sub2 / "timings.json")})
        if adopted:
            assert eng.rt("n12_voice_audio").status.value in {"ITERATE", "BLOCKED"}
        else:
            assert eng.rt("n12_voice_audio").status.value == "STEP_PASS"
        report_path = (proj / "work/n12_voice_audio/a2/voice-seams-report.json") if adopted else (proj / eng.current("voice_seams")[-1].path)
        report = json.loads(report_path.read_text())
        assert [u["unit"] for u in report["units"]] == ["hardcut-attempt"]
        assert report["units"][0]["flags"]


def test_n12_submit_path_registers_voice_seams_and_allows_voice_review(tmp_path):
    proj, eng = _task_n12_project(tmp_path)
    eng.run(max_steps=20)
    _submit_waiting(eng, "a_intake", {"a_intake": str(_write_json(proj / "submit-a.json", {}))})
    eng.run(max_steps=20)
    _submit_n12_voice(eng, proj, loud=True)
    assert eng.rt("n12_voice_audio").status.value == "STEP_PASS"
    assert eng.current("voice_seams")
    node = eng.node("r1_review")
    node.rubric = ["creative"]
    review = {"reviewer": "critic", "scores": [{"dimension": "VOI-PRONUNCIATION", "score": 5, "evidence": "clear"}],
              "defects": []}
    assert not [p for p in eng._review_bind_problems(node, {}, "agent:critic", review=review) if "VOI-SEAMS" in p]


def test_n23_digest_lists_shadow_seams_and_cap_script(tmp_path):
    proj, eng = _task_n12_project(tmp_path)
    eng.run(max_steps=20)
    _submit_waiting(eng, "a_intake", {"a_intake": str(_write_json(proj / "submit-a.json", {}))})
    eng.run(max_steps=20)
    _submit_n12_voice(eng, proj, loud=True)
    acc_dir = proj / "work/accessibility"
    acc_dir.mkdir(parents=True)
    acc = acc_dir / "accessibility-report.json"
    acc.write_text(json.dumps({"caption_script": {"missing": ["each"], "extra": []}, "findings": []}))
    eng.state.artifacts["accessibility_report@test"] = ArtifactRecord(
        id="accessibility_report@test", key="accessibility_report", path=str(acc.relative_to(proj)), sha256="0" * 64,
        bytes=acc.stat().st_size, node="n18_accessibility_legibility", attempt=1, produced_at="now",
        version=eng.state.runtime.current_version)
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    verify.final(ctx)
    digest = (ctx.workdir / "decision-digest.md").read_text()
    assert "voice_seams" in digest and "u1" in digest and "1.00s" in digest
    assert "CAP-SCRIPT" in digest and "each" in digest


def test_n20_task_packet_lists_seam_report_and_flagged_unit(tmp_path):
    proj, eng = _task_n12_project(tmp_path)
    eng.run(max_steps=20)
    _submit_waiting(eng, "a_intake", {"a_intake": str(_write_json(proj / "submit-a.json", {}))})
    eng.run(max_steps=20)
    _submit_n12_voice(eng, proj, loud=True)
    review_node = next(n for n in eng.wf.nodes if n.kind.value == "review")
    ctx = eng._context(eng.node("n20_narrative_pacing") if "n20_narrative_pacing" in eng.index.ids else review_node, 1, "fp", "tok")
    ctx.state.runtime.nodes["n20_narrative_pacing"] = ctx.state.runtime.nodes[review_node.id]
    ctx.node.id = "n20_narrative_pacing"
    res = TaskPacketExecutor().execute(ctx)
    packet = (proj / res.task_packet).read_text()
    assert "voice_seams" in packet and "listen at 1.00s" in packet and "u1" in packet


def test_n12_submit_path_blocks_hard_cut_after_seam_rule_adoption(tmp_path):
    rules = tmp_path / "rules.json"
    shutil.copy2(PKG / "knowledge/rules.json", rules)
    rb = RuleBase(rules)
    rb.transition("R-VOI-004", "approved", by="Wei", evidence=["review"], human=True)
    rb.transition("R-VOI-004", "adopted", by="Wei", evidence=["review"], human=True, adopted_in="rubrics/voice-audio.yaml")
    proj, eng = _task_n12_project(tmp_path / "adopted", rules_path=rules)
    eng.run(max_steps=20)
    _submit_waiting(eng, "a_intake", {"a_intake": str(_write_json(proj / "submit-a.json", {}))})
    eng.run(max_steps=20)
    _submit_n12_voice(eng, proj, loud=True)
    assert eng.rt("n12_voice_audio").status.value in {"ITERATE", "BLOCKED"}
    assert any("VOI-SEAMS" in f for f in eng.rt("n12_voice_audio").failures)


def test_stale_optional_inventory_is_not_used_by_regression(tmp_path):
    chapters = [["demo", 0, 2]]
    baseline = _inventory(chapters, _demo_samples(2), fps=1)
    candidate = _inventory(chapters, _demo_samples(2), fps=1)
    base = _write_json(tmp_path / "base.json", baseline)
    old = _write_json(tmp_path / "old.json", candidate)
    rt = NS(current_version=2, nodes={"n13_motion_scenes": NS(status=NS(value="STEP_PASS"), attempts=2)})
    stale = _artifact("inventory", old.name, version=1)
    stale.attempt = 1
    ctx = HarnessCtx(tmp_path, loads={"inventory": candidate}, artifacts=[stale],
                     config={"regression": {"baseline": {"label": "released", "inventory": str(base)}, "demo_chapters": ["demo"]}})
    ctx.state.runtime = rt
    found = regress.check(ctx).findings
    assert found and "candidate inventory" in found[0].message


def test_malformed_baseline_inventory_becomes_finding(tmp_path):
    chapters = [["c1", 0, 2]]
    candidate = _inventory(chapters, _demo_samples(2), fps=1)
    bad = _write_json(tmp_path / "bad-base.json", {"film": "bad", "fps": 1, "chapters": [["c1", 0, 2]], "samples": [{"t": 0}]})
    cand = _write_json(tmp_path / "candidate.json", candidate)
    ctx = HarnessCtx(tmp_path, loads={"inventory": candidate}, artifacts=[_artifact("inventory", cand.name)],
                     config={"regression": {"baseline": {"label": "released", "inventory": str(bad)}}})
    assert "BAD_BASELINE" in _finding_codes(regress.check(ctx))   # caught by the up-front schema check


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for strip generation")
def test_broken_strip_baseline_json_becomes_finding(tmp_path):
    master = tmp_path / "m.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x36:r=2:d=2",
                    "-pix_fmt", "yuv420p", str(master)], check=True)
    inv = _inventory([["c1", 0, 2]], _demo_samples(2), fps=1)
    candidate = _write_json(tmp_path / "candidate.json", inv)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    ctx = HarnessCtx(tmp_path, loads={"inventory": inv}, paths={"master": master}, artifacts=[_artifact("inventory", candidate.name)],
                     config={"regression": {"baseline": {"label": "released", "inventory": str(broken), "master": str(master)}}})
    assert review_prep.baseline_strips(ctx, candidate_inventory_path=candidate) == []
    assert ctx._baseline_strip_findings and "unreadable" in ctx._baseline_strip_findings[0].message


def test_layout_rows_from_synthetic_audit_and_not_measured(tmp_path):
    audit = {"film": "synthetic", "fps": 2, "samples": 4, "runs": [
        {"kind": "TEXT_OVERLAP", "from": 1.0, "to": 2.0, "dur": 1.0, "text": "WATER", "other": "DATA", "value": 0.4},
        {"kind": "LINE_THROUGH_TEXT", "from": 1.5, "to": 2.0, "dur": 0.5, "text": "PLANET", "other": "signal line", "value": 1},
        {"kind": "TEXT_CLIPPED", "from": 2.0, "to": 3.0, "dur": 1.0, "text": "SIGNAL", "other": "", "value": 0.5},
        {"kind": "OFFSTAGE", "from": 3.0, "to": 4.0, "dur": 1.0, "text": "EDGE", "other": "", "value": 0.2},
        {"kind": "EMPTY_PANEL", "from": 4.0, "to": 7.0, "dur": 3.0, "text": "TIER", "other": "", "value": 1},
    ]}
    found = layout.check(HarnessCtx(tmp_path, loads={"layout_audit": audit})).findings
    assert [f.dimension for f in found] == ["LEG-OVERLAP", "LEG-OVERLAP", "LEG-CLIP", "LEG-OFFSTAGE", "LEG-EMPTY"]
    assert "LINE_THROUGH_TEXT" in found[1].message and "PLANET" in found[1].message
    missing = layout.check(HarnessCtx(tmp_path / "m")).findings
    assert missing[0].dimension == "LEG-OVERLAP" and missing[0].severity == "P3" and "not measured" in missing[0].message


def test_baseline_layout_regression_is_chapter_relative(tmp_path):
    chapters = [["title", 0, 10], ["arch", 80, 100]]
    baseline = _inventory(chapters, [[1, {"texts": ["TITLE"], "cap": None, "zoom": None, "pointer": False, "ring": False}],
                                     [88, {"texts": ["SIGNAL TIER"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    candidate = _inventory(chapters, baseline["samples"])
    inherited = {"kind": "EMPTY_PANEL", "from": 1.2, "to": 4.8, "dur": 3.6, "text": "SIGNAL TIER", "other": "", "value": 1}
    new_overlap = {"kind": "BOX_OVERLAP", "from": 32.8, "to": 37.8, "dur": 5.0, "text": "PEOPLE", "other": "PLANET", "value": 0.08}
    same_text_different_chapter = {"kind": "EMPTY_PANEL", "from": 88.4, "to": 95.0, "dur": 6.6, "text": "SIGNAL TIER", "other": "", "value": 1}
    ctx = _regression_ctx(tmp_path, baseline, candidate, baseline_layout={"film": "base", "fps": 1, "samples": 10, "runs": [inherited]},
                          candidate_layout={"film": "candidate", "fps": 1, "samples": 10,
                                            "runs": [inherited, new_overlap, same_text_different_chapter]})
    found = [f for f in regress.check(ctx).findings if f.check.endswith("LAYOUT_REGRESSION")]
    assert len(found) == 2
    assert "BOX_OVERLAP" in found[0].message
    assert any("arch: EMPTY_PANEL" in f.message for f in found)


def _write_wav(path: Path, segments: list[tuple[float, float, float]], sr=16000):
    dur = max(end for _, end, _ in segments) + 0.4
    x = np.zeros(int(dur * sr), dtype=np.float64)
    for start, end, amp in segments:
        a, b = int(start * sr), int(end * sr)
        x[a:b] += amp * np.sin(2 * math.pi * 440 * np.arange(b - a) / sr)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.clip(x * 32767, -32768, 32767).astype("<i2").tobytes())
    return path


def _write_array_wav(path: Path, x: np.ndarray, sr=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.clip(x * 32767, -32768, 32767).astype("<i2").tobytes())
    return path


def _seams(tmp_path: Path, segments, units):
    wav = _write_wav(tmp_path / "voice.wav", segments)
    return audio.check_seams(HarnessCtx(tmp_path, paths={"voice": wav}, loads={"timings": {"units": units}}))


def test_voice_seams_clean_hard_cut_loud_tail_and_overlap(tmp_path):
    clean = _seams(tmp_path / "clean", [(0.08, 0.60, 0.03)], [{"unit": "u1", "text": "water data", "start": 0, "end": 1}])
    assert not clean.findings
    hard = _seams(tmp_path / "hard", [(0.0, 1.0, 0.2)], [{"unit": "u1", "text": "water data", "start": 0, "end": 1}])
    assert "HARD_CUT" in _finding_codes(hard) or "END_NOT_SILENT" in _finding_codes(hard)
    tail = _seams(tmp_path / "tail", [(0.0, 1.0, 0.2), (1.04, 1.4, 0.1)], [
        {"unit": "u1", "text": "water", "start": 0, "end": 1}, {"unit": "u2", "text": "data", "start": 1.04, "end": 1.4}])
    assert "NO_GAP_LOUD_TAIL" in _finding_codes(tail)
    overlap = _seams(tmp_path / "overlap", [(0.0, 1.0, 0.05)], [
        {"unit": "u1", "text": "water", "start": 0, "end": 1}, {"unit": "u2", "text": "data", "start": 0.9, "end": 1.2}])
    assert "OVERLAP" in _finding_codes(overlap)


def test_end_not_silent_split_threshold_allows_quiet_decay(tmp_path):
    sr = 16000
    t = np.arange(sr) / sr
    x = 0.08 * np.sin(2 * np.pi * 440 * t)
    fade_start = int(0.84 * sr)
    end = int(1.0 * sr)
    x[fade_start:end] *= np.linspace(1, 0.01, end - fade_start)  # about -40 dB by the final 40 ms
    wav = _write_array_wav(tmp_path / "quiet-decay/voice.wav", np.concatenate([x, np.zeros(int(0.3 * sr))]), sr)
    res = audio.check_seams(HarnessCtx(tmp_path / "quiet-decay", paths={"voice": wav},
                                       loads={"timings": {"units": [{"unit": "u1", "text": "water data", "start": 0, "end": 1.0}]}}))
    assert not [f for f in res.findings if f.severity != "nit"], [f.message for f in res.findings]


def test_float_wav_is_reported_not_measured(tmp_path):
    wav = tmp_path / "float.wav"
    wav.write_bytes(b"not a wave")
    res = audio.check_seams(HarnessCtx(tmp_path, paths={"voice": wav},
                                       loads={"timings": {"units": [{"unit": "u1", "text": "water", "start": 0, "end": 1}]}}))
    assert res.findings and res.findings[0].severity == "P3" and "not measured" in res.findings[0].message


def test_caption_script_exact_match_and_missing_word(tmp_path):
    script = {"title": "Synthetic", "chapters": [{"id": "c1", "title": "C", "objective": "show signal data",
                                                   "units": [{"id": "u1", "text": "Each signal asks one question"}]}]}
    caps = tmp_path / "captions.srt"
    caps.write_text("1\n00:00:00,000 --> 00:00:02,000\nEach signal asks one question\n")
    ok = layout.check_caption_script(HarnessCtx(tmp_path, loads={"script": script}, paths={"captions": caps}))
    assert not ok.findings
    caps.write_text("1\n00:00:00,000 --> 00:00:02,000\nsignal asks one question\n")
    bad = layout.check_caption_script(HarnessCtx(tmp_path, loads={"script": script}, paths={"captions": caps}))
    assert bad.findings and "missing: each" in bad.findings[0].message


def test_caption_script_normalizes_ises_izes_variant(tmp_path):
    script = {"title": "Synthetic", "chapters": [{"id": "c1", "title": "C", "objective": "show signal data",
                                                   "units": [{"id": "u1", "text": "The system synthesises signals"}]}]}
    caps = tmp_path / "captions.srt"
    caps.write_text("1\n00:00:00,000 --> 00:00:02,000\nThe system synthesizes signals\n")
    assert not layout.check_caption_script(HarnessCtx(tmp_path, loads={"script": script}, paths={"captions": caps})).findings


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for strip generation")
def test_baseline_strips_generated_for_tiny_mp4s(tmp_path):
    base = tmp_path / "base.mp4"
    cand = tmp_path / "cand.mp4"
    for path, color in [(base, "blue"), (cand, "red")]:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x36:r=2:d=2",
                        "-pix_fmt", "yuv420p", str(path)], check=True)
    inv = _inventory([["c1", 0, 2]], [[0, {"texts": ["A"], "cap": "f0", "zoom": 1, "pointer": False, "ring": False}],
                                    [1, {"texts": ["B"], "cap": "f1", "zoom": 1, "pointer": False, "ring": False}]], fps=1)
    base_inv = _write_json(tmp_path / "base.json", inv)
    cand_inv = _write_json(tmp_path / "cand.json", inv)
    ctx = HarnessCtx(tmp_path, loads={"inventory": inv}, paths={"master": cand},
                     config={"regression": {"baseline": {"label": "released", "inventory": str(base_inv), "master": str(base)},
                                            "demo_chapters": []}})
    strips = review_prep.baseline_strips(ctx, candidate_inventory_path=cand_inv)
    assert len(strips) == 1
    im = Image.open(tmp_path / "work" / strips[0])
    assert im.width == 2 * review_prep.STRIP_FRAME_W + review_prep.STRIP_GAP
    assert im.height == 2 * review_prep.STRIP_FRAME_H + review_prep.STRIP_GAP
    manifest = json.loads((ctx.workdir / "baseline-strips.json").read_text())
    assert manifest["inside_project"] is True and not Path(manifest["root"]).is_absolute() and "paths" not in manifest
    for item in manifest["files"]:                       # resolvable from the project, with no machine-specific path
        assert (ctx.project_dir / manifest["root"] / item["path"]).exists()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for strip generation")
def test_privacy_baseline_strips_register_manifest_not_external_images(tmp_path, monkeypatch):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    base = proj / "base.mp4"
    cand = proj / "cand.mp4"
    for path, color in [(base, "blue"), (cand, "red")]:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x36:r=2:d=2",
                        "-pix_fmt", "yuv420p", str(path)], check=True)
    inv = {"film": "x", "fps": 1, "chapters": [["c1", 0, 2]], "samples": [[0, {"texts": ["A"], "cap": None, "zoom": None, "pointer": False, "ring": False}]]}
    (proj / "base-inv.json").write_text(json.dumps(inv))
    (proj / "names.json").write_text(json.dumps(["Synthetic Person"]))
    wf = yaml.safe_load(mini_workflow(tmp_path).read_text())
    d = next(n for n in wf["nodes"] if n["id"] == "d_build")
    d["outputs"] += [{"key": "master", "kind": "video", "description": "film", "required": False},
                     {"key": "inventory", "kind": "json", "schema": "inventory", "description": "inv", "required": False}]
    r1 = next(n for n in wf["nodes"] if n["id"] == "r1_review")
    r1["run"] = {"tool": "review_prep:creative"}
    r1["outputs"] = [{"key": "baseline_strips", "kind": "json", "description": "strips", "required": False}]
    path = tmp_path / "wf.yaml"
    path.write_text(yaml.safe_dump(wf, sort_keys=False))
    cfg = {"id": "t", "name": "t", "workflow": str(path), "executor": "task", "humans": ["Wei"],
           "privacy": {"names": "names.json"},
           "regression": {"baseline": {"label": "released", "inventory": "base-inv.json", "master": "base.mp4"}}}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))

    eng = Engine(proj)
    eng.run(max_steps=30)
    _submit_waiting(eng, "a_intake", {"a_intake": str(_write_json(proj / "a.json", {}))})
    eng.run(max_steps=30)
    _submit_waiting(eng, "b_plan", {"b_plan": str(_write_json(proj / "b.json", {}))})
    if eng.rt("c_audio").status.value == "WAITING_RUNTIME":
        _submit_waiting(eng, "c_audio", {"c_audio": str(_write_json(proj / "c.json", {}))})
    eng.run(max_steps=30)
    sub = proj / "d-submit"
    sub.mkdir(exist_ok=True)
    (sub / "d_build.json").write_text("{}")
    shutil.copy2(cand, sub / "master.mp4")
    (sub / "inventory.json").write_text(json.dumps(inv))
    _submit_waiting(eng, "d_build", {"d_build": str(sub / "d_build.json"), "master": str(sub / "master.mp4"),
                                     "inventory": str(sub / "inventory.json")})
    eng.run(max_steps=30)
    rt = eng.rt("r1_review")
    review = {"reviewer": "critic", "reviewed": {}, "scores": [
        {"dimension": d["id"], "score": 5, "evidence": "synthetic"}
        for rid in eng.node("r1_review").rubric for d in eng.rubrics[rid]["dimensions"] if d["check"]["type"] == "review"
    ], "defects": []}
    eng.submit("r1_review", {}, token=rt.dispatch_token, actor="agent:critic", review=review)
    assert eng.rt("r1_review").status.value == "STEP_PASS"
    recs = eng.current("baseline_strips")
    assert recs and recs[-1].path.endswith("baseline-strips.json")
    manifest = json.loads((proj / recs[-1].path).read_text())
    assert "paths" not in manifest and manifest["inside_project"] is False and Path(manifest["root"]).is_absolute()
    for item in manifest["files"]:
        p = Path(manifest["root"]) / item["path"]
        assert p.exists() and item["sha256"] == __import__("hashlib").sha256(p.read_bytes()).hexdigest()
    assert not list((proj / "work/r1_review").glob("**/*baseline-vs-candidate*.png"))


def test_required_schema_outputs_keep_previous_registration_behavior(tmp_path):
    proj, eng = _task_n12_project(tmp_path)
    eng.run(max_steps=20)
    _submit_waiting(eng, "a_intake", {"a_intake": str(_write_json(proj / "submit-a.json", {}))})
    eng.run(max_steps=20)
    sub = proj / "submit"
    sub.mkdir(exist_ok=True)
    _write_wav(sub / "voice.wav", [(0, 0.6, 0.03)])
    (sub / "timings.json").write_text(json.dumps({"units": [{"unit": "u1", "text": "water data", "start": 0, "end": 1.0}]}))
    (sub / "vd.json").write_text(json.dumps({"age": "adult"}))  # invalid for its schema, but required outputs are not newly schema-gated
    _submit_waiting(eng, "n12_voice_audio", {"voice_direction": str(sub / "vd.json"), "voice": str(sub / "voice.wav"),
                                             "timings": str(sub / "timings.json")})
    assert eng.current("voice_direction")


def test_reg_verdict_not_applicable_without_a_baseline():
    from vpg.evaluation import merge_review
    from vpg.graph import load_workflow
    from vpg.evaluation import load_rubrics

    node = load_workflow().node("n16_creative_critique")
    review, findings, unscored = merge_review(node, 1, [], {"scores": [{"dimension": "REG-VERDICT", "score": 5, "evidence": "no baseline"}]}, load_rubrics())
    assert "REG-VERDICT" not in unscored


def test_rule_promotion_by_human_makes_layout_row_must(tmp_path):
    rules = tmp_path / "rules.json"
    shutil.copy2(PKG / "knowledge/rules.json", rules)
    rb = RuleBase(rules)
    assert rb.get("R-LAY-001")["lifecycle"]["state"] == "validated"
    rb.transition("R-LAY-001", "approved", by="Wei", evidence=["review:2026-10-02"], human=True)
    rb.transition("R-LAY-001", "adopted", by="Wei", evidence=["review:2026-10-02"], human=True,
                  adopted_in="rubrics/accessibility.yaml")
    proj = tmp_path / "proj"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path)
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump({"id": "t", "name": "t", "workflow": str(wf), "executor": "task"}))
    eng = Engine(proj, executor=FnExecutor(), rules_path=rules)
    dim = next(d for d in eng.rubrics["accessibility"]["dimensions"] if d["id"] == "LEG-OVERLAP")
    assert dim["level"] == "MUST"
    finding = layout.check(HarnessCtx(tmp_path / "audit", loads={"layout_audit": {"film": "f", "fps": 1, "samples": 1, "runs": [
        {"kind": "TEXT_OVERLAP", "from": 1, "to": 2, "dur": 1, "text": "WATER", "other": "DATA", "value": 0.5}]}})).findings[0]
    assert eng._govern_findings([finding])[0].level == "MUST"


def test_voice_review_refused_without_seam_result(tmp_path):
    proj, eng = _n12_project(tmp_path)
    review = {"reviewer": "critic", "scores": [{"dimension": "VOI-PRONUNCIATION", "score": 5, "evidence": "sounds clear"}],
              "defects": []}
    node = eng.node("r1_review")
    node.rubric = ["creative"]
    assert any("VOI-SEAMS" in p for p in eng._review_bind_problems(node, {}, "agent:critic", review=review))


def test_release_summary_lists_declared_departures(tmp_path):
    proj = _human_gated_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="looks right")
    eng.run(max_steps=200)
    dep = {"removals": [{"chapter": "c1", "match": "SIGNAL", "why": "synthetic scoped removal", "cites": ["F012", "CR-004"]}]}
    dep_path = proj / "departures.json"
    dep_path.write_text(json.dumps(dep))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text()) or {}
    cfg["regression"] = {"baseline": {"label": "released", "inventory": "baseline.json"}, "departures": "departures.json"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    ctx.config = cfg
    verify.final(ctx)
    assert "synthetic scoped removal" in (ctx.workdir / "release-summary.md").read_text()
    assert "F012, CR-004" in (ctx.workdir / "decision-digest.md").read_text()


def _pre_tool_on_plan_project(tmp_path):
    """The mini workflow with a deterministic pre-tool on the non-review agent node b_plan."""
    proj = tmp_path / "pre-tool"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    path = mini_workflow(tmp_path)
    wf = yaml.safe_load(path.read_text())
    for n in wf["nodes"]:
        if n["id"] == "b_plan":
            n["run"] = {"tool": "tests.fakes:review_check"}   # reports a MUST LEG-SIZE finding while flags/defect exists
    path.write_text(yaml.safe_dump(wf, sort_keys=False))
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "p", "workflow": str(path), "executor": "task"}))
    (proj / "flags").mkdir()
    (proj / "flags" / "defect").write_text("1")
    return proj


def test_a_non_review_pre_tool_finding_stays_guidance_and_does_not_fail_its_node(tmp_path):
    from tests.test_hardening import submit_packet
    proj = _pre_tool_on_plan_project(tmp_path)
    eng = Engine(proj)                                    # executor: task, as in production
    eng.run(max_steps=10)
    submit_packet(eng, proj, "a_intake")
    eng.run(max_steps=10)
    rt = eng.rt("b_plan")
    assert rt.status.value == "WAITING_RUNTIME"
    assert any(f.dimension == "LEG-SIZE" for f in rt.pre_findings)                  # guidance in the agent's packet
    packet = (proj / rt.task_packet).read_text() if rt.task_packet else ""
    assert "LEG-SIZE" in packet
    submit_packet(eng, proj, "b_plan")
    rt = eng.rt("b_plan")
    assert rt.status.value == "STEP_PASS" and rt.attempts == 1, (rt.status, rt.failures)   # as before v1.8 batch 3


def test_fresh_keeps_only_the_current_version_or_the_latest_passing_n13_attempt():
    from vpg.freshness import fresh
    old = NS(id="A0001", version=1, node="n13_motion_scenes", attempt=1)
    cut = NS(id="A0002", version=2, node="n13_motion_scenes", attempt=3)
    new = NS(id="A0003", version=1, node="n13_motion_scenes", attempt=4)
    state = NS(runtime=NS(current_version=2, nodes={"n13_motion_scenes": NS(status=NS(value="STEP_PASS"), attempts=4)}))
    assert fresh([new, old, cut], state) == [cut, new]
    state.runtime.nodes["n13_motion_scenes"].status = NS(value="ITERATE")   # its latest attempt has not passed
    assert fresh([new, old, cut], state) == [cut]
    assert fresh([new, old], NS()) == [old, new]                           # a bare test context keeps every record


@pytest.mark.parametrize("bad", ["zoom", "chapter"])
def test_a_malformed_configured_baseline_is_bad_baseline_not_a_crash(tmp_path, bad):
    chapters = [["c1", 0, 4]]
    sample = {"texts": ["WATER DATA"], "cap": "f0", "zoom": 1.0, "pointer": True, "ring": False}
    baseline = _inventory(chapters, [[0, dict(sample)]])
    if bad == "zoom":
        baseline["samples"][0][1]["zoom"] = "wide"           # not a number: used to raise ValueError in N17
    else:
        baseline["chapters"] = [["c1", 0]]                   # two elements: used to raise IndexError in the N16 strips
    candidate = _inventory(chapters, [[0, dict(sample)]])
    ctx = _regression_ctx(tmp_path, baseline, candidate, demo_chapters=["c1"])
    assert "BAD_BASELINE" in _finding_codes(regress.check(ctx))
    master = tmp_path / "base.mp4"
    master.write_bytes(b"")                                  # never decoded: the shape check stops first
    ctx.config["regression"]["baseline"]["master"] = str(master)
    cand_inv = tmp_path / "candidate-inventory.json"
    assert review_prep.baseline_strips(ctx, candidate_inventory_path=cand_inv) == []
    assert any("schema" in f.message for f in ctx._baseline_strip_findings)


def test_a_chapterless_literal_taking_exactly_half_of_a_chapter_is_overbroad(tmp_path):
    chapters = [["c1", 0, 4]]
    baseline = _inventory(chapters, [[0, {"texts": ["DATA LAKE", "DATA MESH", "PEOPLE", "PLANET"], "cap": None, "zoom": None,
                                          "pointer": False, "ring": False}]])
    candidate = _inventory(chapters, [[0, {"texts": ["PEOPLE", "PLANET"], "cap": None, "zoom": None, "pointer": False, "ring": False}]])
    found = _finding_codes(regress.check(_regression_ctx(tmp_path, baseline, candidate, {"removals": [
        {"match": "data", "why": "wording", "cites": ["F012"]}]}, )))
    assert "OVERBROAD_DEPARTURE" in found and "UNDECLARED_REMOVAL" in found


def test_the_harness_section_says_whether_cap_script_was_measured(tmp_path):
    lines = {}
    for case, data in (("unmeasured", {}), ("not-measured", {"caption_script": {"caption_script_measured": False}}),
                       ("match", {"caption_script": {"caption_script_measured": True, "missing": [], "extra": []}})):
        report = _write_json(tmp_path / case / "accessibility-report.json", data)
        ctx = HarnessCtx(tmp_path / case, artifacts=[_artifact("accessibility_report", report.name, node="n18_accessibility_legibility")])
        lines[case] = verify._harness_lines(ctx)
    assert "- CAP-SCRIPT: not measured" in lines["unmeasured"]
    assert "- CAP-SCRIPT: not measured" in lines["not-measured"]   # never a false "match" when it was not measured
    assert "- CAP-SCRIPT: captions match the script" in lines["match"]


def test_an_exact_departure_wins_over_a_broader_one_for_the_same_text(tmp_path):
    chapters = [["c1", 0, 4]]
    baseline = _inventory(chapters, [[0, {"texts": ["EXPERT FEEDBACK", "EXPERT", "PEOPLE", "PLANET"], "cap": None, "zoom": None,
                                          "pointer": False, "ring": False}]])
    candidate = _inventory(chapters, [[0, {"texts": ["EXPERT", "PEOPLE", "PLANET"], "cap": None, "zoom": None,
                                           "pointer": False, "ring": False}]])
    res = regress.check(_regression_ctx(tmp_path, baseline, candidate, {"removals": [
        {"match": "expert", "why": "broad, listed first", "cites": ["F012"]},
        {"match": ["expert feedback"], "why": "exact", "cites": ["F012"]}]}))
    absorbed = {d["why"]: d["absorbed"] for d in res.data["declared_departures"]}
    assert absorbed == {"broad, listed first": [], "exact": ["EXPERT FEEDBACK"]}


def test_the_previous_chapters_number_only_at_the_seam_is_chrome_anywhere_else_it_is_content(tmp_path):
    chapters = [["02-people", 0, 4], ["03-solution", 4, 8], ["04-architecture", 8, 12]]
    def sample(t, texts):
        return [t, {"texts": texts, "cap": None, "zoom": None, "pointer": False, "ring": False}]
    def inv(c02, c04_seam, c04_mid):
        return _inventory(chapters, [sample(1, c02), sample(5, ["03", "PROPOSAL"]), sample(8.0, c04_seam), sample(10, c04_mid)])
    baseline = inv(["PEOPLE", "03"], ["03", "04", "TIER A"], ["04", "TIER A"])    # "03" lingers 0.0 s into chapter 04
    res = regress.check(_regression_ctx(tmp_path / "seam", baseline, inv(["PEOPLE", "03"], ["04", "TIER A"], ["04", "TIER A"])))
    assert "UNDECLARED_REMOVAL" not in _finding_codes(res)
    assert res.data["seam_chrome_ignored"] == {"04-architecture": ["03"]}
    assert any("seam chrome ignored in 04-architecture: 03" in line for line in verify._harness_lines(
        HarnessCtx(tmp_path / "seam", artifacts=[_artifact("regression_report", "work/regression-report.json",
                                                           node="n17_design_compliance")])))
    mid = inv(["PEOPLE", "03"], ["03", "04", "TIER A"], ["03", "04", "TIER A"])  # "03" also shown mid-chapter: content
    res = regress.check(_regression_ctx(tmp_path / "mid", mid, inv(["PEOPLE", "03"], ["04", "TIER A"], ["04", "TIER A"])))
    assert any(f.message.endswith(": 03") for f in res.findings if f.check.endswith("UNDECLARED_REMOVAL"))
    res = regress.check(_regression_ctx(tmp_path / "kpi", baseline, inv(["PEOPLE"], ["03", "04", "TIER A"], ["04", "TIER A"])))
    assert any("02-people" in f.message for f in res.findings if f.check.endswith("UNDECLARED_REMOVAL"))   # not the previous number
    res = regress.check(_regression_ctx(tmp_path / "own", baseline, inv(["PEOPLE", "03"], ["03", "TIER A"], ["TIER A"])))
    assert any(f.message.endswith(": 04") for f in res.findings if f.check.endswith("UNDECLARED_REMOVAL"))   # its own number
