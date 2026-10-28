"""The packaged example runs end to end with real local media (the brief's 'representative dry run')."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vpg.dryrun import dry_run
from vpg.engine import EngineError
from vpg.util import PKG_ROOT


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="the dry run renders real media with ffmpeg")
def test_dry_run_end_to_end(tmp_path):
    report = dry_run(out=str(tmp_path / "run"), keep=True)
    failed = [k for k, v in report["checks"].items() if not v]
    assert report["ok"], failed
    assert report["rework_cycles"] == 1 and report["versions"] == 2 and report["resumes"] == 1


def test_dry_run_never_deletes_an_unrelated_directory(tmp_path):
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "notes.md").write_text("mine")
    with pytest.raises(EngineError, match="refusing to replace"):
        dry_run(out=str(precious))
    assert (precious / "notes.md").read_text() == "mine"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="the dry run renders real media with ffmpeg")
def test_dry_run_without_macos_speech_is_explicitly_degraded(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (bindir / name).symlink_to(Path(shutil.which(name)).resolve())
    monkeypatch.setenv("PATH", str(bindir))
    report = dry_run(out=str(tmp_path / "run"), keep=True)
    assert report["ok"]
    assert report["voice_engine"] == "tone fallback"
    import json
    outputs = json.loads((tmp_path / "run/work/n12_voice_audio/a1/outputs.json").read_text())
    assert outputs["data"]["degraded"] is True
    seams = json.loads((tmp_path / "run/work/n12_voice_audio/a1/voice-seams-report.json").read_text())
    assert seams["units"] and all(set(u["flags"]) <= {"HARD_ONSET"} for u in seams["units"])
    state = json.loads((tmp_path / "run/.vpg/state.json").read_text())
    assert all(not a["human"] for a in state["history"]["approvals"])


def test_failed_dry_run_reports_the_blocked_node(tmp_path, monkeypatch):
    import vpg.dryrun as module
    example = tmp_path / "example"
    shutil.copytree(PKG_ROOT / "examples/sample-demo", example,
                    ignore=shutil.ignore_patterns("__pycache__", ".vpg", "work"))
    (example / "pipeline/voice.py").write_text("raise RuntimeError('synthetic voice failure')\n")
    monkeypatch.setattr(module, "EXAMPLE", example)
    with pytest.raises(EngineError, match="BLOCKED.*n12_voice_audio"):
        dry_run(out=str(tmp_path / "run"), keep=True)
