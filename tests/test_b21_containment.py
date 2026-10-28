from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from tests.conftest import FnExecutor, mini_workflow
from tests.test_b2_review_regressions import _project, _release_wait
from tests.test_hardening import make_project, submit_packet
from vpg.engine import Engine, EngineError
from vpg.models import Escalation, Feedback, NodeStatus as S
from vpg.provenance import Ledger
from vpg.tools import privacy
from vpg.util import now


LISTED = "Jordan Sample"


def _declare_names(proj, *, text_scan: str | None = None, n14_hold: str | None = None, hold_dir=None):
    (proj / "names.json").write_text(json.dumps({"names": [LISTED]}))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["privacy"] = {"names": "names.json"}
    if text_scan is not None:
        cfg["privacy"]["text_scan"] = text_scan
    if n14_hold is not None:
        cfg["privacy"]["n14_hold"] = n14_hold
    if hold_dir is not None:
        cfg["privacy"]["hold_dir"] = str(hold_dir.resolve())
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))


def _master_workflow(tmp_path, *, failing_completion: bool = False):
    wf_dir = tmp_path / "wf"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf = mini_workflow(wf_dir)
    data = yaml.safe_load(wf.read_text())
    for node in data["nodes"]:
        if node["id"] == "d_build":
            node["outputs"] = [{"key": "master", "kind": "video", "description": "Master",
                                "state_path": "production.master_ref"}]
            completion_key = "missing" if failing_completion else "master"
            node["completion"] = [{"type": "artifact", "key": completion_key, "description": "master exists"}]
    wf.write_text(yaml.safe_dump(data, sort_keys=False))
    return wf


def _drive_to_master(eng, proj):
    eng.run(max_steps=10)
    submit_packet(eng, proj, "a_intake")
    eng.run(max_steps=10)
    submit_packet(eng, proj, "b_plan")
    eng.run(max_steps=10)
    rt = eng.rt("d_build")
    master = proj / "work/d_build/a1/master.mp4"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"synthetic master")
    assert rt.status == S.WAITING_RUNTIME
    return rt, master


def test_defaults_off_text_output_with_listed_name_is_accepted_and_nothing_moves(tmp_path):
    proj = make_project(tmp_path)
    _declare_names(proj)
    eng = Engine(proj)
    eng.run(max_steps=1)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake/a1/a_intake.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"speaker": LISTED}))
    before = {p.relative_to(proj) for p in proj.rglob("*")}
    eng.submit("a_intake", {"a_intake": str(out)}, token=rt.dispatch_token, actor="agent:test")
    after = {p.relative_to(proj) for p in proj.rglob("*")}
    assert out.exists()
    assert eng.rt("a_intake").status == S.STEP_PASS
    assert before <= after
    assert not list(proj.rglob("*.privacy-refused*"))


def test_defaults_off_n14_master_registers_and_failures_use_attempts(tmp_path, monkeypatch):
    proj = make_project(tmp_path, workflow=_master_workflow(tmp_path))
    _declare_names(proj)
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: pytest.fail("N14 scan should be off by default"))
    eng = Engine(proj)
    rt, master = _drive_to_master(eng, proj)
    eng.submit("d_build", {"master": str(master)}, token=rt.dispatch_token, actor="agent:test")
    assert eng.current("master") and master.exists()

    failing = make_project(tmp_path / "failing", workflow=_master_workflow(tmp_path / "failing", failing_completion=True))
    _declare_names(failing)
    eng2 = Engine(failing)
    rt2, master2 = _drive_to_master(eng2, failing)
    eng2.submit("d_build", {"master": str(master2)}, token=rt2.dispatch_token, actor="agent:test")
    assert eng2.rt("d_build").status == S.ITERATE
    assert eng2.rt("d_build").fail_streak == 1
    assert not any("no attempt used" in f for f in eng2.rt("d_build").failures)


def test_n23_never_trusts_a_forged_privacy_scan_pass(tmp_path, monkeypatch):
    proj = make_project(tmp_path, workflow=_master_workflow(tmp_path))
    _declare_names(proj)
    eng = Engine(proj)
    rt, master = _drive_to_master(eng, proj)
    eng.submit("d_build", {"master": str(master)}, token=rt.dispatch_token, actor="agent:test")
    rec = eng.current("master")[0]
    Ledger(proj / ".vpg/ledger.jsonl").append("privacy_scan_pass", key="forged", node="engine",
                                              video_sha256=rec.sha256, scanner=privacy.SCANNER)
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: {"frames": 1, "fps": 30, "ocr_calls": 1,
                                                                "windows": [{"entry": 1, "start_s": 0, "end_s": 1,
                                                                             "reads": 1, "box": [1, 2, 3, 4]}],
                                                                "live_windows": []})
    ctx = SimpleNamespace(config=eng.project.config, project_dir=proj, workdir=proj / "work/n23", load=lambda k: None)
    problem = privacy.release_problem(ctx, proj / rec.path, rec.sha256)
    assert problem and "listed person's name is readable" in problem


