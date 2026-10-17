"""Asset tools (N10 plan check, N11 generation check).

The recovered lessons behind these rules:
  * product UI is captured from the real product, never generated (no invented UI);
  * logos and brand marks are supplied files used as delivered, never regenerated or deformed;
  * generated imagery must name a specific subject and context: "the assets are too generic" becomes a checkable
    specificity rule (subject ≥ 4 content words, not a stock phrase, tied to ≥1 beat).

A planned asset is an image by default. Its spec format may also name a video (mp4, mov, webm, mkv, m4v; checked
with ffprobe) or a recording (`take` or `recording`): a JSON manifest whose every entry with `file` and `sha256` is
hash-checked. A recorded prototype walkthrough is then a registered, fingerprinted asset whose hash changes with every
new take, instead of a poster still with an unregistered sidecar that downstream memoisation cannot see.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from ..context import CheckResult
from ..util import open_regular, sha256_file
from ._common import FFPROBE, probe

SOURCE_TYPES = {"supplied", "captured", "generated", "rendered"}
RIGHTS = {"owned", "licensed", "generated-internal", "needs-clearance"}
GENERIC = {"abstract background", "technology background", "business people", "data visualization", "stock photo", "futuristic city"}
STOP = {"a", "an", "the", "of", "with", "and", "in", "on", "for", "to", "at"}
VIDEO_FORMATS = {"mp4", "mov", "webm", "mkv", "m4v"}
RECORDING_FORMATS = {"take", "recording"}


def check_plan(ctx) -> CheckResult:
    plan = (ctx.load("asset_plan") or {}).get("assets", [])
    beats = {b["id"] for b in (ctx.load("storyboard") or {}).get("beats", [])}
    msgs = []
    if not plan:
        msgs.append("asset plan is empty")
    ids = [a.get("id") for a in plan]
    if len(ids) != len(set(ids)):
        msgs.append("duplicate asset ids")
    for a in plan:
        aid = a.get("id")
        if a.get("source_type") not in SOURCE_TYPES:
            msgs.append(f"{aid}: source_type must be one of {sorted(SOURCE_TYPES)}")
        if a.get("rights") not in RIGHTS:
            msgs.append(f"{aid}: rights must be one of {sorted(RIGHTS)}")
        if a.get("rights") == "needs-clearance":
            msgs.append(f"{aid}: rights need clearance before production (licence gate)")
        if a.get("role") == "product-ui" and a.get("source_type") not in ("captured", "supplied"):
            msgs.append(f"{aid}: product UI must be captured from the real product, never generated")
        if a.get("role") == "logo" and a.get("source_type") != "supplied":
            msgs.append(f"{aid}: logos must be the supplied files, never regenerated")
        if a.get("source_type") == "generated":
            subject = a.get("subject", "").lower()
            content = [w for w in subject.split() if w not in STOP]
            if len(content) < 4 or subject in GENERIC:
                msgs.append(f"{aid}: generated asset subject '{subject}' is too generic (name the specific subject, setting and detail)")
        if not set(a.get("beats", [])) & beats:
            msgs.append(f"{aid}: not tied to any storyboard beat")
        spec = a.get("spec", {})
        if not (spec.get("w") and spec.get("h") and spec.get("format")):
            msgs.append(f"{aid}: spec needs w, h and format")
    return CheckResult(not msgs, msgs)


def _manifest_entries(node: Any) -> list[dict]:
    """Every entry of a recording manifest that names a file, wherever it sits in the structure."""
    if isinstance(node, dict):
        own = [node] if isinstance(node.get("file"), str) else []
        return own + [e for v in node.values() for e in _manifest_entries(v)]
    if isinstance(node, list):
        return [e for v in node for e in _manifest_entries(v)]
    return []


def _check_recording(aid: str, path: Path, project_dir: Path, spec: dict[str, Any]) -> list[str]:
    try:
        with open_regular(path, "r") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as exc:
        return [f"asset {aid} is a recording, but its manifest is not readable JSON: {exc.__class__.__name__}"]
    entries = _manifest_entries(manifest)
    if not entries:
        return [f"asset {aid} is a recording, but its manifest lists no file"]
    msgs, at_spec = [], False
    root = Path(project_dir).resolve()
    for e in entries:
        f = (path.parent / e["file"]).resolve()
        if not isinstance(e.get("sha256"), str):
            msgs.append(f"asset {aid} recording file {e['file']} is listed without a sha256")
        elif not f.is_relative_to(root):
            msgs.append(f"asset {aid} recording file {e['file']} points outside the project")
        elif not f.is_file():
            msgs.append(f"asset {aid} recording file {e['file']} is missing")
        elif sha256_file(f) != e["sha256"]:
            msgs.append(f"asset {aid} recording file {e['file']} no longer matches its sha256")
        elif not at_spec and spec.get("w") and spec.get("h"):
            try:
                with open_regular(f) as fh, Image.open(fh) as im:
                    at_spec = (im.width, im.height) == (spec["w"], spec["h"])
            except OSError:
                pass
    if spec.get("w") and spec.get("h") and not at_spec and not msgs:
        msgs.append(f"asset {aid} recording lists no frame at the spec size {spec['w']}x{spec['h']}")
    return msgs[:10] + ([f"asset {aid}: {len(msgs) - 10} more recording files fail"] if len(msgs) > 10 else [])


def _check_video(aid: str, path: Path, spec: dict[str, Any]) -> list[str]:
    if not shutil.which(FFPROBE):
        return [f"asset {aid} cannot be checked: ffprobe is not installed"]
    try:
        info = probe(path)
    except Exception as exc:   # ffprobe refuses anything that is not a video container
        return [f"asset {aid} is not a readable video: {exc.__class__.__name__}"]
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        return [f"asset {aid} has no video stream"]
    msgs = []
    if (v.get("width"), v.get("height")) != (spec.get("w"), spec.get("h")):
        msgs.append(f"asset {aid} is {v.get('width')}x{v.get('height')}, spec is {spec.get('w')}x{spec.get('h')}")
    if spec.get("duration_s"):
        dur = float(v.get("duration") or info.get("format", {}).get("duration") or 0)
        if abs(dur - float(spec["duration_s"])) > 0.5:
            msgs.append(f"asset {aid} runs {dur:.2f}s, spec is {float(spec['duration_s']):.2f}s")
    return msgs


def check_generated(ctx) -> CheckResult:
    plan = {a["id"]: a for a in ctx.state.production.asset_plan}
    recs = {r.item: r for r in ctx.current("assets")}
    msgs = []
    for aid, a in plan.items():
        r = recs.get(aid)
        if r is None:
            msgs.append(f"asset {aid} was not produced")
            continue
        p = ctx.project_dir / r.path
        spec = a.get("spec", {})
        wanted = str(spec.get("format", "")).lower()
        if wanted in RECORDING_FORMATS:
            msgs += _check_recording(aid, p, ctx.project_dir, spec)
            continue
        if wanted in VIDEO_FORMATS:
            msgs += _check_video(aid, p, spec)
            continue
        try:
            with open_regular(p) as f, Image.open(f) as im:
                w, h, fmt = im.width, im.height, (im.format or "").lower()
        except OSError as exc:
            msgs.append(f"asset {aid} is not a readable image: {exc}")
            continue
        if (w, h) != (spec.get("w"), spec.get("h")):
            msgs.append(f"asset {aid} is {w}x{h}, spec is {spec.get('w')}x{spec.get('h')}")
        if spec.get("format") and fmt != spec["format"].lower():
            msgs.append(f"asset {aid} format {fmt} ≠ spec {spec['format']}")
    return CheckResult(not msgs, msgs)
