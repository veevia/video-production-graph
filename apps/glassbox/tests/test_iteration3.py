"""Iteration 3: decisions vs FYI, finding titles, compare polarity, workflow selection, telemetry, direct runs and
provisional approvals. Synthetic fixtures only."""
import hashlib
import json
import os
import threading

import pytest

from glassbox import polarity
from glassbox.adapters import JobReader
from glassbox.discovery import Project, project_id
from glassbox.engine import Engine
from glassbox.needs import needs_you, split
from glassbox.reviews import finding_title, normalize_defect, normalize_governed_defect
from glassbox.web import make_server
from synthetic import (age_tree, append_ledger, make_governed_job, make_job, make_workflow_job, update_state, write_json,
                       write_quality_engine, write_reviews_only, write_telemetry)
from test_integration import call, get_json


def pid_of(path):
    return hashlib.sha1(os.path.abspath(str(path)).encode()).hexdigest()[:10]


def reader_for(job, root, graph, store, **kwargs):
    project = Project(id=project_id(str(job)), path=str(job), root=str(root), rel_path=os.path.relpath(str(job), str(root)),
                      name=job.name, slug=None)
    return JobReader(project, graph, store, **kwargs)


@pytest.fixture
def server(tmp_path):
    started = []

    def start(root, engine):
        srv = make_server([str(root)], str(engine), str(tmp_path / "data"), port=0, quiet=True)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        started.append((srv, thread))
        return {"port": srv.app.port, "app": srv.app}

    yield start
    for srv, thread in started:
        srv.shutdown()
        srv.server_close()
        thread.join(5)


def post(ctx, body, headers=None):
    return call(ctx, "POST", "/api/reactions", body, {"Content-Type": "application/json", **(headers or {})})


# ---------------------------------------------------------------------------- 3. empty titles
def test_finding_titles_fall_back_in_order():
    assert finding_title("  ", "Described", "Titled") == "Described"
    assert finding_title(None, None, "Titled") == "Titled"
    assert finding_title(None, None, None, "The cut lands early. It also jumps.") == "The cut lands early."
    assert finding_title(None, None, None, "0:41.2-0:43.0", "CRE-PACING") == "CRE-PACING"
    assert finding_title(None, None, None, None, None, "v26-02") == "Untitled finding (v26-02)"
    defect = normalize_defect({"id": "v26-02", "severity": "P3", "dimension": "T4-craft",
                               "evidence": "Pairwise, T4 is carried narrowly worse than v15. Second sentence."}, "takeaway-60", 0)
    assert defect["what"] is None and defect["title"] == "Pairwise, T4 is carried narrowly worse than v15."
    assert normalize_defect({"id": "X-1", "dimension": "CRE-X"}, "critic", 0)["title"] == "CRE-X"
    assert normalize_defect({"id": "X-2", "fix": "only a fix"}, "critic", 0)["title"] == "Untitled finding (X-2)"
    assert normalize_defect({"what": "   ", "severity": "P3"}, "critic", 3)["title"] == "Untitled finding (critic:#4)"
    governed = normalize_governed_defect({"id": "D9", "severity": "P2", "evidence": "0:30.1", "dimension": "TEC-LOUD"})
    assert governed["title"] == "TEC-LOUD" and governed["t0"] == 30.1


