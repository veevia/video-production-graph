from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from PIL import Image

from tests.conftest import FnExecutor
from tests.test_hardening import make_project
from vpg import cli
from vpg.engine import Engine
from vpg.executors import run_command
from vpg.publish import publish
from vpg.reuse import may_reuse, producer_key, stamp
from vpg.tools import privacy


NAME = "Jane Placeholder"


def test_live_window_reads_the_one_frame_a_10fps_sampler_misses():
    frames = [Image.new("L", (8, 8), 255) for _ in range(30)]
    frames[11].putpixel((0, 0), 0)
    calls = []

    def ocr(img):
        calls.append(img.getpixel((0, 0)))
        return [("Jane", 0, 0, 3, 3), ("Placeholder", 4, 0, 3, 3)] if img.getpixel((0, 0)) == 0 else []

    old_samples = [frames[i] for i in range(0, 30, 3)]
    assert privacy.scan_frames(old_samples, [NAME], fps=10, ocr=ocr)["windows"] == []
    report = privacy.scan_frames(frames, [NAME], fps=30, ocr=ocr, live_windows=[{"start": 0, "end": 1, "what": "drawer"}])
    assert report["windows"] and report["live_windows"][0] == {
        "window": 1, "start": 0, "end": 1, "what": "drawer", "frames_in_window": 30, "frames_decoded": 30,
        "ocr_reads": 3, "unchanged_frames": 27, "sampled_fraction": 1.0}   # repeats of the previous frame are not re-read


def test_numeric_privacy_scan_fps_is_clamped_not_weakened(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "ocr_self_test", lambda ocr: None)
    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": 5.0, "avg_frame_rate": 5.0})
    seen = {}

    def fake_frames(video, fps, every_frame=False):
        seen["fps"], seen["every_frame"] = fps, every_frame
        return [Image.new("L", (4, 4), 255)]

    monkeypatch.setattr(privacy, "gray_frames", fake_frames)
    privacy.scan_video(tmp_path / "v.mp4", [NAME], fps=4, ocr=lambda img: [])
    assert privacy._rate(seen["fps"]) == 10.0 and seen["every_frame"]   # the scan always asks for every frame


def test_release_problem_reports_live_window_stats_without_name_text(tmp_path, monkeypatch):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"video")
    (tmp_path / "names.json").write_text(json.dumps([NAME]))
    text_layers = {"frame": {"w": 8, "h": 8}, "layers": [{"id": "l", "scene": "s", "bbox": [0, 0, 1, 1], "start": 0, "end": 1}],
                   "live_windows": [{"start": 0, "end": 1, "what": "scroll"}]}

    class Ctx:
        project_dir = tmp_path
        config = {"privacy": {"names": "names.json"}}
        workdir = tmp_path / "work"

        def load(self, key):
            return text_layers if key == "text_layers" else None

    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": 30.0, "avg_frame_rate": 30.0})
    monkeypatch.setattr(privacy, "ocr_self_test", lambda ocr: None)
    monkeypatch.setattr(privacy, "gray_frames", lambda video, fps, every_frame=False: [Image.new("L", (8, 8), 255) for _ in range(30)])
    assert privacy.release_problem(Ctx(), master, privacy.sha256_file(master)) is None
    report = json.loads((tmp_path / "work/privacy-report.json").read_text())
    assert report["live_windows"][0]["sampled_fraction"] == 1.0
    assert "Jane" not in json.dumps(report)


