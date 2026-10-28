"""The animatic gate (quality workflow, N27): timed boards, a scratch read and a music sketch are checked for runtime,
beat shares, pace, pauses, voice over music, must-say terms, music hit points and a loudness pre-check before any full
build.

A completion check, like the look gate: a failing animatic fails the attempt and three failures escalate, so full
voice, assets and scenes are never built on timing that breaks the spec. N27 writes no spec document; it checks the
ones N03, N06 and N09 approved (GATES.yaml, STORY.md, VOICE.md, MUSIC.md, MIX.md), which their writers validated.

What is measured and what is declared:
  * measured: the scratch read's (and the timed-board video's) duration, so the runtime; pace, pauses and voice over
    music; where the music sketch has accents (hit points); the scratch mix's loudness and true peak.
  * declared: where each beat starts and ends inside the read. The beats must start at 0, follow each other without a
    gap or an overlap, and cover the measured runtime, so their shares add up to what was measured.
Every file the manifest names sits in this attempt's work folder and is no byte copy of a benchmark file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..context import CheckResult
from ..util import write_json_atomic
from .specchecks import _spoken, term_report

REPORT = "animatic-report.json"
HIT_TOLERANCE_S = 0.15     # a hit point lands when the music has an accent this close to it
ACCENT_WINDOW_S, ACCENT_MARGIN_DB = 0.5, 3.0   # an accent: an onset 3 dB stronger than any other within 0.5 s
ACCENT_CONTEXT_S, SILENCE_BELOW_DB = 1.0, 60.0  # strength: peak over the music's median level within 1 s, silence ignored
BEAT_TOLERANCE_S = 0.05    # the largest gap or overlap between declared beats
COVER_TOLERANCE_S = 0.25   # the beats may stop this short of the measured runtime
ONSET_RISE_DB, ONSET_HOP_S, ONSET_LOOKBACK_S, ONSET_MERGE_S, ONSET_FLOOR_DB = 6.0, 0.01, 0.03, 0.08, 40.0
PRECHECK_MAX_LIMITING_DB = 6.0
NEEDS = {"story": "STORY.md (N06)", "voice": "VOICE.md (N09)", "music": "MUSIC.md (N09)", "mix": "MIX.md (N09)",
         "gates": "GATES.yaml (N03)"}


def onsets(path: Path, rate: int = 22050) -> list[tuple[float, float]]:
    """(time, accent strength in dB) of each onset in the music.

    An onset is where short-term energy (20 ms windows, 10 ms hop, the mono mean of the channels) rises by at least
    ONSET_RISE_DB within ONSET_LOOKBACK_S, within ONSET_FLOOR_DB of the loudest window. Silence counts as
    SILENCE_BELOW_DB under the loudest window, and the file is read with silence before 0 s, so a hit on the first frame
    counts. Its strength is how far its peak (the next 50 ms) rises above the music's median level over the surrounding
    ACCENT_CONTEXT_S, ignoring silence: the music coming in scores about 0, a designed hit over it scores high. Onsets
    closer than ONSET_MERGE_S are one: the first time, the strongest."""
    from .craft import pcm_mono
    hop = int(rate * ONSET_HOP_S)
    win, k = 2 * hop, max(1, int(round(ONSET_LOOKBACK_S / ONSET_HOP_S)))
    lead = win + k * hop
    x = np.concatenate([np.zeros(lead, np.float32), pcm_mono(path, rate)])
    if len(x) < lead + win:
        return []
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    level = 10 * np.log10((x[idx] ** 2).mean(axis=1) + 1e-12)
    floor = float(level.max()) - SILENCE_BELOW_DB
    level = np.maximum(level, floor)
    before = np.min(np.stack([level[k - j:n - j] for j in range(1, k + 1)]), axis=0)
    rise = level[k:] - before
    found = np.nonzero((rise >= ONSET_RISE_DB) & (level[k:] >= level.max() - ONSET_FLOOR_DB))[0] + k
    span = int(round(ACCENT_CONTEXT_S / ONSET_HOP_S))
    peak_n = max(1, int(round(0.05 / ONSET_HOP_S)))
    out: list[tuple[float, float]] = []
    for i in found:
        t = max(0.0, float((i * hop - lead) / rate))
        around = level[max(0, i - span):i + span + 1]
        music = around[around > floor + 1.0]
        strength = float(level[i:i + peak_n].max()) - (float(np.median(music)) if music.size else floor)
        if out and t - out[-1][0] <= ONSET_MERGE_S:
            out[-1] = (out[-1][0], max(out[-1][1], strength))
        else:
            out.append((t, strength))
    return [(round(t, 3), round(s, 1)) for t, s in out]


def hit_points(music: dict[str, Any], measured: list[tuple[float, float]] | None, runtime: float) -> list[dict[str, Any]]:
    """Each MUSIC.md hit point against the music sketch's accents (None: no sketch, so not measured). A hit point lands
    on an onset within HIT_TOLERANCE_S that is at least ACCENT_MARGIN_DB stronger than every other onset within
    ACCENT_WINDOW_S, except onsets that land other declared hit points. A steady pulse has onsets everywhere and
    accents nowhere; two designed hits close together each land."""
    declared = [hp for hp in music.get("hit_points") or [] if isinstance(hp, dict)]
    times = [float(hp.get("at_s", 0)) for hp in declared]
    rows = []
    for n, hp in enumerate(declared):
        at, others = times[n], times[:n] + times[n + 1:]
        row: dict[str, Any] = {"at_s": at, "what": hp.get("what", "")}
        if measured is None:
            row.update(status="not_measured", why="no music sketch")
        elif runtime and at > runtime:
            row.update(status="fail", why=f"after the end of the animatic ({runtime:.1f} s)")
        else:
            cands = [o for o in measured if abs(o[0] - at) <= HIT_TOLERANCE_S]
            if not cands:
                nearest = min(measured, key=lambda o: abs(o[0] - at)) if measured else None
                row.update(status="fail", why="the music sketch has no onset" if nearest is None else
                           f"no onset within {HIT_TOLERANCE_S} s (the nearest is at {nearest[0]} s)")
            else:
                best = max(cands, key=lambda o: o[1])
                rivals = [o for o in measured if o is not best and abs(o[0] - at) <= ACCENT_WINDOW_S
                          and not any(abs(o[0] - h) <= HIT_TOLERANCE_S for h in others)]
                top = max((o[1] for o in rivals), default=None)
                row["landed"] = best[0]
                if top is not None and best[1] < top + ACCENT_MARGIN_DB:
                    row.update(status="fail", why=f"no onset stands out: the one at {best[0]} s is {best[1]} dB above the "
                                                  f"music, another within {ACCENT_WINDOW_S} s is {top} dB (an accent is "
                                                  f"{ACCENT_MARGIN_DB} dB above the rest)")
                else:
                    row.update(status="pass", why="")
        rows.append(row)
    return rows


def duration(path: Path) -> float:
    from ._common import probe
    return float(probe(path)["format"]["duration"])


def beat_problems(beats: list[dict[str, Any]], runtime: float) -> list[str]:
    """The declared beats start at 0, follow each other without a gap or an overlap, and cover the measured runtime."""
    if not beats:
        return ["the animatic declares no timed beats"]
    msgs = []
    try:
        spans = [(str(b.get("id")), float(b["start"]), float(b["end"])) for b in beats]
    except (KeyError, TypeError, ValueError):
        return ["every beat needs a numeric start and end"]
    if abs(spans[0][1]) > BEAT_TOLERANCE_S:
        msgs.append(f"the first beat starts at {spans[0][1]:.2f} s, not 0")
    for (a, _, a_end), (b, b_start, _) in zip(spans, spans[1:]):
        if abs(b_start - a_end) > BEAT_TOLERANCE_S:
            msgs.append(f"beat {b} starts at {b_start:.2f} s but beat {a} ends at {a_end:.2f} s (no gaps or overlaps)")
    msgs += [f"beat {i} ends before it starts" for i, s, e in spans if e <= s]
    if spans[-1][2] < runtime - COVER_TOLERANCE_S:
        msgs.append(f"the beats end at {spans[-1][2]:.2f} s, but the measured scratch read runs {runtime:.2f} s: time every "
                    "board to the read")
    return msgs


def _stereo(path: Path) -> str:
    """The filter that plays a file as stereo, the way the master is metered: a mono read as dual-mono at unity (BS.1770
    meters it 3 dB louder than the same mono file), stereo as it is, more channels downmixed by ffmpeg's matrix."""
    from .craft import media_info
    channels = int(media_info(path).get("channels", 1) or 1)
    return {1: "pan=stereo|c0=c0|c1=c0", 2: "anull"}.get(channels, "aformat=channel_layouts=stereo")


