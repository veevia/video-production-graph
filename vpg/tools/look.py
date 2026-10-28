"""The look gate (quality workflow, N26): style frames and a motion test rendered from the spec alone are measured
against the spec's look-stage gates before any asset is built.

A completion check, so a failing look fails the attempt: the agent gets the failures (gate, rule ids, role, measured
value, envelope, evidence) in its next packet, and three failures escalate. The run never reaches asset generation on a
look that fails a blocking gate, or on one that could not be judged (no envelope, not measured, registry not pinned).
N26 validates only what it writes (DESIGN.md, its tokens and MOTION.md); GATES.yaml is N03's, approved with the bar.

Every file the manifest names must sit in this attempt's work folder, and none may be a byte copy of a file in the
benchmark registry: the gate judges frames rendered for this attempt, never the references themselves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..context import CheckResult
from ..util import sha256_file, write_json_atomic
from ._common import luma_series, probe

OWN_DOCS = ("design", "tokens", "motion")
REPORT = "look-report.json"


def manifest(ctx, key: str) -> tuple[dict[str, Any] | None, Path | None, str]:
    """A gate's manifest (`look_frames`, `animatic`), its folder, or why it cannot be used. It must sit in this
    attempt's work folder."""
    path = ctx.artifact_path(key)
    if path is None or not path.is_file():
        return None, None, f"no {key} manifest was registered"
    if not path.resolve().is_relative_to(Path(ctx.workdir).resolve()):
        return None, None, f"the {key} manifest must be in this attempt's work folder ({ctx.workdir.name}), not {path.name}"
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeDecodeError) as exc:
        return None, None, f"the {key} manifest is not valid JSON ({type(exc).__name__})"
    if not isinstance(data, dict):
        return None, None, f"the {key} manifest must be a JSON object"
    return data, path.parent, ""


def attempt_file(ctx, base: Path, name: Any) -> Path | None:
    """A file a manifest names, when it resolves inside this attempt's work folder (links included); else None."""
    if not isinstance(name, str) or not name.strip():
        return None
    p = (base / name).resolve()
    return p if p.is_relative_to(Path(ctx.workdir).resolve()) else None


IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".opus"}


def _pixels(path: Path) -> str:
    """The sha256 of the decoded picture (size and RGB pixels): a re-saved copy has the same pixels. Similar frames do
    not match: a frame inside the references' envelopes is expected to share their ground."""
    import hashlib
    from PIL import Image
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        return hashlib.sha256(repr(rgb.size).encode() + rgb.tobytes()).hexdigest()


def _samples(path: Path) -> str:
    """The sha256 of the decoded audio (mono mean, 16 kHz, 16-bit): a re-saved or re-wrapped copy has the same samples."""
    import hashlib
    from .craft import pcm_mono
    return hashlib.sha256((pcm_mono(path, 16000) * 32767).astype("int16").tobytes()).hexdigest()


def copies(files: dict[str, Path], registry: dict[str, Any] | None, registry_dir: Path | None = None) -> list[str]:
    """Each named file that copies a reference or degraded-anchor file of the benchmark registry: the same bytes, or the
    same decoded content (a still's pixels, audio's samples), so a re-saved copy counts. A previous_best is this
    production's own work and may be reused. A re-encoded or edited copy is not caught; the human gate still sees it."""
    from ..benchmarks import registered_files
    if not registry or not files:
        return []
    known = registered_files(registry, registry_dir)
    by_sha = {sha: label for label, sha, _ in known if sha}
    stills = [(label, p) for label, _, p in known if p is not None and p.suffix.lower() in IMAGES and p.is_file()]
    sounds = [(label, p) for label, _, p in known if p is not None and p.suffix.lower() in AUDIO and p.is_file()]
    out, ref_pixels, ref_samples = [], None, None
    for what, p in files.items():
        hit = by_sha.get(sha256_file(p))
        try:
            if hit is None and p.suffix.lower() in IMAGES and stills:
                ref_pixels = ref_pixels if ref_pixels is not None else {_pixels(q): label for label, q in stills}
                hit = ref_pixels.get(_pixels(p))
            elif hit is None and p.suffix.lower() in AUDIO and sounds:
                ref_samples = ref_samples if ref_samples is not None else {_samples(q): label for label, q in sounds}
                hit = ref_samples.get(_samples(p))
        except Exception:   # noqa: BLE001 - an unreadable file is judged by the checks that read it
            hit = None
        if hit:
            out.append(f"{what} is a copy of the benchmark file {hit}; it must be made for this production, not copied")
    return out


