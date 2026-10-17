"""Storyboard checks (N08): coverage of the script, one objective per beat, complete seam specifications."""

from __future__ import annotations

from ..context import CheckResult

SEAM_FIELDS = ["outgoing_anchor", "incoming_anchor", "direction", "match_frame", "duration_s", "audio_bridge"]


def check(ctx) -> CheckResult:
    board = (ctx.load("storyboard") or {}).get("beats", [])
    script = ctx.load("script") or {}
    unit_ids = [u["id"] for ch in script.get("chapters", []) for u in ch.get("units", [])]
    msgs = []
    covered = {u for b in board for u in b.get("unit_ids", [])}
    missing = [u for u in unit_ids if u not in covered]
    if missing:
        msgs.append(f"script units with no beat: {missing}")
    for i, b in enumerate(board):
        obj = b.get("objective", "")
        if not obj or ";" in obj or " and also " in obj.lower():
            msgs.append(f"beat {b.get('id')}: needs exactly one communication objective (got '{obj}')")
        if b.get("source", {}).get("type") == "captured" and not b["source"].get("ref"):
            msgs.append(f"beat {b.get('id')}: captured product footage needs a source ref")
        if i < len(board) - 1:
            seam = b.get("transition_out") or {}
            gaps = [f for f in SEAM_FIELDS if seam.get(f) in (None, "")]
            if gaps:
                msgs.append(f"beat {b.get('id')}: seam to the next beat is missing {gaps} (video-shotcraft handoff spec)")
    total = sum(float(b.get("duration_s", 0)) for b in board)
    brief = ctx.load("brief") or {}
    mx = float(brief.get("runtime_max_s") or 0)
    if mx and total > mx:
        msgs.append(f"storyboard runs {total:.1f}s, over the {mx:.0f}s cap")
    return CheckResult(not msgs, msgs, metrics={"beats": len(board), "duration_s": total})
