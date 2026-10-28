"""Safe media resolution inside one candidate directory, content types and HTTP Range parsing."""
from __future__ import annotations

import os
import re

MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".vtt": "text/vtt; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
WAV_TYPE = "audio/wav"
STEM_DIRS = ("n12_voice_audio", "n14_video_assembly")
SRT_AS_VTT = ".srt.vtt"
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_RANGE = re.compile(r"(\d*)-(\d*)")


class MediaError(Exception):
    pass


def _check_segments(segments):
    if isinstance(segments, str):
        segments = segments.split("/")
    if not segments:
        raise MediaError("empty path")
    for segment in segments:
        if (not isinstance(segment, str) or segment in ("", ".", "..") or segment.startswith(".")
                or "\x00" in segment or "\\" in segment or "/" in segment):
            raise MediaError("bad path")
    return list(segments)


def _split_vtt(segments):
    """A request for '<file>.srt.vtt' asks for <file>.srt converted to WebVTT."""
    if segments[-1].lower().endswith(SRT_AS_VTT):
        return segments[:-1] + [segments[-1][: -len(".vtt")]], True
    return segments, False


def _ext_ok(name, wav_ok):
    ext = os.path.splitext(name)[1].lower()
    return ext in MEDIA_TYPES or (ext == ".wav" and wav_ok)


def resolve(project_path, version, segments):
    """(real path, convert_srt) of a servable file inside candidates/<version>/, or MediaError.

    Refuses '..', hidden segments, unknown extensions, symlinks that leave the candidate
    folder and candidate folders that are themselves links to somewhere else. WAV is served
    only from the n12 and n14 folders (voice and stems).
    """
    if not isinstance(version, str) or not _VERSION.match(version) or ".." in version:
        raise MediaError("bad version")
    segments, convert = _split_vtt(_check_segments(segments))
    wav_ok = segments[0] in STEM_DIRS
    if not _ext_ok(segments[-1], wav_ok):
        raise MediaError("type not served")
    candidates_real = os.path.realpath(os.path.join(project_path, "candidates"))
    candidate_real = os.path.realpath(os.path.join(candidates_real, version))
    if os.path.dirname(candidate_real) != candidates_real or not os.path.isdir(candidate_real):
        raise MediaError("candidate folder escapes candidates/")
    target = os.path.realpath(os.path.join(candidate_real, *segments))
    try:
        inside = os.path.commonpath([candidate_real, target]) == candidate_real
    except ValueError:
        inside = False
    if not inside or target == candidate_real:
        raise MediaError("outside the candidate folder")
    if not _ext_ok(target, wav_ok) or (convert and not target.lower().endswith(".srt")):
        raise MediaError("type not served")
    if not os.path.isfile(target):
        raise MediaError("not a file")
    return target, convert


def resolve_run(project_path, segments, allowed):
    """(real path, convert_srt) for a governed-run file: only paths listed in state.json, inside the project."""
    segments, convert = _split_vtt(_check_segments(segments))
    rel = "/".join(segments)
    if rel not in allowed:
        raise MediaError("not listed in the run state")
    wav_ok = allowed.get(rel) in STEM_DIRS
    if not _ext_ok(rel, wav_ok) or (convert and not rel.lower().endswith(".srt")):
        raise MediaError("type not served")
    root = os.path.realpath(project_path)
    target = os.path.realpath(os.path.join(root, *segments))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:
        inside = False
    if not inside or not _ext_ok(target, wav_ok) or not os.path.isfile(target):
        raise MediaError("outside the project or missing")
    return target, convert


def content_type(path, convert=False):
    if convert:
        return MEDIA_TYPES[".vtt"]
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        return WAV_TYPE
    return MEDIA_TYPES.get(ext, "application/octet-stream")


def parse_range(header, size):
    """Single 'bytes=' range -> (start, end) inclusive, 'unsatisfiable', or None to send everything."""
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None
    spec = header[6:].strip()
    if "," in spec:
        return None
    match = _RANGE.fullmatch(spec)
    if not match:
        return None
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if size <= 0:
        return "unsatisfiable"
    if first == "":
        suffix = int(last)
        if suffix == 0:
            return "unsatisfiable"
        return (max(0, size - suffix), size - 1)
    start = int(first)
    if start >= size:
        return "unsatisfiable"
    end = size - 1 if last == "" else min(int(last), size - 1)
    if end < start:
        return None
    return (start, end)