def family(ctx):
    """The project's spec family (spec/, where the control store keeps the spec documents)."""
    from ..spec import load_family
    return load_family(ctx.control_path("spec-gates").parent)


def roles_of(fam) -> list[str]:
    """The style-frame roles DESIGN.md asks for (the template's four when it names none)."""
    from ..spec import style_frame_roles
    return style_frame_roles(fam)


def flashes_per_second(lumas: list[float], fps: float, delta: float = 0.2) -> float:
    """The most mean-luma swings of at least `delta` in any one-second window (WCAG 2.3.1 whole-frame proxy)."""
    if not lumas or fps <= 0:
        return 0.0
    swings = [i for i in range(1, len(lumas)) if abs(lumas[i] - lumas[i - 1]) >= delta]
    win, most, j = int(round(fps)), 0, 0
    for i, s in enumerate(swings):
        while swings[j] <= s - win:
            j += 1
        most = max(most, i - j + 1)
    return float(most)


def motion_test(path: Path, motion: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    from .craft import motion_metrics
    want = float(((motion.get("motion_test") or {}).get("duration_s")) or 5.0)
    try:
        info = probe(path)
        dur = float(info["format"]["duration"])
        video = next(s for s in info["streams"] if s.get("codec_type") == "video")
        num, _, den = str(video.get("avg_frame_rate", "0/1")).partition("/")
        fps = float(num) / float(den or 1)
    except Exception as exc:   # noqa: BLE001 - an unreadable motion test fails the look, never passes it
        return [f"the motion test {path.name} cannot be read ({type(exc).__name__})"], {}
    msgs = []
    if abs(dur - want) > 1.0:
        msgs.append(f"the motion test runs {dur:.1f} s; MOTION.md asks for {want:.1f} s (plus or minus 1 s)")
    rate = int(round(fps)) or 30   # sample the luma at the clip's own rate, so a one-second window is `rate` samples
    flashes = flashes_per_second(luma_series(path, fps=rate), rate)
    if flashes > 3:
        msgs.append(f"the motion test flashes {flashes:.0f} times in one second (at most 3: R-LEG-007)")
    m = motion_metrics(path)
    return msgs, {"duration_s": round(dur, 2), "fps": round(fps, 3), "max_flashes_per_s": flashes,
                  "motion_share": m["video.motion_share"].get("value"), "hold_share": m["video.hold_share"].get("value")}


def _frames(ctx, manifest: dict[str, Any], base: Path, wanted: list[str]) -> tuple[list[Path], list[str], list[str]]:
    msgs: list[str] = []
    frames = [f for f in manifest.get("frames") or [] if isinstance(f, dict)] if isinstance(manifest.get("frames"), list) else []
    if not 3 <= len(frames) <= 5:
        msgs.append(f"the look needs 3 to 5 style frames, not {len(frames)}")
    roles = [str(f.get("role")) for f in frames]
    if len(set(roles)) != len(roles):
        msgs.append("each style frame shows a different role (one frame per role)")
    missing = [r for r in wanted if r not in roles]
    if missing:
        msgs.append(f"style frames missing for: {', '.join(missing)} (DESIGN.md style_frames)")
    stills, have, seen = [], [], set()
    for f in frames:
        p = attempt_file(ctx, base, f.get("file"))
        if f.get("file") and p is None:
            msgs.append(f"style frame {f.get('role')}: {f.get('file')} is outside this attempt's work folder; render it there")
        elif p is None or not p.is_file():
            msgs.append(f"style frame {f.get('role')}: {f.get('file')} is missing")
        elif f.get("sha256") and sha256_file(p) != f["sha256"]:
            msgs.append(f"style frame {f.get('role')}: {f.get('file')} is not the file the manifest names")
        elif sha256_file(p) in seen:
            msgs.append(f"style frame {f.get('role')}: {f.get('file')} is the same image as another role's frame")
        else:
            seen.add(sha256_file(p))
            stills.append(p)
            have.append(str(f.get("role")))
    return stills, have, msgs


def gate_line(r: dict[str, Any], why_registry: str = "") -> str:
    """What was measured, against which envelope, for which rule ids and role."""
    where = f" for the {r['role']} frame" if r.get("role") else ""
    if r["status"] == "fail":
        return (f"{r['gate']} ({', '.join(r['rules'])}){where}: {r['metric']} = {r['value']} is {r['direction']} the "
                f"envelope {r.get('envelope')} [{r['severity']}]")
    extra = f" ({why_registry})" if why_registry and r["status"] == "no_envelope" else ""
    return (f"{r['gate']} ({', '.join(r['rules'])}){where} could not be judged: {r.get('why', r['status'])}{extra} "
            f"[{r['severity']}]")


def gate_lines(rows: list[dict[str, Any]], why_registry: str = "") -> list[str]:
    """One message per blocking row (a failed blocking gate, or one that could not be judged)."""
    from ..spec import blocking
    return [gate_line(r, why_registry) for r in blocking(rows)]


def check(ctx) -> CheckResult:
    """LOOK-GATES: the frames were rendered from the current spec; DESIGN.md, its tokens and MOTION.md lint clean;
    every style-frame role is there; each still passes every look-stage gate against the same role in the pinned
    benchmark registry; and the motion test is the length MOTION.md asks for and does not flash."""
    from ..spec import evaluate_gates, family_hash, lint, load_registry, registry_ref
    from .craft import still_metrics
    data, base, problem = manifest(ctx, "look_frames")
    if data is None:
        return CheckResult(False, [problem])
    fam = family(ctx)
    fam_hash = family_hash(fam)
    msgs: list[str] = []
    if data.get("spec_hash") != fam_hash:
        msgs.append(f"the frames were rendered from spec {str(data.get('spec_hash'))[:12]}, but spec/ is now "
                    f"{fam_hash[:12]} (`vpg spec hash`): render them again from the current spec")
    lint_errors = [i for i in lint(fam, require=OWN_DOCS, only=OWN_DOCS) if i.level == "error"]
    msgs += [f"spec: {i.line()}" for i in lint_errors[:8]]
    gates = [g for g in fam.gates() if g.get("stage") == "look"]
    if not gates:
        msgs.append("GATES.yaml defines no look-stage gate, so the look cannot be judged (N03 sets the bar)")
    wanted = roles_of(fam)
    stills, have, frame_msgs = _frames(ctx, data, base, wanted)
    msgs += frame_msgs
    ocr = any(".ocr_" in str(g.get("metric", "")) for g in gates)
    measurements = still_metrics(stills, ocr=ocr, roles=have) if stills else {}
    registry, why = load_registry(fam)
    if registry is None and any(g.get("envelope") == "benchmark" for g in gates):
        msgs.append(f"the look gates cannot be judged against the benchmarks: {why}")
    rows = evaluate_gates(fam, measurements, stage="look", registry=registry, roles=have)
    msgs += gate_lines(rows, why)
    motion_info: dict[str, Any] = {}
    mt = data.get("motion_test") if isinstance(data.get("motion_test"), dict) else {}
    mt_path = attempt_file(ctx, base, mt.get("file"))
    if not mt.get("file"):
        msgs.append("no motion test was rendered (MOTION.md motion_test)")
    elif mt_path is None:
        msgs.append(f"the motion test {mt['file']} is outside this attempt's work folder; render it there")
    elif not mt_path.is_file():
        msgs.append(f"the motion test {mt['file']} is missing")
    else:
        motion_msgs, motion_info = motion_test(mt_path, fam.fm("motion") or {})
        msgs += motion_msgs
    named = {f"style frame {r}": p for r, p in zip(have, stills)}
    if mt_path is not None and mt_path.is_file():
        named["the motion test"] = mt_path
    msgs += copies(named, registry, registry_ref(fam)[0].parent if registry else None)
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / REPORT, {"spec_hash": fam_hash, "lint_errors": [i.line() for i in lint_errors],
                                            "roles": have, "measurements": measurements, "gates": rows,
                                            "motion_test": motion_info, "registry_problem": why, "passed": not msgs})
    return CheckResult(not msgs, msgs, metrics={"spec_hash": fam_hash, "gates": len(rows),
                                                "failed": sum(r["status"] != "pass" for r in rows)})
