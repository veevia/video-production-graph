"""Deterministic craft metrics (tier T0) measured on rendered media: what the 1-5 reviews could not see.

Each function returns measurement records from `vpg.metrics` (metric id, version, basis hash, value, evidence with
timecodes), so a benchmark envelope, a ratchet or the glass-box viewer can say exactly what was measured and where.
A metric that cannot be measured (missing stem, unreadable file, no OCR) returns a `not_measured` record; it is never
a silent pass.

Media are read through ffmpeg pipes; no frame or sample is written to disk. Measuring the same bytes twice gives the
same values (no randomness, fixed sample times).
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from ..metrics import MEASUREMENTS_SCHEMA, METRICS, OCR_NOT_RUN, measured, not_measured
from ._common import FFMPEG, probe, words

GREY_W, GREY_H = 320, 180


# ------------------------------------------------------------------------------------------------ probes and decoding
def media_info(path: Path) -> dict[str, Any]:
    info = probe(Path(path))
    fmt = info.get("format", {})
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    out: dict[str, Any] = {"duration_s": float(fmt.get("duration") or 0.0)}
    if video:
        num, _, den = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        out.update(width=int(video["width"]), height=int(video["height"]), fps=fps)
    if audio:
        out.update(channels=int(audio.get("channels") or 1), sample_rate=int(audio.get("sample_rate") or 0))
    return out


def sample_times(duration_s: float, step_s: float) -> list[float]:
    """(i + 0.5) * step for every whole step; one frame in the middle of a clip shorter than a step."""
    n = int(duration_s // step_s)
    return [round((i + 0.5) * step_s, 3) for i in range(n)] if n else [round(duration_s / 2, 3)]


def frame_at(path: Path, t: float, w: int, h: int) -> np.ndarray:
    """The RGB frame shown at time t, as an (h, w, 3) uint8 array."""
    p = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf",
                        f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=300)
    need = w * h * 3
    if p.returncode != 0 or len(p.stdout) < need:
        raise RuntimeError(f"could not decode a frame at {t:.2f}s: {p.stderr.decode('utf-8', 'replace')[-200:]}")
    return np.frombuffer(p.stdout[:need], dtype=np.uint8).reshape(h, w, 3)


def _mono_pan(channels: int) -> str:
    """Downmix to mono as the arithmetic mean of every channel (the fixed loudness and pause basis)."""
    if channels <= 1:
        return "pan=mono|c0=c0"
    share = 1.0 / channels
    return "pan=mono|c0=" + "+".join(f"{share:.6f}*c{i}" for i in range(channels))


def pcm_mono(path: Path, rate: int) -> np.ndarray:
    channels = media_info(path).get("channels", 1)
    p = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-af", _mono_pan(channels), "-ar", str(rate),
                        "-f", "s16le", "-acodec", "pcm_s16le", "-"], capture_output=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError(f"could not decode audio: {p.stderr.decode('utf-8', 'replace')[-200:]}")
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def parse_momentary(log: str) -> np.ndarray:
    """ffmpeg ebur128 framelog lines to one momentary value per 100 ms slot, placed by its own timestamp (window i covers
    the 400 ms ending at (i + 1) * 100 ms). A window ffmpeg reports as `nan`, `-inf` or `inf` (digital silence) reads
    -120, and a missing slot reads -120, so two stems line up by time."""
    import re
    rows = re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+|-?inf|nan)", log)
    if not rows:
        return np.zeros(0, dtype=np.float64)
    slots = [max(0, int(round(float(t) * 10)) - 1) for t, _ in rows]
    values = np.full(max(slots) + 1, -120.0, dtype=np.float64)
    for slot, (_, m) in zip(slots, rows):
        values[slot] = -120.0 if m in ("nan", "-inf", "inf") else max(-120.0, float(m))
    return values


def momentary_lufs(path: Path) -> np.ndarray:
    """BS.1770 momentary loudness every 100 ms on the mono-mean downmix at 48 kHz. Window i covers the 400 ms ending at
    (i + 1) * 100 ms: each value is placed by its own timestamp, and a digitally silent window (ffmpeg prints `M: nan`
    or `-inf`) reads -120, so two stems' arrays line up by time even when one has silent gaps."""
    channels = media_info(path).get("channels", 1)
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"{_mono_pan(channels)},aresample=48000,ebur128=framelog=info", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError(f"could not meter loudness: {p.stderr[-200:]}")
    return parse_momentary(p.stderr)