def test_needs_and_export_use_titles(server, tmp_path):
    root = tmp_path / "roots"
    job = make_job(root)
    reviews = job / "candidates" / "v10" / "reviews"
    doc = json.loads((reviews / "creative-critic.json").read_text())
    doc["defects"].append({"id": "CRE-D2", "severity": "P2", "at": "40.0 s", "evidence": "The logo pops in late. More."})
    doc["defects"].append({"id": "CRE-D3", "severity": "P2", "at": "41.0 s"})
    write_json(reviews / "creative-critic.json", doc)
    ctx = server(root, write_quality_engine(tmp_path))
    pid = pid_of(job)
    needs = get_json(ctx, f"/api/needs-you?pid={pid}")
    titles = [item["title"] for item in needs if item["kind"] == "open_defect"]
    assert "P2 CRE-D2: The logo pops in late." in titles and "P2 CRE-D3: Untitled finding (CRE-D3)" in titles
    assert not any(title.rstrip().endswith(":") for title in titles)
    findings = get_json(ctx, f"/api/projects/{pid}/candidates/v10")["findings"]
    assert all(finding["title"].strip() for finding in findings)
    body = {"pid": pid, "kind": "disagree", "target": {"type": "finding", "id": "creative-critic:CRE-D2", "version": "v10"}}
    assert post(ctx, body)[0] == 201
    status, _, data = call(ctx, "GET", f"/api/reactions/export?pid={pid}&format=md")
    assert status == 200 and "#### Finding creative-critic:CRE-D2: The logo pops in late." in data.decode()


# ---------------------------------------------------------------------------- 1. decisions vs FYI
def test_latest_reviewed_version_splits_decisions_and_fyi(job, project, graph, store):
    reviews = job / "candidates" / "v10" / "reviews"
    doc = json.loads((reviews / "technical-qa.json").read_text())
    doc["defects"].append({"id": "TQA-2", "severity": "P2", "at": "5.0 s", "what": "Clipping at the drop"})
    write_json(reviews / "technical-qa.json", doc)
    reader = JobReader(project, graph, store)
    assert reader.latest_reviewed_version() == "v10" and reader.current_version() == "v10"
    decisions, fyi = split(needs_you([reader]))
    assert [i["kind"] for i in decisions] == ["human_gate", "open_defect", "rework_verdict"]
    assert decisions[1]["level"] == "P2" and decisions[1]["title"] == "P2 TQA-2: Clipping at the drop"
    assert decisions[2]["title"] == "creative-critic: rework on v10" and decisions[2]["group"] == "decision"
    assert [(i["kind"], i["level"]) for i in fyi] == [("open_defect", "P3")]
    assert fyi[0]["reason"].startswith("P3") and fyi[0]["finding"] == "creative-critic:CRE-D1"


def test_newer_version_moves_the_reviewed_one_to_fyi(job, project, graph, store):
    v11 = job / "candidates" / "v11"
    (v11 / "n14_video_assembly").mkdir(parents=True)
    (v11 / "n14_video_assembly" / "master.mp4").write_bytes(b"x" * 10)
    write_json(v11 / "reviews" / "technical-qa.json", {"reviewer": "agent:technical-qa", "verdict": "pass", "defects": [
        {"id": "TQA-9", "severity": "P2", "what": "A new click", "at": "2.0 s"}]})
    live = JobReader(project, graph, store)
    assert live.live_versions() == ["v11"]
    in_flight = {(i["kind"], i.get("version")): i.get("reason") for i in split(needs_you([live]))[1]}
    assert in_flight[("in_flight", "v11")] == "in flight" and in_flight[("open_defect", "v11")] == "in flight"
    age_tree(job)
    reader = JobReader(project, graph, store)
    assert reader.latest_reviewed_version() == "v10" and reader.current_version() == "v11" and not reader.live_versions()
    decisions, fyi = split(needs_you([reader]))
    assert [i["kind"] for i in decisions] == ["human_gate"]
    reasons = {(i["kind"], i.get("version")): i.get("reason") for i in fyi}
    assert reasons[("open_defect", "v10")] == "superseded by v11"
    assert reasons[("rework_verdict", "v10")] == "superseded by v11"
    assert reasons[("unreviewed", "v11")] == "review set incomplete"
    assert reasons[("open_defect", "v11")] == "review set incomplete"
    unreviewed = next(i for i in fyi if i["kind"] == "unreviewed")
    assert unreviewed["title"] == "v11: 1 of 5 reviews in"


def test_rework_verdicts_group_into_one_card(job, project, graph, store):
    write_reviews_only(job, "v10", "rework")
    reader = JobReader(project, graph, store)
    decisions, _ = split(needs_you([reader]))
    rework = [i for i in decisions if i["kind"] == "rework_verdict"]
    assert len(rework) == 1 and rework[0]["title"] == "5 of 5 reviewers ask for rework on v10"
    assert rework[0]["reviewers"] == ["technical-qa", "creative-critic", "accessibility", "product-accuracy", "takeaway-60"]


