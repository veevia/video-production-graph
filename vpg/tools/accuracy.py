"""Product-accuracy tools (N07 completion, N19 review): terminology, prohibited claims, verified claims, invented UI.

product.md front-matter is the machine contract:
  terminology:      [{term, source}]            words the film may use for the product (engineers' terms)
  prohibited:       [{term, use_instead, source}] words it must not use (e.g. "predicts" when the product shows an outlook)
  verified_claims:  [{id, text, source}]        the only claims a script unit may make
"""

from __future__ import annotations

import re

from ..context import CheckResult, NodeResult
from ..util import write_json_atomic
from ._common import finding


def contract(ctx) -> dict:
    fm = ctx.fm("product")
    return {"terms": fm.get("terminology", []), "prohibited": fm.get("prohibited", []),
            "claims": {c["id"]: c for c in fm.get("verified_claims", [])}}


def prohibited_hits(text: str, prohibited: list[dict]) -> list[dict]:
    hits = []
    for p in prohibited:
        if re.search(rf"\b{re.escape(p['term'])}\b", text, re.IGNORECASE):
            hits.append(p)
    return hits


def _units(script: dict) -> list[dict]:
    return [u for ch in script.get("chapters", []) for u in ch.get("units", [])]


def script_problems(ctx, script: dict) -> list[tuple[str, str, str]]:
    """(dimension, message, fix) for every accuracy problem in the narration."""
    c = contract(ctx)
    known = {t["term"].lower() for t in c["terms"]}
    out = []
    for u in _units(script):
        for p in prohibited_hits(u["text"], c["prohibited"]):
            out.append(("ACC-TERMS", f"unit {u['id']} uses prohibited term '{p['term']}'", f"use '{p.get('use_instead', '')}' ({p.get('source', '')})"))
        for cid in u.get("claims", []):
            if cid not in c["claims"]:
                out.append(("ACC-CLAIMS", f"unit {u['id']} makes claim {cid}, which is not in product.md verified_claims", "remove the claim or verify it and add it"))
        for t in u.get("terms", []):
            if t.lower() not in known:
                out.append(("ACC-TERMS", f"unit {u['id']} relies on term '{t}', which is not in product.md terminology", "use the source term or add it with a source"))
    return out


def check_script(ctx) -> CheckResult:
    script = ctx.load("script") or {}
    probs = script_problems(ctx, script)
    return CheckResult(not probs, [m for _, m, _ in probs])


def check_film(ctx) -> NodeResult:
    """N19 pre-check: narration and on-screen text against product truth; product UI must be captured, never generated."""
    script = ctx.load("script") or {}
    findings = [finding("accuracy:narration", dim, "P1" if dim == "ACC-CLAIMS" else "P2", msg, traced="n07_script_development",
                        key="script", fix=fix) for dim, msg, fix in script_problems(ctx, script)]
    c = contract(ctx)
    layers = (ctx.load("text_layers") or {}).get("layers", [])
    for layer in layers:
        for p in prohibited_hits(layer.get("text", ""), c["prohibited"]):
            findings.append(finding("accuracy:on-screen", "ACC-TERMS", "P2", f"on-screen text '{layer['text']}' uses prohibited term '{p['term']}'",
                                    evidence=f"layer {layer['id']} at {layer.get('start')}s", traced="n13_motion_scenes", key="text_layers", element=layer["id"],
                                    fix=f"use '{p.get('use_instead', '')}'"))
    for layer in layers:   # #22: a declared redaction must be an opaque bar; a blur or a veil can leave a name readable
        if layer.get("kind") != "redaction":
            continue
        method, opacity = layer.get("method", "bar"), layer.get("opacity", 1)
        why = ("a blur" if method == "blur" else f"'{method}', not a bar" if method != "bar" else
               f"{float(opacity):.0%} opaque" if isinstance(opacity, (int, float)) and opacity < 1 else "")
        if why:
            findings.append(finding("accuracy:redaction", "ACC-UI", "P1",
                                    f"redaction {layer.get('id')} ({layer.get('scene')}, {layer.get('start')}–{layer.get('end')}s) is {why}: "
                                    "real UI pixels may stay readable under it (R-ACC-004)",
                                    traced="n13_motion_scenes", key="text_layers", element=layer.get("id"),
                                    fix="cover the region with an opaque bar (method: bar, opacity 1)"))
    for a in ctx.state.production.asset_plan:
        if a.get("role") == "product-ui" and a.get("source_type") == "generated":
            findings.append(finding("accuracy:ui-integrity", "ACC-UI", "P1", f"asset {a['id']} shows product UI but is generated (invented UI)",
                                    traced="n10_asset_planning", key="asset_plan", element=a["id"], fix="capture the real prototype screen instead"))
    report = {"findings": [f.model_dump() for f in findings], "terms": len(c["terms"]), "prohibited": len(c["prohibited"]),
              "claims": len(c["claims"]), "units": len(_units(script)), "text_layers": len(layers),
              "redactions": sum(layer.get("kind") == "redaction" for layer in layers)}
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "accuracy-report.json", report)
    return NodeResult(outputs={"accuracy_report": "accuracy-report.json"}, findings=findings, data={"accuracy_findings": len(findings)})
