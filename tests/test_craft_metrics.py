"""P0: craft metrics, metric versioning, the benchmark registry and the ratchet.

Acceptance (handoff brief, P0): on synthetic fixtures the metrics separate a good film from a degraded one, and the
benchmark envelopes do not flag the benchmark itself.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

from tests.conftest import PKG
from tests import craft_fixtures as cf
from vpg import benchmarks as bm
from vpg.metrics import METRICS, comparable, current, measured, not_measured
from vpg.tools import craft

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for media metrics")


# ------------------------------------------------------------------------------------------------ pure functions
def test_metric_ids_versions_and_bases_are_stable():
    assert len({m.id for m in METRICS.values()}) == len(METRICS)
    for m in METRICS.values():
        assert m.version >= 1 and len(m.basis_sha) == 16 and m.sides in ("both", "lower", "upper")
        assert m.basis_sha == METRICS[m.id].basis_sha   # deterministic


def test_a_basis_change_is_never_compared():
    a = measured("audio.vo_over_music.p10", 14.0)
    b = dict(a, value=9.0)
    assert comparable(a, b) is None
    moved = dict(b, basis="0" * 16)
    assert "basis changed" in comparable(a, moved) and not current(moved)
    assert "not measured" in comparable(a, not_measured("audio.vo_over_music.p10", "no stem"))
    assert measured("audio.pace_wpm", float("nan"))["value"] is None


def test_colourfulness_tracks_saturation():
    grey = np.full((90, 160, 3), 128, np.uint8)
    assert craft.colourfulness(grey) == pytest.approx(0.0, abs=1e-9)
    y, x = np.mgrid[0:90, 0:160]
    sat = np.stack([127 + 127 * np.sin(x / 9), 127 + 127 * np.sin(x / 9 + 2.1), 127 + 127 * np.sin(y / 7 + 4.2)], -1)
    assert craft.colourfulness(sat.astype(np.uint8)) > 80


def test_ground_excludes_enclosed_cards():
    rgb = np.zeros((160, 256, 3), np.uint8)
    rgb[...] = (30, 34, 40)
    rgb[48:112, 64:192] = (255, 200, 0)   # a flat yellow card in the middle, enclosed by its own edges
    stats = craft.frame_stats(rgb)
    assert stats["ground_colourfulness"] < 10 < stats["colourfulness"]


def _layer(i, bbox, start, end, **kw):
    return {"id": i, "scene": "S", "kind": "text", "text": i, "bbox": bbox, "start": start, "end": end, **kw}


def test_overlaps_cover_transitions_not_only_held_text():
    held = {"frame": {"w": 1920, "h": 1080}, "layers": [_layer("a", [0, 0, 100, 40], 0, 2), _layer("b", [50, 10, 100, 40], 1, 3)]}
    assert [o["during"] for o in craft.overlaps(held)] == ["held"]
    fades = {"frame": {"w": 1920, "h": 1080}, "layers": [_layer("a", [0, 0, 100, 40], 0, 2, fade_out_s=0.5),
                                                         _layer("b", [50, 10, 100, 40], 2.2, 4, fade_in_s=0.5)]}
    assert [o["during"] for o in craft.overlaps(fades)] == ["transition"]
    fades["layers"][0]["allow_overlap"] = ["b"]
    assert craft.overlaps(fades) == []
    apart = {"frame": {"w": 1920, "h": 1080}, "layers": [_layer("a", [0, 0, 100, 40], 0, 2), _layer("b", [0, 200, 100, 40], 0, 2)]}
    assert craft.overlaps(apart) == []


def test_touching_line_boxes_are_not_an_overlap():
    """Stacked lines of one headline touch (their line boxes meet by a pixel or two); text that crosses is an overlap."""
    def pair(dy):
        return {"frame": {"w": 1920, "h": 1080}, "layers": [_layer("a", [160, 350, 640, 130], 0, 5, font_px=120),
                                                             _layer("b", [160, 480 - dy, 640, 128], 0, 5, font_px=120)]}
    assert craft.overlaps(pair(1.7)) == []
    (o,) = craft.overlaps(pair(3.0))
    assert o["box_height_vs_type"] == [1.1, 1.1]
    assert METRICS["text.overlap_all.count"].version == 2


# ------------------------------------------------------------------------------------------------ eval regressions
def test_momentary_windows_are_placed_by_time():
    log = ("t: 0.1 TARGET:-23 LUFS M: -30.0 S:-40\n"
           "t: 0.2 TARGET:-23 LUFS M: nan S: nan\n"
           "t: 0.3 TARGET:-23 LUFS M:-inf S:-inf\n"
           "t: 0.5 TARGET:-23 LUFS M:-25.5 S:-30\n")
    assert craft.parse_momentary(log).tolist() == [-30.0, -120.0, -120.0, -120.0, -25.5]
    assert METRICS["audio.vo_over_music.p10"].version == METRICS["audio.vo_over_music.median"].version == 2


@needs_ffmpeg
def test_digital_silence_after_loud_speech_keeps_the_stems_in_step(tmp_path):
    """ffmpeg reports `M: nan` for 400 ms of digital zeros after loud audio; dropping those windows used to pair the
    voice with music from seconds earlier (a real v1.9 eval finding on production stems)."""
    rate = 48000
    rng = np.random.default_rng(1)
    voice = np.zeros(rate * 6, np.float32)
    for a in (0, 2, 4):
        voice[a * rate:(a + 1) * rate] = np.clip(rng.standard_normal(rate) * 0.3, -0.95, 0.95)
    v = cf._write_wav(tmp_path / "v.wav", voice)
    raw = subprocess.run([cf.FFMPEG, "-hide_banner", "-nostats", "-i", str(v), "-af",
                          "pan=mono|c0=c0,aresample=48000,ebur128=framelog=info", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    assert re.search(r"M:\s*nan", raw)                         # the case this guards against really occurs
    m = cf.music(tmp_path / "m.wav", gain_db=-30.0, seconds=6.0)
    lv, lm = craft.momentary_lufs(v), craft.momentary_lufs(m)
    assert len(lv) == len(lm) and (lv == -120.0).sum() >= 10
    res = craft.mix_metrics(v, m)["audio.vo_over_music.median"]
    expect = float(np.median((lv - lm)[lv > -35.0]))
    assert res["value"] == pytest.approx(expect)


def test_narration_comes_from_timing_units_and_no_words_is_not_measured(tmp_path):
    timings = tmp_path / "timings.json"
    timings.write_text(json.dumps({"chapters": [{"id": "ch1", "start": 0, "end": 5}],
                                   "units": [{"unit": "U1", "text": "Signals reach the right owner."}]}))
    assert craft.narration_text(timings) == "Signals reach the right owner."
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"chapters": [{"id": "ch1", "units": [{"id": "u1", "text": "One owner per signal."}]}]}))
    assert craft.narration_text(script) == "One owner per signal."
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"chapters": [{"id": "ch1"}], "units": []}))
    with pytest.raises(ValueError, match="no narration text"):
        craft.narration_text(empty)
    x = np.concatenate([np.zeros(8000), 0.2 * np.sin(np.arange(32000) / 3.0), np.zeros(8000)]).astype(np.float32)
    pace = craft.voice_metrics(tmp_path / "unused.wav", narration=" ... ", x=x)["audio.pace_wpm"]
    assert pace["value"] is None and "no words" in pace["not_measured"]


@needs_ffmpeg
@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract is required for the OCR metrics")
def test_ui_text_size_at_player_width_tells_small_from_readable(tmp_path):
    """The fixed 8 px share calls 5 px and 7 px UI text both small; the word-size metric tells them apart, and text too
    small to read confidently is not measured, so a blocking gate on it fails closed."""
    size = {px: craft.ocr_metrics(cf.ocr_clip(tmp_path / f"c{px}.mp4", text_px=px, columns=2), step_s=2.0)
            for px in (6, 9, 13)}
    small, readable = size[9]["text.ocr_word_px.dense_min"]["value"], size[13]["text.ocr_word_px.dense_min"]["value"]
    assert small is not None and readable is not None and readable - small >= 1.5
    assert size[6]["text.ocr_word_px.dense_min"]["value"] is None
    assert "no text-dense frame" in size[6]["text.ocr_word_px.dense_min"]["not_measured"]


def test_min_text_at_player_width():
    tl = {"frame": {"w": 1920, "h": 1080}, "layers": [_layer("a", [0, 0, 10, 10], 0, 1, font_px=24),
                                                      _layer("b", [0, 50, 10, 10], 0, 1, font_px=40, essential=True),
                                                      _layer("c", [0, 90, 10, 10], 0, 1, font_px=12, essential=False)]}
    rec = craft.text_metrics(tl)["text.min_px_at_player"]
    assert rec["value"] == pytest.approx(24 * 960 / 1920) and rec["evidence"]["layer"] == "a"


# ------------------------------------------------------------------------------------------------ media fixtures
@pytest.fixture(scope="module")
def films(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for media metrics")
    root = tmp_path_factory.mktemp("craft")
    sets = {f"good{s}": cf.good_set(root / f"good{s}", seed=s) for s in range(3)}
    sets["degraded"] = cf.degraded_set(root / "degraded")
    return root, sets, {name: craft.measure(**files)["measurements"] for name, files in sets.items()}


SEPARATING = {   # metric → which way the degraded film must sit relative to every good film
    "video.colourfulness.median": "higher", "video.ground_colourfulness.median": "higher",
    "video.motion_share": "higher", "video.hold_share": "lower", "audio.pause_share": "lower",
    "audio.pace_wpm": "higher", "audio.vo_over_music.p10": "lower", "audio.vo_over_music.median": "lower",
    "text.overlap_all.count": "higher",
}


@needs_ffmpeg
def test_metrics_separate_good_from_degraded(films):
    _, _, m = films
    for mid, way in SEPARATING.items():
        good = [m[f"good{s}"][mid]["value"] for s in range(3)]
        bad = m["degraded"][mid]["value"]
        assert None not in good and bad is not None, mid
        assert (bad > max(good)) if way == "higher" else (bad < min(good)), (mid, good, bad)
    assert m["degraded"]["text.overlap_all.count"]["evidence"]["transition_only"] == 1
    assert m["good0"]["audio.pause_share"]["evidence"]["pauses"]   # timecoded evidence for the viewer


@needs_ffmpeg
def test_measuring_twice_gives_the_same_values(films):
    _, sets, m = films
    again = craft.measure(**sets["good1"])["measurements"]
    assert {k: v["value"] for k, v in again.items()} == {k: v["value"] for k, v in m["good1"].items()}


def _registry(root, sets, measurements, with_anchor=True):
    reg = bm.new_registry("synthetic", "Synthetic benchmarks")
    for name in ("good0", "good1", "good2"):
        files = {k: str(v.relative_to(root)) for k, v in sets[name].items()}
        reg["entries"].append({"id": name.upper(), "kind": "reference", "evidence": "observed",
                               "media": {k: {"path": p} for k, p in files.items()}, "measurements": measurements[name]})
    if with_anchor:
        reg["entries"].append({"id": "ANCHOR-SAT", "kind": "degraded_anchor", "degrades": "GOOD0",
                               "how": "saturated ground, constant motion, no pauses, music close to the voice",
                               "media": {k: {"path": str(v.relative_to(root))} for k, v in sets["degraded"].items()},
                               "measurements": measurements["degraded"]})
    assert bm.validate(reg) == []
    return reg


@needs_ffmpeg
def test_acceptance_envelopes_never_flag_the_benchmark_and_catch_the_anchor(films):
    root, sets, m = films
    reg = _registry(root, sets, m)
    report = bm.verify(reg, base=root)
    assert report["ok"], report
    assert report["references_flagged"] == [] and report["anchors_missed"] == []
    assert len(report["anchors_caught"]["ANCHOR-SAT"]) >= 6
    envs = bm.envelopes(reg)
    for name in ("good0", "good1", "good2"):   # fresh re-measurements of the benchmark media stay inside
        fresh = craft.measure(**sets[name])["measurements"]
        assert [r for r in bm.check(fresh, envs) if r["status"] not in ("inside",)] == [], name
    flagged = {r["metric"] for r in bm.check(m["degraded"], envs) if r["status"] in ("below", "above")}
    assert {"video.colourfulness.median", "video.motion_share", "audio.pause_share", "audio.vo_over_music.p10",
            "text.overlap_all.count"} <= flagged


@needs_ffmpeg
def test_an_anchor_that_is_not_caught_fails_verification(films):
    root, sets, m = films
    reg = _registry(root, sets, m)
    reg["entries"][-1]["measurements"] = m["good1"]   # an "anchor" indistinguishable from a reference
    report = bm.verify(reg)
    assert not report["ok"] and report["anchors_missed"] == ["ANCHOR-SAT"]


@needs_ffmpeg
def test_a_floor_above_a_reference_flags_the_benchmark(films):
    root, sets, m = films
    reg = _registry(root, sets, m, with_anchor=False)
    reg["envelope_policy"] = {"audio.vo_over_music.p10": {"floor": 30.0}}
    assert bm.verify(reg)["references_flagged"]   # the rollback trigger: never ship such an envelope


@needs_ffmpeg
def test_ratchet_needs_a_human_waiver(films):
    _, _, m = films
    rows = bm.ratchet(m["good0"], m["degraded"])
    regressed = {r["metric"] for r in rows if r["status"] == "regressed"}
    assert {"audio.pause_share", "audio.vo_over_music.p10", "video.motion_share", "text.overlap_all.count"} <= regressed
    agent = bm.ratchet(m["good0"], m["degraded"], waivers=[{"metric": "video.motion_share", "by": "agent:critic", "reason": "x"}])
    assert next(r for r in agent if r["metric"] == "video.motion_share")["status"] == "regressed"
    human = bm.ratchet(m["good0"], m["degraded"], waivers=[{"metric": "video.motion_share", "by": "Wei", "reason": "kinetic opener"}])
    assert next(r for r in human if r["metric"] == "video.motion_share")["status"] == "waived"
    assert bm.ratchet(m["good0"], m["good0"]) == []


@needs_ffmpeg
def test_stale_measurements_are_remeasured_never_compared(films, tmp_path):
    root, sets, m = films
    reg = _registry(root, sets, m)
    old = reg["entries"][0]["measurements"]["video.motion_share"]
    reg["entries"][0]["measurements"]["video.motion_share"] = dict(old, basis="f" * 16, value=0.9)
    assert "video.motion_share" not in bm.envelopes(reg)          # a metric some reference lacks gets no envelope
    assert bm.stale(reg)["GOOD0"] == ["video.motion_share"]
    cand = dict(m["degraded"], **{"audio.pause_share": dict(m["degraded"]["audio.pause_share"], basis="e" * 16)})
    row = next(r for r in bm.check(cand, bm.envelopes(reg)) if r["metric"] == "audio.pause_share")
    assert row["status"] == "not_comparable"
    path = root / "registry.json"
    bm.save(path, reg)
    done = bm.remeasure(path)
    assert done == {"GOOD0": ["video.motion_share"]}
    assert bm.load(path)["entries"][0]["measurements"]["video.motion_share"]["value"] == old["value"]
    assert "video.motion_share" in bm.envelopes(bm.load(path))


def test_measuring_with_ocr_fills_only_what_ocr_can_fill_and_settles(tmp_path):
    import hashlib
    from vpg.metrics import METRICS, measured, not_measured
    from vpg.util import sha256_file
    (tmp_path / "ref.mp4").write_bytes(b"a film")
    master = {"path": "ref.mp4", "sha256": sha256_file(tmp_path / "ref.mp4")}
    ref = {mid: measured(mid, 1.0) for mid in METRICS}
    ref["text.ocr_word_px.dense_min"] = not_measured("text.ocr_word_px.dense_min", "needs a master and --ocr")
    ref["audio.pace_wpm"] = not_measured("audio.pace_wpm", "no narration text was given")
    dense = dict(ref, **{"text.ocr_word_px.dense_min": not_measured(          # OCR ran and found too little text
        "text.ocr_word_px.dense_min", "no text-dense frame (none with 20 or more confidently read words)")})
    anchor = dict(ref, **{mid: not_measured(mid, "needs style frames and --ocr") for mid in METRICS if mid.startswith("still.ocr")})
    path = tmp_path / "registry.json"
    bm.save(path, {**bm.new_registry("r"), "entries": [
        {"id": "REF", "kind": "reference", "media": {"master": master}, "measurements": ref},
        {"id": "DENSE", "kind": "reference", "media": {"master": master}, "measurements": dense},
        {"id": "NOFILM", "kind": "reference", "media": {}, "measurements": dict(ref)},    # nothing for OCR to read
        {"id": "ANCHOR", "kind": "degraded_anchor", "degrades": "REF", "how": "x", "media": {"master": master},
         "measurements": anchor}]})
    assert bm.stale(bm.load(path)) == {}
    before = path.read_bytes()
    assert bm.remeasure(path) == {} and path.read_bytes() == before           # nothing stale: the file is not rewritten
    assert bm.stale(bm.load(path), ocr=True) == {"REF": ["text.ocr_word_px.dense_min"],
                                                 "ANCHOR": ["text.ocr_word_px.dense_min"]}
    calls = []

    def reader(ocr=False, **files):
        calls.append(sorted(files))
        return {"measurements": {mid: measured(mid, 5.0 if ocr else 1.0) for mid in METRICS}}
    assert bm.remeasure(path, ocr=True, measure_fn=reader) == {"REF": ["text.ocr_word_px.dense_min"],
                                                                "ANCHOR": ["text.ocr_word_px.dense_min"]}
    after = bm.load(path)["entries"][0]["measurements"]
    assert after["text.ocr_word_px.dense_min"]["value"] == 5.0 and after["audio.pace_wpm"]["value"] is None
    filled = path.read_bytes()
    assert bm.stale(bm.load(path), ocr=True) == {}                            # settled: a second run has nothing to do
    assert bm.remeasure(path, ocr=True, measure_fn=reader) == {} and path.read_bytes() == filled and len(calls) == 2
    same = bm.load(path)["entries"][0]["measurements"]                         # --all, and every value comes out the same
    assert bm.remeasure(path, ["REF"], all_metrics=True, measure_fn=lambda **_: {"measurements": same}) == {}
    assert path.read_bytes() == filled and hashlib.sha256(filled).hexdigest() == sha256_file(path)


@needs_ffmpeg
def test_only_a_named_human_sets_the_baseline(films):
    root, sets, _ = films
    path = root / "baseline-registry.json"
    files = {k: str(v) for k, v in sets["good0"].items()}
    with pytest.raises(bm.BenchmarkError, match="named human"):
        bm.add_entry(path, "BEST-1", "previous_best", files, by="agent:producer", reason="best so far")
    with pytest.raises(bm.BenchmarkError, match="reason"):
        bm.add_entry(path, "BEST-1", "previous_best", files, by="Wei")
    first = bm.add_entry(path, "BEST-1", "previous_best", files, by="Wei", reason="approved at the end review")
    second = bm.add_entry(path, "BEST-2", "previous_best", {k: str(v) for k, v in sets["good1"].items()}, by="Wei",
                          reason="the new cut is better")
    data = bm.load(path)
    assert bm.live_baseline(data)["id"] == second["id"] and bm.entry(data, first["id"])["superseded_by"] == "BEST-2"
    assert bm.media_problems(bm.entry(data, "BEST-1"), root) == []


@needs_ffmpeg
def test_the_mix_basis_is_fixed_across_channel_layouts(tmp_path):
    """A mono voice and the same voice as true dual-mono stereo (L = R = the voice) measure the same margin. Metered
    as-is, BS.1770 sums both channels and reads the dual-mono copy about 3 dB louder: the trap a fixed basis avoids."""
    import re
    ff = shutil.which("ffmpeg")
    v = cf.voice(tmp_path / "v.wav", seed=3)
    mu = cf.music(tmp_path / "m.wav", gain_db=-26.0)
    stereo = tmp_path / "v2.wav"
    subprocess.run([ff, "-v", "error", "-y", "-i", str(v), "-af", "pan=stereo|c0=c0|c1=c0", str(stereo)], check=True)
    a = craft.mix_metrics(v, mu)["audio.vo_over_music.p10"]["value"]
    b = craft.mix_metrics(stereo, mu)["audio.vo_over_music.p10"]["value"]
    assert abs(a - b) < 0.2, (a, b)

    def as_is(path):
        err = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])
    assert as_is(stereo) - as_is(v) == pytest.approx(3.0, abs=0.3)


@needs_ffmpeg
@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract is required for the OCR metric")
def test_small_text_at_player_width_is_measured(tmp_path):
    big = craft.ocr_metrics(cf.ocr_clip(tmp_path / "big.mp4", text_px=26), step_s=2.0)["text.ocr_small_share"]
    small = craft.ocr_metrics(cf.ocr_clip(tmp_path / "small.mp4", text_px=9), step_s=2.0)["text.ocr_small_share"]
    assert big["value"] is not None and small["value"] is not None and small["value"] > big["value"] + 0.4


def test_missing_media_is_not_measured_never_passed(tmp_path):
    report = craft.measure(text_layers=cf.text_layers(tmp_path / "t.json"))
    nm = {k for k, v in report["measurements"].items() if v["value"] is None}
    assert {"video.colourfulness.median", "audio.pause_share", "audio.vo_over_music.p10"} <= nm
    assert all(report["measurements"][k]["not_measured"] for k in nm)
    import jsonschema
    jsonschema.validate(report, json.loads((PKG / "schemas/measurements.schema.json").read_text()))


@needs_ffmpeg
def test_cli_measure_and_benchmark(films, tmp_path):
    root, sets, _ = films
    run = lambda *a: subprocess.run([sys.executable, "-m", "vpg", *a], cwd=PKG, capture_output=True, text=True)  # noqa: E731
    out = tmp_path / "degraded.json"
    r = run("measure", *sum(([f"--{k.replace('_', '-')}", str(v)] for k, v in sets["degraded"].items()), []), "--out", str(out))
    assert r.returncode == 0, r.stderr
    reg = root / "cli-registry.json"
    assert run("benchmark", "init", str(reg)).returncode == 0
    for s in range(3):
        files = sum(([f"--{k.replace('_', '-')}", str(v)] for k, v in sets[f"good{s}"].items()), [])
        r = run("benchmark", "add", str(reg), "--id", f"REF-{s}", "--kind", "reference", "--by", "agent:test", *files)
        assert r.returncode == 0, r.stderr
    assert run("benchmark", "verify", str(reg)).returncode == 0
    r = run("benchmark", "check", str(reg), "--measurements", str(out))
    assert r.returncode == 1 and '"below"' in r.stdout and '"above"' in r.stdout
