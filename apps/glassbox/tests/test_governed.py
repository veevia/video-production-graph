"""Governed-run jobs (.vpg/state.json without candidates/), decision eligibility and neutral wording."""
import hashlib
import json
import os

import pytest

from glassbox import media
from glassbox.adapters import CAVEAT, JobReader
from glassbox.discovery import Project, project_id
from glassbox.mapping import engine_words, governed_class
from glassbox.needs import needs_you
from glassbox.timecode import srt_to_vtt
from synthetic import MASTER_BYTES, SRT_TEXT, make_governed_job, write_reviews_only
from test_integration import call, get_json, live  # noqa: F401  (fixture reuse)


@pytest.fixture
def gov(tmp_path, graph, store):
    root = tmp_path / "roots"
    job = make_governed_job(root)
    project = Project(id=project_id(str(job)), path=str(job), root=str(root), rel_path=os.path.relpath(str(job), str(root)),
                      name="Governed film", slug="gov-film")
    return JobReader(project, graph, store)


def test_engine_status_words():
    assert governed_class("RUN_PASS") == "passed"
    assert governed_class("STEP_PASS") == "passed"
    assert engine_words("PLANNED") == "not reached"
    assert engine_words("WAITING_HUMAN") == "waiting for you"
    assert engine_words(None) == "no record"


def test_srt_to_vtt():
    vtt = srt_to_vtt("\ufeff" + SRT_TEXT)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:02.500" in vtt and "00:01:05.250 --> 00:01:07.000" in vtt
    assert "Second <i>line</i>" in vtt and "," not in vtt.split("\n")[3]


def test_future_times_are_not_live(gov):
    # The fixture's governed versions are dated 2030: a future time is not evidence of recent work.
    assert not any(gov.is_live(v) for v in gov.versions()) and not gov.live_versions()


def test_governed_versions_media_and_captions(gov):
    assert gov.versions() == ["v1", "v2", "v3"]
    assert [gov.version_source(v) for v in gov.versions()] == ["governed"] * 3
    assert gov.focus_version() == "v3" and not gov.has_master("v1") and gov.has_master("v2")
    detail = gov.candidate_detail("v3")
    assert detail["source"] == "governed" and detail["duration_s"] == 43.0
    assert detail["media"]["master"] == f"/media/{gov.pid}/_run/work/n14_video_assembly/a3/master.mp4"
    assert detail["media"]["vtt"] == f"/media/{gov.pid}/_run/work/n14_video_assembly/a3/captions.srt.vtt"
    assert detail["media"]["poster"] is None
    assert [c["text"] for c in detail["captions"]] == ["First line", "Second line"]
    assert [(k["label"], k["t"]) for k in detail["keyframes"]] == [("B01 002.50", 2.5), ("B02 010.00", 10.0)]
    assert gov.candidate_detail("v1")["media"]["master"] is None
    assert gov.candidate_summary("v3")["nodes_present"] == ["n12_voice_audio", "n14_video_assembly"]