def test_projects_count_decisions_and_fyi(server, tmp_path):
    root = tmp_path / "roots"
    job = make_job(root)
    ctx = server(root, write_quality_engine(tmp_path))
    projects = get_json(ctx, "/api/projects")
    needs = get_json(ctx, "/api/needs-you")
    decisions = [n for n in needs if n["group"] == "decision"]
    assert projects[0]["id"] == pid_of(job)
    assert projects[0]["needs_you"] == len(decisions) == 2
    assert projects[0]["needs_fyi"] == len(needs) - len(decisions) == 1
    assert {n["group"] for n in needs} == {"decision", "fyi"}


# ---------------------------------------------------------------------------- 4. compare polarity
def test_polarity_rules():
    target = {"lufs": -16.0, "tolerance": 1.0}
    assert polarity.loudness(-16.2, -15.9, target) == {"polarity": "neutral", "reason": "both within \u00b11 LU of -16 LUFS"}
    assert polarity.loudness(-18.5, -16.3, target)["polarity"] == "better"
    assert polarity.loudness(-16.3, -18.5, target)["polarity"] == "worse"
    assert polarity.loudness(-19.0, -18.0, target)["reason"] == "both outside \u00b11 LU of -16 LUFS; B is 1 LU closer"
    window = polarity.runtime_window(["Runtime: 60 s target, 55-65 s; -16 LUFS"])
    assert (window["low"], window["high"], window["target"], window["label"]) == (55.0, 65.0, 60.0, "55-65 s")
    assert polarity.duration(62.3, 68.0, window) == {"polarity": "worse", "reason": "B outside 55-65 s, A within"}
    assert polarity.duration(70.0, 64.0, window)["polarity"] == "better"
    assert polarity.duration(60.0, 61.0, window)["polarity"] == "neutral"
    assert polarity.duration(60.0, 61.0, None) == {"polarity": "neutral", "reason": "no runtime target known"}
    assert polarity.direction(17.28, 13.95, True, " LU") == {"polarity": "worse", "reason": "lower by 3.3 LU"}
    assert polarity.direction(40, 36, False, counts=True) == {"polarity": "better", "reason": "4 fewer"}
    assert polarity.direction(None, 3)["polarity"] == "n/a"
    assert polarity.defect_levels({"P2": 2, "P3": 9}, {"P2": 1, "P3": 12}) == {"polarity": "better", "reason": "fewer P2 (1 vs 2)"}
    assert polarity.defect_levels({"P3": 1}, {"P3": 1})["polarity"] == "unchanged"
    changes = [{"polarity": p} for p in ("worse", "worse", "better", "unchanged", "unchanged", "unchanged")]
    assert polarity.summarize(changes)["text"] == "B is worse on 2, better on 1, unchanged on 3"


def test_runtime_and_loudness_targets():
    assert polarity.parse_runtime("Format: four chapters, 100 s target / 105 s cap; the clip reused") == {
        "target": 100.0, "low": None, "high": None, "cap": 105.0}
    one_minute = polarity.parse_runtime("One minute (54-60 s, feedback of 2026-10-25 on v7; v3-v7 ran 80-90 s), 1920x1080")
    assert (one_minute["low"], one_minute["high"]) == (54.0, 60.0)
    assert polarity.runtime_window(["Runtime: 60 s target"])["label"] == "54-66 s"
    brief = polarity.runtime_window(["Story: no runtime here"], {"runtime_target_s": 100, "runtime_max_s": 105})
    assert brief["label"] == "at most 105 s" and brief["source"] == "brief (governed run)"
    both = polarity.runtime_window(["Runtime: 60 s target, 55-65 s"], {"runtime_target_s": 60, "runtime_max_s": 65})
    assert both["label"] == "55-65 s" and both["source"] == "brief (governed run) and vpg.project.yaml constraints"
    assert polarity.runtime_window(["Story: none"]) is None
    assert polarity.loudness_target(["1920x1080; -14 LUFS \u00b12 LU, true peak -1 dBTP"])["tolerance"] == 2.0
    from_thresholds = polarity.loudness_target([], {"audio": {"integrated_lufs": -14.0, "lufs_tolerance": 1.5}})
    assert (from_thresholds["lufs"], from_thresholds["tolerance"], from_thresholds["source"]) == (-14.0, 1.5, "controls/evaluation.md thresholds")
    assert polarity.loudness_target([])["lufs"] == -16.0 and polarity.loudness_target([])["source"] == "default"


