"""Compare polarity: which direction is better for each "What changed" metric, and the project's targets.

Pure functions; the reader passes in the texts and numbers it found. Every result carries words, never only a sign.
"""
from __future__ import annotations

import re

DEFAULT_LUFS = -16.0
DEFAULT_LUFS_TOLERANCE = 1.0
TARGET_ONLY_WINDOW = 0.10
OVERRIDE_FLOOR_S = 80.0
LEVELS = ("P1", "P2", "P3", "P4", "P?")
POLARITIES = ("better", "worse", "unchanged", "neutral", "n/a")
BUCKETS = ("worse", "better", "unchanged", "neutral", "uncertain", "n/a")

_LUFS = re.compile(r"(-\d+(?:\.\d+)?)\s*LUFS", re.I)
_LU_TOLERANCE = re.compile(r"(?:\u00b1|\+/-|\+-)\s*(\d+(?:\.\d+)?)\s*LU\b", re.I)
_UNIT = r"\s*s(?:ec(?:ond)?s?)?\b"
_RT_TARGET = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)" + _UNIT + r"\s+target", re.I)
_RT_CAP_AFTER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)" + _UNIT + r"\s+(?:cap|max(?:imum)?|limit)\b", re.I)
_RT_CAP_BEFORE = re.compile(r"(?:\bcap(?:ped)?(?:\s+at)?|\bmax(?:imum)?|\bat most|\u2264|<=)\s*(\d+(?:\.\d+)?)" + _UNIT, re.I)
_RT_RANGE = re.compile(r"(?<![\d.:])(\d+(?:\.\d+)?)\s*(?:-|\u2013|to)\s*(\d+(?:\.\d+)?)" + _UNIT, re.I)
_RT_CONTEXT = re.compile(r"runtime|duration|length|minute|target|\bcap\b|format", re.I)
_RT_BARE = re.compile(r"(?<![\d.:-])(\d+(?:\.\d+)?)" + _UNIT)


def _num(text):
    if isinstance(text, bool):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def fmt_num(value):
    if value is None:
        return "unknown"
    return f"{value:.1f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def loudness_target(constraint_texts, thresholds=None):
    """{lufs, tolerance, source, text}: vpg.project.yaml constraints first, then evaluation thresholds, else -16 LUFS."""
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    audio = thresholds.get("audio") if isinstance(thresholds.get("audio"), dict) else {}
    th_lufs = _num(audio.get("integrated_lufs")) if not isinstance(audio.get("integrated_lufs"), bool) else None
    th_tol = _num(audio.get("lufs_tolerance")) if not isinstance(audio.get("lufs_tolerance"), bool) else None
    for text in constraint_texts:
        match = _LUFS.search(text)
        if match:
            tolerance = _LU_TOLERANCE.search(text[match.end():match.end() + 24])
            tol = _num(tolerance.group(1)) if tolerance else None
            return {"lufs": float(match.group(1)), "tolerance": tol or th_tol or DEFAULT_LUFS_TOLERANCE,
                    "source": "vpg.project.yaml constraints", "text": text[:300]}
    if th_lufs is not None:
        return {"lufs": th_lufs, "tolerance": th_tol or DEFAULT_LUFS_TOLERANCE,
                "source": "controls/evaluation.md thresholds", "text": None}
    return {"lufs": DEFAULT_LUFS, "tolerance": th_tol or DEFAULT_LUFS_TOLERANCE, "source": "default", "text": None}


def parse_runtime(text):
    """Runtime statements in one constraint: {target, low, high, cap}; None values when not stated."""
    out = {"target": None, "low": None, "high": None, "cap": None}
    match = _RT_TARGET.search(text)
    if match:
        out["target"] = _num(match.group(1))
    match = _RT_CAP_AFTER.search(text) or _RT_CAP_BEFORE.search(text)
    if match:
        out["cap"] = _num(match.group(1))
    for match in _RT_RANGE.finditer(text):
        low, high = _num(match.group(1)), _num(match.group(2))
        if low is not None and high is not None and 0 < low < high:
            out["low"], out["high"] = low, high
            break
    if not any(value is not None for value in out.values()) and re.match(r"\s*(runtime|duration|length|format)\b", text, re.I):
        match = _RT_BARE.search(text)
        if match:
            out["target"] = _num(match.group(1))
    return out


