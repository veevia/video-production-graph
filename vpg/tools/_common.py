"""Shared helpers for tools: thresholds, findings, media probes, WCAG contrast."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ..models import Finding
from ..util import PKG_ROOT, read_json as _read_json

FFPROBE = shutil.which("ffprobe") or "ffprobe"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def defaults() -> dict[str, Any]:
    return yaml.safe_load((PKG_ROOT / "config" / "defaults.yaml").read_text())


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def thresholds(ctx) -> dict[str, Any]:
    """Defaults (config/defaults.yaml) overridden by the project's evaluation.md front-matter `thresholds:`."""
    return deep_merge(defaults()["thresholds"], ctx.fm("evaluation").get("thresholds", {}))


def finding(check: str, dimension: str, severity: str, message: str, evidence: str = "", traced: str | None = None,
            key: str | None = None, fix: str = "", level: str = "MUST", element: str | None = None) -> Finding:
    return Finding(check=check, dimension=dimension, severity=severity, message=message, evidence=evidence,
                   traced_node=traced, artifact_key=key, smallest_fix=fix, level=level,
                   element=str(element) if element is not None else None)


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe(path: Path) -> dict[str, Any]:
    p = run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {p.stderr[-300:]}")
    return json.loads(p.stdout)


def loudness(path: Path) -> dict[str, float]:
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], timeout=600)
    text = p.stderr[p.stderr.rfind("Summary:"):]
    get = lambda pat: float(re.search(pat, text).group(1))  # noqa: E731
    return {"integrated_lufs": get(r"I:\s+(-?[\d.]+) LUFS"), "lra": get(r"LRA:\s+([\d.]+) LU"),
            "true_peak_dbtp": get(r"Peak:\s+(-?[\d.]+) dBFS")}


def silences(path: Path, noise_db: float = -50, min_s: float = 1.5) -> list[tuple[float, float]]:
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", p.stderr)]
    return list(zip(starts, ends + [float("inf")] * (len(starts) - len(ends))))


def blacks(path: Path, min_s: float = 0.5) -> list[tuple[float, float]]:
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vf", f"blackdetect=d={min_s}:pic_th=0.98", "-an", "-f", "null", "-"])
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", p.stderr)]


def luma_series(path: Path, fps: int = 30) -> list[float]:
    """Mean frame luma (0–1) per frame, for the three-flashes check (WCAG 2.3.1)."""
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
             "-an", "-f", "null", "-"], timeout=600)
    return [float(v) / 255.0 for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", p.stderr)]


def hex_rgb(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def rel_luminance(value: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_rgb(value)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((rel_luminance(fg), rel_luminance(bg)), reverse=True)
    return round((a + 0.05) / (b + 0.05), 2)


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", text)


def reading_words(text: str) -> list[str]:
    """Words a viewer reads on screen. A code or identifier such as 'G/TBT/N/GBR/124' is one word, not five: only
    whitespace separates on-screen words. Spoken text keeps `words` (a code is several words when read aloud)."""
    return [t for t in text.split() if re.search(r"[A-Za-z0-9]", t)]


def read_json(path: Path) -> Any:
    return _read_json(path)
