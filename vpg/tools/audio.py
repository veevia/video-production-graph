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
from ..models import Finding
from ..util import open_regular
from ..util import write_json_atomic
from ._common import finding, thresholds


def _read_wav(path) -> tuple[np.ndarray, int]:
    with open_regular(path) as f, wave.open(f) as w:
        sr, n, ch, width = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if width not in (1, 2, 3, 4):
        raise ValueError(f"unsupported WAV sample width {width} bytes")
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
        return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), sr
    if width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        signed = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        signed = np.where(signed & 0x800000, signed - 0x1000000, signed)
        x = signed.astype(np.float64) / float(2 ** 23)
        return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), sr
    dtype = {2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float64) / float(np.iinfo(dtype).max)
    return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), sr


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12)) if len(x) else -120.0


def _slice(x: np.ndarray, sr: int, start: float, end: float) -> np.ndarray:
    a, b = max(0, int(start * sr)), max(0, int(end * sr))
    return x[a:b]


def _seam_thresholds(ctx) -> dict:
    return thresholds(ctx)["audio"]["seams"]


def _flag(unit: dict, code: str, msg: str, severity: str = "P1", **metrics) -> Finding:
    return finding(f"audio:{code}", "VOI-SEAMS", severity, f"{unit.get('unit')}: {msg}", evidence=", ".join(f"{k}={v}" for k, v in metrics.items()),
                   traced="n12_voice_audio", key="voice", element=unit.get("unit"), fix="re-render or crossfade this seam; never cut mid-sound")


def check_seams(ctx) -> CheckResult:
    pending = getattr(ctx, "pending_outputs", {})
    p = ctx.output_path("voice", pending["voice"]) if pending.get("voice") else ctx.artifact_path("voice")
    timings = {}
    if pending.get("timings"):
        from ..util import read_json
        timings = read_json(ctx.output_path("timings", pending["timings"]))
    else:
        timings = ctx.load("timings") or {}
    units = timings.get("units") or []
    if p is None or not p.exists() or not units:
        msg = "voice seam audit not measured: voice.wav or timings.json missing"
        findings = [finding("audio:seams-not-measured", "VOI-SEAMS", "P3", msg, traced="n12_voice_audio", key="voice")]
        return CheckResult(True, [], findings=findings, metrics={"measured": False})
    try:
        x, sr = _read_wav(p)
    except (ValueError, KeyError, wave.Error) as exc:
        findings = [finding("audio:unsupported-wav", "VOI-SEAMS", "P3", f"voice seam audit not measured: {exc}",
                            traced="n12_voice_audio", key="voice")]
        return CheckResult(True, [], findings=findings, metrics={"measured": False})
    th = _seam_thresholds(ctx)
    findings: list[Finding] = []
    rows = []
    win = max(1, int(th["hard_cut_window_s"] * sr))
    for i, u in enumerate(units):
        start, end = float(u["start"]), float(u["end"])
        nxt = float(units[i + 1]["start"]) if i + 1 < len(units) else None
        gap = None if nxt is None else nxt - end
        tail_in = _rms_db(_slice(x, sr, end - th["tail_inside_s"], end))
        tail_out = _rms_db(_slice(x, sr, end, end + th["tail_after_s"]))
        head_in = _rms_db(_slice(x, sr, start, start + th["head_inside_s"]))
        head_before = _rms_db(_slice(x, sr, start - th["head_before_s"], start))
        flags = []
        around = _slice(x, sr, end - th["hard_cut_radius_s"], end + th["hard_cut_radius_s"])
        levels = [_rms_db(around[k:k + win]) for k in range(0, max(0, len(around) - win + 1), win)]
        boundary_loud = tail_in > th["end_tail_inside_dbfs"] or tail_out > th["end_tail_after_dbfs"]
        if boundary_loud and any(levels[k] >= th["audible_dbfs"] and min(levels[k + 1:k + 3] or [0]) <= th["silence_dbfs"]
                                 for k in range(max(0, len(levels) - 2))):
            flags.append("HARD_CUT")
            findings.append(_flag(u, "HARD_CUT", "audible 10 ms window drops to digital silence within 20 ms",
                                  at=round(end, 3), tail_db=round(tail_in, 1)))
        if (gap is None or gap >= th["end_gap_s"]) and (tail_in > th["end_tail_inside_dbfs"] or tail_out > th["end_tail_after_dbfs"]):
            flags.append("END_NOT_SILENT")
            findings.append(_flag(u, "END_NOT_SILENT", "unit end is not silent before a real gap/end",
                                  gap=None if gap is None else round(gap, 3), tail_in_db=round(tail_in, 1), tail_out_db=round(tail_out, 1)))
        if gap is not None and gap < th["no_gap_s"] and tail_in > th["loud_tail_dbfs"]:
            flags.append("NO_GAP_LOUD_TAIL")
            findings.append(_flag(u, "NO_GAP_LOUD_TAIL", "loud tail runs into the next unit",
                                  gap=round(gap, 3), tail_in_db=round(tail_in, 1)))
        if gap is not None and gap < 0:
            flags.append("OVERLAP")
            findings.append(_flag(u, "OVERLAP", "timings overlap the next unit", gap=round(gap, 3)))
        if head_in > th["hard_onset_dbfs"] and head_before < th["pre_onset_silence_dbfs"]:
            flags.append("HARD_ONSET")
            findings.append(_flag(u, "HARD_ONSET", "unit starts hard after silence",
                                  severity="nit",
                                  head_in_db=round(head_in, 1), before_db=round(head_before, 1)))
        rows.append({"unit": u.get("unit"), "start": start, "end": end, "gap_to_next": gap, "tail_in_db": round(tail_in, 1),
                     "tail_out_db": round(tail_out, 1), "head_in_db": round(head_in, 1), "head_before_db": round(head_before, 1),
                     "flags": flags})
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "voice-seams-report.json", {"measured": True, "units": rows,
                                                                "findings": [f.model_dump() for f in findings]})
    return CheckResult(not any(f.severity != "nit" for f in findings), [f.message for f in findings if f.severity != "nit"],
                       findings=findings, metrics={"measured": True, "findings": len(findings)})


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
