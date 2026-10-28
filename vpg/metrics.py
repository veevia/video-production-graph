"""Craft metrics: their identities, versions and measurement bases.

Every measurement names its metric, the metric's version and the hash of its basis: the exact method and parameters
(frame sampling, downmix, meter, thresholds). Two measurements compare only when all three match. When they differ,
the older one is re-measured under the current basis before any comparison (`benchmarks.recompute`); a value is never
compared across bases, because a basis change can move a number without the film changing (a +3 dB change in how a
mono voice was metered once hid a falling voice-over-music margin).

Changing a metric's method or a parameter is a new version: bump `version`, and every stored measurement of the old
version becomes "not comparable" until it is re-measured. Values come from `vpg/tools/craft.py`.

Evidence: the metric set and its v1 parameters reproduce a local investigation of a recent 90-second case-study film
(NDA) against two benchmark films (reported). Thresholds are not set here: envelopes come from a project's benchmark
registry (`vpg/benchmarks.py`), so the benchmark films define what is in character.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .util import sha256_json

MEASUREMENTS_SCHEMA = "vpg-measurements/v1"


@dataclass(frozen=True)
class Metric:
    id: str
    version: int
    unit: str
    source: str          # "master" (video frames), "stills", "voice", "voice+music", "voice+script", "text_layers"
    sides: str           # which side of a benchmark envelope is a defect: "both", "lower" or "upper"
    tolerance: float     # absolute margin outside the benchmark range before a value counts as out of envelope
    basis: dict[str, Any]
    description: str
    rule: str = ""       # the knowledge rule whose evidence the metric serves, when one exists
    ladder: tuple[str, ...] = field(default_factory=tuple)   # related metrics shown beside it

    @property
    def basis_sha(self) -> str:
        """16 hex characters naming the method: id, version and every basis parameter."""
        return sha256_json({"id": self.id, "version": self.version, "basis": self.basis})[:16]

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "basis_sha": self.basis_sha, "unit": self.unit,
                "source": self.source, "sides": self.sides, "tolerance": self.tolerance, "basis": self.basis,
                "rule": self.rule, "description": self.description}


_FRAMES = {"sample": "one frame at t = (i + 0.5) * step_s for i in 0..floor(duration / step_s) - 1, seeked by ffmpeg",
           "step_s": 2.0, "pixels": "full resolution, sRGB 8-bit RGB"}
_STILLS = {"sample": "every still in the set (PNG or JPEG), decoded with Pillow",
           "pixels": "resized to 1920 pixels wide (aspect kept, Lanczos), sRGB 8-bit RGB",
           "roles": "each still carries its role (title, product_ui, data_callout, closing, other); evidence.per_role holds "
                    "each role's own value, and the look gate compares each role only with the same role in the benchmarks"}
CANONICAL_STILL_WIDTH = 1920
_OCR_WORDS = {"ocr": "tesseract --psm 11 on a 2x greyscale copy, one pass per frame (vpg.tools.craft.ocr_read)",
              "player_width_px": 960,
              "words": "confidently read words: confidence >= 60, two or more characters, at least one letter or digit"}
_BLOCKS = {"block_px": 16, "edge_threshold": 40.0,
           "luma": "Y = 0.2126 R + 0.7152 G + 0.0722 B; block edge = max |horizontal or vertical neighbour difference|"}
_MOTION = {"decode": "every frame at native rate, scaled to 320x180, 8-bit grey", "moving_mean_abs_diff": 0.15,
           "hold_min_s": 1.0}
_VOICED = {"resample": "16 kHz mono (mean of channels)", "hop_s": 0.02,
           "voiced": "frame RMS dB > p90(frames above -80 dB) - 30 dB", "span": "first to last voiced frame"}
_LOUDNESS = {"downmix": "mono, the arithmetic mean of all channels (a true dual-mono stem measures the same as its mono "
                       "source; metered as-is, BS.1770 would sum both channels and read it 3 dB louder)",
             "rate_hz": 48000,
             "meter": "ITU-R BS.1770 momentary loudness (400 ms window, 100 ms step), ffmpeg ebur128",
             "windows": "each window placed by its own timestamp, so the voice and the music are compared at the same "
                        "moment; a digitally silent window (ffmpeg prints M: nan or -inf) reads -120 LUFS",
             "active": "windows where the voice is above -35 LUFS momentary"}

METRIC_LIST: list[Metric] = [
    Metric("video.colourfulness.median", 1, "M", "master", "both", 6.0,
           {**_FRAMES, "formula": "Hasler-Suesstrunk M = sqrt(sd_rg^2 + sd_yb^2) + 0.3 sqrt(mean_rg^2 + mean_yb^2), "
                                  "rg = R - G, yb = (R + G) / 2 - B, over every pixel", "aggregate": "median over frames"},
           "How colourful the whole frame is. A saturated gradient used as every frame's ground raises it several-fold."),
    Metric("video.ground_colourfulness.median", 1, "M", "master", "both", 6.0,
           {**_FRAMES, **_BLOCKS, "ground": "flat blocks (edge at or below edge_threshold) connected to the frame "
                                            "border through flat blocks: the background, not the inside of a card",
            "formula": "Hasler-Suesstrunk M over ground pixels",
            "aggregate": "median over frames where ground covers at least 10% of blocks"},
           "How colourful the ground (flat background) is, apart from the content drawn on it."),
    Metric("video.coverage.median", 1, "share", "master", "both", 0.06,
           {**_FRAMES, **_BLOCKS, "formula": "share of blocks whose edge is above edge_threshold",
            "aggregate": "median over frames"},
           "How much of the frame carries drawn content (text, lines, UI) rather than open ground."),
    Metric("video.motion_share", 1, "share", "master", "upper", 0.05,
           {**_MOTION, "formula": "share of consecutive frame pairs whose mean absolute grey difference exceeds "
                                  "moving_mean_abs_diff"},
           "Frames in motion. Regressed versions moved markedly more of the time than the benchmark films."),
    Metric("video.hold_share", 1, "share", "master", "lower", 0.05,
           {**_MOTION, "formula": "share of frame pairs inside still runs lasting at least hold_min_s"},
           "Visual breathing room: time spent in holds of a second or more."),
    Metric("audio.pause_share", 1, "share", "voice", "lower", 0.03,
           {**_VOICED, "pause": "a gap of at least 0.3 s between voiced frames inside the span",
            "formula": "total pause time / span"},
           "Pauses as a share of the spoken span; regressed versions kept about half the benchmark films' share."),
    Metric("audio.pace_wpm", 1, "wpm", "voice+script", "both", 8.0,
           {**_VOICED, "words": "spoken words of the narration text (vpg.tools._common.words)",
            "formula": "words / span minutes"},
           "Narration pace over the voiced span.", rule="R-PRO-006"),
    Metric("audio.vo_over_music.p10", 2, "LU", "voice+music", "lower", 1.5,
           {**_LOUDNESS, "formula": "10th percentile of voice minus music momentary loudness over active windows"},
           "How far the voice sits above the music in its quietest tenth (the basis is fixed: both stems are "
           "downmixed the same way before metering).", rule="R-AUD-002"),
    Metric("audio.vo_over_music.median", 2, "LU", "voice+music", "lower", 1.5,
           {**_LOUDNESS, "formula": "median of voice minus music momentary loudness over active windows"},
           "Typical voice-over-music margin.", rule="R-AUD-002"),
    Metric("text.overlap_all.count", 2, "count", "text_layers", "upper", 0.0,
           {"layers": "text-layers.json layers of kind text (or no kind), any opacity above 0",
            "visible": "[start - fade_in_s, end + fade_out_s] when the renderer declares fades, else [start, end]",
            "overlap": "two layers visible at the same time whose boxes cross by more than 2 px both ways (line boxes "
                       "that touch, as stacked lines of one headline do, are not an overlap)",
            "exempt": "a pair is exempt only when one layer declares the other in allow_overlap"},
           "Overlapping text at any time, transitions included (R-LAY-001 everywhere, not only on held text).",
           rule="R-LAY-001"),
    Metric("text.min_px_at_player", 1, "px", "text_layers", "lower", 0.5,
           {"layers": "essential text layers (essential is true or absent) with font_px",
            "player_width_px": 960, "formula": "min(font_px * player_width_px / frame.w)"},
           "The smallest essential text as a viewer sees it in a 960-pixel-wide player.", rule="R-LEG-001"),
    Metric("text.ocr_small_share", 1, "share", "master", "upper", 0.08,
           {**_FRAMES, "ocr": "tesseract --psm 11 on a 2x greyscale copy (vpg.tools.privacy.tesseract_words)",
            "player_width_px": 960, "small_px_at_player": 8.0,
            "formula": "share of read words whose box height * player_width_px / frame width is below small_px_at_player",
            "aggregate": "median over frames with at least one read word"},
           "On-screen text under a fixed 8 px at player width (needs tesseract). A fixed cut-off: product-UI text of 3 px "
           "and of 6 px both count as small, so it cannot compare UI legibility with the benchmarks; "
           "text.ocr_word_px.dense_min does."),
    Metric("text.ocr_word_px.dense_min", 1, "px", "master", "lower", 0.5,
           {**_FRAMES, **_OCR_WORDS, "dense": "a frame with at least 20 confidently read words (product UI, dashboards, data)",
            "formula": "per dense frame, the median word box height * player_width_px / frame width",
            "aggregate": "the smallest of those medians, so one unreadable UI shot shows"},
           "Product-UI legibility at player width: the smallest typical text size on any text-dense frame (needs "
           "tesseract; a film with no text-dense frame is not measured).", rule="R-LEG-001"),
    # Style frames (the look gate): the same formulas on a set of stills instead of frames sampled from a film. A still
    # set and a film are different bases, so look-stage gates compare stills only with the benchmarks' style frames.
    Metric("still.colourfulness.median", 1, "M", "stills", "both", 6.0,
           {**_STILLS, "formula": "Hasler-Suesstrunk M over every pixel", "aggregate": "median over the stills"},
           "How colourful the style frames are as a set."),
    Metric("still.ground_colourfulness.median", 1, "M", "stills", "both", 6.0,
           {**_STILLS, **_BLOCKS, "ground": "flat blocks connected to the frame border through flat blocks",
            "formula": "Hasler-Suesstrunk M over ground pixels", "aggregate": "median over stills whose ground covers at least 10%"},
           "How colourful the style frames' ground is."),
    Metric("still.coverage.median", 1, "share", "stills", "both", 0.06,
           {**_STILLS, **_BLOCKS, "formula": "share of blocks whose edge is above edge_threshold", "aggregate": "median over the stills"},
           "How much of each style frame carries drawn content."),
    Metric("still.ocr_small_share", 1, "share", "stills", "upper", 0.08,
           {**_STILLS, "ocr": "tesseract --psm 11 on a 2x greyscale copy (vpg.tools.privacy.tesseract_words)",
            "player_width_px": 960, "small_px_at_player": 8.0,
            "formula": "share of read words whose box height * player_width_px / still width is below small_px_at_player",
            "aggregate": "median over stills with at least one read word"},
           "Style-frame text under a fixed 8 px at player width (needs tesseract). Like text.ocr_small_share it cannot "
           "tell 3 px UI text from 6 px; the look gate compares still.ocr_word_px.median per role."),
    Metric("still.ocr_word_px.median", 1, "px", "stills", "lower", 0.5,
           {**_STILLS, **_OCR_WORDS, "formula": "per still with at least 3 confidently read words, the median word box "
                                                "height * player_width_px / still width",
            "aggregate": "median over those stills; evidence.per_role holds each role's own"},
           "Text size in the style frames at player width (needs tesseract); the look gate compares each role with the "
           "same role in the benchmarks.", rule="R-LEG-001"),
]

METRICS: dict[str, Metric] = {m.id: m for m in METRIC_LIST}


def metric(metric_id: str) -> Metric:
    try:
        return METRICS[metric_id]
    except KeyError:
        raise KeyError(f"unknown metric '{metric_id}'; known: {', '.join(sorted(METRICS))}") from None


def measured(metric_id: str, value: float | int, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """One measurement record. `evidence` names where the value came from (timecodes, frames, files)."""
    m = metric(metric_id)
    v = float(value)
    if not math.isfinite(v):
        return not_measured(metric_id, f"the value is not finite ({value})")
    return {"metric": m.id, "version": m.version, "basis": m.basis_sha, "unit": m.unit,
            "value": round(v, 4), "evidence": evidence or {}}


OCR_NOT_RUN = "OCR was not requested"


def ocr_not_run(why: Any) -> bool:
    """A not-measured reason meaning OCR was never asked for (vpg.tools.craft words it one of these two ways), as opposed
    to OCR that ran and found too little text, or could not run."""
    return isinstance(why, str) and (why == OCR_NOT_RUN or why.endswith(" and --ocr"))


def not_measured(metric_id: str, why: str) -> dict[str, Any]:
    """A metric that could not be measured says so. It is never a pass."""
    m = metric(metric_id)
    return {"metric": m.id, "version": m.version, "basis": m.basis_sha, "unit": m.unit, "value": None,
            "not_measured": why, "evidence": {}}


def comparable(a: dict[str, Any] | None, b: dict[str, Any] | None) -> str | None:
    """None when both records measure the same thing the same way; otherwise why they must not be compared."""
    if not a or not b:
        return "a measurement is missing"
    if a.get("metric") != b.get("metric"):
        return f"different metrics ({a.get('metric')} vs {b.get('metric')})"
    if a.get("value") is None or b.get("value") is None:
        return "not measured: " + str(a.get("not_measured") or b.get("not_measured") or "no value")
    if (a.get("version"), a.get("basis")) != (b.get("version"), b.get("basis")):
        return (f"basis changed (v{a.get('version')}/{a.get('basis')} vs v{b.get('version')}/{b.get('basis')}); "
                "re-measure the older one under the current basis before comparing")
    return None


def current(record: dict[str, Any] | None) -> bool:
    """Was the record measured under the metric's current version and basis?"""
    if not record or record.get("metric") not in METRICS:
        return False
    m = METRICS[record["metric"]]
    return record.get("version") == m.version and record.get("basis") == m.basis_sha


def catalog() -> list[dict[str, Any]]:
    return [m.describe() for m in METRIC_LIST]
