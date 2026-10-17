"""Pacing tools (N07 completion, N20 review): runtime budget, speaking rate, reading time per beat."""

from __future__ import annotations

from ..context import CheckResult, NodeResult
from ..util import write_json_atomic
from ._common import finding, reading_words, thresholds, words


def _brief(ctx) -> dict:
    return ctx.load("brief") or {}


def estimate_script(ctx) -> CheckResult:
    script = ctx.load("script") or {}
    fm = ctx.fm("production")
    wps = float(fm.get("narration_wps", 2.5))
    pause = float(fm.get("unit_pause_s", 0.4))
    units = [u for ch in script.get("chapters", []) for u in ch.get("units", [])]
    est = sum(len(words(u["text"])) for u in units) / wps + pause * max(0, len(units) - 1) + float(fm.get("head_tail_s", 1.0))
    mx = float(_brief(ctx).get("runtime_max_s", 0) or fm.get("runtime_max_s", 0))
    ok = mx <= 0 or est <= mx
    return CheckResult(ok, [] if ok else [f"estimated runtime {est:.1f}s exceeds the {mx:.0f}s cap at {wps} words/s; cut words, not pauses"],
                       metrics={"estimated_runtime_s": round(est, 2)})


def analyze(ctx) -> NodeResult:
    th = thresholds(ctx)["pacing"]
    spans = (ctx.load("timings") or {}).get("units", [])
    beats = (ctx.load("storyboard") or {}).get("beats", [])
    brief = _brief(ctx)
    findings = []
    per_unit = []
    for s in spans:
        dur = max(1e-3, s["end"] - s["start"])
        rate = len(words(s["text"])) / dur
        per_unit.append({"unit": s["unit"], "wps": round(rate, 2)})
        if rate > th["wps_max"]:
            findings.append(finding("pacing:rate", "PAC-RATE", "P3", f"unit {s['unit']} is spoken at {rate:.2f} words/s (> {th['wps_max']})",
                                    evidence=f"{s['start']:.2f}–{s['end']:.2f}s", traced="n07_script_development", key="script", element=s["unit"],
                                    fix="cut words from the unit; never speed up the voice"))
        if rate < th["wps_min"]:
            findings.append(finding("pacing:rate", "PAC-RATE", "P3", f"unit {s['unit']} drags at {rate:.2f} words/s (< {th['wps_min']})",
                                    evidence=f"{s['start']:.2f}–{s['end']:.2f}s", traced="n12_voice_audio", key="voice", element=s["unit"]))
    runtime = float((ctx.load("scenes") or {}).get("runtime_s") or (spans[-1]["end"] + 1.0 if spans else 0))
    mx = float(brief.get("runtime_max_s") or 0)
    if mx and runtime > mx + 1e-6:
        findings.append(finding("pacing:runtime", "PAC-RUNTIME", "P2", f"runtime {runtime:.2f}s exceeds the {mx:.0f}s cap",
                                traced="n08_storyboard_shots", key="storyboard", fix="shorten beats or cut a unit"))
    text_th = thresholds(ctx)["text"]
    shown: set[str] = set()
    for b in beats:   # a string still on screen from the previous beat was read there; only new text needs reading time
        texts = [str(t) for t in b.get("on_screen_text", [])]
        n_words = sum(len(reading_words(t)) for t in texts if t not in shown)
        shown = set(texts)
        need = max(text_th["dwell_min_s"], text_th["dwell_s_per_word"] * n_words)
        if n_words and b.get("duration_s", 0) < need:
            findings.append(finding("pacing:reading-time", "PAC-READ", "P2", f"beat {b['id']} holds {n_words} new on-screen words for {b['duration_s']}s (needs ≥{need:.2f}s)",
                                    traced="n08_storyboard_shots", key="storyboard", element=b["id"], fix="lengthen the hold or remove words"))
    report = {"runtime_s": runtime, "runtime_max_s": mx, "units": per_unit, "findings": [f.model_dump() for f in findings]}
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "pacing-report.json", report)
    return NodeResult(outputs={"pacing_report": "pacing-report.json"}, findings=findings, data={"runtime_s": runtime})