def test_command_context_carries_producer_code_and_reuse_helper_keys_it(tmp_path):
    proj = make_project(tmp_path)
    script = proj / "producer.py"
    script.write_text("print('producer')\n")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({"commands": {"a_intake": f"{{python}} {script.name}"}, "command_deps": {"a_intake": [script.name]}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    captured = {}

    def command(ctx, command_line, result, timeout_s):
        captured.update(ctx.producer_code)
        return result

    from vpg import engine as engine_mod
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(engine_mod, "run_command", command)
    try:
        eng = Engine(proj, executor=FnExecutor())
        eng.step()
    finally:
        monkeypatch.undo()
    assert "producer.py" in captured
    payload = {"producer": {"node": "a_intake", "attempt": 1, "code": captured,
                            "code_sha256": __import__("vpg.util").util.sha256_json(sorted(captured.items()))}}
    rec = stamp({"path": "old.mov"}, payload, schema="take/v1")
    assert may_reuse(rec, payload, schema="take/v1")
    assert not may_reuse({**rec, "vpg_producer_key": "old"}, payload, schema="take/v1")
    assert not may_reuse(stamp({"path": "old.mov"}, {}), {})   # no code identity (an older engine): never reuse


def test_reuse_lint_warns_only_when_attempt_reads_lack_helper(tmp_path):
    proj = make_project(tmp_path)
    code = proj / "pipe.py"
    code.write_text("from pathlib import Path\nlist(Path('work/n11/a1').glob('outputs.json'))\n")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update({"commands": {"a_intake": "{python} pipe.py"}, "command_deps": {"a_intake": ["pipe.py"]}})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj, executor=FnExecutor())
    assert any("vpg.reuse" in w for w in eng.status()["warnings"])
    code.write_text("import time\ntime.timestamp()\nlist(Path('work/n11/a1').glob('outputs.json'))\n")
    assert any("vpg.reuse" in w for w in Engine(proj, read_only=True).status()["warnings"])   # timestamp( is not the helper
    code.write_text("from vpg.reuse import may_reuse\nmay_reuse({}, {}, schema='x')\nlist(Path('work/n11/a1').glob('outputs.json'))\n")
    assert not any("vpg.reuse" in w for w in Engine(proj, read_only=True).status()["warnings"])


def _publish_project(tmp_path, *, privacy_names: bool = False):
    proj = make_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["publish"] = {"dir": "outputs", "keys": ["master"], "auto": False}
    if privacy_names:
        (proj / "names.json").write_text(json.dumps([NAME]))
        cfg["privacy"] = {"names": "names.json"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    return proj


def test_publish_check_publish_and_idempotence(tmp_path):
    proj = _publish_project(tmp_path)

    def d_build(ctx):
        p = ctx.workdir / "master.mp4"
        p.write_bytes(b"master-v1")
        return {"d_build": p.name}

    eng = Engine(proj, executor=FnExecutor({"d_build": d_build}))
    eng.run(max_steps=200)
    # The mini workflow's master-producing node emits key d_build; publish that key for this test project.
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["publish"]["keys"] = ["d_build"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj)
    assert publish(eng, check=True)["published"] == [1]
    assert publish(eng)["published"] == [1]
    assert (proj / "outputs/v1/master.mp4").exists()
    assert publish(Engine(proj), version=1)["in_sync"] == [1]


def test_auto_publish_runs_when_master_version_passes(tmp_path):
    proj = _publish_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["publish"].update({"keys": ["d_build"], "auto": True})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))

    def d_build(ctx):
        p = ctx.workdir / "master.mp4"
        p.write_bytes(b"master-v1")
        return {"d_build": p.name}

    eng = Engine(proj, executor=FnExecutor({"d_build": d_build}))
    eng.run(max_steps=200)
    index = json.loads((proj / "outputs/_vpg-index.json").read_text())
    assert index["versions"][0]["status"] == "published"
    assert (proj / "outputs/v1/master.mp4").exists()


def test_publish_holds_version_when_privacy_scan_finds_name(tmp_path, monkeypatch):
    proj = _publish_project(tmp_path, privacy_names=True)

    def d_build(ctx):
        p = ctx.workdir / "master.mp4"
        p.write_bytes(b"master-v1")
        return {"d_build": p.name}

    eng = Engine(proj, executor=FnExecutor({"d_build": d_build}))
    eng.run(max_steps=200)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["publish"]["keys"] = ["d_build"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    scans = []

    def scan(*a, **k):
        scans.append(a[0])
        return {"frames": 1, "fps": 30, "ocr_calls": 1, "windows": [{"entry": 1}], "live_windows": []}

    monkeypatch.setattr(privacy, "scan_video", scan)
    result = publish(Engine(proj))
    assert result["held"] == [1]
    assert not (proj / "outputs/v1/master.mp4").exists()
    index = json.loads((proj / "outputs/_vpg-index.json").read_text())
    assert index["versions"][0]["reason"] == "readable listed name"          # the scan ran on the master and found one
    assert publish(Engine(proj))["held"] == [1] and len(scans) == 1            # a held version is not re-scanned by default
    assert publish(Engine(proj), version=1)["held"] == [1] and len(scans) == 2  # unless it is asked for by version
    assert (proj / "outputs/_vpg-README.md").exists() and not (proj / "outputs/README.md").exists()   # never a project's README
    assert "held versions v1 (readable listed name)" in " ".join(Engine(proj, read_only=True).status()["warnings"])


def test_publish_opt_out_status_is_byte_identical(tmp_path, capsys):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=1)
    before = capsys.readouterr()
    assert cli.main(["status", str(proj)]) == 0
    first = capsys.readouterr().out
    assert cli.main(["status", str(proj)]) == 0
    assert capsys.readouterr().out == first
    assert before is not None


# ------------------------------------------------------------------------------------------------ review fixes (v1.6)
def test_the_producer_key_covers_the_command_line():
    base = {"producer": {"node": "n11", "command": "{python} pipeline/assets.py", "code_sha256": "abc"}}
    flag = {"producer": {**base["producer"], "command": "{python} pipeline/assets.py --fast"}}
    assert producer_key(base) and producer_key(base) != producer_key(flag)
    assert not may_reuse(stamp({"take": "A10"}, base), flag)


