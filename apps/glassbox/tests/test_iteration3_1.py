"""Iteration 3.1: per-version runtime targets, measurement basis changes and in-flight detection that ignores OS
metadata. Synthetic fixtures only."""
import json
import os
import time

import pytest

from glassbox import fsio, polarity
from glassbox.adapters import JobReader
from synthetic import age_tree, make_job, write_full_candidate, write_json
from test_integration import get_json, live  # noqa: F401  (fixture reuse)

BASIS = "BS.1770: the voice in both channels against the music's summed channel powers (synthetic rev 1)"


def set_script(job, version, target):
    write_json(job / "candidates" / version / "plan" / "script.json", {"units": [], "runtime_target_s": target})


def patch_mix(job, version, change):
    path = job / "candidates" / version / "n14_video_assembly" / "mix-report.json"
    doc = json.loads(path.read_text())
    change(doc)
    write_json(path, doc)


@pytest.fixture
def scripted(job):
    """The demo job plus v21, v22 and v23 with their own script targets (88, 60 and 90 s), the project's gate overrides
    (88 s target, 80-95 s) and mix reports where only v23 states its voice-over-music basis."""
    for version, target, seconds, p10 in (("v21", 88.0, 89.9, 17.28), ("v22", 60.0, 61.0, 12.6), ("v23", 90.0, 92.0, 13.95)):
        write_full_candidate(job, version, rework=False)
        set_script(job, version, target)
        patch_mix(job, version, lambda doc, seconds=seconds, p10=p10: (
            doc["timeline"].update(film_seconds=seconds),
            doc["voice_over_music_lu"].update(p10=p10),
            doc.setdefault("mix_measures", {}).update(voice_over_music_lu_momentary={"median": 20.0, "p10": p10}),
        ))
    patch_mix(job, "v23", lambda doc: doc["mix_measures"]["voice_over_music_lu_momentary"].update(basis=BASIS))
    write_json(job / "pipeline" / "gate-overrides.json", {"note": "synthetic override", "runtime_target_s": 88.0,
                                                          "runtime_min_s": 80.0, "runtime_max_s": 95.0})
    age_tree(job)
    return job


def test_script_windows_and_bands():
    overrides = {"runtime_target_s": 88.0, "runtime_min_s": 80.0, "runtime_max_s": 95.0}
    assert polarity.script_window(90.0, overrides)[:2] == (80.0, 95.0)
    assert polarity.script_window(88.0, overrides)[:2] == (80.0, 95.0)
    assert polarity.script_window(60.0, overrides) == (54.0, 66.0, "+/-10 % of the script target")
    assert polarity.script_window(85.0, {"runtime_max_s": 95.0})[:2] == (76.5, 95.0)
    assert polarity.script_window(70.0, {"runtime_max_s": 95.0})[:2] == (63.0, 77.0)
    assert polarity.script_window(0, overrides) is None and polarity.script_window(True, overrides) is None
    assert polarity.same_window({"low": 80.0, "high": 95.0}, {"low": 80.0, "high": 95.0})
    assert not polarity.same_window({"low": 54.0, "high": 66.0}, {"low": 80.0, "high": 95.0})
    assert polarity.duration_between(61.0, 92.0, {"low": 54.0, "high": 66.0, "target": 60.0, "label": "54-66 s"},
                                     {"low": 80.0, "high": 95.0, "target": 90.0, "label": "80-95 s"}) == {
        "polarity": "n/a", "reason": "targets differ (60 s vs 90 s)"}
    assert polarity.duration_between(61.0, 92.0, None, {"low": 80.0, "high": 95.0, "target": 90.0, "label": "80-95 s"},
                                     "v2", "v3")["reason"] == "no runtime target for v2"
    assert polarity.duration_between(61.0, 92.0, None, None)["polarity"] == "neutral"


