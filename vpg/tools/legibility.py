"""Accessibility and legibility (N18 pre-check).

Checks the text-layer manifest exported by the composition step (what is actually drawn, when and where), the
caption sync and the master's luminance for flashing. Thresholds come from config/defaults.yaml and the
project's evaluation.md.
"""

from __future__ import annotations

from ..context import NodeResult
from ..util import read_text, write_json_atomic
from ._common import contrast, finding, luma_series, reading_words, thresholds
from .captions import parse_srt, sync
from . import layout

N13 = "n13_motion_scenes"


def max_simultaneous(spans: list[tuple[float, float]]) -> tuple[int, float]:
    """The most spans on screen at one moment, and the first moment it happens. A span ending at t is gone before one
    starting at t appears, so progressive disclosure (reveal one idea, then the next) is never counted as a crowd."""
    spans = [(round(start, 6), round(end, 6)) for start, end in spans]   # 0.1 + 0.2 ends when 0.3 starts
    events = sorted([(start, 1) for start, end in spans if end > start] + [(end, -1) for start, end in spans if end > start])
    now_on, best, at = 0, 0, 0.0
    for t, delta in events:
        now_on += delta
        if now_on > best:
            best, at = now_on, t
    return best, at


def scene_limits(density: dict) -> dict[str, int]:
    """Per-scene density limits from evaluation.md (`thresholds.density.scene_limits`, e.g. {B02: 18}). Each replaces the
    film-wide limit for its scene only, so an exception approved for one beat never loosens the rest of the film."""
    limits = density.get("scene_limits") or {}
    if not isinstance(limits, dict):
        raise ValueError("evaluation.md thresholds.density.scene_limits must map scene ids to limits, e.g. {B02: 18}")
    bad = [str(k) for k, v in limits.items() if isinstance(v, bool) or not isinstance(v, int) or v < 1]
    if bad:
        raise ValueError(f"evaluation.md thresholds.density.scene_limits needs a whole number of text elements (≥ 1) for {', '.join(bad)}")
    return {str(k): v for k, v in limits.items()}


