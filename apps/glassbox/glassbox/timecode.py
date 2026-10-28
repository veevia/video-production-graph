"""Timecode, key-frame file name and caption parsing."""
from __future__ import annotations

import html
import math
import re

_CLOCK = re.compile(r"(?<![\d.:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:\.\d+)?)(?![\d:])")
_SECONDS = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*[-\u2013\u2014]\s*\d+(?:\.\d+)?)?\s*s(?:ec(?:ond)?s?)?(?!\w)"
)
_BARE_RANGE = re.compile(r"(?<![\w.:])(\d+\.\d+)\s*[-\u2013\u2014]\s*\d+\.\d+(?![\w.:])")
_KEYFRAME = re.compile(r"^kf-(\d+)-(.*?)-f(\d+)-(\d+(?:\.\d+)?)s\.(?:png|jpe?g)$", re.I)
_TRAILING_T = re.compile(r"(?:^|[-_])(\d+(?:\.\d+)?)s?\.(?:png|jpe?g)$", re.I)
_IMAGE = re.compile(r"\.(?:png|jpe?g)$", re.I)
_CUE = re.compile(r"((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})")
_TAG = re.compile(r"<[^>]*>")


def parse_t0(value):
    """Seconds of the FIRST timecode in a free-text location, or None.

    "mm:ss(.xx)" converts as m*60+s even when s > 59 ("00:71.70" is 71.7). Otherwise the
    first "N s" or "N-N s" counts. A list of bare decimal ranges that ends with a unit
    ("50.03-52.00, 54.63-56.47 s") starts at its first range. The earliest match wins.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 3) if math.isfinite(value) and value >= 0 else None
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = parse_t0(item)
            if parsed is not None:
                return parsed
        return None
    if not isinstance(value, str):
        return None
    candidates = []
    clock = _CLOCK.search(value)
    if clock:
        hours = int(clock.group(1)) if clock.group(1) else 0
        seconds = hours * 3600 + int(clock.group(2)) * 60 + float(clock.group(3))
        candidates.append((clock.start(), seconds))
    unit = _SECONDS.search(value)
    if unit:
        candidates.append((unit.start(), float(unit.group(1))))
        bare = _BARE_RANGE.search(value, 0, unit.start())
        if bare:
            candidates.append((bare.start(), float(bare.group(1))))
    if not candidates:
        return None
    return round(min(candidates)[1], 3)


def split_leading_time(text):
    """Split "43.23-43.30 s: camera snap" into ("43.23-43.30 s", "camera snap")."""
    index = text.find(": ")
    if 0 < index <= 120:
        head = text[:index]
        if parse_t0(head) is not None:
            return head.strip(), text[index + 2:].strip()
    return None, text


def parse_keyframe_name(name):
    """kf-02-opening-title-poster-f86-2.867s.png -> label, frame and time."""
    match = _KEYFRAME.match(name)
    if match:
        return {
            "file": name,
            "index": int(match.group(1)),
            "label": match.group(2).replace("-", " ").strip(),
            "frame": int(match.group(3)),
            "t": float(match.group(4)),
        }
    if not _IMAGE.search(name):
        return None
    stem = name.rsplit(".", 1)[0]
    trailing = _TRAILING_T.search(name)
    return {
        "file": name,
        "index": None,
        "label": re.sub(r"[-_]+", " ", stem).strip(),
        "frame": None,
        "t": float(trailing.group(1)) if trailing else None,
    }


def _cue_seconds(stamp):
    parts = stamp.replace(",", ".").split(":")
    seconds = float(parts[-1]) + int(parts[-2]) * 60
    if len(parts) > 2:
        seconds += int(parts[-3]) * 3600
    return round(seconds, 3)


def parse_captions(text):
    """WebVTT or SRT text -> [{start, end, text}]."""
    cues = []
    if not text:
        return cues
    blocks = re.split(r"\n[ \t]*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = block.strip("\n").split("\n")
        for position, line in enumerate(lines):
            match = _CUE.search(line)
            if not match:
                continue
            body = "\n".join(lines[position + 1:]).strip()
            body = html.unescape(_TAG.sub("", body))
            cues.append({"start": _cue_seconds(match.group(1)), "end": _cue_seconds(match.group(2)), "text": body})
            break
    return cues


_SRT_STAMP = re.compile(r"(\d{1,2}:\d{2}:\d{2}),(\d{1,3})")


def srt_to_vtt(text):
    """Convert SubRip to WebVTT: a header, dots for the millisecond separator, cue blocks kept."""
    body = (text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    out = []
    for block in re.split(r"\n[ \t]*\n", body):
        lines = block.split("\n")
        converted = []
        for line in lines:
            if "-->" in line:
                line = _SRT_STAMP.sub(lambda m: f"{m.group(1)}.{m.group(2).ljust(3, '0')}", line)
            converted.append(line)
        if any("-->" in line for line in converted):
            out.append("\n".join(converted))
    return "WEBVTT\n\n" + "\n\n".join(out) + "\n"