def test_runtime_target_resolution_order(scripted, project, graph, store):
    reader = JobReader(project, graph, store)
    v21 = reader.runtime_target("v21")
    assert (v21["low"], v21["high"], v21["target"], v21["label"], v21["source"]) == (80.0, 95.0, 88.0, "80-95 s", "v21 script target 88 s")
    assert "gate-overrides.json" in v21["detail"]
    v22 = reader.runtime_target("v22")
    assert (v22["label"], v22["source"]) == ("54-66 s", "v22 script target 60 s") and "+/-10 %" in v22["detail"]
    write_json(scripted / "plan" / "script.json", {"runtime_target_s": 75.0})
    assert JobReader(project, graph, store).runtime_target("v10")["source"] == "project script target 75 s"
    assert JobReader(project, graph, store).runtime_target("v10")["label"] == "67.5-82.5 s"
    (scripted / "plan" / "script.json").unlink()
    (scripted / "controls").mkdir()
    (scripted / "controls" / "evaluation.md").write_text("---\nthresholds:\n  runtime: {min_s: 50, max_s: 70}\n---\n")
    window = JobReader(project, graph, store).runtime_target("v10")
    assert (window["label"], window["source"]) == ("50-70 s", "controls/evaluation.md thresholds")
    (scripted / "controls" / "evaluation.md").unlink()
    window = JobReader(project, graph, store).runtime_target("v10")
    assert (window["label"], window["source"]) == ("54-66 s", "vpg.project.yaml constraints")
    (scripted / "vpg.project.yaml").write_text('id: demo-film\nname: "Demo"\n')
    assert JobReader(project, graph, store).runtime_target("v10") is None


def test_duration_is_judged_against_each_version_target(scripted, project, graph, store):
    reader = JobReader(project, graph, store)
    same = {c["key"]: c for c in reader.compare("v21", "v23")["changes"]}["duration_s"]
    assert same["rule"] == "within 80-95 s (v23 script target 90 s; v21 script target 88 s)"
    assert (same["polarity"], same["reason"]) == ("neutral", "both within 80-95 s")
    assert "gate-overrides.json" in same["rule_detail"]
    data = reader.compare("v22", "v23")
    differ = {c["key"]: c for c in data["changes"]}["duration_s"]
    assert (differ["polarity"], differ["reason"]) == ("n/a", "targets differ (60 s vs 90 s)")
    assert differ["rule"] == "A within 54-66 s (v22 script target 60 s); B within 80-95 s (v23 script target 90 s)"
    assert data["targets"]["runtime"]["a"]["target"] == 60.0 and data["targets"]["runtime"]["b"]["target"] == 90.0
    assert data["change_summary"]["n/a"] >= 1 and "not comparable on" in data["change_summary"]["text"]
    same_band = {c["key"]: c for c in reader.compare("v10", "v22")["changes"]}["duration_s"]
    assert same_band["polarity"] != "n/a" and same_band["rule"] == "within 54-66 s (v22 script target 60 s; vpg.project.yaml constraints)"


def test_basis_difference_marks_the_metric_uncertain(scripted, project, graph, store):
    data = JobReader(project, graph, store).compare("v21", "v23")
    vom = {c["key"]: c for c in data["changes"]}["voice_over_music_p10"]
    assert (vom["a"], vom["b"], vom["polarity"]) == (17.28, 13.95, "worse")
    assert vom["uncertain"] is True and vom["basis"]["a"] is None
    assert vom["basis"]["b"] == {"text": BASIS, "path": "mix_measures.voice_over_music_lu_momentary"}
    assert data["change_summary"]["uncertain"] == 1 and data["change_summary"]["worse"] == 0
    assert data["change_summary"]["text"] == "B is worse on 0, better on 0, unchanged on 3, neutral on 2, uncertain on 1"
    assert data["metric_bases"]["voice_over_music_median"]["differs"] is True
    assert data["metric_bases"]["integrated_lufs"] == {"a": None, "b": None, "differs": False}
    assert data["basis_changes"] == [{"path": "mix_measures.voice_over_music_lu_momentary", "a": None, "b": BASIS}]
    patch_mix(scripted, "v21", lambda doc: doc["mix_measures"]["voice_over_music_lu_momentary"].update(basis=BASIS))
    patch_mix(scripted, "v23", lambda doc: doc["master"].update(basis="EBU R128, gated"))
    data = JobReader(project, graph, store).compare("v21", "v23")
    changes = {c["key"]: c for c in data["changes"]}
    assert "basis" not in changes["voice_over_music_p10"] and changes["voice_over_music_p10"]["polarity"] == "worse"
    assert changes["integrated_lufs"]["basis"]["b"]["path"] == "master" and changes["integrated_lufs"]["uncertain"] is True
    assert data["change_summary"]["worse"] == 1 and data["change_summary"]["uncertain"] == 1


