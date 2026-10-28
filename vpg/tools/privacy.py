"""Privacy scan: no listed person's name may be readable in any frame of the master.

Producer commands veil names as pixels, so the engine cannot see whether a veil held; this measures the delivered
video itself. The project declares a confidential name list in vpg.project.yaml, kept tenant-side:

    privacy:
      names: /tenant/secure/redact-names.json   # a JSON list of strings, or {"names": [...]}, kept out of the
                                                # repository: an absolute path, or a gitignored project folder
      scan_fps: native                          # optional: native (default) or a numeric sampling rate, clamped to ≥10
      time_budget_s: 1800                       # optional; exceeding it blocks the release instead of passing it

Nothing here ever writes a name: problems and reports carry the list index, the time and the box. Frames stream from
ffmpeg as grey images and reach OCR through a pipe, so no frame is written to disk. The first frame is read in full.
After that, rows that move between two samples are read as full-width strips (motion such as a scroll). Whenever the
picture comes to rest, and at least once a second while it does not, every row that changed since the last such read
is read again (the end of a fade, text typed in, a veil lifting, a fade while a spinner turns elsewhere). Full-width
strips keep unchanged words next to the changed ones in view. The scan fails closed: OCR that is missing, broken or
failing its self-test, an unreadable video, an unusable list or an exceeded time budget each block the release, never
pass it.

Check a render before submitting it:  python -m vpg.tools.privacy <project dir> <video> [--names <list.json>]
(--names checks against a list without changing vpg.project.yaml; N23 still needs privacy.names declared.)
Exit 1 means a listed name is readable, 2 or 3 that nothing was checked (usage or no list; a refused list or project
config, no OCR, the time budget spent, or any other failure). The last line says which (PASS, FAIL or NOT CHECKED); read the exit code itself, not through a
pipe (or set -o pipefail).
"""

from __future__ import annotations

import difflib
import io
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from math import ceil
from typing import Any, Callable, Iterable, Iterator

from PIL import Image, ImageChops, ImageDraw, ImageFont

from ..util import PKG_ROOT, sha256_file
from ._common import FFMPEG, probe

SCANNER = "privacy-scan/3: native-frame scan, full-width strips, rest reads, live windows"
FULL_MIN = 0.8        # similarity for a multi-word name (a full name) to count as readable
SINGLE_MIN = 0.95     # single-word entries must match almost exactly: common words resemble short names
MERGE_MIN = 0.85      # a span with fewer words than the name must be nearly as long as the whole name (a merge)
CHANGE_LEVEL = 24     # grey-level step that counts as motion between two samples
REST_LEVEL = 6        # below this everywhere, the picture is at rest
NOISE_PIXELS = 8      # fewer changed pixels than this is codec noise
MARGIN = 12
Word = tuple[str, int, int, int, int]


class OcrUnavailable(RuntimeError):
    pass


class PrivacyConfigError(ValueError):
    pass


class PrivacyScanError(RuntimeError):
    pass


def privacy_feature_enabled(config: dict[str, Any] | None, key: str) -> bool:
    val = (((config or {}).get("privacy") or {}).get(key, "off"))
    if isinstance(val, bool):
        return val
    return isinstance(val, str) and val.strip().lower() in {"on", "true", "yes"}


def privacy_feature_value_ok(val: Any) -> bool:
    return isinstance(val, bool) or (isinstance(val, str) and val.strip().lower() in {"on", "off", "true", "false", "yes", "no"})