def test_compare_reports_polarity_and_summary(job, project, graph, store, tmp_path):
    (job / "controls").mkdir()
    (job / "controls" / "evaluation.md").write_text("---\nthresholds:\n  audio: {integrated_lufs: -15.0, lufs_tolerance: 1.0}\n---\n# Eval\n")
    data = JobReader(project, graph, store).compare("v2", "v10")
    changes = {change["key"]: change for change in data["changes"]}
    assert list(changes) == ["duration_s", "integrated_lufs", "voice_over_music_p10", "open_defects", "verdicts_passing", "findings"]
    assert changes["integrated_lufs"]["rule"] == "closer to -15 LUFS is better" and changes["integrated_lufs"]["polarity"] == "n/a"
    assert changes["duration_s"]["rule"] == "within 54-66 s (vpg.project.yaml constraints)"
    assert changes["open_defects"]["polarity"] == "worse" and changes["open_defects"]["reason"].startswith("more P3")
    assert (changes["verdicts_passing"]["a"], changes["verdicts_passing"]["b"], changes["verdicts_passing"]["polarity"]) == (5, 4, "worse")
    assert changes["findings"]["polarity"] == "worse"
    assert data["change_summary"]["text"] == "B is worse on 3, better on 0, unchanged on 0, not comparable on 3"
    assert data["targets"]["loudness"]["source"] == "controls/evaluation.md thresholds"


# ---------------------------------------------------------------------------- 6. workflow selection
def test_engine_selects_the_project_workflow(tmp_path):
    engine = Engine(str(write_quality_engine(tmp_path)))
    project = tmp_path / "p"
    (project / "flows").mkdir(parents=True)
    (project / "flows" / "mine.yaml").write_text((tmp_path / "engine" / "graph" / "workflow-quality.yaml").read_text())
    quality = engine.select("quality", str(project))
    assert quality.found and quality.name == "quality" and len(quality.graph.nodes) == 27 and quality.note is None
    assert "n26_look_development" in quality.graph.human_gate_ids() and "n27_animatic" in quality.graph.human_gate_ids()
    assert len(quality.graph.human_gate_ids()) == 7 and quality.graph.by_id["n27_animatic"]["phase"] == "Design"
    default = engine.select("default", str(project))
    assert default.found and len(default.graph.nodes) == 25 and default.note is None
    missing = engine.select("cinema", str(project))
    assert not missing.found and len(missing.graph.nodes) == 25
    assert missing.note == "workflow 'cinema' not found in the engine; showing default"
    assert len(engine.select("flows/mine.yaml", str(project)).graph.nodes) == 27
    assert not engine.select("../../escape.yaml", str(project)).found
    assert engine.workflows() == ["default", "quality"]
    info = quality.info(engine.dir)
    assert info["file"] == os.path.join("graph", "workflow-quality.yaml") and info["nodes"] == 27 and info["human_gates"] == 7


