"""Design-system tools (N09 completion, N17 review): palette contrast, token compliance, logo integrity, prohibited patterns.

design.md front-matter is the machine contract:
  palette:          {name: "#RRGGBB"}
  text_pairs:       [{fg: name, bg: name, use: body|large}]  the only foreground/background pairs text may use
  fonts:            ["Family"]                                 approved families
  approved_assets:  [{path, sha256, role}]                     logos and supplied marks, used as delivered
  prohibited:       ["font:Inter", "color:#FFBF00", "effect:gradient-on-white-card"]
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..context import CheckResult, NodeResult
from ..util import open_regular, sha256_file, write_json_atomic
from ._common import contrast, finding, thresholds
from . import regress


def _fm(ctx) -> dict:
    return ctx.fm("design")


def check_palette(ctx) -> CheckResult:
    fm, th = _fm(ctx), thresholds(ctx)["text"]
    pal = {k: v.upper() for k, v in (fm.get("palette") or {}).items()}
    msgs = []
    if not pal:
        msgs.append("design.md front-matter has no palette")
    if not fm.get("fonts"):
        msgs.append("design.md front-matter has no approved fonts")
    if not fm.get("prohibited"):
        msgs.append("design.md must list prohibited patterns (defaults are decisions)")
    for p in fm.get("text_pairs", []):
        if p["fg"] not in pal or p["bg"] not in pal:
            msgs.append(f"text pair {p} uses an undefined palette name")
            continue
        need = th["min_contrast_large"] if p.get("use") == "large" else th["min_contrast_normal"]
        ratio = contrast(pal[p["fg"]], pal[p["bg"]])
        if ratio < need:
            msgs.append(f"text pair {p['fg']} on {p['bg']} is {ratio}:1, below {need}:1 for {p.get('use', 'body')} text")
    if not fm.get("text_pairs"):
        msgs.append("design.md must declare the approved text/background pairs")
    for a in fm.get("approved_assets", []):
        path = ctx.project_dir / a["path"]
        if not path.exists():
            msgs.append(f"approved asset {a['path']} is missing")
        elif a.get("sha256") and sha256_file(path) != a["sha256"]:
            msgs.append(f"approved asset {a['path']} does not match its recorded hash (substituted or edited)")
    return CheckResult(not msgs, msgs)


def compliance(ctx) -> NodeResult:
    """N17 pre-check over the rendered text-layer manifest exported by the composition step."""
    fm = _fm(ctx)
    pal = {v.upper() for v in (fm.get("palette") or {}).values()}
    pairs = {((fm["palette"][p["fg"]]).upper(), (fm["palette"][p["bg"]]).upper()) for p in fm.get("text_pairs", [])}
    fonts = {f.lower() for f in fm.get("fonts", [])}
    prohibited = [p.lower() for p in fm.get("prohibited", [])]
    approved = {a["path"]: a for a in fm.get("approved_assets", [])}
    layers = (ctx.load("text_layers") or {}).get("layers", [])
    findings = []
    for layer in layers:
        where = f"layer {layer['id']} ({layer.get('scene')}, {layer.get('start')}–{layer.get('end')}s)"
        if layer.get("kind", "text") == "text":
            fg, bg = layer.get("color", "").upper(), layer.get("bg", "").upper()
            if fg not in pal or bg not in pal:
                findings.append(finding("design:tokens", "DES-TOKENS", "P2", f"{where} uses off-palette colour {fg} on {bg}", traced="n13_motion_scenes",
                                        key="text_layers", element=layer["id"], fix="use a design.md palette token"))
            elif (fg, bg) not in pairs:
                findings.append(finding("design:pairs", "DES-TOKENS", "P3", f"{where} uses {fg} on {bg}, which is not an approved text pair",
                                        traced="n13_motion_scenes", key="text_layers", element=layer["id"], fix="use an approved text pair"))
            if layer.get("font", "").lower() not in fonts:
                findings.append(finding("design:type", "DES-TYPE", "P2", f"{where} uses font '{layer.get('font')}', not an approved family",
                                        traced="n13_motion_scenes", key="text_layers", element=layer["id"], fix="use the bookend type family in its defined role"))
            for p in prohibited:
                kind, _, val = p.partition(":")
                if (kind == "font" and layer.get("font", "").lower() == val) or (kind == "color" and val.upper() in (fg, bg)):
                    findings.append(finding("design:prohibited", "DES-PROHIBITED", "P2", f"{where} uses prohibited pattern {p}",
                                            traced="n13_motion_scenes", key="text_layers", element=layer["id"]))
        if layer.get("kind") == "logo":
            a = approved.get(layer.get("asset", ""))
            if a is None:
                findings.append(finding("design:logo", "DES-LOGO", "P1", f"{where} shows a logo that is not a supplied approved asset",
                                        traced="n11_asset_generation", key="assets", element=layer["id"], fix="use the supplied mark; never regenerate a logo"))
                continue
            path = ctx.project_dir / a["path"]
            with open_regular(path) as f, Image.open(f) as im:
                ar = im.width / im.height
            w, h = layer["bbox"][2], layer["bbox"][3]
            if abs((w / h) - ar) / ar > 0.01:
                findings.append(finding("design:logo", "DES-LOGO", "P1", f"{where} deforms the logo (aspect {w / h:.3f} vs {ar:.3f})",
                                        traced="n13_motion_scenes", key="text_layers", element=layer["id"], fix="scale uniformly"))
            if a.get("sha256") and sha256_file(path) != a["sha256"]:
                findings.append(finding("design:logo", "DES-LOGO", "P1", f"{where}: logo file differs from the approved asset",
                                        traced="n11_asset_generation", key="assets", element=layer["id"]))
    reg = regress.check(ctx)
    findings += reg.findings
    report = {"layers": len(layers), "regression": reg.data, "findings": [f.model_dump() for f in findings]}
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "design-report.json", report)
    return NodeResult(outputs={"design_report": "design-report.json", **reg.outputs}, findings=findings)