def loudness_precheck(voice: Path, music: Path | None, mix: dict[str, Any], workdir: Path) -> tuple[list[str], dict[str, Any]]:
    """The scratch mix does not clip, and can reach MIX.md's integrated target within its true-peak ceiling without more
    than `loudness.precheck_max_limiting_db` (default 6 dB) of limiting. The mix is the read plus the music sketch at
    unity, both as stereo at 48 kHz, for the length of the read, in 32-bit float (so a peak above full scale is kept), and
    is metered with ffmpeg ebur128 (BS.1770), as technical QA meters the master."""
    from ._common import FFMPEG, loudness, run
    lo = mix.get("loudness") if isinstance(mix.get("loudness"), dict) else {}
    target, ceiling = lo.get("integrated_lufs"), lo.get("true_peak_max_dbtp")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (target, ceiling)):
        return ["MIX.md loudness needs integrated_lufs and true_peak_max_dbtp; the loudness pre-check cannot run"], {}
    allowed = float(lo.get("precheck_max_limiting_db", PRECHECK_MAX_LIMITING_DB))
    out = workdir / "scratch-mix.wav"
    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(voice)]
    try:
        graph = f"[0:a]{_stereo(voice)},aresample=48000[v]"
        if music:
            cmd += ["-i", str(music)]
            graph += f";[1:a]{_stereo(music)},aresample=48000[m];[v][m]amix=inputs=2:normalize=0:duration=first[out]"
        else:
            graph += ";[v]anull[out]"
        p = run(cmd + ["-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_f32le", str(out)], timeout=600)
        if p.returncode != 0:
            raise RuntimeError(p.stderr[-200:])
        meas = loudness(out)
    except Exception as exc:   # noqa: BLE001 - an unmeasurable mix fails the pre-check, never passes it
        return [f"the scratch mix's loudness cannot be measured ({type(exc).__name__})"], {}
    i, tp = meas["integrated_lufs"], meas["true_peak_dbtp"]
    limiting = round(max(0.0, tp + (float(target) - i) - float(ceiling)), 1)
    info = {**meas, "target_lufs": target, "ceiling_dbtp": ceiling, "limiting_db": limiting, "allowed_limiting_db": allowed,
            "basis": "voice plus music sketch at unity; ffmpeg ebur128 (BS.1770)"}
    msgs = []
    if i <= -69.0:
        msgs.append(f"the scratch mix is silent ({i} LUFS)")
    if tp > 0.0:
        msgs.append(f"the scratch mix clips: true peak {tp} dBTP (above 0)")
    if limiting > allowed:
        msgs.append(f"reaching MIX.md's {target} LUFS would take the true peak {limiting} dB over its {ceiling} dBTP ceiling, "
                    f"more limiting than the pre-check allows ({allowed} dB): tame the peaks in the read or the music")
    return msgs, info


