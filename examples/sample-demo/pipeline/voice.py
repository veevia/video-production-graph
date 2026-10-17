"""N12 command: render narration unit by unit, trim, place it with planned pauses, and measure the timings.

Uses macOS `say` (the example only; a real production binds an identity-locked TTS with ASR QC). Without `say`, it falls
back to a modulated tone and reports `degraded: true`, so the dry run can never pass off a fallback as a voice.
"""
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from _ctx import FFMPEG, done, load, read_json

ctx = load()
wd = Path(ctx["workdir"])
script = read_json(ctx["inputs"]["script"][-1])
direction = read_json(ctx["agent_outputs"]["voice_direction"]) if ctx["agent_outputs"].get("voice_direction") else {}
RATE = int(direction.get("rate_wpm", 170))
units = [u for ch in script["chapters"] for u in ch["units"]]
SR, HEAD, PAUSE, TAIL = 48000, 0.6, 0.45, 1.3
say = shutil.which("say")


def speak(text: str, aiff: Path) -> None:
    """macOS `say` now and then never returns (seen for hours in a test run); a fresh call normally does."""
    for attempt in range(3):
        try:
            subprocess.run([say, "-r", str(RATE), "-o", str(aiff), text], check=True, timeout=60)
            return
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise


def render(i: int, text: str) -> np.ndarray:
    wav = wd / f"u{i}.wav"
    if say:
        aiff = wd / f"u{i}.aiff"
        speak(text, aiff)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(aiff), "-ar", str(SR), "-ac", "1", "-sample_fmt", "s16", str(wav)], check=True)
        with wave.open(str(wav)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32767
        env = np.convolve(np.abs(x), np.ones(480) / 480, mode="same")
        voiced = np.where(env > 10 ** (-45 / 20))[0]
        return x[max(0, voiced[0] - 480): voiced[-1] + 960] if len(voiced) else x
    n = int(SR * 0.33 * len(text.split()))
    t = np.arange(n) / SR
    return 0.3 * np.sin(2 * np.pi * 180 * t) * (0.65 + 0.35 * np.sin(2 * np.pi * 3 * t))


parts, spans, silences, t = [np.zeros(int(HEAD * SR))], [], [{"start": 0.0, "end": HEAD, "why": "head"}], HEAD
for i, u in enumerate(units):
    clip = render(i, u["text"])
    spans.append({"unit": u["id"], "text": u["text"], "start": round(t, 3), "end": round(t + len(clip) / SR, 3)})
    parts.append(clip)
    t += len(clip) / SR
    gap = PAUSE if i < len(units) - 1 else TAIL
    silences.append({"start": round(t, 3), "end": round(t + gap, 3), "why": "pause" if i < len(units) - 1 else "tail"})
    parts.append(np.zeros(int(gap * SR)))
    t += gap
audio = np.concatenate(parts)
audio = audio / max(np.max(np.abs(audio)), 1e-9) * 10 ** (-3 / 20)
with wave.open(str(wd / "voice.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((audio * 32767).astype(np.int16).tobytes())
(wd / "timings.json").write_text(__import__("json").dumps({"units": spans, "planned_silences": silences, "sample_rate": SR,
                                                           "duration_s": round(len(audio) / SR, 3)}, indent=1))
done(ctx, {"voice": "voice.wav", "timings": "timings.json"}, {"engine": "macOS say" if say else "tone fallback", "degraded": not say,
                                                               "duration_s": round(len(audio) / SR, 3)})
