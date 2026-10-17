"""Shared helpers for the example production commands (bound to nodes in vpg.project.yaml)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def load() -> dict:
    return json.loads(Path(os.environ["VPG_CONTEXT"]).read_text())


def read_json(path) -> dict:
    return json.loads(Path(path).read_text())


def done(ctx: dict, outputs: dict, data: dict | None = None, findings: list | None = None) -> None:
    Path(ctx["workdir"], "outputs.json").write_text(json.dumps({"outputs": outputs, "data": data or {}, "findings": findings or []}, indent=1))


def sh(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd[:6])} …\n{p.stderr[-800:]}")
    return p.stderr


def duration(path) -> float:
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(p.stdout.strip())
