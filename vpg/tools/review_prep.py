"""Creative-critique preparation (N16 pre-step): keyframes at every scene midpoint, so the critic judges the
rendered film (what the audience sees) rather than the source."""

from __future__ import annotations

from ..context import NodeResult
from ._common import FFMPEG, run


def creative(ctx) -> NodeResult:
    master = ctx.artifact_path("master")
    scenes = (ctx.load("scenes") or {}).get("scenes", [])
    out = ctx.workdir / "keyframes"
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for s in scenes:
        t = (s["start"] + s["end"]) / 2
        dest = out / f"{s['beat']}-{t:06.2f}.png"
        p = run([FFMPEG, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(master), "-frames:v", "1", str(dest)])
        if p.returncode == 0 and dest.exists():
            frames.append(str(dest.relative_to(ctx.workdir)))
    return NodeResult(outputs={"keyframes": frames} if frames else {}, data={"keyframes": len(frames)})