def test_the_reuse_advisory_is_per_file_and_needs_the_real_import(tmp_path):
    """Review S3: SRA's assemble.py defines its own `stamp(t)`, which made every pipeline/ node look like it used vpg.reuse."""
    proj = make_project(tmp_path)
    (proj / "pipeline").mkdir()
    (proj / "pipeline" / "assets.py").write_text("from pathlib import Path\ndef stamp(t):\n    return t\n"
                                                 "old = sorted(Path('work/n11/').glob('a*/'))\n")   # its own stamp() is not the helper
    (proj / "pipeline" / "assemble.py").write_text("def stamp(t):\n    return f'{t:.3f}'\n")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["commands"] = {"a_intake": "{python} pipeline/assets.py", "d_build": "{python} pipeline/assemble.py"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    warnings = [w for w in Engine(proj, executor=FnExecutor()).status()["warnings"] if "vpg.reuse" in w]
    assert warnings == ["pipeline/assets.py (code of a_intake, d_build) appears to read earlier attempts' outputs without "
                        "vpg.reuse; key that reuse on the producer's code (vpg.reuse.may_reuse), or a fix to the producer is skipped"]
    (proj / "pipeline" / "assets.py").write_text("from vpg.reuse import may_reuse\nold = sorted(Path('work/n11/').glob('a*/'))\n")
    assert not [w for w in Engine(proj, read_only=True).status()["warnings"] if "vpg.reuse" in w]


def _one_version(tmp_path, **publish):
    proj = _publish_project(tmp_path)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["publish"].update({"keys": ["d_build"], **publish})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))

    def d_build(ctx):
        (ctx.workdir / "master.mp4").write_bytes(b"master-" + str(ctx.attempt).encode())
        return {"d_build": "master.mp4"}
    eng = Engine(proj, executor=FnExecutor({"d_build": d_build}))
    eng.run(max_steps=200)
    return proj, eng


def test_publish_never_overwrites_and_never_publishes_a_master_changed_since_it_was_registered(tmp_path):
    proj, eng = _one_version(tmp_path)
    (proj / "outputs/v1").mkdir(parents=True)
    (proj / "outputs/v1/master.mp4").write_bytes(b"a human's own file")
    assert publish(Engine(proj), version=1)["held"] == [1]
    assert (proj / "outputs/v1/master.mp4").read_bytes() == b"a human's own file"
    (proj / "outputs/v1/master.mp4").unlink()
    master = proj / next(a.path for a in eng.state.artifacts.values() if a.key == "d_build" and a.status == "current")
    master.write_bytes(b"edited after the version was cut")
    assert publish(Engine(proj), version=1)["held"] == [1] and not (proj / "outputs/v1/master.mp4").exists()
    index = json.loads((proj / "outputs/_vpg-index.json").read_text())
    assert index["versions"][0]["reason"].endswith("changed on disk since the version registered it")


def test_auto_publish_runs_after_the_save_and_only_for_the_version_just_cut(tmp_path):
    proj, eng = _one_version(tmp_path, auto=True)
    ledger = [json.loads(line) for line in open(proj / ".vpg/ledger.jsonl")]
    types = [e["type"] for e in ledger]
    passed = max(i for i, e in enumerate(ledger) if e["type"] == "node_passed" and e["data"]["node"] == "d_build")
    published = types.index("version_published")
    assert published > passed and ledger[published]["data"]["version"] == 1
    assert types.count("version_published") == 1


def test_publish_check_reads_without_the_lock_and_finds_an_edited_copy(tmp_path, capsys):
    proj, eng = _one_version(tmp_path)
    assert publish(Engine(proj))["published"] == [1]
    (proj / "outputs/v1/master.mp4").write_bytes(b"edited copy")
    from vpg.lock import project_lock
    with project_lock(proj, "another vpg process", timeout=1):
        assert cli.main(["publish", str(proj), "--check", "--lock-timeout", "0"]) == 0
    assert '"out_of_sync": [1]' in capsys.readouterr().out


def test_publish_scans_nothing_for_a_master_changed_since_it_was_registered(tmp_path, monkeypatch):
    proj = _publish_project(tmp_path, privacy_names=True)

    def d_build(ctx):
        (ctx.workdir / "master.mp4").write_bytes(b"master-v1")
        return {"d_build": "master.mp4"}
    eng = Engine(proj, executor=FnExecutor({"d_build": d_build}))
    eng.run(max_steps=200)
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["publish"]["keys"] = ["d_build"]
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    master = proj / next(a.path for a in eng.state.artifacts.values() if a.key == "d_build" and a.status == "current")
    master.write_bytes(b"edited after the version was cut")
    scans = []
    monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: scans.append(a) or {"windows": [], "live_windows": []})
    assert publish(Engine(proj), version=1)["held"] == [1] and scans == []   # never scans (or passes) other bytes
    index = json.loads((proj / "outputs/_vpg-index.json").read_text())
    assert index["versions"][0]["reason"] == "the master changed on disk since the version registered it"


def test_publish_dir_must_stay_inside_the_project():
    from vpg.publish import config_problems
    assert config_problems({"dir": "../outputs"}) and config_problems({"dir": "renders/../../x"})
    assert not config_problems({"dir": "outputs/graph"})
