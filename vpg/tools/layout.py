"""Layout-audit and caption/script regression checks.

The project-side N13 command may produce DOM-derived layout and inventory artifacts. The engine consumes those
artifacts only; it does not run Playwright here.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..context import NodeResult
from ..util import read_text, write_json_atomic
from ._common import finding
from .captions import parse_srt

N07 = "n07_script_development"
N13 = "n13_motion_scenes"
N14 = "n14_video_assembly"

_KIND_DIM = {
    "TEXT_OVERLAP": "LEG-OVERLAP",
    "BOX_OVERLAP": "LEG-OVERLAP",
    "LINE_THROUGH_TEXT": "LEG-OVERLAP",
    "TEXT_CLIPPED": "LEG-CLIP",
    "OFFSTAGE": "LEG-OFFSTAGE",
    "EMPTY_PANEL": "LEG-EMPTY",
}


def _ts(value: Any) -> str:
    try:
        s = float(value)
    except (TypeError, ValueError):
        return "?:??"
    return f"{int(s // 60)}:{s % 60:04.1f}"


def _not_measured() -> list:
    return [finding("layout:not-measured", dim, "P3", "layout audit not measured: no fresh N13 layout_audit artifact is registered",
                    traced=N13, key="layout_audit", fix="run the project's N13 layout audit command")
            for dim in ("LEG-OVERLAP", "LEG-CLIP", "LEG-OFFSTAGE", "LEG-EMPTY")]


def _fresh_records(ctx, key: str, producer: str = N13):
    from ..freshness import fresh_current
    return fresh_current(ctx, key, producer)


def check(ctx) -> NodeResult:
    recs = _fresh_records(ctx, "layout_audit")
    current_recs = ctx.current("layout_audit") if hasattr(ctx, "current") else []
    audit = ctx.load("layout_audit") if (recs or not hasattr(ctx, "current") or (not current_recs and ctx.load("layout_audit"))) else None
    if not audit:
        findings = _not_measured()
        report = {"measured": False, "findings": [f.model_dump() for f in findings]}
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ctx.workdir / "layout-report.json", report)
        return NodeResult(outputs={"layout_report": "layout-report.json"}, findings=findings, data={"layout_measured": False})
    findings = []
    for run in audit.get("runs", []):
        kind = str(run.get("kind", ""))
        dim = _KIND_DIM.get(kind)
        if dim is None:
            continue
        text = str(run.get("text") or "").strip()
        other = str(run.get("other") or "").strip()
        msg = f"{kind} at {_ts(run.get('from'))} for {float(run.get('dur') or 0):.1f}s"
        if text:
            msg += f": {text}"
        if other:
            msg += f" / {other}"
        findings.append(finding(f"layout:{kind}", dim, "P2", msg, evidence=f"{run.get('value', '')}",
                                traced=N13, key="layout_audit", element=text or kind,
                                fix="repair the composition; do not strip labels to pass density"))
    report = {"measured": True, "film": audit.get("film"), "runs": len(audit.get("runs", [])),
              "findings": [f.model_dump() for f in findings]}
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "layout-report.json", report)
    return NodeResult(outputs={"layout_report": "layout-report.json"}, findings=findings, data={"layout_measured": True})


def _words(text: str) -> list[str]:
    return [_norm_word(w) for w in re.findall(r"[a-z0-9']+", text.lower().replace("-", " "))]


def _norm_word(word: str) -> str:
    if word.endswith("ises") and len(word) > 5:
        return word[:-4] + "izes"
    return word


def _script_words(script: dict) -> list[str]:
    return [w for ch in script.get("chapters", []) for u in ch.get("units", []) for w in _words(str(u.get("text", "")))]


def check_caption_script(ctx) -> NodeResult:
    script = ctx.load("script") or {}
    cap = ctx.artifact_path("captions")
    if not script or not cap:
        msg = "caption/script equality not measured: script or captions artifact missing"
        findings = [finding("captions:script-not-measured", "CAP-SCRIPT", "P3", msg, traced=N14, key="captions",
                            fix="register N07 script and N14 captions")]
        return NodeResult(findings=findings, data={"caption_script_measured": False})
    cues = parse_srt(read_text(cap))
    want = _script_words(script)
    got_by_cue = [(_ts(c.get("start")), _words(str(c.get("text", "")))) for c in cues]
    got = [w for _t, ws in got_by_cue for w in ws]
    missing_counter = Counter(want) - Counter(got)
    extra_counter = Counter(got) - Counter(want)
    if not missing_counter and not extra_counter:
        return NodeResult(data={"caption_script_measured": True, "caption_script_words": len(want)})
    missing = sorted(missing_counter.elements())
    extra = sorted(extra_counter.elements())
    first_time = next((t for t, ws in got_by_cue if extra and any(w in extra for w in ws)), got_by_cue[0][0] if got_by_cue else "?:??")
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(missing[:12]))
    if extra:
        parts.append("extra: " + ", ".join(extra[:12]))
    findings = [finding("captions:script-words", "CAP-SCRIPT", "P2", f"caption words differ from script at {first_time}: {'; '.join(parts)}",
                        traced=N14, key="captions", fix="regenerate captions from the approved script without dropping words")]
    return NodeResult(findings=findings, data={"caption_script_measured": True, "missing": missing, "extra": extra})