def test_quality_workflow_over_http(server, tmp_path):
    root = tmp_path / "roots"
    quality = make_workflow_job(root, "a/quality", "quality", waiting_node="n26_look_development")
    missing = make_workflow_job(root, "b/missing", "cinema")
    ctx = server(root, write_quality_engine(tmp_path))
    qid, mid = pid_of(quality), pid_of(missing)
    detail = get_json(ctx, f"/api/projects/{qid}")
    assert detail["workflow"]["name"] == "quality" and detail["workflow"]["nodes"] == 27 and detail["workflow"]["note"] is None
    assert len(detail["graph"]) == 27 and detail["phases"] == ["Understand", "Design", "Build", "Learn"]
    nodes = {n["id"]: n for n in get_json(ctx, f"/api/projects/{qid}/nodes")}
    assert len(nodes) == 27 and nodes["n26_look_development"]["human_gate"]["decision"] == "Approve the look"
    assert nodes["n26_look_development"]["status"] == "waiting" and nodes["n27_animatic"]["phase"] == "Design"
    board = get_json(ctx, f"/api/projects/{qid}/gates")
    assert [g["node"] for g in board["human"]][-2:] == ["n26_look_development", "n27_animatic"]
    drawer = get_json(ctx, f"/api/projects/{qid}/nodes/n27_animatic")
    assert drawer["node"]["gate_state"]["state"] == "not_reached"
    needs = get_json(ctx, f"/api/needs-you?pid={qid}")
    assert needs[0]["title"] == "N26: Approve the look" and needs[0]["group"] == "decision"
    other = get_json(ctx, f"/api/projects/{mid}")
    assert other["workflow"]["note"] == "workflow 'cinema' not found in the engine; showing default" and len(other["graph"]) == 25
    assert call(ctx, "GET", f"/api/projects/{mid}/nodes/n26_look_development")[0] == 404
    note = {"kind": "note", "text": "Warmer frames", "target": {"type": "node", "id": "n26_look_development"}}
    assert post(ctx, dict(note, pid=qid))[0] == 201
    status, _, data = post(ctx, dict(note, pid=mid))
    assert status == 400 and b"not a node" in data
    approve = {"pid": qid, "kind": "approve", "target": {"type": "gate", "id": "n26_look_development"}}
    assert post(ctx, approve)[0] == 201
    assert get_json(ctx, "/api/meta")["workflows"] == ["default", "quality"]


# ---------------------------------------------------------------------------- 7. engine telemetry
def test_telemetry_is_read_tolerantly_per_node(tmp_path, graph, store):
    root = tmp_path / "roots"
    job = make_governed_job(root)
    write_telemetry(job)
    reader = reader_for(job, root, graph, store)
    summary = reader.telemetry_summary()
    assert summary["present"] and summary["ok"] is False and summary["missing_count"] == 2
    assert summary["missing"] == ["node n22_targeted_regeneration", "1 gate decision(s)"]
    assert summary["skipped_lines"] == 2 and summary["evaluations"] == 3 and summary["counts"]["nodes_run"] == 24
    assert summary["stale"] is False
    critic = reader.node_detail("n16_creative_critique", "v3")["telemetry"]
    assert critic["present"] and critic["total"] == 2 and critic["skipped_lines"] == 2
    review = next(e for e in critic["evaluations"] if e["name"] == "vpg.review.CRE-PACING")
    assert (review["score"], review["label"], review["evidence_tag"], review["attempt"]) == (3.0, "fail", "reported", 1)
    assert review["explanation"] == "CRE-PACING scored 3.0 against 4 (MUST)" and review["source"] == "state evaluation.reviews[2]"
    assert review["evidence"][0]["kind"] == "reviewer_note" and review["evidence"][0]["t0"] == 41.0
    completion = next(e for e in critic["evaluations"] if e["name"] == "vpg.node.completion")
    artifact, check, metric = completion["evidence"]
    assert artifact["url"] == f"/media/{reader.pid}/_run/work/n16_creative_critique/a3/keyframes/B01-002.50.png"
    assert check["text"] == "review has a verdict"
    assert (metric["metric"], metric["value"], metric["unit"], metric["t0"], metric["frame"]) == ("voice_over_music_p10", 16.4, "LU", 12.5, 375)
    assert critic["spans"][0]["duration_s"] == 279.0 and critic["spans"][0]["outcome"] == "pass"
    scenes = reader.node_detail("n13_motion_scenes", "v3")["telemetry"]
    assert scenes["evaluations"][0]["label"] == "ungoverned" and scenes["evaluations"][0]["evidence"][0]["url"] is None


