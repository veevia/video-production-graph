"""A quality-workflow copy of the sample production for the P4 acceptance tests: the fictional sample, `workflow: quality`,
a synthetic benchmark registry with style frames for every look role (pinned in GATES.yaml), and scripted outputs for the
two new gates. Nothing here comes from a real production.

`look="good"` renders style frames like the references (a quiet ground, one accent); `look="bad"` renders every frame on
a saturated gradient, the way a look regresses when nothing measures it before the build.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from tests import craft_fixtures as cf
from tests.conftest import PKG
from vpg import benchmarks
from vpg.spec import family_hash, load_family
from vpg.util import sha256_file

ROLES = ["title", "product_ui", "data_callout", "closing"]
W, H = 1920, 1080


def still(out: Path, *, ground: str, role: str, seed: int = 0) -> Path:
    """A 1920x1080 style frame: text-like bars and one accent chip on a quiet ground, or on a saturated gradient."""
    rng = np.random.default_rng(seed + ROLES.index(role) * 17)
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    if ground == "muted":
        img = np.zeros((H, W, 3), np.float32)
        img[...] = (30 + seed, 35 + seed, 42 + seed)
        img += ((x / W) * 6.0)[..., None]
    else:
        phase = (x / W + y / H) * np.pi + seed
        img = np.stack([128 + 127 * np.sin(phase), 128 + 127 * np.sin(phase + 2.1), 128 + 127 * np.sin(phase + 4.2)], -1)
    for i in range(3 + ROLES.index(role)):
        x0, y0 = int(rng.integers(120, 900)), 160 + i * 140
        img[y0:y0 + 36, x0:x0 + int(rng.integers(300, 760))] = (225, 228, 232)
    cx, cy = int(rng.integers(1100, 1500)), int(rng.integers(500, 800))
    img[cy:cy + 90, cx:cx + 260] = (0, 168, 150)   # the one accent
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(out)
    return out


def motion_test(out: Path, seconds: float = 5.0) -> Path:
    subprocess.run([cf.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x1e232a:s=640x360:r=25:d={seconds}",
                    "-vf", "drawbox=x='40+t*60':y=150:w=120:h=40:color=0x00a896:t=fill", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-x264-params", "threads=1:sliced-threads=0", str(out)], check=True)
    return out


GATES = """# Test gates (P4 acceptance): look gates on stills, animatic gates on the scratch read, build gates on the master.
spec: {{family: vpg-spec/v1, doc: gates, version: 1}}
benchmarks: {{path: ../benchmarks/registry.json, sha256: {sha}}}
player_width_px: 960
waivers: []
gates:
  - {{id: GT-GROUND-001, metric: still.ground_colourfulness.median, version: 1, envelope: benchmark, severity: P2, stage: look, rules: [DS-GROUND-001]}}
  - {{id: GT-COLOUR-001, metric: still.colourfulness.median, version: 1, envelope: benchmark, severity: P2, stage: look, rules: [DS-GROUND-001]}}
  - {{id: GT-UI-001, metric: still.ocr_word_px.median, version: 1, envelope: benchmark, severity: nit, stage: look, rules: [DS-UI-001], roles: [product_ui]}}
  - {{id: GT-GROUND-002, metric: video.ground_colourfulness.median, version: 1, envelope: benchmark, severity: P2, stage: build, rules: [DS-GROUND-001], ratchet: true}}
  - {{id: GT-MOTION-001, metric: video.motion_share, version: 1, envelope: benchmark, severity: P3, stage: build, rules: [MO-SHARE-001], ratchet: true}}
  - {{id: GT-OVERLAP-001, metric: text.overlap_all.count, version: 2, envelope: {{hi: 0}}, severity: P1, stage: build, rules: [DS-TEXT-001]}}
  - {{id: GT-PACE-001, metric: audio.pace_wpm, version: 1, envelope: {{lo: 30, hi: 600}}, severity: P2, stage: animatic, rules: [VO-PACE-001]}}
  - {{id: GT-PAUSE-001, metric: audio.pause_share, version: 1, envelope: benchmark, severity: P2, stage: animatic, rules: [VO-PAUSE-001]}}
  - {{id: GT-VOM-001, metric: audio.vo_over_music.p10, version: 2, envelope: benchmark, severity: P1, stage: animatic, rules: [MX-VOM-001]}}
  - {{id: GT-VOM-003, metric: audio.vo_over_music.median, version: 2, envelope: benchmark, severity: P2, stage: animatic, rules: [MX-VOM-002]}}
  - {{id: GT-VOM-004, metric: audio.vo_over_music.median, version: 2, envelope: benchmark, severity: P2, stage: build, rules: [MX-VOM-002], ratchet: true}}
