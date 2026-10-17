"""Scene checks (N13): every beat rendered, contiguous timing, a valid text-layer manifest."""

from __future__ import annotations

from ..context import CheckResult


def check(ctx) -> CheckResult:
    scenes = (ctx.load("scenes") or {}).get("scenes", [])
    beats = [b["id"] for b in (ctx.load("storyboard") or {}).get("beats", [])]
    layers = (ctx.load("text_layers") or {}).get("layers", [])
    msgs = []
    have = [s["beat"] for s in scenes]
    if have != beats:
        msgs.append(f"scenes {have} do not match the storyboard beats {beats}")
    for a, b in zip(scenes, scenes[1:]):
        if abs(b["start"] - a["end"]) > 1e-3:
            msgs.append(f"scene timing gap or overlap between {a['beat']} and {b['beat']}")
    base = ctx.workdir
    for s in scenes:
        if not (base / s["image"]).exists() and not (ctx.project_dir / s["image"]).exists():
            msgs.append(f"scene {s['beat']} image {s['image']} is missing")
    known = set(have)
    for layer in layers:
        if layer.get("scene") not in known:
            msgs.append(f"text layer {layer.get('id')} references unknown scene {layer.get('scene')}")
        if layer.get("kind", "text") == "text" and not layer.get("text", "").strip():
            msgs.append(f"text layer {layer.get('id')} is empty")
    if not layers:
        msgs.append("no text-layer manifest: legibility and design compliance could not be audited")
    return CheckResult(not msgs, msgs, metrics={"scenes": len(scenes), "text_layers": len(layers)})