def test_hold_refused_text_never_moves_outside_workdir_or_deletes(tmp_path):
    hold = tmp_path / "hold"
    hold.mkdir()
    proj = make_project(tmp_path)
    _declare_names(proj, text_scan="on", hold_dir=hold)
    eng = Engine(proj)
    eng.run(max_steps=1)
    rt = eng.rt("a_intake")
    source = proj / "sources/source.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps({"speaker": LISTED}))
    with pytest.raises(EngineError, match=r"\[list entry #1\]"):
        eng.submit("a_intake", {"a_intake": str(source)}, token=rt.dispatch_token, actor="agent:test")
    assert source.exists()
    assert not list(hold.iterdir())
    assert not list(proj.rglob("*.privacy-refused*"))

    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["privacy"]["hold_dir"] = str((tmp_path / "hold-file").resolve())
    (tmp_path / "hold-file").write_text("not a directory")
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(EngineError, match="privacy.hold_dir"):
        Engine(proj)


def test_hold_dir_not_validated_when_both_settings_off(tmp_path):
    proj = make_project(tmp_path)
    _declare_names(proj, hold_dir=tmp_path / "missing-hold")
    assert Engine(proj).project.config["privacy"]["hold_dir"].endswith("missing-hold")


def test_privacy_settings_accept_strings_and_yaml_booleans(tmp_path):
    for idx, (text_scan, n14_hold) in enumerate((('"on"', '"off"'), ("true", "false"), ("on", "off"), ("yes", "no"))):
        proj = make_project(tmp_path / str(idx))
        hold = tmp_path / f"hold-{idx}"
        hold.mkdir()
        (proj / "names.json").write_text(json.dumps({"names": [LISTED]}))
        (proj / "vpg.project.yaml").write_text(
            f"id: t\nname: t\nworkflow: {(tmp_path / str(idx) / 'mini.yaml')}\nexecutor: task\n"
            f"privacy:\n  names: names.json\n  text_scan: {text_scan}\n  n14_hold: {n14_hold}\n  hold_dir: {hold}\n")
        eng = Engine(proj)
        assert eng._privacy_feature_on("text_scan") is True
        assert eng._privacy_feature_on("n14_hold") is False


def test_unblock_never_releases_a_hold_its_escalation_did_not_cite(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    owner = eng.node("g_gate").escalation.to
    eng.state.evaluation.feedback.append(Feedback(id="F001", text="the ending is flat", actor=owner, version=1,
                                                  created_at=now(), status="interpreted", hold="held for its author"))
    eng.state.runtime.escalations.append(Escalation(node="g_gate", reason="E12: blocked", report="unknown defect cited by F001",
                                                    to=owner, at=now(), holds=[]))
    eng.rt("g_gate").status = S.BLOCKED
    eng._save("probe")
    eng.unblock("g_gate", actor=owner, note="fixed the interpreter")
    assert eng.state.evaluation.feedback[0].hold == "held for its author"


def test_default_digest_control_doc_part_only_match_has_no_k_item_but_full_name_does(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    listed = LISTED
    proj, eng = _project(tmp_path, monkeypatch, store=store, privacy_names=[listed])
    (proj / "controls/creative.md").write_text((proj / "controls/creative.md").read_text() + "\nSample only.\n")
    ctx = _release_wait(eng, proj)
    digest = (ctx.workdir / "decision-digest.md").read_text()
    template = json.loads((ctx.workdir / "decision.template.json").read_text())
    assert "control doc creative.md contains privacy-name text" not in digest
    assert not any("control doc creative.md contains privacy-name text" in json.dumps(v) for v in template["item_map"].values())

    proj2, eng2 = _project(tmp_path, monkeypatch, store=tmp_path / "prefs2", privacy_names=[listed])
    (proj2 / "controls/creative.md").write_text((proj2 / "controls/creative.md").read_text() + f"\nOwner: {listed}\n")
    ctx2 = _release_wait(eng2, proj2)
    digest2 = (ctx2.workdir / "decision-digest.md").read_text()
    template2 = json.loads((ctx2.workdir / "decision.template.json").read_text())
    assert listed not in digest2
    assert "control doc creative.md contains privacy-name text: [list entry #1]" in digest2
    assert any(str(v.get("target", "")).startswith("ack_warnings.") for v in template2["item_map"].values())