def test_compare_api_carries_bases_and_runtime_targets(live):  # noqa: F811
    job, pid = live["job"], live["pid"]
    set_script(job, "v10", 90.0)
    patch_mix(job, "v10", lambda doc: doc["voice_over_music_lu"].update(basis="synthetic basis"))
    data = get_json(live, f"/api/projects/{pid}/compare?a=v2&b=v10")
    assert data["targets"]["runtime"]["b"]["source"] == "v10 script target 90 s"
    assert data["metric_bases"]["voice_over_music_p10"]["b"] == {"text": "synthetic basis", "path": "voice_over_music_lu"}
    assert {c["key"]: c for c in data["changes"]}["duration_s"]["reason"] == "targets differ (60 s vs 90 s)"


def test_in_flight_ignores_dotfiles_and_os_metadata(job, project, graph, store):
    assert not JobReader(project, graph, store).live_versions()
    base = job / "candidates" / "v1"
    for path in (base / ".DS_Store", base / "._voice.wav", base / "n12_voice_audio" / ".DS_Store",
                 base / "n12_voice_audio" / "Thumbs.db", base / "n12_voice_audio" / "take.swp", base / "n12_voice_audio" / "notes~"):
        path.write_bytes(b"metadata")
    (base / "scratch").mkdir()
    (base / "scratch" / ".DS_Store").write_bytes(b"metadata")
    reader = JobReader(project, graph, store)
    assert reader.live_versions() == [] and reader.updated_at("v1") < time.time() - 3600
    (base / "n12_voice_audio" / "voice-take2.wav").write_bytes(b"RIFF")
    assert JobReader(project, graph, store).live_versions() == ["v1"]
    assert all(fsio.is_os_metadata(name) for name in (".DS_Store", "._x", "Thumbs.db", "a.swp", "b~", ".hidden"))
    assert not any(fsio.is_os_metadata(name) for name in ("master.mp4", "notes.md", "swap.json"))


def test_future_metadata_never_counts(job, project, graph, store):
    base = job / "candidates" / "v1"
    (base / ".DS_Store").write_bytes(b"x")
    future = time.time() + 7200
    os.utime(base, (future, future))
    assert JobReader(project, graph, store).live_versions() == []


def test_folder_change_time_attribution(job, project, graph, store):
    base = job / "candidates" / "v1"
    voice = base / "n12_voice_audio" / "voice.wav"
    (base / "n12_voice_audio" / ".DS_Store").write_bytes(b"x")
    assert fsio.folder_change_time(str(base / "n12_voice_audio")) == voice.stat().st_mtime
    assert JobReader(project, graph, store).vmtime("v1") == voice.stat().st_mtime
    copied = base / "n13_motion_scenes"
    copied.mkdir()
    (copied / "scenes.json").write_text("{}")
    old = time.time() - 7200
    os.utime(copied / "scenes.json", (old, old))
    assert fsio.folder_change_time(str(copied)) > time.time() - 60
    assert JobReader(project, graph, store).live_versions() == ["v1"]
