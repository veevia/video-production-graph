"""Voice and audio checks (N12).

Recovered rules: narration must cover every script unit in order, with no dropped or clipped words. Round 07's
clipped "experts" came from a mix step that lowered a detector-flagged "pause" by 32 dB inside a word, so speech
spans must never be attenuated by more than `max_speech_dip_db`. The voice direction must name an adult
professional delivery, and human listening stays a separate sign-off that the agent never claims.
"""

from __future__ import annotations

import wave

import numpy as np

from ..context import CheckResult
from ..util import open_regular
from ._common import thresholds


def _read_wav(path) -> tuple[np.ndarray, int]:
    with open_regular(path) as f, wave.open(f) as w:
        sr, n, ch, width = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    dtype = {2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float64) / float(np.iinfo(dtype).max)
    return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), sr


def check_voice(ctx) -> CheckResult:
    th = thresholds(ctx)["audio"]
    msgs = []
    spans = (ctx.load("timings") or {}).get("units", [])
    script = ctx.load("script") or {}
    unit_ids = [u["id"] for ch in script.get("chapters", []) for u in ch.get("units", [])]
    if [s["unit"] for s in spans] != unit_ids:
        msgs.append(f"voice spans {[s['unit'] for s in spans]} do not cover the script units {unit_ids} in order")
    for a, b in zip(spans, spans[1:]):
        if b["start"] < a["end"]:
            msgs.append(f"spans {a['unit']} and {b['unit']} overlap")
    direction = ctx.load("voice_direction") or {}
    if direction.get("age") != "adult" or not direction.get("delivery"):
        msgs.append("voice direction must specify an adult professional delivery (never a child-like or youthful read)")
    if direction.get("human_listening") not in ("pending", "approved"):
        msgs.append("voice direction must record the human listening sign-off status (pending|approved); the agent never claims it")
    p = ctx.artifact_path("voice")
    if p is None or not p.exists():
        msgs.append("voice stem missing")
        return CheckResult(False, msgs)
    x, sr = _read_wav(p)
    dur = len(x) / sr
    if spans and dur + 0.05 < spans[-1]["end"]:
        msgs.append(f"voice stem is {dur:.2f}s but the spans end at {spans[-1]['end']:.2f}s (dropped audio)")
    peak = 20 * np.log10(max(np.max(np.abs(x)), 1e-9))
    if peak > -0.5:
        msgs.append(f"voice stem peaks at {peak:.2f} dBFS (clipping risk; keep ≤ -0.5 dBFS)")
    hop = int(0.02 * sr)
    for s in spans:
        seg = x[int(s["start"] * sr):int(s["end"] * sr)]
        if len(seg) < hop * 3:
            msgs.append(f"span {s['unit']} is too short to contain speech")
            continue
        frames = seg[: len(seg) // hop * hop].reshape(-1, hop)
        rms = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
        voiced = rms[rms > rms.max() - 30]
        inner = rms[3:-3] if len(rms) > 8 else rms
        dips = inner[(inner < np.median(voiced) - th["max_speech_dip_db"] - 24)]
        if len(dips) > max(8, 0.35 * len(inner)):
            msgs.append(f"span {s['unit']} has {len(dips)} near-silent frames inside speech (possible dropped or masked words)")
    return CheckResult(not msgs, msgs, metrics={"voice_duration_s": round(dur, 2), "voice_peak_dbfs": round(float(peak), 2)})
