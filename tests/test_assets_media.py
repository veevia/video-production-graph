"""A recorded prototype walkthrough must be a first-class asset (first real run, #32/#45: N11 refused a take manifest as
"not a readable image", so the take became a poster still plus an unregistered sidecar, and downstream memoisation
reused a stale render after the take was re-recorded)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from vpg.tools import assets

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def _ctx(project, plan, files):
    recs = [NS(item=aid, path=rel) for aid, rel in files.items()]
    return NS(state=NS(production=NS(asset_plan=plan)), current=lambda key: recs, project_dir=project)


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _take(project):
    wd = project / "work/n11_asset_generation/a1"
    (wd / "A10/stills").mkdir(parents=True)
    frames = []
    for i, (size, colour) in enumerate((((64, 36), "white"), ((64, 36), "black"), ((32, 18), "red"))):
        p = wd / f"A10/stills/s{i}.png"
        Image.new("RGB", size, colour).save(p)
        frames.append(p)
    manifest = {"schema": "take/1", "states": [{"id": f"s{i}", "file": f"A10/stills/s{i}.png", "sha256": _sha(p)}
                                               for i, p in enumerate(frames[:2])],
                "screencast": {"frames": [{"i": 0, "file": "A10/stills/s2.png", "sha256": _sha(frames[2])}]}}
    (wd / "A10.take.json").write_text(json.dumps(manifest))
    return wd, frames


def test_a_recording_manifest_is_a_hash_checked_asset(tmp_path):
    project = tmp_path / "proj"
    wd, frames = _take(project)
    files = {"A10": "work/n11_asset_generation/a1/A10.take.json"}
    ctx = _ctx(project, [{"id": "A10", "spec": {"w": 64, "h": 36, "format": "take"}}], files)
    assert assets.check_generated(ctx).ok
    wrong_size = _ctx(project, [{"id": "A10", "spec": {"w": 3840, "h": 2160, "format": "take"}}], files)
    assert "lists no frame at the spec size 3840x2160" in assets.check_generated(wrong_size).messages[0]
    Image.new("RGB", (32, 18), "blue").save(frames[2])                  # a screencast frame changed after the take
    res = assets.check_generated(ctx)
    assert not res.ok and "A10/stills/s2.png no longer matches its sha256" in res.messages[0]
    frames[0].unlink()
    assert any("A10/stills/s0.png is missing" in m for m in assets.check_generated(ctx).messages)


def test_a_recording_cannot_reach_outside_the_project_skip_hashes_or_list_nothing(tmp_path):
    project = tmp_path / "proj"
    wd, _ = _take(project)
    outside = tmp_path / "outside.png"                                  # inside the test's folder, outside the project
    Image.new("RGB", (64, 36)).save(outside)
    plan = [{"id": "A10", "spec": {"w": 64, "h": 36, "format": "recording"}}]
    ctx = _ctx(project, plan, {"A10": "work/n11_asset_generation/a1/A10.take.json"})
    (wd / "A10.take.json").write_text(json.dumps({"states": [{"file": "../../../../outside.png", "sha256": _sha(outside)}]}))
    assert "points outside the project" in assets.check_generated(ctx).messages[0]
    (wd / "A10.take.json").write_text(json.dumps({"states": [{"file": "A10/stills/s0.png"}]}))
    assert "listed without a sha256" in assets.check_generated(ctx).messages[0]
    (wd / "A10.take.json").write_text(json.dumps({"states": []}))
    assert "lists no file" in assets.check_generated(ctx).messages[0]


@needs_ffmpeg
def test_a_video_asset_is_checked_with_ffprobe(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    ok = _ctx(tmp_path, [{"id": "A11", "spec": {"w": 320, "h": 180, "format": "mp4", "duration_s": 1}}], {"A11": "clip.mp4"})
    assert assets.check_generated(ok).ok
    wrong = _ctx(tmp_path, [{"id": "A11", "spec": {"w": 640, "h": 360, "format": "mp4"}}], {"A11": "clip.mp4"})
    assert "is 320x180, spec is 640x360" in assets.check_generated(wrong).messages[0]
    monkeypatch.setattr(assets, "FFPROBE", "/nonexistent/ffprobe")
    assert "ffprobe is not installed" in assets.check_generated(ok).messages[0]


def test_images_are_still_checked_as_images(tmp_path):
    Image.new("RGB", (64, 36)).save(tmp_path / "a.png")
    ctx = _ctx(tmp_path, [{"id": "A01", "spec": {"w": 64, "h": 36, "format": "png"}}], {"A01": "a.png"})
    assert assets.check_generated(ctx).ok
    (tmp_path / "a.json").write_text("{}")
    bad = _ctx(tmp_path, [{"id": "A01", "spec": {"w": 64, "h": 36, "format": "png"}}], {"A01": "a.json"})
    assert "not a readable image" in assets.check_generated(bad).messages[0]