# ------------------------------------------------------------------------------------------------ frame metrics
def colourfulness(pixels: np.ndarray) -> float:
    """Hasler and Suesstrunk (2003) colourfulness M over an (..., 3) array of RGB values."""
    a = pixels.reshape(-1, 3).astype(np.float64)
    if a.size == 0:
        return float("nan")
    rg = a[:, 0] - a[:, 1]
    yb = 0.5 * (a[:, 0] + a[:, 1]) - a[:, 2]
    return float(math.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * math.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def block_edges(rgb: np.ndarray, block: int = 16) -> np.ndarray:
    """Per block, the largest luma step between neighbouring pixels (0-255)."""
    f = rgb.astype(np.float32)
    y = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    gx = np.abs(np.diff(y, axis=1))[:-1, :]
    gy = np.abs(np.diff(y, axis=0))[:, :-1]
    g = np.maximum(gx, gy)
    bh, bw = g.shape[0] // block, g.shape[1] // block
    if not bh or not bw:
        return np.zeros((0, 0), dtype=np.float32)
    return g[:bh * block, :bw * block].reshape(bh, block, bw, block).max(axis=(1, 3))


def border_ground(flat: np.ndarray) -> np.ndarray:
    """Flat blocks connected to the frame border through flat blocks: the background, not the inside of a card."""
    if not flat.size:
        return flat
    ground = np.zeros_like(flat)
    ground[0, :], ground[-1, :], ground[:, 0], ground[:, -1] = flat[0, :], flat[-1, :], flat[:, 0], flat[:, -1]
    while True:
        grown = ground.copy()
        grown[1:, :] |= ground[:-1, :]
        grown[:-1, :] |= ground[1:, :]
        grown[:, 1:] |= ground[:, :-1]
        grown[:, :-1] |= ground[:, 1:]
        grown &= flat
        if (grown == ground).all():
            return ground
        ground = grown


def frame_stats(rgb: np.ndarray) -> dict[str, float]:
    basis = METRICS["video.ground_colourfulness.median"].basis
    block, edge = int(basis["block_px"]), float(basis["edge_threshold"])
    blocks = block_edges(rgb, block)
    out = {"colourfulness": colourfulness(rgb), "coverage": float((blocks > edge).mean()) if blocks.size else float("nan")}
    ground = border_ground(blocks <= edge)
    out["ground_share"] = float(ground.mean()) if blocks.size else 0.0
    if blocks.size and out["ground_share"] >= 0.10:
        mask = np.repeat(np.repeat(ground, block, axis=0), block, axis=1)
        region = rgb[:mask.shape[0], :mask.shape[1]]
        out["ground_colourfulness"] = colourfulness(region[mask])
    else:
        out["ground_colourfulness"] = float("nan")
    return out


def _median(values: Iterable[float]) -> float:
    v = [x for x in values if x is not None and math.isfinite(x)]
    return float(np.median(v)) if v else float("nan")


def frame_metrics(master: Path, step_s: float | None = None,
                  frame_reader: Callable[[Path, float, int, int], np.ndarray] = frame_at) -> dict[str, dict[str, Any]]:
    """Colourfulness, ground colourfulness and coverage on frames sampled every `step_s` seconds."""
    ids = ["video.colourfulness.median", "video.ground_colourfulness.median", "video.coverage.median"]
    try:
        info = media_info(master)
        if "width" not in info:
            raise RuntimeError("no video stream")
    except Exception as exc:   # noqa: BLE001 - any failure to read is reported, never passed
        return {i: not_measured(i, f"{type(exc).__name__}: {exc}") for i in ids}
    step = float(step_s or METRICS[ids[0]].basis["step_s"])
    rows = []
    for t in sample_times(info["duration_s"], step):
        stats = frame_stats(frame_reader(master, t, info["width"], info["height"]))
        rows.append({"t": t, **{k: (round(v, 3) if math.isfinite(v) else None) for k, v in stats.items()}})

    def worst(key: str, n: int = 3) -> list[dict[str, Any]]:
        ranked = sorted((r for r in rows if r.get(key) is not None), key=lambda r: -r[key])[:n]
        return [{"t": r["t"], key: r[key]} for r in ranked]

    ev = {"frames": len(rows), "step_s": step, "file": str(master)}
    return {
        ids[0]: measured(ids[0], _median(r["colourfulness"] for r in rows), {**ev, "most": worst("colourfulness")}),
        ids[1]: measured(ids[1], _median(r["ground_colourfulness"] for r in rows if r["ground_colourfulness"] is not None),
                         {**ev, "most": worst("ground_colourfulness")}),
        ids[2]: measured(ids[2], _median(r["coverage"] for r in rows), {**ev, "most": worst("coverage")}),
        "_rows": {"frames": rows},
    }


def _grey_frames(master: Path) -> Iterable[np.ndarray]:
    proc = subprocess.Popen([FFMPEG, "-v", "error", "-i", str(master), "-vf", f"scale={GREY_W}:{GREY_H},format=gray",
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = GREY_W * GREY_H
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).astype(np.int16)
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait(timeout=60)


def motion_metrics(master: Path, frames: Iterable[np.ndarray] | None = None, fps: float | None = None) -> dict[str, dict[str, Any]]:
    """Share of frame pairs in motion, and the share spent in still holds of at least hold_min_s."""
    ids = ["video.motion_share", "video.hold_share"]
    basis = METRICS[ids[0]].basis
    try:
        rate = fps or media_info(master).get("fps") or 0.0
        if rate <= 0:
            raise RuntimeError("no frame rate")
        diffs, prev = [], None
        for fr in (frames if frames is not None else _grey_frames(master)):
            if prev is not None:
                diffs.append(float(np.abs(fr - prev).mean()))
            prev = fr
    except Exception as exc:   # noqa: BLE001
        return {i: not_measured(i, f"{type(exc).__name__}: {exc}") for i in ids}
    if not diffs:
        return {i: not_measured(i, "fewer than two frames") for i in ids}
    d = np.array(diffs)
    moving = d > float(basis["moving_mean_abs_diff"])
    hold_len = max(1, int(math.ceil(float(basis["hold_min_s"]) * rate)))
    runs, start = [], None   # runs of moving pairs, for evidence
    held = np.zeros(len(d), dtype=bool)
    still_start = None
    for k, mv in enumerate([bool(x) for x in moving] + [None]):
        if mv and start is None:
            start = k
        if (not mv) and start is not None:
            runs.append((start, k))
            start = None
        if mv is False and still_start is None:
            still_start = k
        if mv is not False and still_start is not None:
            if k - still_start >= hold_len:
                held[still_start:k] = True
            still_start = None
    longest = sorted(runs, key=lambda r: r[0] - r[1])[:5]
    ev = {"pairs": len(d), "fps": round(rate, 3), "file": str(master),
          "longest_motion": [{"from": round((a + 1) / rate, 2), "to": round((b + 1) / rate, 2)} for a, b in longest]}
    return {ids[0]: measured(ids[0], float(moving.mean()), ev),
            ids[1]: measured(ids[1], float(held.mean()), {**ev, "hold_min_s": basis["hold_min_s"]})}


# ------------------------------------------------------------------------------------------------ voice and music
def voiced_frames(x: np.ndarray, rate: int, hop_s: float = 0.02) -> tuple[np.ndarray, float]:
    hop = int(round(rate * hop_s))
    n = len(x) // hop
    if n < 3:
        return np.zeros(0, dtype=bool), hop_s
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    loud = db[db > -80]
    if loud.size == 0:
        return np.zeros(n, dtype=bool), hop_s
    return db > (np.percentile(loud, 90) - 30), hop_s


def voice_metrics(voice: Path, narration: str | None = None, x: np.ndarray | None = None,
                  rate: int = 16000) -> dict[str, dict[str, Any]]:
    """Pause share (gaps of at least 0.3 s inside the spoken span) and, given the narration, pace in words per minute."""
    ids = ["audio.pause_share", "audio.pace_wpm"]
    try:
        x = pcm_mono(voice, rate) if x is None else x
    except Exception as exc:   # noqa: BLE001
        return {i: not_measured(i, f"{type(exc).__name__}: {exc}") for i in ids}
    voiced, hop_s = voiced_frames(x, rate)
    idx = np.where(voiced)[0]
    if idx.size < 2:
        return {i: not_measured(i, "no voiced span found") for i in ids}
    first, last = int(idx[0]), int(idx[-1])
    span = (last - first + 1) * hop_s
    pauses, run_start = [], None
    for k in range(first, last + 1):
        if not voiced[k] and run_start is None:
            run_start = k
        elif voiced[k] and run_start is not None:
            if (k - run_start) * hop_s >= 0.3:
                pauses.append((run_start * hop_s, k * hop_s))
            run_start = None
    total = sum(b - a for a, b in pauses)
    ev = {"span": [round(first * hop_s, 2), round((last + 1) * hop_s, 2)], "span_s": round(span, 2),
          "pauses": [{"from": round(a, 2), "to": round(b, 2)} for a, b in pauses][:40], "pause_count": len(pauses),
          "longest_pause_s": round(max((b - a for a, b in pauses), default=0.0), 2), "file": str(voice)}
    out = {ids[0]: measured(ids[0], total / span, ev)}
    if narration is None:
        out[ids[1]] = not_measured(ids[1], "no narration text was given")
    elif not words(narration):
        out[ids[1]] = not_measured(ids[1], "the narration has no words")
    else:
        n_words = len(words(narration))
        out[ids[1]] = measured(ids[1], n_words / (span / 60.0), {"words": n_words, "span_s": round(span, 2), "file": str(voice)})
    return out


def mix_metrics(voice: Path, music: Path, v: np.ndarray | None = None, m: np.ndarray | None = None) -> dict[str, dict[str, Any]]:
    """Voice over music on one fixed basis: both stems downmixed to the mono mean, then metered the same way."""
    ids = ["audio.vo_over_music.p10", "audio.vo_over_music.median"]
    try:
        v = momentary_lufs(voice) if v is None else v
        m = momentary_lufs(music) if m is None else m
    except Exception as exc:   # noqa: BLE001
        return {i: not_measured(i, f"{type(exc).__name__}: {exc}") for i in ids}
    n = min(len(v), len(m))
    v, m = v[:n], m[:n]
    active = v > -35.0
    if not active.any():
        return {i: not_measured(i, "the voice never rises above -35 LUFS momentary") for i in ids}
    d = v[active] - m[active]
    t = np.arange(n)[active] * 0.1 + 0.4   # momentary windows end 400 ms after they start
    worst = np.argsort(d)[:5]
    ev = {"active_windows": int(active.sum()), "speech_share": round(float(active.mean()), 3),
          "lowest": [{"t": round(float(t[i]), 1), "lu": round(float(d[i]), 1)} for i in worst],
          "files": [str(voice), str(music)]}
    return {ids[0]: measured(ids[0], float(np.percentile(d, 10)), ev),
            ids[1]: measured(ids[1], float(np.median(d)), ev)}


# ------------------------------------------------------------------------------------------------ text layers
def _visible(layer: dict[str, Any]) -> tuple[float, float]:
    return (float(layer["start"]) - float(layer.get("fade_in_s") or 0.0),
            float(layer["end"]) + float(layer.get("fade_out_s") or 0.0))


OVERLAP_MIN_PX = 2.0   # boxes must cross by more than this both ways: stacked line boxes of one headline touch


def overlaps(text_layers: dict[str, Any]) -> list[dict[str, Any]]:
    """Every pair of text layers visible at the same moment whose boxes cross by more than OVERLAP_MIN_PX both ways,
    transitions included."""
    layers = [layer for layer in text_layers.get("layers", [])
              if layer.get("kind", "text") == "text" and float(layer.get("opacity", 1.0)) > 0 and layer.get("bbox")]
    found = []
    for i, a in enumerate(layers):
        for b in layers[i + 1:]:
            if b.get("id") in (a.get("allow_overlap") or []) or a.get("id") in (b.get("allow_overlap") or []):
                continue
            (sa, ea), (sb, eb) = _visible(a), _visible(b)
            t0, t1 = max(sa, sb), min(ea, eb)
            if t1 <= t0:
                continue
            ax, ay, aw, ah = a["bbox"]
            bx, by, bw, bh = b["bbox"]
            ix = min(ax + aw, bx + bw) - max(ax, bx)
            iy = min(ay + ah, by + bh) - max(ay, by)
            if ix > OVERLAP_MIN_PX and iy > OVERLAP_MIN_PX:
                held = max(0.0, min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"])))
                ratio = [round(box[3] / float(lay["font_px"]), 1) if lay.get("font_px") else None
                         for box, lay in ((a["bbox"], a), (b["bbox"], b))]
                found.append({"a": a.get("id"), "b": b.get("id"), "from": round(t0, 3), "to": round(t1, 3),
                              "area_px": round(ix * iy, 1), "during": "held" if held > 0 else "transition",
                              "box_height_vs_type": ratio,   # far above ~1.3 per line: a container box, not the text's own
                              "scene": a.get("scene") if a.get("scene") == b.get("scene") else f"{a.get('scene')}|{b.get('scene')}",
                              "text": [a.get("text", ""), b.get("text", "")]})
    return found


def text_metrics(text_layers: dict[str, Any], player_width_px: float | None = None) -> dict[str, dict[str, Any]]:
    ids = ["text.overlap_all.count", "text.min_px_at_player"]
    hits = overlaps(text_layers)
    out = {ids[0]: measured(ids[0], len(hits), {"overlaps": hits[:40],
                                                "transition_only": sum(h["during"] == "transition" for h in hits)})}
    pw = float(player_width_px or METRICS[ids[1]].basis["player_width_px"])
    frame_w = float((text_layers.get("frame") or {}).get("w") or 0)
    sized = [(layer, float(layer["font_px"])) for layer in text_layers.get("layers", [])
             if layer.get("kind", "text") == "text" and layer.get("essential", True) and layer.get("font_px")]
    if not frame_w or not sized:
        out[ids[1]] = not_measured(ids[1], "no frame width or no essential text layer with font_px")
    else:
        layer, px = min(sized, key=lambda s: s[1])
        out[ids[1]] = measured(ids[1], px * pw / frame_w, {"layer": layer.get("id"), "font_px": px, "frame_w": frame_w,
                                                           "player_width_px": pw, "at": layer.get("start")})
    return out


OCR_MIN_CONF, OCR_DENSE_WORDS, OCR_STILL_WORDS = 60.0, 20, 3


def ocr_read(image: Any) -> list[tuple[str, float, int]]:
    """One tesseract pass, read exactly as vpg.tools.privacy.tesseract_words reads (a 2x greyscale copy, --psm 11, TSV):
    (text, confidence, box height in the image's own pixels) per word."""
    import io
    import shutil as _shutil
    from PIL import Image

    from .privacy import OcrUnavailable
    exe = _shutil.which("tesseract")
    if not exe:
        raise OcrUnavailable("tesseract is not installed")
    big = image.convert("L").resize((image.width * 2, image.height * 2), Image.LANCZOS)
    png = io.BytesIO()
    big.save(png, format="PNG")
    try:
        p = subprocess.run([exe, "stdin", "stdout", "--psm", "11", "tsv"], input=png.getvalue(), capture_output=True,
                           timeout=120)
    except subprocess.TimeoutExpired:
        raise OcrUnavailable("tesseract timed out") from None
    if p.returncode != 0:
        raise OcrUnavailable(f"tesseract exited {p.returncode}")
    out = []
    for row in p.stdout.decode("utf-8", "replace").splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) == 12 and cols[11].strip():
            try:
                out.append((cols[11].strip(), float(cols[10]), int(cols[9]) // 2))
            except ValueError:
                continue
    return out


def _letter_heights(words: list[tuple[str, float, int]]) -> list[int]:
    """Heights of words with a letter: the words vpg.tools.privacy.tesseract_words keeps (text.ocr_small_share's basis)."""
    import re
    return [h for txt, _, h in words if re.search(r"[^\W\d_]", txt)]


def _confident_heights(words: list[tuple[str, float, int]]) -> list[int]:
    return [h for txt, conf, h in words if conf >= OCR_MIN_CONF and len(txt) >= 2 and any(ch.isalnum() for ch in txt)]


def ocr_metrics(master: Path, ocr: Callable | None = None, step_s: float | None = None,
                frame_reader: Callable[[Path, float, int, int], np.ndarray] = frame_at) -> dict[str, dict[str, Any]]:
    """From one OCR pass per sampled frame: the share of words under a fixed size at player width (median over frames
    with words), and the smallest median word size on any text-dense frame. `ocr` reads (text, confidence, height)."""
    from PIL import Image

    from .privacy import OcrUnavailable, ocr_self_test, tesseract_words
    mid, wid = "text.ocr_small_share", "text.ocr_word_px.dense_min"
    basis = METRICS[mid].basis
    try:
        reader = ocr or ocr_read
        if ocr is None:
            ocr_self_test(tesseract_words)
        info = media_info(master)
        step = float(step_s or basis["step_s"])
        scale = float(basis["player_width_px"]) / float(info["width"])
        shares, worst, dense = [], [], []
        for t in sample_times(info["duration_s"], step):
            words = reader(Image.fromarray(frame_reader(master, t, info["width"], info["height"])))
            heights = [h * scale for h in _letter_heights(words)]
            if heights:
                small = sum(h < float(basis["small_px_at_player"]) for h in heights) / len(heights)
                shares.append(small)
                worst.append({"t": t, "small_share": round(small, 3), "words": len(heights)})
            sure = [h * scale for h in _confident_heights(words)]
            if len(sure) >= OCR_DENSE_WORDS:
                dense.append({"t": t, "median_px": round(float(np.median(sure)), 2), "words": len(sure)})
    except OcrUnavailable as exc:
        return {i: not_measured(i, f"OCR unavailable: {exc}") for i in (mid, wid)}
    except Exception as exc:   # noqa: BLE001
        return {i: not_measured(i, f"{type(exc).__name__}: {exc}") for i in (mid, wid)}
    out = {}
    if not shares:
        out[mid] = not_measured(mid, "no words were read")
    else:
        worst.sort(key=lambda r: -r["small_share"])
        out[mid] = measured(mid, float(np.median(shares)), {"frames_with_words": len(shares), "most": worst[:5],
                                                            "file": str(master)})
    if not dense:
        out[wid] = not_measured(wid, f"no text-dense frame (none with {OCR_DENSE_WORDS} or more confidently read words)")
    else:
        dense.sort(key=lambda r: r["median_px"])
        out[wid] = measured(wid, dense[0]["median_px"], {"dense_frames": len(dense), "smallest": dense[:5],
                                                         "file": str(master)})
    return out


# ------------------------------------------------------------------------------------------------ style frames
def canonical_still(im: Any) -> np.ndarray:
    """A still as RGB at the canonical width (block-based metrics depend on resolution)."""
    from PIL import Image

    from ..metrics import CANONICAL_STILL_WIDTH
    rgb = im.convert("RGB")
    if rgb.width != CANONICAL_STILL_WIDTH:
        h = max(1, round(rgb.height * CANONICAL_STILL_WIDTH / rgb.width))
        rgb = rgb.resize((CANONICAL_STILL_WIDTH, h), Image.LANCZOS)
    return np.asarray(rgb)


def still_metrics(stills: Iterable[Path], ocr: bool = False, reader: Callable | None = None,
                  roles: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """The still.* metrics on a set of style frames at the canonical width: medians over the set, and each role's own
    value in evidence.per_role (the look gate compares role with role)."""
    from PIL import Image
    ids = ["still.colourfulness.median", "still.ground_colourfulness.median", "still.coverage.median"]
    paths = [Path(p) for p in stills]
    roles = list(roles or [p.stem for p in paths])
    ocr_ids = ["still.ocr_small_share", "still.ocr_word_px.median"]
    if not paths:
        return {i: not_measured(i, "no style frames") for i in ids + ocr_ids}
    rows, images = [], []
    try:
        for p, role in zip(paths, roles):
            with Image.open(p) as im:
                rgb = canonical_still(im)
            images.append(rgb)
            s = frame_stats(rgb)
            rows.append({"file": str(p), "role": role, **{k: (round(v, 3) if math.isfinite(v) else None) for k, v in s.items()}})
    except Exception as exc:   # noqa: BLE001 - an unreadable still is reported, never passed
        return {i: not_measured(i, f"{type(exc).__name__}: {exc}") for i in ids + ocr_ids}

    def ev(key: str) -> dict[str, Any]:
        return {"stills": len(rows), "per_role": {r["role"]: r[key] for r in rows},
                "per_still": [{"file": r["file"], "role": r["role"], key: r[key]} for r in rows]}
    out = {ids[0]: measured(ids[0], _median(r["colourfulness"] for r in rows), ev("colourfulness")),
           ids[1]: measured(ids[1], _median(r["ground_colourfulness"] for r in rows if r["ground_colourfulness"] is not None),
                            ev("ground_colourfulness")),
           ids[2]: measured(ids[2], _median(r["coverage"] for r in rows), ev("coverage"))}
    mid, wid = ocr_ids
    if not ocr:
        out.update({i: not_measured(i, OCR_NOT_RUN) for i in ocr_ids})
        return out
    from .privacy import OcrUnavailable, ocr_self_test, tesseract_words
    basis = METRICS[mid].basis
    try:
        read = reader or ocr_read
        if reader is None:
            ocr_self_test(tesseract_words)
        shares, per, per_role, sizes, size_rows, size_role = [], [], {}, [], [], {}
        for p, role, rgb in zip(paths, roles, images):
            scale = float(basis["player_width_px"]) / float(rgb.shape[1])
            words = read(Image.fromarray(rgb))
            heights = [h * scale for h in _letter_heights(words)]
            if heights:
                small = sum(h < float(basis["small_px_at_player"]) for h in heights) / len(heights)
                shares.append(small)
                per_role[role] = round(small, 4)
                per.append({"file": str(p), "role": role, "small_share": round(small, 3), "words": len(heights)})
            sure = [h * scale for h in _confident_heights(words)]
            if len(sure) >= OCR_STILL_WORDS:
                size = round(float(np.median(sure)), 2)
                sizes.append(size)
                size_role[role] = size
                size_rows.append({"file": str(p), "role": role, "median_px": size, "words": len(sure)})
    except OcrUnavailable as exc:
        out.update({i: not_measured(i, f"OCR unavailable: {exc}") for i in ocr_ids})
        return out
    out[mid] = measured(mid, float(np.median(shares)), {"stills_with_words": len(shares), "per_role": per_role,
                                                        "per_still": per}) if shares \
        else not_measured(mid, "no words were read")
    out[wid] = measured(wid, float(np.median(sizes)), {"stills_with_words": len(sizes), "per_role": size_role,
                                                       "per_still": size_rows}) if sizes \
        else not_measured(wid, f"no still with {OCR_STILL_WORDS} or more confidently read words")
    return out


# ------------------------------------------------------------------------------------------------ one report
def narration_text(path: Path) -> str:
    """Spoken text from timings.json (top-level units; its chapters carry only timing), script.json (chapters of units)
    or a plain text file. A file that yields no words is refused, never read as silence."""
    p = Path(path)
    if p.suffix.lower() != ".json":
        text = p.read_text()
    else:
        data = json.loads(p.read_text())
        units = data.get("units") if isinstance(data, dict) else None
        if not units and isinstance(data, dict):
            units = [u for ch in data.get("chapters") or [] if isinstance(ch, dict) for u in ch.get("units") or []]
        text = " ".join(str(u.get("text", "")) for u in units or [] if isinstance(u, dict))
    if not text.strip():
        raise ValueError(f"{p.name}: no narration text (no units with text)")
    return text


def measure(master: Path | None = None, voice: Path | None = None, music: Path | None = None,
            text_layers: Path | None = None, narration: Path | None = None, ocr: bool = False,
            player_width_px: float | None = None, stills: Iterable[Path] | None = None,
            still_roles: list[str] | None = None) -> dict[str, Any]:
    """Every craft metric the given files allow. Missing inputs give not-measured records, never silent passes."""
    out: dict[str, Any] = {}
    frames: dict[str, Any] = {}
    stills = list(stills or [])
    if stills:
        out.update(still_metrics(stills, ocr=ocr, roles=still_roles))
    if master:
        fm = frame_metrics(Path(master))
        frames = fm.pop("_rows", {})
        out.update(fm)
        out.update(motion_metrics(Path(master)))
        if ocr:
            out.update(ocr_metrics(Path(master)))
    if voice:
        out.update(voice_metrics(Path(voice), narration_text(Path(narration)) if narration else None))
        if music:
            out.update(mix_metrics(Path(voice), Path(music)))
    if text_layers:
        out.update(text_metrics(json.loads(Path(text_layers).read_text()), player_width_px))
    for mid, m in METRICS.items():
        if mid not in out:
            needs = {"master": "a master", "voice": "a voice stem", "voice+music": "voice and music stems",
                     "voice+script": "a voice stem and the narration", "text_layers": "text-layers.json",
                     "stills": "style frames"}[m.source]
            out[mid] = not_measured(mid, f"needs {needs}" + (" and --ocr" if ".ocr_" in mid else ""))
    inputs = {k: str(v) for k, v in {"master": master, "voice": voice, "music": music, "text_layers": text_layers,
                                     "narration": narration}.items() if v}
    if stills:
        inputs["stills"] = ", ".join(str(s) for s in stills)
    return {"schema": MEASUREMENTS_SCHEMA, "inputs": inputs, "measurements": dict(sorted(out.items())),
            "frames": frames.get("frames", [])}