def check(ctx) -> NodeResult:
    th = thresholds(ctx)
    t = th["text"]
    manifest = ctx.load("text_layers") or {}
    frame_h = manifest.get("frame", {}).get("h", 1080)
    scale = frame_h / 1080.0
    layers = manifest.get("layers", [])
    findings = []
    caption_on = bool(ctx.artifact_path("captions"))
    for layer in layers:
        if layer.get("kind", "text") != "text":
            if layer.get("kind") == "ring" and layer["end"] - layer["start"] < th["highlight"]["min_ring_s"]:
                findings.append(finding("legibility:ring", "LEG-RING", "P3", f"highlight ring {layer['id']} lasts {layer['end'] - layer['start']:.2f}s (< {th['highlight']['min_ring_s']}s)",
                                        traced=N13, key="text_layers", element=layer["id"], fix="hold each highlight at least 1 s"))
            continue
        where = f"'{layer['text'][:32]}' ({layer['scene']}, {layer['start']:.2f}–{layer['end']:.2f}s)"
        px = layer["font_px"] / scale
        if px < t["min_font_px_at_1080"]:
            findings.append(finding("legibility:size", "LEG-SIZE", "P2", f"{where} is {px:.0f}px at 1080p (< {t['min_font_px_at_1080']}px)",
                                    traced=N13, key="text_layers", element=layer["id"], fix="enlarge the text or cut it"))
        ratio = contrast(layer["color"], layer["bg"])
        large = px >= t["large_text_px"] or (layer.get("bold") and px >= t["large_text_bold_px"])
        need = t["min_contrast_large"] if large else t["min_contrast_normal"]
        if ratio < need:
            findings.append(finding("legibility:contrast", "LEG-CONTRAST", "P2", f"{where} contrast {ratio}:1 < {need}:1",
                                    traced=N13, key="text_layers", element=layer["id"], fix="use an approved high-contrast text pair"))
        n_words = len(reading_words(layer["text"]))
        need_s = max(t["dwell_min_s"], t["dwell_s_per_word"] * n_words)
        if layer["end"] - layer["start"] + 1e-6 < need_s:
            findings.append(finding("legibility:dwell", "LEG-DWELL", "P2", f"{where} is on screen {layer['end'] - layer['start']:.2f}s; {n_words} words need ≥{need_s:.2f}s",
                                    traced=N13, key="text_layers", element=layer["id"], fix="hold the text longer or cut words"))
        x, y, w, h = layer["bbox"]
        sa = t["safe_area"]
        if x < sa["x0"] * scale or x + w > sa["x1"] * scale or y < sa["y0"] * scale or y + h > sa["y1"] * scale:
            findings.append(finding("legibility:safe-area", "LEG-SAFE", "P2", f"{where} leaves the title-safe area", traced=N13, key="text_layers", element=layer["id"],
                                    fix="keep essential text inside the title-safe margins"))
        if caption_on and layer.get("essential", True) and y + h > t["caption_band_top_px"] * scale:
            findings.append(finding("legibility:caption-band", "LEG-SAFE", "P2", f"{where} sits in the caption band while captions are on",
                                    traced=N13, key="text_layers", element=layer["id"], fix="move essential content above the caption band"))
        if 0 < layer.get("opacity", 1.0) < 1.0 and layer["end"] - layer["start"] > t["ghost_opacity_min_s"]:
            findings.append(finding("legibility:ghost", "LEG-GHOST", "P2", f"{where} is held at partial opacity {layer['opacity']} (ghost text)",
                                    traced=N13, key="text_layers", element=layer["id"], fix="step de-emphasised text down to a solid grey token, never partial opacity"))
    per_scene: dict[str, list[tuple[float, float]]] = {}
    for layer in layers:
        if layer.get("kind", "text") == "text":
            per_scene.setdefault(layer["scene"], []).append((float(layer["start"]), float(layer["end"])))
    limit = th["density"]["max_text_layers_per_scene"]
    own_limits = scene_limits(th["density"])
    for scene, spans in per_scene.items():
        n, at = max_simultaneous(spans)
        cap = own_limits.get(scene, limit)
        if n > cap:
            whose = ", this scene's own limit in evaluation.md" if scene in own_limits else ""
            findings.append(finding("legibility:density", "LEG-DENSITY", "P2", f"scene {scene} shows {n} text elements at once at {at:.2f}s (> {cap}{whose}); one message per frame",
                                    traced=N13, key="text_layers", element=scene, fix="progressive disclosure: reveal one idea per spoken cue"))
    cap = ctx.artifact_path("captions")
    timings = ctx.load("timings") or {}
    spans = timings.get("units", [])
    cap_report = (sync(parse_srt(read_text(cap)), spans, th["captions"], measured=timings.get("words"))
                  if cap and spans else {"cues": 0, "findings": []})
    findings += cap_report.pop("findings")
    luma = luma_series(ctx.artifact_path("master")) if ctx.artifact_path("master") else []
    fps = float(ctx.fm("production").get("fps", 30))
    flashes = [i for i in range(1, len(luma)) if abs(luma[i] - luma[i - 1]) >= th["flash"]["luma_delta"]]
    worst = 0
    for i in flashes:
        window = [k for k in flashes if i <= k < i + fps]
        worst = max(worst, len(window) // 2)
    if worst > th["flash"]["max_flashes_per_s"]:
        findings.append(finding("legibility:flash", "LEG-FLASH", "P1", f"{worst} flashes within one second (> {th['flash']['max_flashes_per_s']})",
                                traced=N13, key="scenes", fix="remove the strobing transition"))
    report = {"text_layers": len(layers), "captions": cap_report, "flash_pairs_max_per_s": worst,
              "findings": [f.model_dump() for f in findings]}
    unused = sorted(set(own_limits) - set(per_scene))
    if unused:   # an exception naming no scene of this film (a typo, a renamed beat) exempts nothing; say so
        report["unused_scene_limits"] = unused
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    layout_result = NodeResult()
    caption_script = NodeResult()
    if hasattr(ctx, "current"):   # engine context: surface optional N13/N14 measurements honestly as nits when absent
        layout_result = layout.check(ctx)
        caption_script = layout.check_caption_script(ctx)
        findings += layout_result.findings + caption_script.findings
        if layout_result.outputs:
            report["layout"] = layout_result.data
        if caption_script.data:
            report["caption_script"] = caption_script.data
    report["findings"] = [f.model_dump() for f in findings]
    write_json_atomic(ctx.workdir / "accessibility-report.json", report)
    outputs = {"accessibility_report": "accessibility-report.json", **layout_result.outputs}
    return NodeResult(outputs=outputs, findings=findings,
                      data={"caption_lags": cap_report.get("lags", 0), "flash_max_per_s": worst})