def test_telemetry_absent_and_ledger_fallback(job, project, graph, store):
    reader = JobReader(project, graph, store)
    assert reader.telemetry_summary() is None
    assert reader.node_detail("n16_creative_critique", "v10")["telemetry"]["present"] is False
    append_ledger(job, [("evaluation", {"name": "vpg.gate", "gen_ai.evaluation.name": "vpg.gate", "gen_ai.evaluation.score.label": "rework",
                                        "node": "n21_feedback_interpretation", "attempt": 1, "evidence_tag": "observed",
                                        "evidence": [{"kind": "version", "version": 10}, {"kind": "defect", "id": "CRE-D1"}]},
                         "2030-01-01T00:00:06+00:00")])
    reader = JobReader(project, graph, store)
    assert reader.telemetry_summary() == {"present": False, "ledger_evaluations": 1}
    gate = reader.node_detail("n21_feedback_interpretation", "v10")["telemetry"]
    assert gate["source"] == "ledger" and gate["evaluations"][0]["label"] == "rework"
    assert gate["evaluations"][0]["evidence"][1]["fields"] == [["id", "CRE-D1"]]
    tail = reader.run()["ledger"]
    assert tail["linked"] is True and tail["last"][0]["type"] == "evaluation" and tail["last"][0]["detail"] == "vpg.gate: rework"


# ---------------------------------------------------------------------------- 8. direct runs and provisional approvals
def test_direct_runs_recorded_in_the_ledger(job, project, graph, store):
    nodes = {n["id"]: n for n in JobReader(project, graph, store).node_entries("v10")}
    assert "direct run (not in engine ledger)" in nodes["n13_motion_scenes"]["status_reason"]
    update_state(job, lambda state: state.setdefault("history", {}).update(direct_runs=[
        {"id": "DR-001", "node": "n13_motion_scenes", "actor": "agent:motion", "reason": "the gate waited",
         "at": "2030-01-01T00:00:05+00:00", "label": "v10", "outputs": {"film": {"path": "candidates/v10/x.mp4", "sha256": "ab"}}}]))
    append_ledger(job, [("direct_run", {"id": "DR-002", "node": "n14_video_assembly", "actor": "agent:edit", "label": "v2",
                                        "reason": "quick fix"}, "2030-01-01T00:00:07+00:00"),
                        ("provisional_expired", {"node": "n04_product_understanding", "actor": "agent:relay",
                                                 "expired_at": "2030-01-01T00:00:00+00:00", "evidence": "assumed"},
                         "2030-01-01T00:00:08+00:00")])
    reader = JobReader(project, graph, store)
    nodes = {n["id"]: n for n in reader.node_entries("v10")}
    scenes = nodes["n13_motion_scenes"]
    assert "direct run (recorded in ledger)" in scenes["status_reason"] and scenes["direct_recorded"] is True
    assert scenes["direct_runs"][0]["id"] == "DR-001" and scenes["provenance"] == "direct"
    assert "direct run (not in engine ledger)" in nodes["n14_video_assembly"]["status_reason"]
    assert {n["id"]: n for n in reader.node_entries("v2")}["n14_video_assembly"]["direct_recorded"] is True
    last = reader.run()["ledger"]["last"]
    assert [e["type"] for e in last[:2]] == ["provisional_expired", "direct_run"]
    assert last[0]["detail"].startswith("approval by agent:relay, expired ") and last[1]["detail"] == "DR-002 v2 by agent:edit"