def _rate(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "native":
            return None
        if "/" in text:
            a, _, b = text.partition("/")
            try:
                den = float(b)
                return float(a) / den if den else None
            except ValueError:
                return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


MIN_SCAN_FPS = 10.0    # never sample below this, whatever the configuration or the probe says
MAX_SCAN_FPS = 120.0   # above this the scan cannot promise it decodes every frame, so it fails closed


def stream_rates(video: Path) -> dict[str, Any]:
    """The video stream's rates from ffprobe, as ffprobe states them (e.g. "30000/1001"): r_frame_rate (ffprobe's guess
    at a rate every timestamp fits, from the first frames) and avg_frame_rate (frames over duration, which a mostly
    still recording pulls low). Neither is proof: `gray_frames` checks that decoding dropped no frame."""
    try:
        videos = [s for s in probe(video)["streams"] if s.get("codec_type") == "video"]
    except Exception as exc:
        raise PrivacyScanError(f"the video frame rate cannot be probed ({exc.__class__.__name__})") from None
    if len(videos) != 1:
        raise PrivacyScanError(f"the master has {len(videos)} video streams; exactly one can be checked")
    return {"r_frame_rate": videos[0].get("r_frame_rate"), "avg_frame_rate": videos[0].get("avg_frame_rate")}


def decode_rate(scan_fps: Any, video: Path) -> tuple[float, str, dict[str, Any]]:
    """The rate the scan decodes at, as a number and as the exact argument for ffmpeg's fps filter: the highest of
    10 fps, a pinned `privacy.scan_fps` and the stream's stated rates (a tie keeps the stream's own rational, so a
    30000/1001 master is decoded at exactly its rate). Above 120 fps it fails closed (a bogus time base, or a master that
    needs re-encoding at a constant rate). A stream that shows frames faster than this rate is caught in `gray_frames`."""
    rates = stream_rates(video)
    cands = [(MIN_SCAN_FPS, 0, "10")]
    for rank, raw in ((3, rates.get("r_frame_rate")), (2, rates.get("avg_frame_rate")), (1, scan_fps)):
        value = _rate(raw)
        if value:
            cands.append((value, rank, raw if isinstance(raw, str) and "/" in raw else f"{value:.6f}".rstrip("0").rstrip(".")))
    rate, _, arg = max(cands)
    if rate > MAX_SCAN_FPS:
        raise PrivacyScanError(f"the master's frame rate ({rate:g} fps) is above the {MAX_SCAN_FPS:g} fps the scan can decode, "
                               "so it cannot show that every frame was read; re-encode it at a constant rate")
    return rate, arg, {"pinned_fps": _rate(scan_fps), "r_frame_rate": rates.get("r_frame_rate"),
                       "avg_frame_rate": rates.get("avg_frame_rate")}


_FPS_SUMMARY = re.compile(r"Parsed_fps_\d+ @ [^\]]*\] (\d+) frames in, (\d+) frames out; (\d+) frames dropped")


def validate_scan_fps(value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and value.strip().lower() == "native":
        return
    if _rate(value) is None:
        raise PrivacyConfigError("privacy.scan_fps must be 'native' or a positive number (e.g. 30 or 30000/1001)")


def _coerce_live_window(obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    try:
        start, end = float(obj["start"]), float(obj["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    return {"start": start, "end": end, "what": str(obj.get("what") or obj.get("id") or "live UI")}


def manifest_live_windows(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Declared product-UI motion ranges from a text-layers artifact; invalid declarations fail closed at release."""
    if not manifest:
        return []
    raw = manifest.get("live_windows") or []
    if not isinstance(raw, list):
        raise PrivacyConfigError("text-layers live_windows must be a list of {start, end, what}")
    windows = [_coerce_live_window(w) for w in raw]
    if any(w is None for w in windows):
        raise PrivacyConfigError("each text-layers live window needs numeric start < end and optional what")
    return sorted(windows, key=lambda w: (w["start"], w["end"]))


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def folded_spelling(text: str) -> str:
    """ASCII-ish spelling used for text artifacts: NFC/NFD-insensitive, diacritics folded, punctuation as spaces."""
    return " ".join(re.sub(r"[^\w]+", " ", fold(unicodedata.normalize("NFC", text))).split())


def text_privacy_hits(text: str, names: list[str]) -> tuple[list[int], list[int]]:
    """(full-entry hits, part-only hits) by 1-based name-list index.

    Full entries use the same folded spelling discipline as the frame matcher: every name part must appear contiguously
    after folding. Individual name parts are warnings only; they never refuse a run.
    """
    folded = folded_spelling(text)
    compact = re.sub(r"[\s_\-'\u2019]+", "", folded)
    haystack = f" {folded} "
    full: list[int] = []
    parts_only: list[int] = []
    for idx, name in enumerate(names, 1):
        parts = _parts(name)
        if not parts:
            continue
        phrase = " ".join(parts)
        merged = "".join(parts)
        reversed_phrase = " ".join(reversed(parts))
        comma_reversed = (len(parts) >= 2 and "," in text
                          and re.search(rf"(?<!\w){re.escape(' '.join(parts[1:]))}\s+{re.escape(parts[0])}(?!\w)", folded))
        if (re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded)
                or re.search(rf"(?<!\w){re.escape(reversed_phrase)}(?!\w)", folded) and "," in text
                or merged in compact
                or comma_reversed):
            full.append(idx)
        elif any(len(p) >= 4 and re.search(rf"(?<!\w){re.escape(p)}(?!\w)", haystack) for p in parts):
            parts_only.append(idx)
    return full, parts_only


def iter_json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from iter_json_strings(v)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_strings(item)


def text_for_privacy_scan(path: Path, *, json_mode: bool = False) -> str:
    """Decode a text artifact for privacy scanning. Undecodable text fails closed."""
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if not json_mode:
        return text
    data = json.loads(text)
    return "\n".join(iter_json_strings(data))


def _parts(name: str) -> list[str]:
    return [p for p in (re.sub(r"[^\w]", "", w) for w in fold(name).split()) if p]


def _checkable(name: str) -> bool:
    parts = _parts(name)
    return len(parts) > 1 or (len(parts) == 1 and len(parts[0]) >= 4)


def load_names(project_dir: Path, config: dict[str, Any]) -> tuple[list[str], list[int]]:
    """(names, 1-based indices of entries too short to check); ([], []) when the project declares no list.
    A declared list that cannot be read, is not a list of strings, or has no checkable entry raises."""
    rel = ((config or {}).get("privacy") or {}).get("names")
    if not rel:
        return [], []
    if rel == "none":
        return [], []
    if not isinstance(rel, str):
        raise PrivacyConfigError("privacy.names must be a string path, or 'none' with a reason")
    try:
        data = json.loads((Path(project_dir) / str(rel)).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrivacyConfigError(f"cannot read the name list {rel} ({exc.__class__.__name__})") from None
    names = data.get("names") if isinstance(data, dict) else data
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise PrivacyConfigError(f"the name list {rel} must be a non-empty JSON list of strings, or an object with a \"names\" list")
    skipped = [i + 1 for i, n in enumerate(names) if not _checkable(n)]
    if len(skipped) == len(names):
        raise PrivacyConfigError(f"the name list {rel} has no checkable entry (a single-word entry needs 4 or more letters)")
    return names, skipped


def none_declared(config: dict[str, Any]) -> bool:
    """True when the project explicitly declares that no privacy name list exists, with a reason."""
    privacy = (config or {}).get("privacy")
    return isinstance(privacy, dict) and privacy.get("names") == "none" and bool(str(privacy.get("reason", "")).strip())


def declaration_problem(config: dict[str, Any]) -> str | None:
    """Fail-closed message for projects that did not declare what privacy names should be scanned."""
    privacy = (config or {}).get("privacy")
    if not isinstance(privacy, dict):
        return ("no privacy name list is declared; set privacy.names to a JSON list path, or explicitly set "
                "privacy: {names: none, reason: \"...\"}")
    names = privacy.get("names")
    if names == "none":
        if none_declared(config):
            return None
        return "privacy.names is none but privacy.reason is missing; explain why no name list is required"
    if names is not None and not isinstance(names, str):
        return "privacy.names must be a string path, or 'none' with a reason"
    if names:
        return None
    return ("no privacy name list is declared; set privacy.names to a JSON list path, or explicitly set "
            "privacy: {names: none, reason: \"...\"}")


def tesseract_words(image: Image.Image) -> list[Word]:
    """Words with boxes in the given image's pixel space (read at 2× for small UI text)."""
    exe = shutil.which("tesseract")
    if not exe:
        raise OcrUnavailable("tesseract is not installed")
    big = image.convert("L").resize((image.width * 2, image.height * 2), Image.LANCZOS)
    png = io.BytesIO()
    big.save(png, format="PNG")
    try:   # piped, never a file: no frame is written to disk, and macOS leptonica rewrites /tmp paths
        p = subprocess.run([exe, "stdin", "stdout", "--psm", "11", "tsv"], input=png.getvalue(), capture_output=True,
                           timeout=120)
    except subprocess.TimeoutExpired:
        raise OcrUnavailable("tesseract timed out") from None
    if p.returncode != 0:
        raise OcrUnavailable(f"tesseract exited {p.returncode}: {p.stderr.decode('utf-8', 'replace').strip()[-160:]}")
    lines = p.stdout.decode("utf-8", "replace").splitlines()
    if not lines or not lines[0].startswith("level"):
        raise OcrUnavailable("tesseract returned no TSV output")
    words = []
    for row in lines[1:]:
        cols = row.split("\t")
        if len(cols) == 12 and cols[11].strip() and re.search(r"[^\W\d_]", cols[11]):
            words.append((cols[11], int(cols[6]) // 2, int(cols[7]) // 2, int(cols[8]) // 2, int(cols[9]) // 2))
    return words


def ocr_self_test(ocr: Callable[[Image.Image], list[Word]]) -> None:
    """OCR that runs but reads nothing (missing language data, a broken build) must not pass a master as clean."""
    img = Image.new("L", (720, 140), 255)
    try:
        font = ImageFont.truetype(str(PKG_ROOT / "examples/sample-demo/assets/fonts/MonaSansVF.ttf"), 56)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((24, 36), "VPGPROBE READY", fill=0, font=font)
    if not any("vpgprobe" in fold(w[0]) for w in ocr(img)):
        raise OcrUnavailable("the OCR self-test failed: a rendered probe word was not read")


def _variants(parts: list[str]) -> set[str]:
    orders = itertools.permutations(parts) if len(parts) <= 3 else [parts]
    return {"".join(p) for p in orders}


def match_names(words: list[Word], names: list[str]) -> list[tuple[int, float, list[int]]]:
    """(1-based list index, similarity, [x, y, w, h]) for every listed name the words spell out, in any order, split or
    merged into one word more or fewer than the name has."""
    toks = [(t, w) for t, w in ((re.sub(r"[^\w]", "", fold(w[0])), w) for w in words) if t]
    hits = []
    for idx, name in enumerate(names, 1):
        parts = _parts(name)
        if not _checkable(name):
            continue
        best = None
        if len(parts) == 1:
            spans, targets, need = [1], {parts[0]}, SINGLE_MIN
        else:
            spans, targets, need = sorted({len(parts) - 1, len(parts), len(parts) + 1} - {0}), _variants(parts), FULL_MIN
        for span in spans:
            for j in range(len(toks) - span + 1):
                seg = toks[j:j + span]
                joined = "".join(t for t, _ in seg)
                if span < len(parts) and len(joined) < MERGE_MIN * min(len(v) for v in targets):
                    continue                              # fewer words only as a genuine merge, never a lone surname
                score = max(difflib.SequenceMatcher(None, joined, v).ratio() for v in targets)
                if score >= need and (best is None or score > best[0]):
                    boxes = [w for _, w in seg]
                    x0, y0 = min(b[1] for b in boxes), min(b[2] for b in boxes)
                    x1, y1 = max(b[1] + b[3] for b in boxes), max(b[2] + b[4] for b in boxes)
                    best = (score, [x0, y0, x1 - x0, y1 - y0])
        if best:
            hits.append((idx, round(best[0], 2), best[1]))
    return hits


def _rows(img: Image.Image, other: Image.Image, level: int) -> tuple[int, int] | None:
    """The row band where more than codec noise changed between two frames, with a margin; None when nothing did."""
    mask = ImageChops.difference(img, other).point(lambda p: 255 if p > level else 0)
    if mask.histogram()[255] < NOISE_PIXELS:
        return None
    box = mask.getbbox()
    return (max(0, box[1] - MARGIN), min(img.height, box[3] + MARGIN))


def frame_range(window: dict[str, Any], fps: float) -> tuple[int, int]:
    """The frames a window covers at `fps`, as [first, stop): frame i shows at i / fps. Membership and the expected count
    both come from this one integer range, so a window edge can never be counted one way and tested another."""
    return ceil(window["start"] * fps - 1e-6), ceil(window["end"] * fps - 1e-6)


def scan_frames(frames: Iterable[Image.Image], names: list[str], fps: float,
                ocr: Callable[[Image.Image], list[Word]] = tesseract_words, deadline: float | None = None,
                live_windows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Scan grey frames in order; returns counts and hit windows (list index, first/last read, box). Never any text."""
    per_frame: list[tuple[int, int, float, list[int]]] = []
    calls = count = ref_i = 0
    ref = prev = None
    period = max(1, round(fps))                            # at least one read per second while the picture never rests
    declared = list(live_windows or [])
    ranges = [frame_range(w, fps) for w in declared]
    full_ref = None   # the last frame read in full: an identical frame has nothing left to read
    window_stats = [{"window": k + 1, "start": w["start"], "end": w["end"], "what": w.get("what", "live UI"),
                     "frames_in_window": 0, "frames_decoded": 0, "ocr_reads": 0, "unchanged_frames": 0, "sampled_fraction": 0.0}
                    for k, w in enumerate(declared)]

    def read(img: Image.Image, band: tuple[int, int], i: int) -> None:
        nonlocal calls
        calls += 1
        y0 = band[0]
        words = [(w[0], w[1], w[2] + y0, w[3], w[4]) for w in ocr(img.crop((0, band[0], img.width, band[1])))]
        per_frame.extend((i, idx, score, box) for idx, score, box in match_names(words, names))

    for i, img in enumerate(frames):
        count += 1
        if deadline is not None and time.monotonic() > deadline:
            raise PrivacyScanError(f"the time budget ran out at {i / fps:.1f} s of the video")
        live = [j for j, (first, stop) in enumerate(ranges) if first <= i < stop]
        if live:
            # Every frame of a live window is read in full, unless it is byte-identical to the last frame read in full
            # (the repeats a rate above the source's inserts, or a held still). A frame the scan only partly read before
            # the window never counts, so a name faded in below the motion threshold is read on the window's first frame.
            same = full_ref is not None and ImageChops.difference(img, full_ref).getbbox() is None
            if not same:
                read(img, (0, img.height), i)
                full_ref = img
            for j in live:
                window_stats[j]["frames_decoded"] += 1
                window_stats[j]["unchanged_frames" if same else "ocr_reads"] += 1
            prev = img
            if ref is None:
                ref, ref_i = img, i
            continue
        if prev is None:
            read(img, (0, img.height), i)
            ref = prev = full_ref = img
            continue
        moving = _rows(img, prev, CHANGE_LEVEL)
        if moving:
            read(img, moving, i)
        at_rest = _rows(img, prev, REST_LEVEL) is None
        if at_rest or i - ref_i >= period:                  # read whatever changed since the last such read
            drift = _rows(img, ref, REST_LEVEL)
            if drift:
                read(img, drift, i)
                ref, ref_i = img, i       # the reference moves only past what was read
            elif at_rest:
                ref_i = i
        prev = img
    if prev is not None and ref is not prev:
        drift = _rows(prev, ref, REST_LEVEL)
        if drift:
            read(prev, drift, count - 1)
    windows: list[dict[str, Any]] = []
    for i, idx, score, box in sorted(per_frame, key=lambda h: (h[1], h[0])):
        w = windows[-1] if windows and windows[-1]["entry"] == idx and i - windows[-1]["_last"] <= 2 else None
        if w is None:
            windows.append({"entry": idx, "start_s": round(i / fps, 2), "end_s": round(i / fps, 2), "reads": 1,
                            "best": score, "box": box, "_last": i})
        else:
            w.update(end_s=round(i / fps, 2), reads=w["reads"] + 1, best=max(w["best"], score), _last=i)
    for w in windows:
        w.pop("_last")
    for w, (first, stop) in zip(window_stats, ranges):
        w["frames_in_window"] = max(0, min(stop, count) - first)
        if first >= count:   # the window starts after the video ends: nothing in it was checked
            w.update(starts_after_end=True, sampled_fraction=0.0)
            continue
        if stop > count:
            w["clamped_to_end_s"] = round(count / fps, 3)   # a window running past the last frame is checked up to it
        # no frame starts inside a window shorter than one frame: nothing there to miss
        w["sampled_fraction"] = round(w["frames_decoded"] / w["frames_in_window"], 6) if w["frames_in_window"] else 1.0
    return {"frames": count, "ocr_calls": calls, "fps": fps, "windows": sorted(windows, key=lambda w: w["start_s"]),
            "live_windows": window_stats}


def gray_frames(video: Path, fps: float | str | None = None, every_frame: bool = False) -> Iterator[Image.Image]:
    """Stream the video's frames as grey images through a pipe (no temporary image files). With `every_frame` (the
    privacy scan), ffmpeg's fps filter must report that it dropped no frame, or the scan fails closed: a stream whose
    metadata understates its fastest stretch would otherwise lose frames silently. Without it, a lower rate samples."""
    try:
        videos = [s for s in probe(video)["streams"] if s.get("codec_type") == "video"]
        width, height = int(videos[0]["width"]), int(videos[0]["height"])
    except Exception as exc:
        raise PrivacyScanError(f"the video cannot be probed ({exc.__class__.__name__})") from None
    if len(videos) != 1:
        raise PrivacyScanError(f"the master has {len(videos)} video streams; exactly one can be checked")
    size = width * height
    vf = [] if fps is None else ["-vf", f"fps={fps}"]
    with tempfile.TemporaryFile() as err:
        check = bool(vf) and every_frame
        p = subprocess.Popen([FFMPEG, "-nostdin", "-v", "verbose" if check else "error", "-i", str(video), "-map", "0:v:0", *vf, "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE, stderr=err)
        try:
            while True:
                buf = p.stdout.read(size)
                if not buf:
                    break
                if len(buf) < size:
                    raise PrivacyScanError("ffmpeg returned a truncated frame")
                yield Image.frombytes("L", (width, height), buf)
            if p.wait() != 0:
                err.seek(0)
                lines = [x for x in err.read().decode("utf-8", "replace").splitlines() if "error" in x.lower()]
                raise PrivacyScanError(f"ffmpeg exited {p.returncode}: {' | '.join(lines)[-160:]}")
            if check:
                err.seek(0)
                summary = _FPS_SUMMARY.findall(err.read().decode("utf-8", "replace"))
                dropped = sum(int(d) for _, _, d in summary)
                if not summary:
                    raise PrivacyScanError("ffmpeg did not report whether its fps filter dropped frames, so a full read "
                                           "cannot be shown")
                if dropped:
                    raise PrivacyScanError(f"decoding at {fps} fps dropped {dropped} frame(s): the video shows frames faster "
                                           "than its stated rate. Re-encode it at a constant rate, or pin privacy.scan_fps "
                                           "to its fastest rate")
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


def scan_video(video: Path, names: list[str], fps: float | str | None = None, time_budget_s: float = 1800,
               ocr: Callable[[Image.Image], list[Word]] = tesseract_words,
               live_windows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Scan a video at `decode_rate` (never below 10 fps, a pinned rate or the stream's own). Live windows are read at the
    same rate, frame by frame."""
    ocr_self_test(ocr)
    deadline = time.monotonic() + time_budget_s
    rate, arg, rates = decode_rate(fps, video)
    report = scan_frames(gray_frames(video, arg, every_frame=True), names, rate, ocr, deadline, live_windows)
    report.update(decode_fps=rate, decode_arg=arg, **rates)
    return report


def live_window_problem(report: dict[str, Any]) -> str | None:
    """A declared live window the scan did not fully check, named by its number (fail closed)."""
    bad = []
    for w in report.get("live_windows", []):
        if w.get("starts_after_end"):
            bad.append(f"live window #{w['window']} ({w['what']}, {w['start']:g}–{w['end']:g} s) starts after the video ends")
        elif w.get("sampled_fraction") != 1.0:
            bad.append(f"live window #{w['window']} ({w['what']}) decoded {w['frames_decoded']} of {w['frames_in_window']} frames")
    return "; ".join(bad) or None


def describe(window: dict[str, Any]) -> str:
    x, y, w, h = window["box"]
    return (f"list entry #{window['entry']} readable from {window['start_s']:.2f} s (read {window['reads']} time(s), last at "
            f"{window['end_s']:.2f} s; still frames are not re-read) at box {x},{y} {w}×{h}")


def release_problem(ctx, master: Path, registered_sha: str) -> str | None:
    """N23: a readable listed name, or a declared list that cannot be checked, makes the version unreleasable. Every
    failure is a problem, never a pass; the report kept in the attempt's workdir carries no name and is never re-used."""
    undeclared = declaration_problem(ctx.config)
    if undeclared:
        return f"{undeclared}, so readable names cannot be ruled out"
    if none_declared(ctx.config):
        return None
    try:
        names, skipped = load_names(ctx.project_dir, ctx.config)
    except PrivacyConfigError as exc:
        return f"the declared privacy name list is unusable ({exc}), so readable names cannot be ruled out"
    if not names:
        return None
    try:
        if sha256_file(master) != registered_sha:
            return None                                   # already a release problem: not the reviewed master
        cfg = (ctx.config or {}).get("privacy") or {}
        validate_scan_fps(cfg.get("scan_fps"))
        windows = manifest_live_windows(ctx.load("text_layers") if hasattr(ctx, "load") else None)
        extra = {"live_windows": windows} if windows else {}
        report = scan_video(master, names, cfg.get("scan_fps", "native"), float(cfg.get("time_budget_s", 1800)), **extra)
    except (OcrUnavailable, PrivacyScanError) as exc:
        return f"the privacy scan could not complete ({exc}), so readable names cannot be ruled out"
    except PrivacyConfigError as exc:
        return f"the declared live-window privacy scan config is unusable ({exc}), so readable names cannot be ruled out"
    except Exception as exc:   # fail closed: an unexpected failure is a problem, never a crash that loses the summary
        return f"the privacy scan failed ({exc.__class__.__name__}), so readable names cannot be ruled out"
    report.update(master_sha256=registered_sha, scanner=SCANNER, skipped_entries=skipped)
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    (ctx.workdir / "privacy-report.json").write_text(json.dumps(report, indent=1))
    if report["windows"]:
        return ("a listed person's name is readable in the master: " + "; ".join(describe(w) for w in report["windows"][:6])
                + ("; …" if len(report["windows"]) > 6 else "") + ". Veil every frame where it shows, motion included")
    missed = live_window_problem(report)
    if missed:
        return f"{missed}, so readable names cannot be ruled out there"
    return None


def masker(names: list[str]) -> Callable[[str], str]:
    """Replace every listed name, and every 4+-letter part of one, with its list index (for free text shown to people)."""
    pats = []
    for idx, name in enumerate(names, 1):
        for piece in sorted({name, *[p for p in name.split() if len(p) >= 4]}, key=len, reverse=True):
            if piece.strip():
                pats.append((re.compile(r"(?<!\w)" + re.escape(piece) + r"(?!\w)", re.I), f"[list entry #{idx}]"))

    def apply(text: str) -> str:
        for pat, repl in pats:
            text = pat.sub(repl, text)
        return text
    return apply


def main(argv: list[str] | None = None) -> int:
    try:
        return _check(sys.argv[1:] if argv is None else argv)
    except Exception as exc:   # a crash is never a finding: exit 1 means a readable name
        print(f"privacy scan failed: {type(exc).__name__}")
        print("privacy: NOT CHECKED (the check failed)")
        return 3


def _check(argv: list[str]) -> int:
    override = None
    if len(argv) == 4 and argv[2] == "--names":    # check against a list without changing the project config
        argv, override = argv[:2], str(Path(argv[3]).resolve())    # relative to where it is run, not the project
    if len(argv) != 2:
        print("usage: python -m vpg.tools.privacy <project dir> <video> [--names <list.json>]")
        print("privacy: NOT CHECKED (usage)")
        return 2
    import yaml
    project, video = Path(argv[0]), Path(argv[1])
    try:
        config = yaml.safe_load((project / "vpg.project.yaml").read_text()) or {}
    except (OSError, ValueError, yaml.YAMLError) as exc:   # ValueError: not UTF-8
        print(f"privacy scan refused: the project's vpg.project.yaml could not be read ({type(exc).__name__})")
        print("privacy: NOT CHECKED (no readable project config)")
        return 3
    if not isinstance(config, dict):
        print("privacy scan refused: the project's vpg.project.yaml is not a mapping")
        print("privacy: NOT CHECKED (no readable project config)")
        return 3
    if override:
        config = {**config, "privacy": {**(config.get("privacy") or {}), "names": override}}
    try:
        names, skipped = load_names(project, config)
    except PrivacyConfigError as exc:
        print(f"privacy scan refused: {exc}")
        print("privacy: NOT CHECKED (the name list was refused)")
        return 3
    if not names:
        print("no privacy.names list is declared in vpg.project.yaml (or given with --names); nothing to check")
        print("privacy: NOT CHECKED (no name list)")
        return 2
    cfg = config.get("privacy") or {}
    try:
        if not isinstance(cfg, dict):
            raise PrivacyConfigError("privacy must be a mapping")
        validate_scan_fps(cfg.get("scan_fps"))
        report = scan_video(video, names, cfg.get("scan_fps"), float(cfg.get("time_budget_s", 1800)))
    except (OcrUnavailable, PrivacyScanError) as exc:
        print(f"privacy scan could not complete: {exc}")
        print("privacy: NOT CHECKED (the scan could not complete)")
        return 3
    except PrivacyConfigError as exc:
        print(f"privacy scan failed: {exc}")
        print("privacy: NOT CHECKED (the check failed)")
        return 3
    print(f"scanned {report['frames']} frames at {report['fps']:g} fps with {report['ocr_calls']} OCR reads"
          + (f"; list entries {skipped} are too short to check" if skipped else ""))
    for w in report["windows"]:
        print(" -", describe(w))
    n = len(report["windows"])   # last line, so `| tail -1` still shows the verdict when a pipe hides the exit code
    print(f"privacy: FAIL ({n} name window{'s' if n != 1 else ''})" if n else "privacy: PASS")
    return 1 if n else 0


if __name__ == "__main__":
    raise SystemExit(main())
