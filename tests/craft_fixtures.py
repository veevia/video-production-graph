"""Synthetic films for the craft-metric tests: a "good" film and degraded variants, generated deterministically.

Nothing here comes from a real production. The good film has a muted ground with one coloured accent, motion about a
fifth of the time, pauses about a fifth of the spoken span and music well under the voice. Each degraded film breaks
one or more of those the way a regressed version did: a saturated gradient as every frame's ground, motion most of
the time, almost no pauses, music close to the voice, labels that cross during a transition.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

FFMPEG = shutil.which("ffmpeg")
W, H, FPS, SECONDS, RATE = 480, 270, 25, 12.0, 48000


def _encode(frames, out: Path) -> Path:
    proc = subprocess.Popen([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                             "-pix_fmt", "yuv420p", "-x264-params", "threads=1:sliced-threads=0", str(out)],
                            stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for fr in frames:
        proc.stdin.write(np.ascontiguousarray(fr, dtype=np.uint8).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return out


def _ground(kind: str, t: float) -> np.ndarray:
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    if kind == "muted":
        base = np.zeros((H, W, 3), np.float32)
        base[...] = (30, 35, 42)
        base += ((x / W) * 6.0)[..., None]   # a faint, almost colourless vignette
        return base
    # a saturated diagonal gradient whose hue drifts over time
    phase = (x / W + y / H) * np.pi + t * 0.9
    return np.stack([128 + 127 * np.sin(phase), 128 + 127 * np.sin(phase + 2.1), 128 + 127 * np.sin(phase + 4.2)], -1)


def film(out: Path, *, ground: str = "muted", motion: float = 0.2, seed: int = 0) -> Path:
    """Text-like bars hold on screen; a card slides for `motion` of the runtime (split into short moves)."""
    rng = np.random.default_rng(seed)
    n = int(SECONDS * FPS)
    moving = np.zeros(n, dtype=bool)
    moves = max(1, int(round(motion * 6)))
    span = int(round(motion * n / moves))
    starts = np.linspace(0, n - span - 1, moves + 2)[1:-1].astype(int)
    for s in starts:
        moving[s:s + span] = True
    bars = [(int(rng.integers(40, 200)), int(rng.integers(30, 220)), int(rng.integers(80, 220)), 14) for _ in range(5)]

    def frames():
        card_x = 60.0
        drift_t = 0.0
        for k in range(n):
            if moving[k]:
                card_x = (card_x + 6.0) % (W - 120)
                drift_t += 1.0 / FPS
            fr = _ground(ground, drift_t if ground != "muted" else 0.0)
            for (bx, by, bw, bh) in bars:
                fr[by:by + bh, bx:bx + bw] = (235, 238, 242)
            cx = int(card_x)
            fr[190:250, cx:cx + 110] = (255, 196, 0) if ground == "muted" else (250, 250, 250)   # one coloured accent
            yield np.clip(fr, 0, 255)
    return _encode(frames(), out)


def _write_wav(path: Path, x: np.ndarray) -> Path:
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())
    return path


def voice(out: Path, *, pause_s: tuple[float, float] = (0.55, 0.9), phrase_s: tuple[float, float] = (1.6, 2.6),
          seconds: float = SECONDS, seed: int = 1) -> Path:
    """Phrases of a harmonic 'voice' with syllable-rate modulation, separated by silent pauses."""
    rng = np.random.default_rng(seed)
    x = np.zeros(int(seconds * RATE), np.float32)
    t = 0.3
    while t < seconds - 0.5:
        dur = float(rng.uniform(*phrase_s))
        a, b = int(t * RATE), int(min(seconds - 0.2, t + dur) * RATE)
        tt = np.arange(b - a) / RATE
        f0 = 140.0
        tone = sum(np.sin(2 * np.pi * f0 * h * tt) / h for h in range(1, 8))
        env = 0.45 + 0.55 * np.abs(np.sin(2 * np.pi * 2.2 * tt))
        x[a:b] = 0.12 * tone * env
        t += dur + float(rng.uniform(*pause_s))
    x += 1e-4 * rng.standard_normal(len(x)).astype(np.float32)   # a -80 dBFS noise floor, so silence is not digital zero
    return _write_wav(out, x)


def music(out: Path, *, gain_db: float = -26.0, seconds: float = SECONDS) -> Path:
    tt = np.arange(int(seconds * RATE)) / RATE
    pad = sum(np.sin(2 * np.pi * f * tt) for f in (220.0, 277.18, 329.63, 440.0)) / 4
    return _write_wav(out, (10 ** (gain_db / 20)) * pad * (0.8 + 0.2 * np.sin(2 * np.pi * 0.25 * tt)))


def text_layers(out: Path, *, crossing: bool = False) -> Path:
    """Two labels that take turns in one spot; `crossing` makes their fades overlap (the held spans never do)."""
    layers = [
        {"id": "title", "scene": "S1", "kind": "text", "text": "Signals", "font_px": 48, "bbox": [96, 80, 520, 70],
         "start": 0.5, "end": 4.0, "fade_out_s": 0.2},
        {"id": "label-a", "scene": "S2", "kind": "text", "text": "Evidence", "font_px": 30, "bbox": [96, 300, 420, 44],
         "start": 4.5, "end": 7.0, "fade_out_s": 0.6 if crossing else 0.2},
        {"id": "label-b", "scene": "S2", "kind": "text", "text": "Case", "font_px": 30, "bbox": [200, 310, 420, 44],
         "start": 7.4, "end": 10.0, "fade_in_s": 0.6 if crossing else 0.2},
        {"id": "footer", "scene": "S3", "kind": "text", "text": "Closing", "font_px": 26, "bbox": [96, 900, 600, 40],
         "start": 10.2, "end": 11.8},
    ]
    out.write_text(json.dumps({"frame": {"w": 1920, "h": 1080}, "layers": layers}))
    return out


NARRATION = ("Every signal starts as a question from the people closest to the work. The platform gathers the evidence, "
             "shows the reasoning and hands the decision back to the expert who owns it.")
# the same story read faster: more words in the same spoken span
NARRATION_DENSE = NARRATION + (" It routes each case, drafts the plan, tracks every approval and keeps the audit trail "
                               "current without asking anyone to stop.")


def narration(out: Path, dense: bool = False) -> Path:
    out.write_text(NARRATION_DENSE if dense else NARRATION)
    return out


def ocr_clip(out: Path, *, text_px: int, seconds: float = 4.0, columns: int = 1) -> Path:
    """A 1280x720 clip of UI-like rows of real words at `text_px` (in one or two columns), for the player-width OCR
    metrics."""
    from PIL import Image, ImageDraw, ImageFont
    font_path = Path(__file__).resolve().parents[1] / "examples/sample-demo/assets/fonts/MonaSansVF.ttf"
    font = ImageFont.truetype(str(font_path), text_px)
    img = Image.new("RGB", (1280, 720), (245, 246, 248))
    d = ImageDraw.Draw(img)
    rows = ["Signal review queue", "Evidence attached to case", "Owner approval pending", "Plan drafted for review",
            "Audit trail updated today", "Governance step complete"]
    for c in range(columns):
        for i, row in enumerate(rows):
            d.text((80 + c * 600, 60 + i * (text_px * 2 + 24)), row, fill=(20, 24, 30), font=font)
    still = np.asarray(img)
    w, h = 1280, 720
    proc = subprocess.Popen([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(out)],
                            stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for _ in range(int(seconds * FPS)):
        proc.stdin.write(still.tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return out


def good_set(d: Path, seed: int = 0) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    return {"master": film(d / "master.mp4", ground="muted", motion=0.2, seed=seed),
            "voice": voice(d / "voice.wav", seed=seed + 1), "music": music(d / "music.wav", gain_db=-28.0 - seed),
            "text_layers": text_layers(d / "text-layers.json"), "narration": narration(d / "narration.txt")}


def degraded_set(d: Path) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    return {"master": film(d / "master.mp4", ground="saturated", motion=0.6, seed=7),
            "voice": voice(d / "voice.wav", pause_s=(0.3, 0.34), phrase_s=(3.0, 4.2), seed=8),
            "music": music(d / "music.wav", gain_db=-17.0), "text_layers": text_layers(d / "text-layers.json", crossing=True),
            "narration": narration(d / "narration.txt", dense=True)}
