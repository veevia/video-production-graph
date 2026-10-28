"""Iteration 5.1: one structured finding state, superseded release gates, gate evidence for versionless gates,
the versions table fields and the frame fields the client needs to capture stills."""
import json
import os

import pytest

from glassbox.reviews import finding_state
from synthetic import update_state, write_json, write_reviews_only
from test_iteration5 import call, get_json, live  # noqa: F401  (the live server fixture)


@pytest.mark.parametrize("status, state", [
    ("resolved", "resolved"), ("Resolved in v26", "resolved"), ("fixed in v3", "resolved"), ("closed", "resolved"),
    ("", "open"), (None, "open"), ("open", "open"), ("new in v26", "open"), ("partly resolved from TQA-1", "open"),
    ("persisting (TQA-v27-05(b); neither fixed nor declined in the brief)", "open"),
    ("carried from v4 (not resolved)", "open"), ("persisting class (TQA-v26-09, partly fixed)", "open"),
])
def test_finding_state_reads_only_the_first_word(status, state):
    assert finding_state(status) == state


def test_release_gate_of_an_older_version_names_the_version_that_superseded_it(live):
    older = get_json(live, f"/api/projects/{live['pid']}/candidates/v2")["release_gate"]
    newest = get_json(live, f"/api/projects/{live['pid']}/candidates/v10")["release_gate"]
    assert older["superseded_by"] == "v10" and older["state"] == "open"
    assert newest["superseded_by"] is None
    gates = get_json(live, f"/api/projects/{live['pid']}/gates?version=v2")
    release = next(g for g in gates["human"] if g["release"])
    assert release["superseded_by"] == "v10"


def test_open_findings_use_the_structured_state_everywhere(live):
    job = live["job"]
    write_reviews_only(job, "v11")
    write_json(job / "candidates" / "v11" / "reviews" / "technical-qa.json", {
        "reviewer": "agent:technical-qa", "version": "v11", "verdict": "pass", "scores": [],
        "defects": [
            {"id": "A", "severity": "P4", "what": "Carried", "status": "persisting (neither fixed nor declined in the brief)"},
            {"id": "B", "severity": "P4", "what": "Gone", "status": "resolved"},
            {"id": "C", "severity": "P3", "what": "Half done", "status": "partly fixed"},
        ]})
    detail = get_json(live, f"/api/projects/{live['pid']}")
    v11 = next(c for c in detail["candidates"] if c["version"] == "v11")
    assert v11["open_levels"] == {"P4": 1, "P3": 1} and v11["notes_count"] == 0
    states = {f["id"]: f["state"] for f in get_json(live, f"/api/projects/{live['pid']}/candidates/v11")["findings"] if f.get("id")}
    assert states == {"A": "open", "B": "resolved", "C": "open"}
    needs = get_json(live, f"/api/needs-you?pid={live['pid']}")
    titles = " ".join(item.get("title") or "" for item in needs if item.get("version") == "v11")
    assert "Gone" not in titles


def test_versions_table_counts_notes(live):
    status, _, _ = call(live, "POST", "/api/reactions", {"pid": live["pid"], "kind": "note", "text": "A note",
                                                          "target": {"type": "version", "id": "v10"}},
                        {"Origin": f"http://127.0.0.1:{live['port']}"})
    assert status == 201
    detail = get_json(live, f"/api/projects/{live['pid']}")
    v10 = next(c for c in detail["candidates"] if c["version"] == "v10")
    assert v10["notes_count"] == 1 and set(v10["open_levels"]) <= {"P1", "P2", "P3", "P4", "P?"}


def test_gate_evidence_reads_the_node_artifacts_inside_the_project(live):
    job = live["job"]
    brief = job / "work" / "n03_objective_audience" / "a1" / "brief.json"
    write_json(brief, {"objective": "Say one thing well.", "audience": ["Buyers", "Builders"], "runtime_target_s": 60.0,
                       "runtime_max_s": 75.0, "success_criteria": ["Clear takeaway"]})
    (job / "work" / "n03_objective_audience" / "a1" / "notes.md").write_text("# Notes\n\nKeep it short.\n", encoding="utf-8")

    def add(state):
        state["artifacts"] = {
            "brief@n03_objective_audience#a1": {"key": "brief", "path": "work/n03_objective_audience/a1/brief.json",
                                                "node": "n03_objective_audience", "status": "current", "sha256": "b1"},
            "notes@n03_objective_audience#a1": {"key": "notes", "path": "work/n03_objective_audience/a1/notes.md",
                                                "node": "n03_objective_audience", "status": "current", "sha256": "b2"},
            "escape@n03_objective_audience#a1": {"key": "escape", "path": "../../outside.json",
                                                 "node": "n03_objective_audience", "status": "current", "sha256": "b3"},
            "other@n13_motion_scenes#a1": {"key": "other", "path": "work/n13/a1/x.json", "node": "n13_motion_scenes",
                                           "status": "current", "sha256": "b4"},
        }
    update_state(job, add)
    evidence = get_json(live, f"/api/projects/{live['pid']}/gates/n03_objective_audience")
    assert evidence["gate"]["state"] == "waiting" and evidence["reached"] is True and evidence["num"] == 3
    by_key = {a["key"]: a for a in evidence["artifacts"]}
    assert set(by_key) == {"brief", "notes"}
    fields = {f["key"]: f["value"] for f in by_key["brief"]["fields"]}
    assert fields["objective"] == "Say one thing well." and fields["audience"] == ["Buyers", "Builders"]
    assert fields["runtime_max_s"] == 75.0
    assert by_key["notes"]["fields"] is None and "Keep it short." in by_key["notes"]["text"]
    assert set(evidence["contract"]) == {"purpose", "outputs", "completion", "human_review"}
    status, _, _ = call(live, "GET", f"/api/projects/{live['pid']}/gates/n13_motion_scenes")
    assert status == 404


def test_film_frames_carry_media_size_and_the_release_version(live):
    films = get_json(live, "/api/films")
    row = next(r for r in films["rows"] if r["pid"] == live["pid"])
    assert row["release_version"] == "v10"
    v10 = next(f for f in row["versions"] if f["label"] == "v10")
    master = live["job"] / "candidates" / "v10" / "n14_video_assembly" / "master.mp4"
    assert v10["media"] == f"/media/{live['pid']}/v10/n14_video_assembly/master.mp4"
    assert v10["size"] == os.path.getsize(master)
    v1 = next(f for f in row["versions"] if f["label"] == "v1")
    assert v1["media"] is None and v1["size"] is None
    gate = next((d for d in films["decisions"] if d["kind"] == "gate"), None)
    if gate:
        assert gate["node_name"]
    assert json.dumps(films)