def window_label(low, high):
    if low is not None and high is not None:
        return f"{fmt_num(low)}-{fmt_num(high)} s"
    if high is not None:
        return f"at most {fmt_num(high)} s"
    return f"at least {fmt_num(low)} s"


def band(target=None, low=None, high=None):
    """(low, high) from explicit bounds, a missing bound filled from the target +/-10 %; None when nothing is known."""
    target = target if isinstance(target, (int, float)) and not isinstance(target, bool) and target > 0 else None
    if low is None and target is not None:
        low = round(target * (1 - TARGET_ONLY_WINDOW), 2)
    if high is None and target is not None:
        high = round(target * (1 + TARGET_ONLY_WINDOW), 2)
    if low is None and high is None:
        return None
    return low, high


def script_window(target, overrides=None):
    """A script's runtime target as a band: the project's gate-overrides range when the target reaches the override's
    floor (its runtime_min_s, else 80 s), else +/-10 % of the target. (low, high, detail) or None."""
    if not isinstance(target, (int, float)) or isinstance(target, bool) or target <= 0:
        return None
    overrides = overrides if isinstance(overrides, dict) else {}
    o_low, o_high = _num(overrides.get("runtime_min_s")), _num(overrides.get("runtime_max_s"))
    floor = o_low if o_low is not None else OVERRIDE_FLOOR_S
    if (o_low is not None or o_high is not None) and target >= floor:
        low, high = band(target, o_low, o_high)
        return low, high, (f"range from pipeline/gate-overrides.json, which applies to scripts targeting at least "
                           f"{fmt_num(floor)} s")
    low, high = band(target)
    return low, high, "+/-10 % of the script target"


def same_window(wa, wb):
    def bounds(window):
        return tuple(None if window.get(key) is None else round(float(window[key]), 1) for key in ("low", "high"))
    return bool(wa) and bool(wb) and bounds(wa) == bounds(wb)


def runtime_window(constraint_texts, brief=None):
    """{low, high, target, cap, source, text, label} or None: the brief artifact's runtime fields win, then the first
    runtime statement in the constraints. A target without a range or cap counts as +/-10 %."""
    brief = brief if isinstance(brief, dict) else {}
    target = _num(brief.get("runtime_target_s")) if not isinstance(brief.get("runtime_target_s"), bool) else None
    cap = _num(brief.get("runtime_max_s")) if not isinstance(brief.get("runtime_max_s"), bool) else None
    target = target if target and target > 0 else None
    cap = cap if cap and cap > 0 else None
    source = "brief (governed run)" if (target or cap) else None
    parsed, text = {"target": None, "low": None, "high": None, "cap": None}, None
    for candidate in constraint_texts:
        if not _RT_CONTEXT.search(candidate):
            continue
        found = parse_runtime(candidate)
        if any(value is not None for value in found.values()):
            parsed, text = found, candidate[:300]
            break
    if source is None and text:
        source = "vpg.project.yaml constraints"
    if source == "brief (governed run)" and text and (parsed["low"] is not None or parsed["high"] is not None):
        source = "brief (governed run) and vpg.project.yaml constraints"
    target = target or parsed["target"]
    cap = cap or parsed["cap"]
    low, high = parsed["low"], parsed["high"]
    if cap is not None:
        high = min(high, cap) if high is not None else cap
    if low is None and high is None and target:
        low, high = round(target * (1 - TARGET_ONLY_WINDOW), 2), round(target * (1 + TARGET_ONLY_WINDOW), 2)
    if low is None and high is None:
        return None
    return {"low": low, "high": high, "target": target, "cap": cap, "source": source, "text": text,
            "label": window_label(low, high)}


def _distance(value, low, high):
    if low is not None and value < low:
        return low - value
    if high is not None and value > high:
        return value - high
    return 0.0


def _result(polarity, reason):
    return {"polarity": polarity, "reason": reason}