def test_governed_reviews_defects_and_gates(gov):
    reviews = {r["node"]: r for r in gov.reviews("v3")}
    assert set(reviews) == {"n15_technical_qa", "n16_creative_critique"}
    tech = reviews["n15_technical_qa"]
    assert tech["verdict"] == "pass" and tech["scores"][0]["result"] == "pass" and tech["scores"][0]["kind"] == "MUST"
    critic = reviews["n16_creative_critique"]
    assert [d["id"] for d in critic["defects"]] == ["D001"] and critic["pending_human"] == ["Owner to judge the music"]
    defect = critic["defects"][0]
    assert (defect["level"], defect["t0"], defect["node"]) == ("P4", 4.2, "n07_script_development")
    assert [d["id"] for d in gov.run_defects("v1")] == ["D002"]
    assert [d["id"] for d in gov.run_defects("v2")] == ["D002", "D003"]
    assert [d["id"] for d in gov.run_defects("v3")] == ["D001", "D003"]
    assert gov.run_defects("v2")[1]["t0"] == 30.1
    gates_v3 = gov.run_gates("v3")
    assert [(g["name"], g["node"], g["pass"]) for g in gates_v3] == [("gate-v3-1", "n21_feedback_interpretation", True)]
    gate_v2 = gov.run_gates("v2")[0]
    assert gate_v2["pass"] is False and gate_v2["findings"] == [{"text": "D003"}] and gate_v2["messages"] == ["D003 blocks"]
    board = gov.gates_board("v3")
    assert [(a["name"], a["node"], a["governed"]) for a in board["automatic"]] == [("gate-v3-1", "n21_feedback_interpretation", True)]
    nodes = {n["id"]: n for n in gov.node_entries("v3")}
    assert nodes["n21_feedback_interpretation"]["status"] == "passed"
    assert nodes["n07_script_development"]["status"] == "warnings"
    assert nodes["n14_video_assembly"]["status"] == "failed"
    assert nodes["n22_targeted_regeneration"]["status"] == "not_run"
    assert "not reached" in nodes["n22_targeted_regeneration"]["status_reason"]


def test_approvals_show_the_actor_verbatim_and_count_in_status(gov):
    n03 = gov.human_gate_state("n03_objective_audience", "v3")
    assert n03["state"] == "engine_decided" and n03["actions"] == ["comment"]
    assert n03["engine_decision"]["actor"] == "Owner (on behalf of Owner; provisional, to be confirmed)"
    assert "provisional" in n03["label"]
    nodes = {n["id"]: n for n in gov.node_entries("v3")}
    assert nodes["n03_objective_audience"]["status"] == "passed" and "engine record" in nodes["n03_objective_audience"]["status_reason"]
    release_v3 = gov.human_gate_state("n23_final_verification", "v3")
    assert release_v3["state"] == "engine_decided" and release_v3["engine_decision"]["actor"] == "Owner"
    release_v2 = gov.human_gate_state("n23_final_verification", "v2")
    assert release_v2["state"] == "not_reached" and release_v2["actions"] == ["comment"]
    assert release_v2["label"] == "The governed run hasn't reached this gate for this version."
    n04 = gov.human_gate_state("n04_product_understanding", "v3")
    assert n04["state"] == "passed" and n04["actions"] == ["comment"]


def test_waiting_release_gate_offers_decisions(tmp_path, graph, store):
    root = tmp_path / "roots"
    job = make_governed_job(root, waiting_release=True)
    project = Project(id=project_id(str(job)), path=str(job), root=str(root), rel_path="x", name="G", slug="g")
    reader = JobReader(project, graph, store)
    current = reader.human_gate_state("n23_final_verification", "v3")
    assert current["state"] == "waiting" and current["actions"] == ["approve", "request_changes", "comment"]
    assert current["caveat"] == CAVEAT and current["compare_with"] == "v2"
    assert reader.human_gate_state("n23_final_verification", "v2")["actions"] == ["comment"]


def test_governed_media_whitelist(gov):
    allowed = gov.run_model().allowed_media()
    root = gov.project.path
    target, convert = media.resolve_run(root, ["work", "n14_video_assembly", "a3", "master.mp4"], allowed)
    assert target.endswith("master.mp4") and convert is False
    target, convert = media.resolve_run(root, ["work", "n14_video_assembly", "a3", "captions.srt.vtt"], allowed)
    assert target.endswith("captions.srt") and convert is True
    assert media.resolve_run(root, ["work", "n12_voice_audio", "a1", "voice.wav"], allowed)[0].endswith("voice.wav")
    assert media.resolve_run(root, ["work", "n24_packaging_delivery", "a1", "package", "01-master", "gov-master.mp4"], allowed)
    for bad in (["work", "n14_video_assembly", "a3", "secret.json"],
                ["work", "n24_packaging_delivery", "a1", "package", "04-voice-only", "gov-voice.wav"],
                ["work", "n14_video_assembly", "a1", "master.mp4"],
                ["work", "..", ".vpg", "state.json"],
                ["vpg.project.yaml"]):
        with pytest.raises(media.MediaError):
            media.resolve_run(root, bad, allowed)
    deliverables = {d["key"]: d["rel"] for d in gov.run_model().deliverables()}
    assert deliverables["01-master/gov-master.mp4"] == "work/n24_packaging_delivery/a1/package/01-master/gov-master.mp4"
    assert deliverables["09-missing/none.json"] is None