"""


def registry(bench: Path) -> Path:
    """Two references, each a good synthetic film with its own style frames for every role, and an accepted baseline
    (a named human's previous best) for the ratchet."""
    bench.mkdir(parents=True, exist_ok=True)
    path = bench / "registry.json"
    for i, ref in enumerate(("REF-A", "REF-B")):
        media = cf.good_set(bench / ref, seed=i)
        frames = [(r, still(bench / ref / f"{r}.png", ground="muted", role=r, seed=i)) for r in ROLES]
        benchmarks.add_entry(path, ref, "reference", media, by="Wei", style_frames=frames)
    benchmarks.add_entry(path, "BEST-1", "previous_best", cf.good_set(bench / "BEST-1", seed=2), by="Wei",
                         reason="the accepted synthetic version")
    return path


def music_with_hits(out: Path, hits: list[float], gain_db: float = -28.5, hit_db: float = -16.0) -> Path:
    """The synthetic pad with a short percussive hit (a decaying noise burst) at each time: a measurable onset."""
    import wave
    cf.music(out, gain_db=gain_db)
    with wave.open(str(out)) as w:
        rate, x = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    rng = np.random.default_rng(11)
    burst = rng.standard_normal(int(0.12 * rate)) * np.exp(-np.arange(int(0.12 * rate)) / (0.025 * rate)) * 10 ** (hit_db / 20)
    for t in hits:
        a = int(t * rate)
        x[a:a + len(burst)] += burst[:max(0, len(x) - a)]
    return cf._write_wav(out, x)


def _fixture(proj: Path, node: str, files: dict[str, Path], outputs: dict[str, str]) -> Path:
    d = proj / "fixtures" / node / "attempt-1"
    d.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        if src.parent != d:
            shutil.copy2(src, d / name)
    (d / "outputs.json").write_text(json.dumps({"outcome": "pass", "outputs": outputs}, indent=1))
    return d


def quality_project(tmp: Path, look: str = "good", registry_dir: Path | None = None) -> Path:
    proj = tmp / "quality-demo"
    shutil.copytree(PKG / "examples" / "sample-demo", proj, ignore=shutil.ignore_patterns("work", ".vpg", "__pycache__"))
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update(workflow="quality", observability={"evaluation_events": True})
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    if registry_dir is not None:   # measured once per test session (it is the slow part)
        shutil.copytree(registry_dir, proj / "benchmarks")
        reg = proj / "benchmarks" / "registry.json"
    else:
        reg = registry(proj / "benchmarks")
    (proj / "spec" / "GATES.yaml").write_text(GATES.format(sha=sha256_file(reg)))
    spec_hash = family_hash(load_family(proj / "spec"))

    d = proj / "fixtures" / "n26_look_development" / "attempt-1"
    d.mkdir(parents=True)
    frames = [still(d / f"{r}.png", ground="muted" if look == "good" else "saturated", role=r, seed=3) for r in ROLES]
    motion_test(d / "motion-test.mp4")
    (d / "look-frames.json").write_text(json.dumps({
        "spec_hash": spec_hash, "renderer": "synthetic test frames",
        "frames": [{"role": r, "file": p.name, "sha256": sha256_file(p)} for r, p in zip(ROLES, frames)],
        "motion_test": {"file": "motion-test.mp4", "shows": ["enter", "hold", "exit", "one transition"]}}, indent=1))
    _fixture(proj, "n26_look_development", {}, {"look_frames": "look-frames.json"})

    d = proj / "fixtures" / "n27_animatic" / "attempt-1"
    d.mkdir(parents=True)
    cf.voice(d / "scratch.wav", seed=5)
    music_with_hits(d / "music.wav", hits=[11.0])
    cut = [0.0, 3.96, 7.92, cf.SECONDS]
    (d / "animatic.json").write_text(json.dumps({
        "spec_hash": spec_hash, "voice": "scratch.wav", "music": "music.wav",
        "beats": [{"id": f"B{i + 1}", "chapter": f"ch{i + 1}", "start": cut[i], "end": cut[i + 1]} for i in range(3)]}, indent=1))
    _fixture(proj, "n27_animatic", {}, {"animatic": "animatic.json"})
    return proj
