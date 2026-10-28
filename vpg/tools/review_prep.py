"""Creative-critique preparation (N16 pre-step): keyframes at every scene midpoint, so the critic judges the
rendered film (what the audience sees) rather than the source."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..context import NodeResult
from ._common import finding
from ..util import read_json, sha256_file, write_json_atomic
from ._common import FFMPEG, run

STRIP_FRAME_W = 192
STRIP_FRAME_H = 108
STRIP_GAP = 6
STRIP_COLS = 12


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
    strips = baseline_strips(ctx)
    findings = getattr(ctx, "_baseline_strip_findings", [])
    manifest = getattr(ctx, "_baseline_strip_manifest", None)
    return NodeResult(outputs=({"keyframes": frames} if frames else {}) | ({"baseline_strips": manifest} if manifest else {}),
                      findings=findings, data={"keyframes": len(frames), "baseline_strips": len(strips),
                                               "baseline_strip_notes": getattr(ctx, "_baseline_strip_notes", [])})


def _configured_path(ctx, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else ctx.project_dir / p


def _chapter_times(inv: dict, cid: str, fps: float) -> list[float]:
    ch = next((c for c in inv.get("chapters", []) if str(c[0]) == str(cid)), None)
    if ch is None:
        return []
    start, end = float(ch[1]), float(ch[2])
    step = 1 / max(float(fps or 1), 0.001)
    n = max(1, int((end - start) * fps))
    return [round(start + i * step, 3) for i in range(n) if start + i * step < end]


def _label(im: Image.Image, label: str) -> Image.Image:
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except Exception:
        font = ImageFont.load_default()
    draw.rectangle([0, 0, min(STRIP_FRAME_W, len(label) * 7 + 8), 18], fill=(0, 0, 0))
    draw.text((4, 3), label, fill=(255, 255, 0), font=font)
    return im


def _grey(label: str) -> Image.Image:
    return _label(Image.new("RGB", (STRIP_FRAME_W, STRIP_FRAME_H), (45, 45, 45)), label)


def _chapter_frames(master: Path | None, inv: dict, cid: str, fps: float, td: Path, row_label: str, notes: list[str]) -> list[Image.Image]:
    times = _chapter_times(inv, cid, fps)
    if master is None or not master.exists():
        return [_grey(f"{row_label} missing") for _ in times]
    ch = next((c for c in inv.get("chapters", []) if str(c[0]) == str(cid)), None)
    if ch is None:
        return []
    start, end = float(ch[1]), float(ch[2])
    out_pat = td / f"{row_label}-{cid}-%04d.png"
    cmd = [FFMPEG, "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{max(0.001, end - start):.3f}", "-i", str(master),
           "-vf", f"fps={fps},scale={STRIP_FRAME_W}:{STRIP_FRAME_H}", str(out_pat)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    files = sorted(td.glob(f"{row_label}-{cid}-*.png"))
    if proc.returncode != 0 or not files:
        notes.append(f"{row_label} {cid}: frame extraction failed")
        return [_grey(f"{row_label} frame unavailable") for _ in times]
    frames = []
    for i, t in enumerate(times):
        if i < len(files):
            try:
                frames.append(_label(Image.open(files[i]).convert("RGB"), f"{row_label} {t:.1f}s"))
            except Exception:
                notes.append(f"{row_label} {cid}: frame {i} unreadable")
                frames.append(_grey(f"{row_label} frame unavailable"))
        else:
            frames.append(_grey(f"{row_label} missing"))
    return frames


def _strip_out_dir(ctx) -> tuple[Path, bool]:
    privacy = ctx.config.get("privacy") if isinstance(ctx.config, dict) else None
    names = privacy.get("names") if isinstance(privacy, dict) else None
    if names and names != "none":
        hold = privacy.get("hold_dir") if isinstance(privacy, dict) else None
        root = Path(hold) if hold else Path(tempfile.gettempdir()) / "vpg-review-strips"
        run_id = getattr(getattr(ctx.state, "runtime", None), "run_id", "run")
        version = getattr(getattr(ctx.state, "runtime", None), "current_version", 0)
        return root / str(run_id) / f"v{version}", True
    return ctx.workdir / "baseline-strips", False


def _fresh_artifact_path(ctx, key: str, producer: str = "n13_motion_scenes") -> Path | None:
    from ..freshness import fresh_current
    recs = fresh_current(ctx, key, producer)
    return ctx.project_dir / recs[-1].path if recs else None


def baseline_strips(ctx, candidate_inventory_path: Path | None = None) -> list[str]:
    findings, notes = [], []
    ctx._baseline_strip_findings = findings
    ctx._baseline_strip_notes = notes
    ctx._baseline_strip_manifest = None
    block = ctx.config.get("regression") if isinstance(ctx.config, dict) else None
    base_cfg = (block or {}).get("baseline") if isinstance(block, dict) else None
    if not isinstance(base_cfg, dict) or not base_cfg.get("master") or not base_cfg.get("inventory"):
        return []
    base_master = _configured_path(ctx, base_cfg.get("master"))
    base_inv_path = _configured_path(ctx, base_cfg.get("inventory"))
    cand_master = ctx.artifact_path("master")
    cand_inv_path = candidate_inventory_path or _fresh_artifact_path(ctx, "inventory")
    if not (base_inv_path and base_inv_path.exists() and cand_inv_path and cand_inv_path.exists()):
        return []
    if not (base_master and base_master.exists()):
        findings.append(finding("regress:baseline-master-missing", "REG-BASELINE", "P1",
                                "configured regression baseline master is missing; baseline strips were not fully measured",
                                traced="n16_creative_critique", key="baseline_strips"))
    from .regress import load_baseline
    base_inv, err = load_baseline(ctx, base_cfg.get("inventory"), "inventory")   # validated: no crash on a bad shape
    try:
        cand_inv = read_json(cand_inv_path)
    except Exception as exc:
        err = err or f"{type(exc).__name__}: {exc}"
    if err:
        findings.append(finding("regress:baseline-strip-input", "REG-BASELINE", "P1",
                                f"baseline strip inputs are unreadable: {err}",
                                traced="n16_creative_critique", key="baseline_strips"))
        return []
    demo = {str(c) for c in (block.get("demo_chapters") or [])}
    out_dir, outside = _strip_out_dir(ctx)
    out_dir.mkdir(parents=True, exist_ok=True)
    if outside:
        notes.append(f"baseline strips written outside the project because privacy.names is configured: {out_dir}")
    strips = []
    root_for_manifest = out_dir
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        chapter_ids = sorted({str(c[0]) for c in base_inv.get("chapters", [])} | {str(c[0]) for c in cand_inv.get("chapters", [])})
        for cid in chapter_ids:
            cid = str(cid)
            fps = 3.0 if cid in demo else 1.0
            base_frames = _chapter_frames(base_master, base_inv, cid, fps, td_path, "baseline", notes)
            cand_frames = _chapter_frames(cand_master, cand_inv, cid, fps, td_path, "candidate", notes)
            if not base_frames and not cand_frames:
                continue
            cols_total = max(len(base_frames), len(cand_frames))
            base_frames += [_grey("baseline missing")] * (cols_total - len(base_frames))
            cand_frames += [_grey("candidate missing")] * (cols_total - len(cand_frames))
            for offset in range(0, cols_total, STRIP_COLS):
                chunk_base = base_frames[offset:offset + STRIP_COLS]
                chunk_cand = cand_frames[offset:offset + STRIP_COLS]
                cols = max(len(chunk_base), len(chunk_cand))
                sheet = Image.new("RGB", (cols * STRIP_FRAME_W + (cols - 1) * STRIP_GAP, 2 * STRIP_FRAME_H + STRIP_GAP), (60, 60, 60))
                for i, im in enumerate(chunk_base + chunk_cand):
                    sheet.paste(im, ((i % cols) * (STRIP_FRAME_W + STRIP_GAP), (i // cols) * (STRIP_FRAME_H + STRIP_GAP)))
                suffix = f"-{offset // STRIP_COLS + 1:02d}" if cols_total > STRIP_COLS else ""
                path = out_dir / f"{cid}-baseline-vs-candidate{suffix}.png"
                sheet.save(path)
                strips.append(str(path) if outside else str(path.relative_to(ctx.workdir)))
    if strips:
        files = []
        for item in strips:
            path = Path(item) if Path(item).is_absolute() else ctx.workdir / item
            files.append({"path": str(path.relative_to(root_for_manifest)) if path.is_relative_to(root_for_manifest) else str(path),
                          "sha256": sha256_file(path)})
        # root: project-relative inside the project; absolute only when privacy keeps the strips outside it
        root = root_for_manifest if outside else root_for_manifest.relative_to(ctx.project_dir)
        manifest = {"schema": "vpg-baseline-strips/v1", "root": str(root), "inside_project": not outside, "files": files,
                    "notes": notes,
                    "chapters": sorted({str(c[0]) for c in base_inv.get("chapters", [])} |
                                       {str(c[0]) for c in cand_inv.get("chapters", [])})}
        write_json_atomic(ctx.workdir / "baseline-strips.json", manifest)
        ctx._baseline_strip_manifest = "baseline-strips.json"
    return strips
