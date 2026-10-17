"""Deterministic tools: every rule-backed check detects its target failure (and passes the clean case)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from tests.conftest import PKG
from vpg.models import ArtifactRecord
from vpg.tools import accuracy, assets, audio, captions, design, legibility, media, pacing
from vpg.tools._common import contrast

EXAMPLE = PKG / "examples" / "sample-demo"
FFMPEG = shutil.which("ffmpeg")


class Ctx:
    """Minimal NodeContext stand-in: JSON artifacts by key, control front-matter from the example project."""

    def __init__(self, tmp: Path, loads: dict | None = None, paths: dict | None = None, fm: dict | None = None, asset_plan=None):
        self.workdir, self.project_dir = tmp / "work", EXAMPLE
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._loads, self._paths, self._fm = loads or {}, paths or {}, fm or {}
        self.state = SimpleNamespace(production=SimpleNamespace(asset_plan=asset_plan or []))
        self.node = SimpleNamespace(outputs=[])

    def load(self, key):
        return self._loads.get(key)

    def artifact_path(self, key):
        return self._paths.get(key)

    def fm(self, doc):
        from vpg.controls import split_front_matter
        base = split_front_matter((EXAMPLE / "controls" / f"{doc}.md").read_text())[0]
        return {**base, **self._fm.get(doc, {})}

    def control_path(self, doc):
        return EXAMPLE / "controls" / f"{doc}.md"


def layer(**kw):
    base = {"id": "L1", "scene": "B1", "kind": "text", "text": "Expected range", "font": "Mona Sans", "font_px": 38, "bold": False,
            "color": "#1A1F26", "bg": "#FFFFFF", "bbox": [200, 300, 400, 50], "start": 0.0, "end": 5.0, "opacity": 1.0, "essential": True}
    base.update(kw)
    return base


def dims(result):
    return sorted({f.dimension for f in result.findings})


# ------------------------------------------------------------------------------------------------------ captions
def test_caption_sync_detects_lag_and_early_clear():
    rules = {"max_lag_s": 0.0, "max_chain_lead_s": 0.2, "max_first_lead_s": 0.45, "max_early_clear_s": 0.1, "max_cps": 20, "min_duration_s": 0.8}
    spans = [{"unit": "u1", "text": "Every plant reports water use.", "start": 1.0, "end": 3.0}]
    good = captions.sync([{"start": 0.8, "end": 3.2, "text": "Every plant reports water use."}], spans, rules)
    assert good["lags"] == 0 and not good["findings"]
    late = captions.sync([{"start": 1.3, "end": 3.2, "text": "Every plant reports water use."}], spans, rules)
    assert late["lags"] == 1 and any(f.check == "captions:lag" for f in late["findings"])
    early = captions.sync([{"start": 0.8, "end": 2.5, "text": "Every plant reports water use."}], spans, rules)
    assert any(f.check == "captions:early-clear" for f in early["findings"])
    fast = captions.sync([{"start": 0.9, "end": 1.2, "text": "Every plant reports water use."}], [{**spans[0], "end": 1.2}], rules)
    assert any(f.check == "captions:rate" for f in fast["findings"])


def test_srt_roundtrip():
    cues = captions.parse_srt("1\n00:00:00,400 --> 00:00:04,992\nEvery plant\n\n2\n00:00:05,052 --> 00:00:10,609\nNext line\n")
    assert [c["start"] for c in cues] == [0.4, 5.052] and cues[1]["text"] == "Next line"


# ---------------------------------------------------------------------------------------------------- legibility
def _legibility(tmp, layers, captions_path=None, spans=None, master=None):
    ctx = Ctx(tmp, loads={"text_layers": {"frame": {"w": 1920, "h": 1080}, "layers": layers}, "timings": {"units": spans or []}},
              paths={"captions": captions_path, "master": master})
    return legibility.check(ctx)


def test_legibility_flags_small_low_contrast_short_ghost(tmp_path):
    clean = _legibility(tmp_path, [layer()])
    assert not clean.findings
    bad = _legibility(tmp_path, [
        layer(id="small", font_px=14),
        layer(id="lowc", color="#9AA5B1", bg="#FFFFFF"),
        layer(id="short", text="A long label with many words to read here", start=0, end=0.6),
        layer(id="ghost", opacity=0.5),
        layer(id="edge", bbox=[20, 300, 400, 50]),
        layer(id="band", bbox=[200, 940, 400, 50]),
        {"id": "ring", "scene": "B1", "kind": "ring", "bbox": [0, 0, 10, 10], "start": 0, "end": 0.4},
    ], captions_path=tmp_path / "c.srt")
    assert {"LEG-SIZE", "LEG-CONTRAST", "LEG-DWELL", "LEG-GHOST", "LEG-SAFE", "LEG-RING"} <= set(dims(bad))


def test_density_limit(tmp_path):
    many = [layer(id=f"L{i}", text=f"label {i}", bbox=[200, 100 + i * 60, 300, 40]) for i in range(9)]
    assert "LEG-DENSITY" in dims(_legibility(tmp_path, many))
    assert "LEG-DENSITY" not in dims(_legibility(tmp_path, many[:8]))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for media checks")
def test_flash_detection(tmp_path):
    strobe = tmp_path / "strobe.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=2", "-vf",
                    "geq=lum='if(lt(mod(N\\,6)\\,3)\\,255\\,0)':cb=128:cr=128,format=yuv420p", "-c:v", "libx264", str(strobe)], check=True)
    calm = tmp_path / "calm.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(calm)], check=True)
    assert "LEG-FLASH" in dims(_legibility(tmp_path, [layer()], master=strobe))
    assert "LEG-FLASH" not in dims(_legibility(tmp_path, [layer()], master=calm))


def test_contrast_matches_wcag_reference():
    assert contrast("#FFFFFF", "#000000") == 21.0
    assert contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)   # the classic just-failing grey


# ------------------------------------------------------------------------------------------------------- design
def test_design_compliance_flags_font_logo_palette(tmp_path):
    ok = design.compliance(Ctx(tmp_path, loads={"text_layers": {"layers": [layer(), {
        "id": "logo", "scene": "B1", "kind": "logo", "asset": "assets/brand/northwind-mark.png", "bbox": [120, 120, 128, 128], "start": 0, "end": 5}]}}))
    assert not ok.findings
    bad = design.compliance(Ctx(tmp_path, loads={"text_layers": {"layers": [
        layer(id="font", font="Inter"), layer(id="color", color="#FFBF00"),
        {"id": "squash", "scene": "B1", "kind": "logo", "asset": "assets/brand/northwind-mark.png", "bbox": [120, 120, 200, 100], "start": 0, "end": 5},
        {"id": "fake", "scene": "B1", "kind": "logo", "asset": "assets/brand/redrawn.png", "bbox": [0, 0, 10, 10], "start": 0, "end": 5}]}}))
    assert {"DES-TYPE", "DES-TOKENS", "DES-PROHIBITED", "DES-LOGO"} <= set(dims(bad))
    assert sum(f.dimension == "DES-LOGO" for f in bad.findings) == 2   # deformation + unapproved asset


def test_palette_check_rejects_low_contrast_pair(tmp_path):
    ctx = Ctx(tmp_path, fm={"design": {"text_pairs": [{"fg": "mist", "bg": "paper", "use": "body"}]}})
    res = design.check_palette(ctx)
    assert not res.ok and "below 4.5:1" in res.messages[0]


# ------------------------------------------------------------------------------------------------------- assets
def test_asset_plan_rules(tmp_path):
    good = {"id": "A1", "beats": ["B1"], "role": "diagram", "source_type": "generated", "rights": "generated-internal",
            "spec": {"w": 10, "h": 10, "format": "png"}, "subject": "one plant's water-use line leaving its expected band"}
    board = {"beats": [{"id": "B1"}]}
    assert assets.check_plan(Ctx(tmp_path, loads={"asset_plan": {"assets": [good]}, "storyboard": board})).ok
    bad = [dict(good, id="ui", role="product-ui", source_type="generated"),
           dict(good, id="logo", role="logo", source_type="generated"),
           dict(good, id="generic", subject="abstract background"),
           dict(good, id="rights", rights="needs-clearance")]
    res = assets.check_plan(Ctx(tmp_path, loads={"asset_plan": {"assets": bad}, "storyboard": board}))
    text = " ".join(res.messages)
    assert "never generated" in text and "never regenerated" in text and "too generic" in text and "clearance" in text


# ----------------------------------------------------------------------------------------------------- accuracy
def _script(text, claims=(), terms=()):
    return {"chapters": [{"id": "c", "title": "t", "objective": "o", "units": [{"id": "u1", "text": text, "claims": list(claims), "terms": list(terms)}]}]}


def test_accuracy_flags_prohibited_and_unverified(tmp_path):
    assert accuracy.check_script(Ctx(tmp_path, loads={"script": _script("A seven-day outlook helps.", ["C02"], ["seven-day outlook"])})).ok
    res = accuracy.check_script(Ctx(tmp_path, loads={"script": _script("It predicts demand.", ["C99"], ["magic score"])}))
    text = " ".join(res.messages)
    assert "prohibited term 'predicts'" in text and "C99" in text and "magic score" in text


def test_accuracy_flags_generated_ui(tmp_path):
    ctx = Ctx(tmp_path, loads={"script": _script("A seven-day outlook helps.", ["C02"]), "text_layers": {"layers": [layer(text="It predicts water use")]}},
              asset_plan=[{"id": "A3", "role": "product-ui", "source_type": "generated"}])
    res = accuracy.check_film(ctx)
    assert {"ACC-UI", "ACC-TERMS"} <= set(dims(res))
    assert next(f for f in res.findings if f.dimension == "ACC-UI").severity == "P1"


# ------------------------------------------------------------------------------------------------------- pacing
def test_runtime_estimate_blocks_over_cap(tmp_path):
    short = pacing.estimate_script(Ctx(tmp_path, loads={"script": _script("Every plant reports its water use."), "brief": {"runtime_max_s": 20}}))
    assert short.ok
    long = pacing.estimate_script(Ctx(tmp_path, loads={"script": _script(" ".join(["word"] * 80)), "brief": {"runtime_max_s": 20}}))
    assert not long.ok and "exceeds" in long.messages[0]


# -------------------------------------------------------------------------------------------------------- voice
def _wav(path: Path, seconds: float, silent_from: float | None = None):
    import wave
    import numpy as np
    sr = 48000
    t = np.arange(int(sr * seconds)) / sr
    x = 0.4 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    if silent_from is not None:
        x[int(silent_from * sr):] = 0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x * 32767).astype("int16").tobytes())


def _voice_ctx(tmp, wav, spans, direction):
    return Ctx(tmp, loads={"timings": {"units": spans}, "script": _script("one two three"), "voice_direction": direction}, paths={"voice": wav})


def test_voice_direction_requires_adult(tmp_path):
    wav = tmp_path / "v.wav"
    _wav(wav, 3)
    spans = [{"unit": "u1", "text": "one two three", "start": 0.2, "end": 2.8}]
    ok = audio.check_voice(_voice_ctx(tmp_path, wav, spans, {"age": "adult", "delivery": "warm, confident professional", "human_listening": "pending"}))
    assert ok.ok, ok.messages
    kid = audio.check_voice(_voice_ctx(tmp_path, wav, spans, {"age": "child", "delivery": "playful", "human_listening": "pending"}))
    assert not kid.ok and "adult" in kid.messages[0]
    claimed = audio.check_voice(_voice_ctx(tmp_path, wav, spans, {"age": "adult", "delivery": "warm, confident professional"}))
    assert not claimed.ok and "listening" in " ".join(claimed.messages)


def test_voice_check_catches_dropped_unit(tmp_path):
    wav = tmp_path / "v.wav"
    _wav(wav, 3, silent_from=1.0)                       # speech drops out after 1 s inside the unit
    spans = [{"unit": "u1", "text": "one two three", "start": 0.2, "end": 2.8}]
    res = audio.check_voice(_voice_ctx(tmp_path, wav, spans, {"age": "adult", "delivery": "warm, confident professional", "human_listening": "pending"}))
    assert not res.ok and "near-silent" in " ".join(res.messages)
    missing = audio.check_voice(_voice_ctx(tmp_path, wav, [], {"age": "adult", "delivery": "warm, confident professional", "human_listening": "pending"}))
    assert "do not cover the script units" in missing.messages[0]


# -------------------------------------------------------------------------------------------------- technical QA
def _master(tmp: Path, name: str, size="1920x1080", silence_at: float | None = None, lufs=-16) -> Path:
    tone = tmp / f"{name}.wav"
    expr = "0.3*sin(2*PI*220*t)" if silence_at is None else f"if(between(t\\,{silence_at}\\,{silence_at + 2.5})\\,0\\,0.3*sin(2*PI*220*t))"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=48000:d=6", "-ac", "2", "-af",
                    f"loudnorm=I={lufs}:TP=-2:LRA=7", "-ar", "48000", str(tone)], check=True)
    out = tmp / f"{name}.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x0B5CAD:s={size}:r=30:d=6", "-i", str(tone), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(out)], check=True)
    return out


def _qa(tmp, master, planned=()):
    srt = tmp / "m.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:03,000\nhello\n")
    ctx = Ctx(tmp, loads={"timings": {"units": [], "planned_silences": list(planned)}, "brief": {"runtime_max_s": 20}},
              paths={"master": master, "captions": srt})
    return media.technical_qa(ctx)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for media checks")
def test_technical_qa_on_real_media(tmp_path):
    good = _qa(tmp_path, _master(tmp_path, "good"))
    assert not good.findings, [f.message for f in good.findings]
    wrong = _qa(tmp_path, _master(tmp_path, "small", size="1280x720", lufs=-24))
    assert {"TEC-FORMAT", "TEC-LOUDNESS"} <= set(dims(wrong))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for media checks")
def test_technical_qa_flags_unexplained_silence(tmp_path):
    m = _master(tmp_path, "gap", silence_at=2.0)
    assert "TEC-SILENCE" in dims(_qa(tmp_path, m))
    assert "TEC-SILENCE" not in dims(_qa(tmp_path, m, planned=[{"start": 2.0, "end": 4.5, "why": "planned pause"}]))
