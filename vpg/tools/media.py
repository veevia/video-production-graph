"""Media tools (N14 completion, N15 technical QA).

Technical QA measures the delivered master itself and never trusts the render log: container and codecs,
resolution and frame rate, duration against the plan, A/V duration match, EBU R128 integrated loudness and true peak,
unexplained silence, black frames, and caption sidecar validity.
"""

from __future__ import annotations

from ..context import CheckResult, NodeResult
from ..util import read_text, write_json_atomic
from ._common import blacks, finding, loudness, probe, silences, thresholds
from .captions import parse_srt

N14 = "n14_video_assembly"


def check_master(ctx) -> CheckResult:
    msgs = []
    master = ctx.artifact_path("master")
    if master is None or not master.exists():
        return CheckResult(False, ["master was not produced"])
    info = probe(master)
    kinds = {s["codec_type"] for s in info["streams"]}
    if not {"video", "audio"} <= kinds:
        msgs.append(f"master has streams {sorted(kinds)}; needs video and audio")
    cap = ctx.artifact_path("captions")
    if cap is None or not parse_srt(read_text(cap)):
        msgs.append("captions are missing or unparseable")
    for key in ("voice_stem", "music_stem", "silent"):
        if ctx.artifact_path(key) is None:
            msgs.append(f"{key} was not produced")
    return CheckResult(not msgs, msgs)


def technical_qa(ctx) -> NodeResult:
    th = thresholds(ctx)
    fm = ctx.fm("production")
    master = ctx.artifact_path("master")
    info = probe(master)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    findings = []

    def add(dim, sev, msg, fix=""):
        findings.append(finding(f"media:{dim.lower()}", dim, sev, msg, evidence=master.name, traced=N14, key="master", fix=fix))

    want_w, want_h = fm.get("resolution", [1920, 1080])
    if (v["width"], v["height"]) != (want_w, want_h):
        add("TEC-FORMAT", "P1", f"resolution {v['width']}x{v['height']} ≠ {want_w}x{want_h}")
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    fps = num / den
    if abs(fps - float(fm.get("fps", 30))) > 0.01:
        add("TEC-FORMAT", "P1", f"frame rate {fps:.3f} ≠ {fm.get('fps', 30)}")
    if v.get("codec_name") != fm.get("video_codec", "h264") or v.get("pix_fmt") != fm.get("pix_fmt", "yuv420p"):
        add("TEC-FORMAT", "P2", f"video codec {v.get('codec_name')}/{v.get('pix_fmt')} ≠ {fm.get('video_codec', 'h264')}/{fm.get('pix_fmt', 'yuv420p')}")
    if a is None or a.get("codec_name") != fm.get("audio_codec", "aac") or int(a.get("sample_rate", 0)) != int(fm.get("sample_rate", 48000)):
        add("TEC-FORMAT", "P2", f"audio stream {a and a.get('codec_name')}/{a and a.get('sample_rate')} ≠ {fm.get('audio_codec', 'aac')}/{fm.get('sample_rate', 48000)}")
    vdur = float(v.get("duration") or info["format"]["duration"])
    adur = float(a.get("duration") or vdur) if a else 0.0
    if a and abs(vdur - adur) > 0.05:
        add("TEC-SYNC", "P1", f"audio and video durations differ by {abs(vdur - adur) * 1000:.0f} ms", "re-mux with matched durations")
    brief = ctx.load("brief") or {}
    mx = float(brief.get("runtime_max_s") or 0)
    if mx and vdur > mx + 0.05:
        add("TEC-DURATION", "P2", f"duration {vdur:.2f}s exceeds the {mx:.0f}s cap")
    loud = loudness(master)
    target, tol = th["audio"]["integrated_lufs"], th["audio"]["lufs_tolerance"]
    if abs(loud["integrated_lufs"] - target) > tol:
        add("TEC-LOUDNESS", "P2", f"integrated loudness {loud['integrated_lufs']} LUFS outside {target}±{tol}", "re-normalise the mix (two-pass loudnorm)")
    if loud["true_peak_dbtp"] > th["audio"]["true_peak_max_dbtp"]:
        add("TEC-LOUDNESS", "P2", f"true peak {loud['true_peak_dbtp']} dBTP above {th['audio']['true_peak_max_dbtp']}")
    planned = [(p["start"], p["end"]) for p in (ctx.load("timings") or {}).get("planned_silences", [])]
    for s, e in silences(master, min_s=th["audio"]["unexplained_silence_s"]):
        e = min(e, vdur)
        if e - s < th["audio"]["unexplained_silence_s"]:
            continue
        if not any(ps - 0.25 <= s and e <= pe + 0.25 for ps, pe in planned):
            add("TEC-SILENCE", "P2", f"unexplained silence {s:.2f}–{e:.2f}s", "fill the gap or add it to the planned pauses")
    for s, e in blacks(master):
        if not (s < 0.1 or e > vdur - 0.6):
            add("TEC-BLACK", "P2", f"black frames {s:.2f}–{e:.2f}s")
    cap = ctx.artifact_path("captions")
    cues = parse_srt(read_text(cap)) if cap else []
    if not cues:
        add("TEC-CAPTIONS", "P1", "caption sidecar missing or empty")
    elif cues[-1]["end"] > vdur + 0.1:
        add("TEC-CAPTIONS", "P3", "captions run past the end of the master")
    report = {"master": master.name, "width": v["width"], "height": v["height"], "fps": round(fps, 3), "video_codec": v.get("codec_name"),
              "pix_fmt": v.get("pix_fmt"), "audio_codec": a and a.get("codec_name"), "sample_rate": a and int(a.get("sample_rate", 0)),
              "duration_s": round(vdur, 3), "av_diff_ms": round(abs(vdur - adur) * 1000, 1), **loud, "captions": len(cues),
              "findings": [f.model_dump() for f in findings]}
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "technical-qa.json", report)
    return NodeResult(outputs={"technical_qa": "technical-qa.json"}, findings=findings, data={"loudness": loud, "duration_s": round(vdur, 3)})