def test_candidate_wav_only_in_stem_folders(job):
    (job / "candidates" / "v10" / "n14_video_assembly" / "voice.wav").write_bytes(b"RIFF")
    (job / "candidates" / "v10" / "n13_motion_scenes" / "scratch.wav").write_bytes(b"RIFF")
    target, _ = media.resolve(str(job), "v10", ["n14_video_assembly", "voice.wav"])
    assert media.content_type(target) == "audio/wav"
    with pytest.raises(media.MediaError):
        media.resolve(str(job), "v10", ["n13_motion_scenes", "scratch.wav"])


def test_governed_job_over_http(tmp_path):
    from glassbox.web import make_server
    import threading
    from synthetic import write_engine
    root = tmp_path / "roots"
    job = make_governed_job(root)
    server = make_server([str(root)], str(write_engine(tmp_path)), str(tmp_path / "data"), port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ctx = {"port": server.app.port}
        pid = hashlib.sha1(os.path.abspath(str(job)).encode()).hexdigest()[:10]
        projects = get_json(ctx, "/api/projects")
        assert projects[0]["candidate_count"] == 3 and projects[0]["latest_version"] == "v3"
        assert projects[0]["versions"] == ["v1", "v2", "v3"]
        detail = get_json(ctx, f"/api/projects/{pid}")
        assert detail["governed_versions"] == ["v1", "v2", "v3"] and [c["source"] for c in detail["candidates"]] == ["governed"] * 3
        base = f"/media/{pid}/_run/work/n14_video_assembly/a3"
        status, headers, data = call(ctx, "GET", f"{base}/master.mp4", headers={"Range": "bytes=10-19"})
        assert status == 206 and data == MASTER_BYTES[10:20]
        status, headers, data = call(ctx, "GET", f"{base}/captions.srt.vtt")
        assert status == 200 and headers["Content-Type"].startswith("text/vtt") and data.startswith(b"WEBVTT")
        assert call(ctx, "GET", f"{base}/secret.json")[0] == 404
        assert call(ctx, "GET", f"/media/{pid}/_run/.vpg/state.json")[0] == 404
        assert call(ctx, "GET", f"/media/{pid}/_run/work/../vpg.project.yaml")[0] == 404
        status, headers, _ = call(ctx, "GET", f"/media/{pid}/_run/work/n12_voice_audio/a1/voice.wav")
        assert status == 200 and headers["Content-Type"] == "audio/wav"
        reviews = get_json(ctx, f"/api/projects/{pid}/candidates/v3")["reviews"]
        assert {r["node"]: r["verdict"] for r in reviews} == {"n15_technical_qa": "pass", "n16_creative_critique": "pass"}
        n03 = get_json(ctx, f"/api/projects/{pid}/nodes/n03_objective_audience?version=v3")
        assert "provisional" in n03["node"]["gate_state"]["engine_decision"]["actor"]
        board = get_json(ctx, f"/api/projects/{pid}/gates?version=v3")
        assert [a["name"] for a in board["automatic"]] == ["gate-v3-1"]
        compare = get_json(ctx, f"/api/projects/{pid}/compare?a=v2&b=v3")
        assert compare["compare_with"] == "v2" and compare["a"]["source"] == compare["b"]["source"] == "governed"
        assert compare["a"]["findings_count"] == 2 and compare["a"]["open_by_level"] == {"P1": 0, "P2": 0, "P3": 1, "P4": 0, "P?": 0}
        assert compare["b"]["open_by_level"]["P4"] == 1 and compare["metrics_delta"]["duration_s"] == 1.0
        body = {"pid": pid, "kind": "approve", "target": {"type": "gate", "id": "n03_objective_audience"}}
        status, _, data = call(ctx, "POST", "/api/reactions", body, {"Content-Type": "application/json"})
        assert status == 409 and b"not waiting" in data
        body = {"pid": pid, "kind": "note", "text": "Looks right", "target": {"type": "gate", "id": "n03_objective_audience"}}
        assert call(ctx, "POST", "/api/reactions", body, {"Content-Type": "application/json"})[0] == 201
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_decisions_only_where_the_gate_waits(live):  # noqa: F811
    pid = live["pid"]
    json_type = {"Content-Type": "application/json"}
    status, _, data = call(live, "POST", "/api/reactions", {"pid": pid, "kind": "approve", "target": {"type": "gate", "id": "n04_product_understanding"}}, json_type)
    assert status == 409
    gate = get_json(live, f"/api/projects/{pid}/nodes/n04_product_understanding?version=v10")["node"]["gate_state"]
    assert gate["actions"] == ["comment"] and gate["label"] == "The governed run hasn't reached this gate."
    release = get_json(live, f"/api/projects/{pid}/candidates/v10")["release_gate"]
    assert release["state"] == "open" and release["actions"] == ["approve", "request_changes", "comment"]
    assert call(live, "POST", "/api/reactions", {"pid": pid, "kind": "request_changes", "version": "v10", "text": "Trim"}, json_type)[0] == 201
    release = get_json(live, f"/api/projects/{pid}/candidates/v10")["release_gate"]
    # Since 5.3 a recorded decision on the latest fully reviewed version can be replaced by a newer one; it stays a
    # note for the producer, never an engine write.
    assert release["state"] == "recorded" and release["redecide"] and "approve" in release["actions"]
    assert call(live, "POST", "/api/reactions", {"pid": pid, "kind": "approve", "version": "v10"}, json_type)[0] == 201
    waiting = get_json(live, f"/api/projects/{pid}/nodes/n03_objective_audience")["node"]["gate_state"]
    assert waiting["state"] == "waiting" and "approve" in waiting["actions"]
    assert call(live, "POST", "/api/reactions", {"pid": pid, "kind": "approve", "target": {"type": "gate", "id": "n03_objective_audience"}}, json_type)[0] == 201


def test_neutral_release_wording_and_compare_with(job, project, graph, store):
    write_reviews_only(job, "v11", "pass")
    (job / "candidates" / "v11" / "n14_video_assembly").mkdir(parents=True)
    (job / "candidates" / "v11" / "n14_video_assembly" / "master.mp4").write_bytes(b"x")
    reader = JobReader(project, graph, store)
    items = needs_you([reader])
    release = next(i for i in items if i["kind"] == "human_gate" and i.get("version") == "v11")
    assert release["title"] == "Decide release for v11" and release["detail"] == "Gates passed, your decision."
    assert release["caveat"] == CAVEAT
    assert release["secondary"] == {"label": "Compare with v10", "link": f"#/p/{project.id}/compare?a=v10&b=v11"}
    state = reader.human_gate_state("n23_final_verification", "v11")
    assert state["label"] == "Gates passed, your decision." and state["caveat"] == CAVEAT
    blob = json.dumps([items, reader.candidate_detail("v11"), reader.candidate_detail("v10"), reader.gates_board("v11"),
                       reader.node_entries("v11")])
    assert "release-ready" not in blob.lower() and "Release v" not in blob
    base = {"actor": "owner", "pid": project.id, "project_path": project.path, "tags": [], "text": "", "t": None}
    store.append(dict(base, kind="prefer", a="v2", b="v10", choice="a", version=None, target=None))
    assert JobReader(project, graph, store).compare_with("v11") == "v2"
    store.append(dict(base, kind="approve", version="v10", target={"type": "version", "id": "v10", "version": "v10"}))
    assert JobReader(project, graph, store).compare_with("v11") == "v10"
    assert JobReader(project, graph, store).compare_with("v1") is None