def test_provisional_approvals_expire_into_waiting(job, project, graph, store):
    update_state(job, lambda state: state.setdefault("history", {}).update(approvals=[
        {"node": "n04_product_understanding", "decision": "approved", "actor": "agent:relay", "human": False,
         "at": "2000-12-30T09:00:00+00:00", "provisional": True, "evidence": "assumed", "expires_at": "2001-01-01T09:00:00+00:00"},
        {"node": "n06_creative_direction", "decision": "approved", "actor": "agent:relay", "human": False,
         "at": "2000-12-30T09:00:00+00:00", "provisional": True, "evidence": "assumed", "expires_at": "2999-01-01T00:00:00+00:00"},
        {"node": "n09_visual_system", "decision": "approved", "actor": "agent:relay", "human": False, "at": "2000-12-30T09:00:00+00:00",
         "provisional": True, "evidence": "assumed", "expires_at": "2001-01-01T09:00:00+00:00", "confirmed_at": "2000-12-31T08:00:00+00:00"},
    ]))
    reader = JobReader(project, graph, store)
    expired = reader.human_gate_state("n04_product_understanding", None)
    assert expired["state"] == "waiting" and expired["provisional_expired"] and "approve" in expired["actions"]
    assert expired["label"].startswith("Waiting for you again: the provisional approval by agent:relay expired ")
    standing = reader.human_gate_state("n06_creative_direction", None)
    assert standing["state"] == "engine_decided" and standing["engine_decision"]["provisional"] is True
    assert "Provisional (assumed), expires" in standing["label"] and standing["actions"] == ["comment"]
    confirmed = reader.human_gate_state("n09_visual_system", None)
    assert confirmed["state"] == "engine_decided" and not confirmed["provisional_expired"]
    nodes = {n["id"]: n for n in reader.node_entries("v10")}
    assert nodes["n04_product_understanding"]["status"] == "waiting"
    assert "waiting for you again" in nodes["n04_product_understanding"]["status_reason"]
    assert "provisional (assumed), expires" in nodes["n06_creative_direction"]["status_reason"]
    decisions, _ = split(needs_you([reader]))
    gate = next(i for i in decisions if i.get("node") == "n04_product_understanding")
    assert gate["reason"] == "provisional approval expired" and gate["detail"].startswith("The provisional approval by agent:relay expired")
    store.append({"actor": "owner", "pid": project.id, "project_path": project.path, "kind": "approve", "version": None,
                  "target": {"type": "gate", "id": "n04_product_understanding"}, "t": None, "tags": [], "text": ""})
    reader = JobReader(project, graph, store)
    assert reader.human_gate_state("n04_product_understanding", None)["state"] == "recorded"
    assert not [i for i in needs_you([reader]) if i.get("node") == "n04_product_understanding"]


def test_telemetry_and_evidence_links_stay_inside_the_project(tmp_path, graph, store):
    root = tmp_path / "roots"
    job = make_governed_job(root)
    outside = write_telemetry(tmp_path / "elsewhere")
    (job / ".vpg" / "telemetry").symlink_to(outside, target_is_directory=True)
    reader = reader_for(job, root, graph, store)
    assert reader.telemetry_summary() is None
    assert reader.node_detail("n16_creative_critique", "v3")["telemetry"]["present"] is False
    assert reader.evidence_link("work/n14_video_assembly/a3/master.mp4") == f"/media/{reader.pid}/_run/work/n14_video_assembly/a3/master.mp4"
    for bad in ("work/n14_video_assembly/a3/secret.json", "../escape.mp4", "/etc/passwd", "candidates/v3/x.mp4", ".vpg/state.json"):
        assert reader.evidence_link(bad) is None, bad


def test_candidate_evidence_links_use_the_media_whitelist(job, project, graph, store):
    reader = JobReader(project, graph, store)
    assert reader.evidence_link("candidates/v10/n14_video_assembly/master.mp4") == f"/media/{project.id}/v10/n14_video_assembly/master.mp4"
    assert reader.evidence_link("candidates/v10/n13_motion_scenes/scenes.json").endswith("/v10/n13_motion_scenes/scenes.json")
    assert reader.evidence_link("candidates/v1/n12_voice_audio/voice.wav").endswith("/v1/n12_voice_audio/voice.wav")
    assert reader.evidence_link("candidates/v10/n13_motion_scenes/missing.mp4") is None
    assert reader.evidence_link("candidates/v10/../../vpg.project.yaml") is None