def check(ctx) -> CheckResult:
    """ANI-TIMING: made from the current spec; the measured runtime within STORY.md and covered by the beats; every beat
    near its share; pace, pauses and voice over music inside their animatic gates; every must-say term spoken and no
    banned phrase; every MUSIC.md hit point on a measured onset; the scratch mix passes the loudness pre-check."""
    from ..spec import beat_shares, evaluate_gates, family_hash, load_registry, registry_ref
    from .craft import mix_metrics, voice_metrics
    from .look import attempt_file, copies, family, gate_lines, manifest
    data, base, problem = manifest(ctx, "animatic")
    if data is None:
        return CheckResult(False, [problem])
    fam = family(ctx)
    fam_hash = family_hash(fam)
    msgs: list[str] = []
    if data.get("spec_hash") != fam_hash:
        msgs.append(f"the animatic was made from spec {str(data.get('spec_hash'))[:12]}, but spec/ is now {fam_hash[:12]} "
                    "(`vpg spec hash`): make it again from the current spec")
    msgs += [f"{name} is missing; the animatic is checked against it" for doc, name in NEEDS.items() if doc not in fam.docs]
    story, music_spec, mix_spec = fam.fm("story") or {}, fam.fm("music") or {}, fam.fm("mix") or {}
    narration = _spoken(ctx.load("script") or {})
    files: dict[str, Path] = {}
    for key, what in (("voice", "the scratch read"), ("music", "the music sketch"), ("video", "the timed-board video")):
        if not data.get(key):
            continue
        p = attempt_file(ctx, base, data.get(key))
        if p is None:
            msgs.append(f"{what} {data.get(key)} is outside this attempt's work folder; make it there")
        elif not p.is_file():
            msgs.append(f"{what} {data.get(key)} is missing")
        else:
            files[key] = p
    voice, mus = files.get("voice"), files.get("music")
    measurements: dict[str, Any] = {}
    runtime, loud = 0.0, {}
    if voice is None:
        msgs.append("no scratch read: the animatic needs a voice track to measure its runtime, pace and pauses")
    else:
        try:
            runtime = max([duration(voice)] + ([duration(files["video"])] if "video" in files else []))
        except Exception as exc:   # noqa: BLE001 - an unreadable read fails the animatic, never passes it
            msgs.append(f"the scratch read cannot be read ({type(exc).__name__})")
        measurements.update(voice_metrics(voice, narration or None))
        if mus is not None:
            measurements.update(mix_metrics(voice, mus))
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        loud_msgs, loud = loudness_precheck(voice, mus, mix_spec, ctx.workdir)
        msgs += loud_msgs
    gates = [g for g in fam.gates() if g.get("stage") == "animatic"]
    registry, why = load_registry(fam)
    if registry is None and any(g.get("envelope") == "benchmark" for g in gates):
        msgs.append(f"the animatic gates cannot be judged against the benchmarks: {why}")
    msgs += copies({f"the {k} track" if k != "video" else "the timed-board video": p for k, p in files.items()}, registry,
                   registry_ref(fam)[0].parent if registry else None)
    rows = evaluate_gates(fam, measurements, stage="animatic", registry=registry)
    msgs += gate_lines(rows, why)
    beats = [b for b in data.get("beats") or [] if isinstance(b, dict)] if isinstance(data.get("beats"), list) else []
    if voice is not None and runtime:
        msgs += beat_problems(beats, runtime)
    runtime = max([runtime] + [float(b["end"]) for b in beats if isinstance(b.get("end"), (int, float))])
    cap = float((story.get("runtime_s") or {}).get("max") or 0)
    if cap and runtime > cap:
        msgs.append(f"the animatic runs {runtime:.1f} s (measured); STORY.md caps it at {cap:.0f} s (cut words, never pauses)")
    chapter_of = {b.get("id"): b.get("chapter") for b in (ctx.load("storyboard") or {}).get("beats", [])}
    timed = [(chapter_of.get(b.get("id"), b.get("chapter")), float(b["end"]) - float(b["start"])) for b in beats
             if isinstance(b.get("start"), (int, float)) and isinstance(b.get("end"), (int, float))]
    shares = beat_shares(story, timed)
    msgs += [f"story beat {b['beat']} takes {b['share']:.0%} of the runtime; STORY.md plans {b['spec']:.0%} (plus or "
             f"minus {b['tolerance']:.0%})" for b in shares if b["status"] == "fail"]
    terms = term_report(story.get("must_say") or [], story.get("banned_phrases") or [], narration)
    msgs += [f"the must-say term '{t}' is never spoken" for t in terms["missing"]]
    msgs += [f"the banned phrase '{b}' is spoken" for b in terms["banned_hits"]]
    measured_onsets: list[tuple[float, float]] | None = None
    if mus is not None:
        try:
            measured_onsets = onsets(mus)
        except Exception as exc:   # noqa: BLE001 - unmeasured hit points fail below, never pass
            msgs.append(f"the music sketch cannot be read ({type(exc).__name__})")
    hits = hit_points(music_spec, measured_onsets, runtime)
    for h in hits:
        if h["status"] == "not_measured":
            msgs.append(f"MUSIC.md hit point at {h['at_s']:.2f} s ({h['what']}) cannot be checked: {h['why']}")
        elif h["status"] == "fail":
            msgs.append(f"MUSIC.md hit point at {h['at_s']:.2f} s ({h['what']}) is not landed: {h['why']}")
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / REPORT, {"spec_hash": fam_hash, "measurements": measurements, "gates": rows,
                                            "runtime_s": runtime, "beats": shares, "terms": terms, "hits": hits,
                                            "onsets": measured_onsets, "loudness": loud, "registry_problem": why,
                                            "passed": not msgs})
    return CheckResult(not msgs, msgs, metrics={"runtime_s": runtime, "gates": len(rows)})