def direction(a, b, higher_is_better=True, unit="", counts=False):
    """better / worse / unchanged for a number where one direction is better; counts read 'N more' or 'N fewer'."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return _result("n/a", "not measured on both versions")
    delta = round(b - a, 3)
    if delta == 0:
        return _result("unchanged", "same value")
    better = delta > 0 if higher_is_better else delta < 0
    if counts:
        return _result("better" if better else "worse", f"{fmt_num(abs(delta))} {'more' if delta > 0 else 'fewer'}")
    word = "higher" if delta > 0 else "lower"
    return _result("better" if better else "worse", f"{word} by {fmt_num(abs(delta))}{unit}")


def loudness(a, b, target):
    """Closer to the target is better; both inside +/-tolerance is neutral."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return _result("n/a", "not measured on both versions")
    lufs, tol = target["lufs"], target["tolerance"]
    band = f"\u00b1{fmt_num(tol)} LU of {fmt_num(lufs)} LUFS"
    da, db = abs(a - lufs), abs(b - lufs)
    a_in, b_in = da <= tol + 1e-9, db <= tol + 1e-9
    if a_in and b_in:
        return _result("neutral", f"both within {band}")
    if b_in:
        return _result("better", f"B within {band}, A outside")
    if a_in:
        return _result("worse", f"B outside {band}, A within")
    if abs(db - da) < 0.05:
        return _result("unchanged", f"both outside {band}")
    closer = db < da
    return _result("better" if closer else "worse",
                   f"both outside {band}; B is {fmt_num(round(abs(da - db), 2))} LU {'closer' if closer else 'farther'}")


def duration(a, b, window):
    """Neutral without a runtime target; with one, inside the window is better than outside."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return _result("n/a", "duration unknown on one version")
    if not window:
        return _result("neutral", "no runtime target known")
    label = window["label"]
    da, db = _distance(a, window["low"], window["high"]), _distance(b, window["low"], window["high"])
    if da == 0 and db == 0:
        return _result("neutral", f"both within {label}")
    if db == 0:
        return _result("better", f"B within {label}, A outside")
    if da == 0:
        return _result("worse", f"B outside {label}, A within")
    if abs(db - da) < 0.05:
        return _result("unchanged", f"both outside {label}")
    closer = db < da
    return _result("better" if closer else "worse",
                   f"both outside {label}; B is {fmt_num(round(abs(da - db), 1))} s {'closer' if closer else 'farther'}")


def _target_text(window):
    return f"{fmt_num(window['target'])} s" if window.get("target") else window["label"]


def duration_between(a, b, wa, wb, name_a="A", name_b="B"):
    """Duration judged against each version's own runtime window: not comparable when the windows differ or only one
    version has one; neutral when neither has one; else as duration()."""
    if wa and wb and not same_window(wa, wb):
        return _result("n/a", f"targets differ ({_target_text(wa)} vs {_target_text(wb)})")
    if bool(wa) != bool(wb):
        missing = name_a if not wa else name_b
        return _result("n/a", f"no runtime target for {missing}")
    return duration(a, b, wb)


def defect_levels(a_levels, b_levels):
    """Fewer is better, judged level by level from P1 down: the first level that differs decides."""
    a_levels = a_levels or {}
    b_levels = b_levels or {}
    for level in LEVELS:
        va, vb = int(a_levels.get(level) or 0), int(b_levels.get(level) or 0)
        if va != vb:
            fewer = vb < va
            return _result("better" if fewer else "worse", f"{'fewer' if fewer else 'more'} {level} ({vb} vs {va})")
    return _result("unchanged", "same counts at every level")


def bucket(change):
    """Where a change counts in the summary: a comparable metric measured on different bases counts as uncertain."""
    if change.get("uncertain") and change["polarity"] != "n/a":
        return "uncertain"
    return change["polarity"]


def summarize(changes):
    """'B is worse on 2, better on 1, unchanged on 3' (neutral, uncertain and not comparable added when present)."""
    counts = {name: sum(1 for change in changes if bucket(change) == name) for name in BUCKETS}
    parts = [f"worse on {counts['worse']}", f"better on {counts['better']}", f"unchanged on {counts['unchanged']}"]
    if counts["neutral"]:
        parts.append(f"neutral on {counts['neutral']}")
    if counts["uncertain"]:
        parts.append(f"uncertain on {counts['uncertain']}")
    if counts["n/a"]:
        parts.append(f"not comparable on {counts['n/a']}")
    return dict(counts, text="B is " + ", ".join(parts))
