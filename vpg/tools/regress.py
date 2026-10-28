"""Baseline regression checks over N13 inventory artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..context import NodeResult
from ..util import PKG_ROOT, read_json, write_json_atomic
from ._common import finding

N13 = "n13_motion_scenes"
DEFAULT_THRESHOLDS = {
    "content_retained": 0.7,
    "demo_cast": 0.8,
    "demo_pointer": 0.8,
    "demo_zoom_max": 1.25,
    "demo_static_extra_s": 1.5,
}
MAX_DEPARTURE_LITERAL = 120


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9%µ/·]+", " ", text.lower()).strip()


def by_chapter(inv: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    samples = inv.get("samples") or []
    for cid, start, end in inv.get("chapters") or []:
        out[str(cid)] = [payload for t, payload in samples if float(start) <= float(t) < float(end)]
    return out


def demo_metrics(samples: list[dict], fps: float, still_pattern: str = r"^[as]\d\d-") -> dict[str, Any]:
    caps = [s.get("cap") for s in samples if s.get("cap")]
    still_re = re.compile(still_pattern)
    stills = {s.get("cap") for s in samples if s.get("cap") and (s.get("still") is True or (s.get("still") is None and still_re.match(str(s.get("cap") or ""))))}
    cast = {c for c in caps if c not in stills}
    pointer = sum(1 for s in samples if s.get("pointer")) / max(1, len(samples))
    ring = sum(1 for s in samples if s.get("ring")) / max(1, len(samples))
    zooms = [float(s["zoom"]) for s in samples if s.get("zoom") is not None]
    longest = run = 0.0
    prev = None
    moves = 0
    step = 1 / max(float(fps or 1), 0.001)
    for s in samples:
        key = (s.get("cap"), round(float(s.get("zoom") or 0), 2))
        if key == prev:
            run += step
        else:
            if prev is not None:
                moves += 1
            run = step
        prev = key
        longest = max(longest, run)
    return {"cast_frames": len(cast), "stills": len(stills), "pointer_share": round(pointer, 3), "ring_share": round(ring, 3),
            "mean_zoom": round(sum(zooms) / len(zooms), 3) if zooms else None, "longest_static_s": round(longest, 3),
            "view_changes": moves}


def _path(ctx, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else ctx.project_dir / p


def _load_optional_json(ctx, value: str | None) -> tuple[Any | None, str | None]:
    p = _path(ctx, value)
    if p is None:
        return None, None
    try:
        return read_json(p), None
    except Exception as exc:
        return None, f"{p}: {type(exc).__name__}: {exc}"


SCHEMA_ERR = "schema: "


def load_baseline(ctx, value: str | None, schema_name: str) -> tuple[Any | None, str | None]:
    """A configured baseline file, read and checked against its artifact schema once (#154), so a malformed baseline
    is a BAD_BASELINE finding and never a crash in a later step. Schema errors start with SCHEMA_ERR."""
    data, err = _load_optional_json(ctx, value)
    if err or data is None:
        return data, err
    import jsonschema
    schema = read_json(PKG_ROOT / "schemas" / "artifacts" / f"{schema_name}.schema.json")
    errs = sorted(jsonschema.validators.validator_for(schema)(schema).iter_errors(data), key=lambda e: list(e.path))
    if errs:
        return None, SCHEMA_ERR + f"{value} does not match the {schema_name} schema: " + "; ".join(
            f"{e.json_path}: {e.message}" for e in errs[:3])
    return data, None


def _valid_citations(ctx) -> set[str]:
    feedback = set()
    for f in getattr(getattr(ctx.state, "evaluation", None), "feedback", []):
        actor = getattr(f, "actor", "")
        triage = getattr(f, "triage", "")
        status = getattr(f, "status", "")
        if status == "dismissed" or triage == "dismissed":
            continue
        if not str(actor).startswith("agent:") or triage == "accepted":
            feedback.add(getattr(f, "id", ""))
    changes = {getattr(c, "id", "") for c in getattr(getattr(ctx.state, "history", None), "change_requests", [])
               if getattr(c, "status", "") in ("approved", "applied")}
    return {x for x in feedback | changes if re.match(r"^(F|CR)-?\d+", x)}


def _compile_departures(raw: list[dict]) -> tuple[list[dict], list]:
    compiled, findings = [], []
    for d in raw:
        if d.get("regex") is True:
            findings.append(finding("regress:BAD_DEPARTURE", "REG-BASELINE", "P1",
                                    "departure regex is not supported; list literal strings in match instead",
                                    traced=N13, key="inventory"))
            continue
        item = dict(d)
        raw_match = d.get("match")
        pats = raw_match if isinstance(raw_match, list) else [raw_match]
        literals = []
        for pat in pats:
            text = norm(str(pat or ""))
            if len(text) < 4 or len(text) > MAX_DEPARTURE_LITERAL:
                findings.append(finding("regress:BAD_DEPARTURE", "REG-BASELINE", "P1",
                                        f"departure literal {pat!r} must normalize to 4–{MAX_DEPARTURE_LITERAL} characters",
                                        traced=N13, key="inventory"))
            else:
                literals.append(text)
        if not literals:
            continue
        item["_literals"] = literals
        item["_match"] = lambda text, lits=literals: any(lit in norm(text) for lit in lits)
        item["_absorbed"] = []
        compiled.append(item)
    return compiled, findings


def _departure_for(departures: list[dict], cid: str, text: str) -> dict | None:
    """The departure that declares this removed text: an exact whole-text literal wins over a broader substring one."""
    scoped = [d for d in departures if d.get("chapter") in (None, cid) and d["_match"](text)]
    dep = next((d for d in scoped if norm(text) in d.get("_literals", [])), scoped[0] if scoped else None)
    if dep is not None:
        dep.setdefault("_absorbed", []).append(text)
    return dep


def _inventory_path(ctx) -> Path | None:
    current_recs = ctx.current("inventory") if hasattr(ctx, "current") else []
    recs = _fresh_records(ctx, "inventory") if hasattr(ctx, "current") else []
    if recs:
        return ctx.project_dir / recs[-1].path
    if current_recs:
        return None
    p = ctx.artifact_path("inventory") if hasattr(ctx, "artifact_path") else None
    return p if p and p.exists() else None


def _fresh_records(ctx, key: str, producer: str = N13):
    from ..freshness import fresh_current
    return fresh_current(ctx, key, producer)


SEAM_CHROME_S = 1.0   # how long the previous chapter's number may linger into the next chapter and still be chrome


def _seam_chrome(inv: dict, cid: str) -> set[str]:
    """The previous chapter's number, when this chapter shows it only within its first SEAM_CHROME_S seconds: the
    previous chapter's numbering still on screen at the seam, not content. Anywhere later it is compared as content,
    and so is the chapter's own number."""
    spans = sorted(((str(c), float(a), float(b)) for c, a, b in inv.get("chapters") or []), key=lambda x: x[1])
    idx = next((i for i, span in enumerate(spans) if span[0] == cid), 0)
    prev = re.match(r"^(\d+)", spans[idx - 1][0]) if idx > 0 else None
    if prev is None:
        return set()
    number, (_, start, end) = norm(prev.group(1)), spans[idx]
    offsets = [float(t) - start for t, p in inv.get("samples") or []
               if start <= float(t) < end and any(norm(str(x)) == number for x in p.get("texts", []))]
    return {number} if offsets and max(offsets) < SEAM_CHROME_S else set()


def _chapter_at(inv: dict, t: Any) -> str | None:
    try:
        sec = float(t)
    except (TypeError, ValueError):
        return None
    for cid, start, end in inv.get("chapters") or []:
        if float(start) <= sec < float(end):
            return str(cid)
    return None


def _layout_key(inv: dict, run: dict) -> tuple[str | None, str, str, str]:
    return (_chapter_at(inv, run.get("from")), str(run.get("kind") or ""), norm(str(run.get("text") or "")),
            norm(str(run.get("other") or "")))


def _layout_regressions(ctx, block: dict, baseline: dict, candidate: dict) -> tuple[list, dict[str, Any] | None]:
    base_path = (block.get("baseline") or {}).get("layout_audit") if isinstance(block.get("baseline"), dict) else None
    if not base_path:
        return [], None
    base_layout, err = load_baseline(ctx, base_path, "layout-audit")
    current_layout = ctx.current("layout_audit") if hasattr(ctx, "current") else []
    candidate_layout = ctx.load("layout_audit") if (hasattr(ctx, "load") and (_fresh_records(ctx, "layout_audit") or not hasattr(ctx, "current") or not current_layout)) else None
    if err or not base_layout:
        code = "regress:BAD_BASELINE" if err and err.startswith(SCHEMA_ERR) else "regress:LAYOUT_REGRESSION"
        return [finding(code, "REG-BASELINE", "P1",
                        f"configured baseline layout audit is unreadable: {err or 'missing regression.baseline.layout_audit'}",
                        traced=N13, key="layout_audit")], None
    if not candidate_layout:
        return [finding("regress:LAYOUT_REGRESSION", "REG-BASELINE", "P1",
                        "configured baseline layout comparison has no candidate layout_audit artifact",
                        traced=N13, key="layout_audit")], None
    try:
        inherited = {_layout_key(baseline, r) for r in base_layout.get("runs", [])}
        findings = []
        new_runs = []
        for run in candidate_layout.get("runs", []):
            key = _layout_key(candidate, run)
            if key in inherited:
                continue
            new_runs.append({"chapter": key[0], "kind": key[1], "from": run.get("from"), "text": run.get("text"), "other": run.get("other")})
            findings.append(finding("regress:LAYOUT_REGRESSION", "REG-BASELINE", "P1",
                                    f"LAYOUT_REGRESSION {key[0] or '?'}: {key[1]} at {float(run.get('from') or 0):.1f}s "
                                    f"was not present in the released baseline for that chapter",
                                    traced=N13, key="layout_audit", element=key[0] or key[1],
                                    fix="restore the baseline layout or record a cited departure"))
    except Exception as exc:
        return [finding("regress:BAD_BASELINE", "REG-BASELINE", "P1",
                        f"baseline layout audit has the wrong shape: {type(exc).__name__}: {exc}",
                        traced=N13, key="layout_audit")], None
    return findings, {"baseline_runs": len(base_layout.get("runs", [])), "candidate_runs": len(candidate_layout.get("runs", [])),
                      "new_runs": new_runs}


def check(ctx) -> NodeResult:
    block = getattr(ctx, "config", {}).get("regression")
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    if not isinstance(block, dict):
        report = {"applicable": False, "note": "REG-BASELINE not applicable: no released baseline was declared"}
        write_json_atomic(ctx.workdir / "regression-report.json", report)
        return NodeResult(outputs={"regression_report": "regression-report.json"}, data=report)
    th = {**DEFAULT_THRESHOLDS, **(block.get("thresholds") or {})}
    still_pattern = str(block.get("still_pattern") or r"^[as]\d\d-")
    findings = []
    cand_path = _inventory_path(ctx)
    candidate = ctx.load("inventory") if (hasattr(ctx, "load") and (cand_path or not hasattr(ctx, "current"))) else None
    if not candidate and cand_path and cand_path.exists():
        try:
            candidate = read_json(cand_path)
        except Exception as exc:
            findings.append(finding("regress:missing-inventory", "REG-BASELINE", "P1",
                                    f"configured regression gate cannot read candidate inventory: {type(exc).__name__}: {exc}",
                                    traced=N13, key="inventory"))
    if not candidate:
        findings.append(finding("regress:missing-inventory", "REG-BASELINE", "P1",
                                "configured regression gate has no candidate inventory artifact", traced=N13, key="inventory",
                                fix="register the N13 inventory.json output"))
        report = {"applicable": True, "findings": [f.model_dump() for f in findings]}
        write_json_atomic(ctx.workdir / "regression-report.json", report)
        return NodeResult(outputs={"regression_report": "regression-report.json"}, findings=findings, data=report)
    baseline, err = load_baseline(ctx, ((block.get("baseline") or {}).get("inventory") if isinstance(block.get("baseline"), dict) else None),
                                  "inventory")
    if err or not baseline:
        findings.append(finding("regress:BAD_BASELINE" if err and err.startswith(SCHEMA_ERR) else "regress:missing-baseline",
                                "REG-BASELINE", "P1",
                                f"configured regression gate cannot read baseline inventory: {err or 'missing regression.baseline.inventory'}",
                                traced=N13, key="inventory"))
        report = {"applicable": True, "findings": [f.model_dump() for f in findings]}
        write_json_atomic(ctx.workdir / "regression-report.json", report)
        return NodeResult(outputs={"regression_report": "regression-report.json"}, findings=findings, data=report)
    dep_data, dep_err = _load_optional_json(ctx, block.get("departures"))
    departures, departure_findings = _compile_departures((dep_data or {}).get("removals") or [])
    findings += departure_findings
    valid = _valid_citations(ctx)
    for d in departures:
        cites = [str(c) for c in d.get("cites") or []]
        if not cites or not any(c in valid for c in cites):
            findings.append(finding("regress:UNTRACED_DEPARTURE", "REG-BASELINE", "P1",
                                    f"UNTRACED_DEPARTURE {d.get('chapter') or '*'} / {d.get('match')}: cites {cites or 'none'} "
                                    "but no cited feedback id or approved change request is recorded in this run",
                                    traced=N13, key="inventory", fix="cite a recorded F… or approved CR-… id, or undo the departure"))
    if dep_err:
        findings.append(finding("regress:departures-unreadable", "REG-BASELINE", "P1",
                                f"regression departures file is unreadable: {dep_err}", traced=N13, key="inventory"))
    declared_departures = [{"chapter": d.get("chapter"), "match": d.get("match"), "why": d.get("why"),
                            "cites": [str(c) for c in d.get("cites") or []], "regex": bool(d.get("regex")),
                            "absorbed": d.get("_absorbed", [])} for d in departures]

    try:
        base_ch, cand_ch = by_chapter(baseline), by_chapter(candidate)
    except Exception as exc:
        findings.append(finding("regress:BAD_INVENTORY", "REG-BASELINE", "P1",
                                f"baseline regression inventory has the wrong shape: {type(exc).__name__}: {exc}",
                                traced=N13, key="inventory"))
        report = {"applicable": True, "baseline": (block.get("baseline") or {}).get("label"), "thresholds": th,
                  "declared_departures": declared_departures, "findings": [f.model_dump() for f in findings]}
        write_json_atomic(ctx.workdir / "regression-report.json", report)
        return NodeResult(outputs={"regression_report": "regression-report.json"}, findings=findings, data=report)
    metrics: dict[str, dict[str, Any]] = {}
    seam_chrome: dict[str, list[str]] = {}
    for cid, base_samples in base_ch.items():
        cand_samples = cand_ch.get(cid, [])
        bt = {norm(t): t for s in base_samples for t in s.get("texts", []) if norm(str(t))}
        ct = {norm(t): t for s in cand_samples for t in s.get("texts", []) if norm(str(t))}
        drop_b, drop_c = _seam_chrome(baseline, str(cid)), _seam_chrome(candidate, str(cid))
        ignored = sorted((drop_b & set(bt)) | (drop_c & set(ct)))
        if ignored:
            seam_chrome[str(cid)] = ignored
            bt = {k: v for k, v in bt.items() if k not in drop_b}
            ct = {k: v for k, v in ct.items() if k not in drop_c}
        removed = [bt[k] for k in bt if k not in ct]
        declared_removed = []
        absorbed_by_dep: dict[int, list[str]] = {}
        exact_declared = set()
        for text in removed:
            dep = _departure_for(departures, cid, text)
            if dep is None:
                findings.append(finding("regress:UNDECLARED_REMOVAL", "REG-BASELINE", "P1",
                                        f"UNDECLARED_REMOVAL in {cid}: baseline text never appears in candidate: {text}",
                                        traced=N13, key="inventory", element=cid, fix="restore the text or declare the removal with F/CR citations"))
            else:
                absorbed_by_dep.setdefault(id(dep), []).append(text)
                if any(lit == norm(text) for lit in dep.get("_literals", [])):
                    exact_declared.add(norm(text))
        broad_norms = {norm(t) for dep in departures for t in absorbed_by_dep.get(id(dep), []) if norm(t) not in exact_declared}
        overbroad = bool(broad_norms) and (len(broad_norms) > 0.5 * max(1, len(bt))
                                           or (len(bt) >= 4 and 2 * len(broad_norms) >= len(bt)))
        if overbroad:
            findings.append(finding("regress:OVERBROAD_DEPARTURE", "REG-BASELINE", "P1",
                                    f"OVERBROAD_DEPARTURE in {cid}: departures absorbed {len(broad_norms)} of {len(bt)} baseline texts",
                                    traced=N13, key="inventory", element=cid,
                                    fix="list exact removed texts or narrow the departure"))
        for dep in departures:
            absorbed = absorbed_by_dep.get(id(dep), [])
            for text in absorbed:
                ntext = norm(text)
                if not overbroad or ntext in exact_declared:
                    declared_removed.append(ntext)
                else:
                    findings.append(finding("regress:UNDECLARED_REMOVAL", "REG-BASELINE", "P1",
                                            f"UNDECLARED_REMOVAL in {cid}: baseline text removed by overbroad departure: {text}",
                                            traced=N13, key="inventory", element=cid, fix="restore the text or narrow the departure"))
        expected = {k: v for k, v in bt.items() if k not in declared_removed}
        retained = len([k for k in expected if k in ct]) / max(1, len(expected))
        if expected and retained < float(th["content_retained"]):
            findings.append(finding("regress:CONTENT_LOSS", "REG-BASELINE", "P1",
                                    f"CONTENT_LOSS in {cid}: candidate retained {len([k for k in expected if k in ct])} of {len(expected)} baseline distinct texts "
                                    f"({retained:.0%} < {float(th['content_retained']):.0%})",
                                    traced=N13, key="inventory", element=cid, fix="restore baseline content unless a cited request removes it"))
    for cid in block.get("demo_chapters") or []:
        bm = demo_metrics(base_ch.get(str(cid), []), float(baseline.get("fps") or 1), still_pattern)
        cm = demo_metrics(cand_ch.get(str(cid), []), float(candidate.get("fps") or 1), still_pattern)
        metrics[str(cid)] = {"baseline": bm, "candidate": cm}
        if bm["cast_frames"] and cm["cast_frames"] < float(th["demo_cast"]) * bm["cast_frames"]:
            findings.append(finding("regress:DEMO_REGRESSION", "REG-BASELINE", "P1",
                                    f"DEMO_REGRESSION {cid}: cast frames {cm['cast_frames']} vs baseline {bm['cast_frames']}",
                                    traced=N13, key="inventory", element=str(cid)))
        if bm["pointer_share"] and cm["pointer_share"] < float(th["demo_pointer"]) * bm["pointer_share"]:
            findings.append(finding("regress:DEMO_REGRESSION", "REG-BASELINE", "P1",
                                    f"DEMO_REGRESSION {cid}: pointer on screen {cm['pointer_share']:.0%} vs baseline {bm['pointer_share']:.0%}",
                                    traced=N13, key="inventory", element=str(cid)))
        if bm["mean_zoom"] and cm["mean_zoom"] and cm["mean_zoom"] > float(th["demo_zoom_max"]) * bm["mean_zoom"]:
            findings.append(finding("regress:DEMO_REGRESSION", "REG-BASELINE", "P1",
                                    f"DEMO_REGRESSION {cid}: mean zoom {cm['mean_zoom']} vs baseline {bm['mean_zoom']}",
                                    traced=N13, key="inventory", element=str(cid)))
        if cm["longest_static_s"] > bm["longest_static_s"] + float(th["demo_static_extra_s"]):
            findings.append(finding("regress:DEMO_REGRESSION", "REG-BASELINE", "P1",
                                    f"DEMO_REGRESSION {cid}: longest static hold {cm['longest_static_s']} s vs baseline {bm['longest_static_s']} s",
                                    traced=N13, key="inventory", element=str(cid)))
        if not base_ch.get(str(cid)) or not cand_ch.get(str(cid)):
            findings.append(finding("regress:DEMO_CHAPTER_MISSING", "REG-BASELINE", "P1",
                                    f"demo_chapters id {cid!r} is missing from "
                                    f"{'baseline' if not base_ch.get(str(cid)) else 'candidate'} inventory",
                                    traced=N13, key="inventory", element=str(cid)))
    layout_findings, layout_report = _layout_regressions(ctx, block, baseline, candidate)
    findings += layout_findings
    declared_departures = [{"chapter": d.get("chapter"), "match": d.get("match"), "why": d.get("why"),
                            "cites": [str(c) for c in d.get("cites") or []], "regex": bool(d.get("regex")),
                            "absorbed": d.get("_absorbed", [])} for d in departures]
    report = {"applicable": True, "baseline": (block.get("baseline") or {}).get("label"), "thresholds": th,
              "declared_departures": declared_departures, "demo_metrics": metrics,
              "layout_regression": layout_report, "seam_chrome_ignored": seam_chrome,
              "findings": [f.model_dump() for f in findings]}
    write_json_atomic(ctx.workdir / "regression-report.json", report)
    return NodeResult(outputs={"regression_report": "regression-report.json"}, findings=findings, data=report)
