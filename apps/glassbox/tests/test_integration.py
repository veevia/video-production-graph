"""Integration: run the real server on an ephemeral port against a synthetic job."""
import hashlib
import http.client
import json
import os
import threading

import pytest

from glassbox.web import make_server
from synthetic import MASTER_BYTES, make_job, write_engine

KIND_ORDER = ["human_gate", "open_defect", "rework_verdict", "unreviewed", "in_flight"]


@pytest.fixture
def live(tmp_path):
    root = tmp_path / "roots"
    job = make_job(root)
    engine = write_engine(tmp_path)
    data_dir = tmp_path / "data"
    server = make_server([str(root)], str(engine), str(data_dir), port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"port": server.app.port, "job": job, "data": data_dir,
           "pid": hashlib.sha1(os.path.abspath(str(job)).encode()).hexdigest()[:10]}
    server.shutdown()
    server.server_close()
    thread.join(5)


def call(live, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", live["port"], timeout=10)
    sent = {"Host": f"127.0.0.1:{live['port']}"}
    sent.update(headers or {})
    payload = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    data = response.read()
    status, received = response.status, response.headers
    connection.close()
    return status, received, data


def get_json(live, path):
    status, _, data = call(live, "GET", path)
    assert status == 200, data
    return json.loads(data)


def test_projects_and_candidate_detail(live):
    projects = get_json(live, "/api/projects")
    assert len(projects) == 1
    project = projects[0]
    assert project["id"] == live["pid"] and project["name"] == "Demo film \u2014 synthetic"
    assert project["candidate_count"] == 3 and project["latest_version"] == "v10"
    assert project["run"]["status"] == "WAITING_HUMAN" and project["run"]["current_version"] == 0
    detail = get_json(live, f"/api/projects/{live['pid']}")
    assert [c["version"] for c in detail["candidates"]] == ["v10", "v2", "v1"]
    assert len(detail["readme_head"].splitlines()) == 40 and len(detail["graph"]) == 25
    assert detail["run"]["ledger"]["count"] == 5 and detail["run"]["ledger"]["linked"] is True
    assert [e["seq"] for e in detail["run"]["ledger"]["last"]] == [5, 4, 3, 2, 1]
    latest = detail["candidates"][0]
    assert latest["duration_s"] == 80.5 and latest["has_master"] is True
    assert latest["nodes_present"] == ["n13_motion_scenes", "n14_video_assembly"]
    assert latest["qa"] == {"findings": 1, "blocking": 1, "gates": 6, "failing": 1}
    assert {r["key"]: r["verdict"] for r in latest["reviews"]}["creative-critic"] == "rework"
    candidate = get_json(live, f"/api/projects/{live['pid']}/candidates/v10")
    assert candidate["media"]["master"] == f"/media/{live['pid']}/v10/n14_video_assembly/master.mp4"
    assert candidate["media"]["vtt"].endswith("/captions.en.vtt")
    assert candidate["metrics"]["bpm"] == 110.0 and candidate["metrics"]["voice_over_music_median"] == 16.5
    assert [c["text"] for c in candidate["captions"]] == ["Hello there & welcome", "Second line\nwraps here"]
    assert [k["t"] for k in candidate["keyframes"]] == [1.0, 3.0]
    assert [n["name"] for n in candidate["notes"]] == ["brief.md", "STATUS.md"]
    assert candidate["findings"][0]["level"] == "P3"
    assert candidate["release_gate"]["state"] == "open" and candidate["release_gate"]["gates_passed"] is False
    assert candidate["release_gate"]["label"].startswith("Gates not passed yet")
    assert candidate["compare_with"] == "v2" and candidate["source"] == "candidate"
    creative = next(r for r in candidate["reviews"] if r["key"] == "creative-critic")
    assert creative["defects"][0]["t0"] == 31.71 and creative["carried"][0]["id"] == "CRE-OLD"
    status, _, _ = call(live, "GET", f"/api/projects/{live['pid']}/candidates/v404")
    assert status == 404


def test_range_requests_and_media_guards(live):
    url = f"/media/{live['pid']}/v10/n14_video_assembly/master.mp4"
    status, headers, data = call(live, "GET", url, headers={"Range": "bytes=100-199"})
    assert status == 206 and data == MASTER_BYTES[100:200]
    assert headers["Content-Range"] == f"bytes 100-199/{len(MASTER_BYTES)}" and headers["Content-Length"] == "100"
    assert headers["Accept-Ranges"] == "bytes" and headers["Content-Type"] == "video/mp4"
    assert headers["Cache-Control"] == "no-store"
    status, _, data = call(live, "GET", url, headers={"Range": "bytes=-10"})
    assert status == 206 and data == MASTER_BYTES[-10:]
    status, headers, _ = call(live, "GET", url, headers={"Range": "bytes=20000-"})
    assert status == 416 and headers["Content-Range"] == f"bytes */{len(MASTER_BYTES)}"
    status, _, data = call(live, "GET", url)
    assert status == 200 and data == MASTER_BYTES
    status, headers, data = call(live, "HEAD", url)
    assert status == 200 and data == b"" and headers["Content-Length"] == str(len(MASTER_BYTES))
    for bad in (
        f"/media/{live['pid']}/v10/..%2F..%2Fvpg.project.yaml",
        f"/media/{live['pid']}/v10/../../vpg.project.yaml",
        f"/media/{live['pid']}/v10/n14_video_assembly/missing.mp4",
        f"/media/{live['pid']}/..%2Fv10/n14_video_assembly/master.mp4",
        "/media/0000000000/v10/n14_video_assembly/master.mp4",
    ):
        status, _, _ = call(live, "GET", bad)
        assert status == 404, bad


def test_reaction_post_rules_and_round_trip(live):
    pid = live["pid"]
    body = {"pid": pid, "kind": "note", "version": "v10", "text": "Hold the title longer", "t": 3.5, "tags": ["look"]}
    json_type = {"Content-Type": "application/json"}
    status, _, _ = call(live, "POST", "/api/reactions", body, {**json_type, "Origin": "http://evil.example"})
    assert status == 403
    assert not (live["data"] / "reactions.jsonl").exists()
    status, _, _ = call(live, "POST", "/api/reactions", body, {**json_type, "Origin": "null"})
    assert status == 403
    status, _, _ = call(live, "POST", "/api/reactions", body, {**json_type, "Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, _, _ = call(live, "POST", "/api/reactions", body, {"Content-Type": "text/plain"})
    assert status == 415
    status, _, _ = call(live, "POST", "/api/reactions", b"x" * (64 * 1024 + 1), json_type)
    assert status == 413
    status, _, data = call(live, "POST", "/api/reactions", b"{not json", json_type)
    assert status == 400
    status, _, data = call(live, "POST", "/api/reactions", dict(body, pid="0000000000"), json_type)
    assert status == 400 and b"unknown pid" in data
    status, _, data = call(live, "POST", "/api/reactions", dict(body, kind="cheer"), json_type)
    assert status == 400
    status, _, data = call(live, "POST", "/api/reactions", body, json_type)
    assert status == 201
    saved = json.loads(data)
    assert saved["actor"] == "owner" and saved["project_path"] == str(live["job"]) and saved["t"] == 3.5
    status, _, _ = call(live, "POST", "/api/reactions", body, {**json_type, "Origin": f"http://localhost:{live['port']}"})
    assert status == 201
    listed = get_json(live, f"/api/reactions?pid={pid}&version=v10")
    assert saved["id"] in [r["id"] for r in listed]
    ack = {"pid": pid, "kind": "ack", "ref": saved["id"], "text": "on it"}
    status, _, _ = call(live, "POST", "/api/reactions", ack, json_type)
    assert status == 400
    status, _, data = call(live, "POST", "/api/reactions", ack, {**json_type, "X-Actor": "agent"})
    assert status == 201 and json.loads(data)["actor"] == "agent"
    listed = {r["id"]: r for r in get_json(live, f"/api/reactions?pid={pid}")}
    assert listed[saved["id"]]["acks"][0]["text"] == "on it"
    status, headers, data = call(live, "GET", f"/api/reactions/export?pid={pid}&format=md&download=1")
    text = data.decode()
    assert status == 200 and headers["Content-Type"].startswith("text/markdown")
    assert "attachment" in headers["Content-Disposition"] and 'filename="vpg-reactions-demo-film-' in headers["Content-Disposition"]
    assert saved["id"] in text and "## Acknowledged" in text and "## Not yet acknowledged" in text
    assert len((live["data"] / "reactions.jsonl").read_text().splitlines()) == 3


def test_node_gate_compare_and_needs_endpoints(live):
    pid = live["pid"]
    nodes = get_json(live, f"/api/projects/{pid}/nodes?version=v10")
    assert len(nodes) == 25
    by_id = {node["id"]: node for node in nodes}
    assert by_id["n03_objective_audience"]["status"] == "waiting"
    assert by_id["n03_objective_audience"]["human_gate"]["required"] is True
    assert by_id["n13_motion_scenes"]["status"] == "failed" and by_id["n13_motion_scenes"]["evidence_counts"]["gates"] == 1
    gate = get_json(live, f"/api/projects/{pid}/nodes/n03_objective_audience?version=v10")
    assert gate["contract"]["purpose"] == "Purpose of node 3, for tests"
    assert gate["contract"]["edges"]["out"][0]["id"] == "E07"
    assert gate["governed"]["status"] == "WAITING_HUMAN" and gate["node"]["gate_state"]["state"] == "waiting"
    scenes = get_json(live, f"/api/projects/{pid}/nodes/n13_motion_scenes?version=v10")
    assert scenes["evidence"]["folder"] == "candidates/v10/n13_motion_scenes"
    assert [g["name"] for g in scenes["gates"]] == ["STILLS"] and scenes["defects"][0]["id"] == "CRE-D1"
    intake = get_json(live, f"/api/projects/{pid}/nodes/n01_context_intake")
    assert intake["governed"]["task_packet_text"].startswith("Synthetic task packet")
    assert call(live, "GET", f"/api/projects/{pid}/nodes/n99_nope")[0] == 404
    assert call(live, "GET", f"/api/projects/{pid}/nodes?version=v404")[0] == 404
    board = get_json(live, f"/api/projects/{pid}/gates?version=v10")
    assert [g["node"] for g in board["human"]] == ["n03_objective_audience", "n04_product_understanding", "n06_creative_direction",
                                                   "n09_visual_system", "n23_final_verification"]
    assert {g["name"]: g["pass"] for g in board["automatic"]}["STILLS"] is False
    assert len(board["reviewers"]) == 5
    compare = get_json(live, f"/api/projects/{pid}/compare?a=v2&b=v10")
    assert compare["a"]["version"] == "v2" and compare["verdicts"]["creative-critic"]["b"] == "rework"
    assert compare["metrics_delta"]["bpm"] is None
    assert call(live, "GET", f"/api/projects/{pid}/compare?a=v2&b=v404")[0] == 404
    needs = get_json(live, "/api/needs-you")
    assert needs[0]["kind"] == "human_gate" and needs[0]["link"] == f"#/p/{pid}/n/n03_objective_audience"
    kinds = [item["kind"] for item in needs]
    assert kinds == sorted(kinds, key=KIND_ORDER.index)
    meta = get_json(live, "/api/meta")
    assert meta["read_only"] is True and meta["graph_nodes"] == 25


def test_host_guard_static_and_errors(live):
    status, _, _ = call(live, "GET", "/api/projects", headers={"Host": f"evil.example:{live['port']}"})
    assert status == 421
    status, _, _ = call(live, "GET", "/api/projects", headers={"Host": "127.0.0.1:1"})
    assert status == 421
    status, _, _ = call(live, "POST", "/api/reactions", {"pid": live["pid"]}, {"Host": "attacker.test", "Content-Type": "application/json"})
    assert status == 421
    assert call(live, "GET", "/api/meta", headers={"Host": f"localhost:{live['port']}"})[0] == 200
    status, headers, data = call(live, "GET", "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert "default-src 'none'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"
    assert b'<script type="module" src="/static/app.js"></script>' in data
    status, headers, _ = call(live, "GET", "/static/app.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")
    for bad in ("/static/../server.py", "/static/", "/static/%2e%2e/server.py", "/nope", "/api/nope", "/api/projects/0000000000"):
        status, _, data = call(live, "GET", bad)
        assert status == 404, bad
        assert b"Traceback" not in data
    status, _, _ = call(live, "PUT", "/api/reactions", b"{}", {"Content-Type": "application/json"})
    assert status == 405
