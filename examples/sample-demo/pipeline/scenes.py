"""N13 command: compose one frame per storyboard beat, cued to the measured narration, and export the text-layer manifest.

Every word, logo and ring drawn here is recorded (font, px, colours, box, time), and the legibility, design and accuracy
audits read that manifest. A static composition plus a subtle push-in keeps the example small; a real production binds
its deterministic renderer here.
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from _ctx import load, read_json, done

ctx = load()
root, wd = Path(ctx["project_dir"]), Path(ctx["workdir"])
beats = read_json(ctx["inputs"]["storyboard"][-1])["beats"]
spans = read_json(ctx["inputs"]["timings"][-1])
assets = ctx["inputs_items"]["assets"]
params = read_json(ctx["agent_outputs"]["scene_params"]) if ctx["agent_outputs"].get("scene_params") else {}
FONT = root / "assets/fonts/MonaSansVF.ttf"
C = {"ink": "#1A1F26", "paper": "#FFFFFF", "brand": "#0B5CAD", "mist": "#EEF1F4", "slate": "#5A6572"}
by_unit = {u["unit"]: u for u in spans["units"]}
total = spans["duration_s"]


def font(px, weight):
    f = ImageFont.truetype(str(FONT), px)
    f.set_variation_by_axes([100, weight, 0])
    return f


layers, scenes = [], []
starts = [0.0] + [max(0.0, by_unit[b["unit_ids"][0]]["start"] - 0.25) for b in beats[1:]]
for i, b in enumerate(beats):
    start, end = starts[i], (starts[i + 1] if i + 1 < len(beats) else total)
    lay = b.get("layout", "diagram")
    asset = Path(assets[b["asset_ids"][0]])
    im = Image.open(asset).convert("RGB").resize((1920, 1080))
    d = ImageDraw.Draw(im)

    def text(t, x, y, px, weight, fg, bg, essential=True):
        f = font(px, weight)
        x0, y0, x1, y1 = d.textbbox((x, y), t, font=f)
        d.text((x, y), t, font=f, fill=C[fg])
        layers.append({"id": f"L{len(layers) + 1:02d}", "scene": b["id"], "kind": "text", "text": t, "font": "Mona Sans", "font_px": px,
                       "bold": weight >= 600, "color": C[fg], "bg": C[bg], "bbox": [x0, y0, x1 - x0, y1 - y0], "start": round(start, 3),
                       "end": round(end, 3), "opacity": 1.0, "essential": essential})

    texts = b.get("on_screen_text", [])
    if lay == "title":
        logo = Image.open(assets[b["asset_ids"][1]]).convert("RGBA").resize((128, 128))
        im.paste(logo, (120, 120), logo)
        layers.append({"id": f"L{len(layers) + 1:02d}", "scene": b["id"], "kind": "logo", "asset": "assets/brand/northwind-mark.png",
                       "bbox": [120, 120, 128, 128], "start": round(start, 3), "end": round(end, 3), "opacity": 1.0, "essential": True})
        d = ImageDraw.Draw(im)
        text(texts[0], 120, 400, params.get("title_px", 92), 800, "paper", "brand")
        if len(texts) > 1:
            text(texts[1], 124, 530, 40, 500, "paper", "brand")
    elif lay == "diagram":
        text(texts[0], 160, 330, 38, 600, "slate", "paper")
        if len(texts) > 1:
            text(texts[1], 1180, 200, 40, 700, "ink", "paper")
    elif lay == "ui":
        text("Synthetic data", 1560, 110, 26, 600, "ink", "mist", essential=False)
        regions = read_json(root / "sources/ui/dashboard-regions.json")["regions"]
        x, y, w, h = regions["outlook_card"]
        ring_start = max(start, by_unit[b["unit_ids"][0]]["start"] + 0.4)
        d.rounded_rectangle([x - 10, y - 10, x + w + 10, y + h + 10], radius=20, outline=C["ink"], width=6)
        layers.append({"id": f"L{len(layers) + 1:02d}", "scene": b["id"], "kind": "ring", "bbox": [x - 10, y - 10, w + 20, h + 20],
                       "start": round(ring_start, 3), "end": round(end, 3), "opacity": 1.0, "essential": True})
    frame = wd / f"{b['id']}.png"
    im.save(frame)
    scenes.append({"beat": b["id"], "image": frame.name, "start": round(start, 3), "end": round(end, 3), "motion": "push-in",
                   "transition": "crossfade"})
(wd / "scenes.json").write_text(json.dumps({"runtime_s": round(total, 3), "scenes": scenes}, indent=1))
(wd / "text-layers.json").write_text(json.dumps({"frame": {"w": 1920, "h": 1080}, "layers": layers}, indent=1))
done(ctx, {"scenes": "scenes.json", "text_layers": "text-layers.json", "scene_frames": [s["image"] for s in scenes]},
     {"scenes": len(scenes), "text_layers": len(layers)})
