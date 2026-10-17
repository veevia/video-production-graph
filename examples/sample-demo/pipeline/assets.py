"""N11 command: produce each planned asset at spec.

Supplied and captured assets are copied untouched (never redrawn). Rendered plates and diagrams are drawn text-free:
every word on screen is drawn in N13, so the text-layer manifest is complete.
"""
import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from _ctx import done, load, read_json

ctx = load()
root = Path(ctx["project_dir"])
plan = read_json(ctx["inputs"]["asset_plan"][-1])["assets"]
todo = set(ctx.get("items") or [a["id"] for a in plan])
out = {}
for a in plan:
    if a["id"] not in todo:
        continue
    w, h = a["spec"]["w"], a["spec"]["h"]
    dest = Path(ctx["workdir"]) / f"{a['id']}.png"
    if a["source_type"] in ("supplied", "captured"):
        shutil.copy2(root / a["ref"], dest)            # untouched: provenance is the source file itself
    elif a["role"] == "title-plate":
        im = Image.new("RGB", (w, h), "#0B5CAD")
        d = ImageDraw.Draw(im)
        pts = [(x, 820 - 40 * math.sin(x / 140)) for x in range(0, w + 1, 12)]
        d.line(pts, fill="#FFFFFF", width=6)
        im.save(dest)
    elif a["role"] == "diagram":
        im = Image.new("RGB", (w, h), "#FFFFFF")
        d = ImageDraw.Draw(im)
        d.rectangle([160, 400, 1760, 640], fill="#E6EEF7")
        pts = [(160 + i * 50, 540 - 50 * math.sin(i / 3) - (190 if i == 26 else 0)) for i in range(33)]
        d.line(pts, fill="#1A1F26", width=7)
        x, y = pts[26]
        d.ellipse([x - 26, y - 26, x + 26, y + 26], outline="#E03E2D", width=9)
        im.save(dest)
    else:
        raise SystemExit(f"no recipe for asset {a['id']} ({a['role']}, {a['source_type']})")
    out[a["id"]] = dest.name
done(ctx, {"assets": out}, {"produced": sorted(out)})
