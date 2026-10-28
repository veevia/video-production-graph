from __future__ import annotations

import json
import subprocess
import sys

import jsonschema
import pytest

from tests.conftest import PKG
from vpg.context import NodeContext, NodeResult
from vpg.engine import Engine
from vpg.models import ArtifactRecord, GateResult, NodeStatus as S
from vpg.provenance import Ledger
from vpg import scoring
from vpg.tools import briefs
from vpg.util import now, read_json, sha256_file


CONCEPT_DIMS = scoring.CONCEPT_FORK_DIMS


def test_fork_schema_accepts_scored_candidates():
    schema = json.loads((PKG / "schemas/artifacts/fork.schema.json").read_text())
    fork = {
        "fork_id": "F-001",
        "fork_type": "concept",
        "node": "n06_creative_direction",
        "version": 0,
        "dims": CONCEPT_DIMS,
        "candidates": [
            {
                "id": "C1",
                "label": "Concept A",
                "scores": {"CON-FIT": 5, "NAR-ARC": 4, "PAC-READ": 3},
                "passes_rubric": True,
                "rationale": "Fits the objective with a clear arc.",
                "artifact_ref": "concepts@n05_narrative_ideation#a1",
            },
            {
                "id": "C2",
                "label": "Concept B",
                "scores": {"CON-FIT": 3, "NAR-ARC": 5, "PAC-READ": 4},
                "passes_rubric": True,
                "rationale": "Strong structure with more pacing risk.",
            },
        ],
        "scored": {
            "model_version": 0,
            "predicted": {"C1": 0.57, "C2": 0.43},
            "confidence": 0.76,
            "band": "flag",
            "build": ["C1", "C2"],
        },
    }
    jsonschema.validate(fork, schema)


def test_old_and_scored_concepts_validate():
    schema = json.loads((PKG / "schemas/artifacts/concepts.schema.json").read_text())
    old = {
        "concepts": [
            {"id": "C1", "title": "A", "logline": "A", "anchor": "problem", "structure": ["a", "b"], "risks": ["r"]},
            {"id": "C2", "title": "B", "logline": "B", "anchor": "day", "structure": ["a", "b"], "risks": ["r"]},
            {"id": "C3", "title": "C", "logline": "C", "anchor": "system", "structure": ["a", "b"], "risks": ["r"]},
        ]
    }
    jsonschema.validate(old, schema)
    scored = {
        "concepts": [
            {
                **c,
                "scores": {d["id"]: 4 for d in CONCEPT_DIMS},
                "score_rationale": "Synthetic rationale.",
            }
            for c in old["concepts"]
        ]
    }
    jsonschema.validate(scored, schema)
    bad = json.loads(json.dumps(scored))
    bad["concepts"][0]["scores"]["CON-FIT"] = 6
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)

    ctx = type("Ctx", (), {"load": lambda _self, key: scored if key == "concepts" else None})()
    assert briefs.check_concepts(ctx).ok


def test_n06_records_scores_and_selects_top_concept(project, tmp_path):
    proj, _wf = project
    eng = Engine(proj)
    eng.state.runtime.accepted_policy = {"humans": ["Wei"], "autonomy": {"mode": "provisional", "principal": "Wei"}}
    workdir = proj / "work/n06_creative_direction/a1"
    workdir.mkdir(parents=True)
    concepts_path = workdir / "concepts.json"
    concepts_path.write_text(json.dumps({
        "concepts": [
            {"id": "C1", "title": "A", "logline": "A", "anchor": "problem", "structure": ["a", "b"], "risks": ["r"],
             "scores": {d["id"]: 5 for d in CONCEPT_DIMS}, "score_rationale": "Strongest fit."},
            {"id": "C2", "title": "B", "logline": "B", "anchor": "day", "structure": ["a", "b"], "risks": ["r"],
             "scores": {d["id"]: 1 for d in CONCEPT_DIMS}, "score_rationale": "Weaker fit."},
            {"id": "C3", "title": "C", "logline": "C", "anchor": "system", "structure": ["a", "b"], "risks": ["r"],
             "scores": {d["id"]: 2 for d in CONCEPT_DIMS}, "score_rationale": "Some useful pieces."},
        ]
    }))
    decision_path = workdir / "creative-decision.json"
    decision_path.write_text(json.dumps({
        "selected": "C2",
        "rationale": "Initial agent choice.",
        "rejected": [{"id": "C1", "reason": "Initial rejection."}, {"id": "C3", "reason": "Initial rejection."}],
        "narrative_arc": ["open", "close"],
    }))
    concepts_rec = ArtifactRecord(id="concepts@n05#a1", key="concepts", path=str(concepts_path.relative_to(proj)),
                                  sha256="x", bytes=1, node="n05_narrative_ideation", attempt=1, produced_at=now())
    ctx = NodeContext(
        node=type("Node", (), {"id": "n06_creative_direction"})(),  # type: ignore[arg-type]
        attempt=1,
        workdir=workdir,
        project_dir=proj,
        pkg_root=PKG,
        state=eng.state,
        fingerprint="",
        dispatch_token="",
        config={},
        rubrics={},
        controls=eng.controls,
        registered={"concepts": [concepts_rec]},
    )
    res = NodeResult(outputs={"creative_decision": decision_path.name})

    eng._prepare_scored_fork(ctx, res)

    fork_rel = res.outputs["fork"]["F-001"]
    fork = read_json(workdir / fork_rel)
    assert fork["scored"]["build"][0] == "C1"
    assert fork["scored"]["agent_pick"] == "C2" and fork["scored"]["model_top"] == "C1"
    assert read_json(decision_path)["selected"] == "C2"
    assert res.data["_fork_scored"]["top"] == "C1"


def test_generic_fork_record_validates_and_ledgers(project):
    proj, _wf = project
    fork_path = proj / "fork.json"
    fork_path.write_text(json.dumps({
        "fork_id": "F-099",
        "fork_type": "scene",
        "node": "n13_motion_scenes",
        "version": 1,
        "dims": [{"id": "PAC-READ", "measured": True}],
        "candidates": [{"id": "A", "label": "Scene A", "scores": {"PAC-READ": 4}, "passes_rubric": True,
                        "rationale": "Readable."}],
        "scored": {"model_version": 0, "predicted": {"A": 1.0}, "confidence": 0.8, "band": "flag", "build": ["A"]},
    }))
    eng = Engine(proj)
    eng.record_fork(fork_path, actor="agent:test")

    assert eng.state.production.forks_ref["F-099"].endswith("work/forks/F-099.json")
    assert any(e["data"]["fork_id"] == "F-099" for e in Ledger(proj / ".vpg/ledger.jsonl").events("fork_recorded"))


def test_fork_record_refuses_hostile_id_and_failing_candidate(project):
    proj, _wf = project
    eng = Engine(proj)
    base = {
        "fork_id": "../../.vpg/state",
        "fork_type": "scene",
        "node": "n13_motion_scenes",
        "version": 1,
        "dims": [{"id": "PAC-READ", "measured": True}],
        "candidates": [{"id": "A", "label": "Scene A", "scores": {"PAC-READ": 4}, "passes_rubric": True,
                        "rationale": "Readable."}],
        "scored": {"model_version": 0, "predicted": {"A": 1.0}, "confidence": 0.8, "band": "flag", "build": ["A"]},
    }
    p = proj / "fork.json"
    p.write_text(json.dumps(base))
    with pytest.raises(Exception, match="fork record refused"):
        eng.record_fork(p, actor="agent:test")
    base["fork_id"] = "F-100"
    base["candidates"][0]["passes_rubric"] = False
    p.write_text(json.dumps(base))
    with pytest.raises(Exception, match="rubric-failing"):
        eng.record_fork(p, actor="agent:test")


def test_default_workflow_end_review_records_choice_and_release(tmp_path, monkeypatch):
    import shutil
    from vpg.tools import verify

    proj = tmp_path / "default-review"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "review", "name": "review", "workflow": "default",
                                                        "humans": ["Wei"]}))
    eng = Engine(proj)
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None: tmp_path / "prefs")
    eng.state.runtime.accepted_policy = {"humans": ["Wei"], "autonomy": {"mode": "provisional", "principal": "Wei"}}

    n05_dir = proj / "work/n05_narrative_ideation/a1"
    n05_dir.mkdir(parents=True)
    concepts_path = n05_dir / "concepts.json"
    concepts_path.write_text(json.dumps({
        "concepts": [
            {"id": "C1", "title": "A", "logline": "A", "anchor": "problem", "structure": ["a", "b"], "risks": ["r"],
             "scores": {d["id"]: 5 for d in CONCEPT_DIMS}, "score_rationale": "Best objective fit."},
            {"id": "C2", "title": "B", "logline": "B", "anchor": "day", "structure": ["a", "b"], "risks": ["r"],
             "scores": {d["id"]: 3 for d in CONCEPT_DIMS}, "score_rationale": "Useful but less direct."},
            {"id": "C3", "title": "C", "logline": "C", "anchor": "system", "structure": ["a", "b"], "risks": ["r"],
             "scores": {d["id"]: 1 for d in CONCEPT_DIMS}, "score_rationale": "Weakest fit."},
        ]
    }))
    eng.state.artifacts["concepts@n05#a1"] = ArtifactRecord(
        id="concepts@n05#a1", key="concepts", path=str(concepts_path.relative_to(proj)),
        sha256=sha256_file(concepts_path), bytes=concepts_path.stat().st_size, node="n05_narrative_ideation",
        attempt=1, produced_at=now())

    n06 = eng.node("n06_creative_direction")
    n06rt = eng.rt("n06_creative_direction")
    n06rt.status, n06rt.attempts = S.RUNNING, 1
    n06_ctx = eng._context(n06, 1, "fp", "tok")
    n06_ctx.workdir.mkdir(parents=True, exist_ok=True)
    (n06_ctx.workdir / "creative-decision.json").write_text(json.dumps({
        "selected": "C2", "rationale": "Initial choice.", "rejected": [{"id": "C1", "reason": "initial"},
        {"id": "C3", "reason": "initial"}], "narrative_arc": ["open", "close"]}))
    (n06_ctx.workdir / "narrative.md").write_text("# Narrative\n")
    eng._complete(n06_ctx, NodeResult(outputs={"creative_decision": "creative-decision.json", "narrative": "narrative.md"}))
    assert eng.state.production.forks_ref and read_json(n06_ctx.workdir / "creative-decision.json")["selected"] == "C2"
    assert any(e["type"] == "fork_scored" for e in Ledger(proj / ".vpg/ledger.jsonl").events())

    master = proj / "renders/master.mp4"
    master.parent.mkdir()
    master.write_bytes(b"synthetic master")
    eng.state.runtime.current_version = 1
    eng.state.artifacts["master@n14#a1"] = ArtifactRecord(
        id="master@n14#a1", key="master", path=str(master.relative_to(proj)), sha256=sha256_file(master),
        bytes=master.stat().st_size, node="n14_video_assembly", attempt=1, produced_at=now(), version=1)
    eng.state.production.master_ref = str(master.relative_to(proj))
    eng.state.evaluation.gate_results.append(GateResult(id="G-001", node="n21_feedback_interpretation", version=1,
                                                        outcome="pass", reasons=["clean"], created_at=now()))
    n23 = eng.node("n23_final_verification")
    n23rt = eng.rt("n23_final_verification")
    n23rt.status, n23rt.attempts, n23rt.started_at = S.RUNNING, 1, now()
    n23_ctx = eng._context(n23, 1, "fp", "tok")
    verify.final(n23_ctx)
    for key, filename in {"verification": "verification.json", "review_packet": "review-packet.md",
                          "decision_template": "decision.template.json"}.items():
        path = n23_ctx.workdir / filename
        eng.state.artifacts[f"{key}@n23#a1"] = ArtifactRecord(
            id=f"{key}@n23#a1", key=key, path=str(path.relative_to(proj)), sha256=sha256_file(path),
            bytes=path.stat().st_size, node="n23_final_verification", attempt=1, produced_at=now(), version=1)
    n23rt.status = S.WAITING_HUMAN
    decision = read_json(n23_ctx.workdir / "decision.template.json")
    decision["forks"]["F-001"]["ranking"] = ["C1", "C2", "C3"]
    decision["forks"]["F-001"]["ratings"] = {"C1": {"CON-FIT": 5}}
    decision["forks"]["F-001"]["why"] = "Clearer story."
    decision["release"] = "approve"
    decision["ack_warnings"] = "all"
    decision_path = proj / "decision.json"
    decision_path.write_text(json.dumps(decision))

    eng.record_review(decision_path, actor="Wei")

    assert eng.rt("n23_final_verification").status == S.STEP_PASS
    pref_row = json.loads((tmp_path / "prefs/decisions.jsonl").read_text().splitlines()[0])
    assert pref_row["run"] == eng.state.runtime.run_id and pref_row["review_id"]
    assert pref_row["equal_fidelity"] is False and pref_row["candidate_scores"]["C1"]["CON-FIT"] == 5
    assert "title" not in json.dumps(pref_row).lower() and "logline" not in json.dumps(pref_row).lower()
    events = list(Ledger(proj / ".vpg/ledger.jsonl").events())
    assert any(e["type"] == "human_choice" and e["data"]["fork_id"] == "F-001" for e in events)
    assert any(e["type"] == "decision" and e["data"]["node"] == "n23_final_verification" for e in events)


def test_review_record_refuses_standing_provisional_without_confirm(tmp_path, monkeypatch):
    import shutil
    from vpg.models import Approval
    from vpg.tools import verify

    proj = tmp_path / "standing"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "standing", "name": "standing", "workflow": "default", "humans": ["Wei"]}))
    eng = Engine(proj)
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None: tmp_path / "prefs")
    eng.state.runtime.accepted_policy = {"humans": ["Wei"], "autonomy": {"mode": "provisional", "principal": "Wei"}}
    eng.state.history.approvals.append(Approval(node="n04_product_understanding", decision="approved", actor="agent:x",
                                                human=False, at=now(), provisional=True, principal="Wei", instruction="go"))
    master = proj / "renders/master.mp4"; master.parent.mkdir(); master.write_bytes(b"x")
    eng.state.runtime.current_version = 1
    eng.state.artifacts["master"] = ArtifactRecord(id="master", key="master", path="renders/master.mp4", sha256=sha256_file(master),
                                                   bytes=1, node="n14_video_assembly", attempt=1, produced_at=now(), version=1)
    eng.state.evaluation.gate_results.append(GateResult(id="G", node="n21_feedback_interpretation", version=1,
                                                        outcome="pass", reasons=[], created_at=now()))
    rt = eng.rt("n23_final_verification")
    rt.status, rt.attempts, rt.started_at = S.RUNNING, 1, now()
    ctx = eng._context(eng.node("n23_final_verification"), 1, "fp", "tok")
    verify.final(ctx)
    for key, filename in {"verification": "verification.json", "decision_template": "decision.template.json"}.items():
        p = ctx.workdir / filename
        eng.state.artifacts[key] = ArtifactRecord(id=key, key=key, path=str(p.relative_to(proj)), sha256=sha256_file(p),
                                                  bytes=p.stat().st_size, node="n23_final_verification", attempt=1, produced_at=now())
    rt.status = S.WAITING_HUMAN
    d = read_json(ctx.workdir / "decision.template.json")
    d["release"] = "approve"; d["ack_warnings"] = "all"; d["confirm_provisional"] = None
    dp = proj / "decision.json"; dp.write_text(json.dumps(d))
    with pytest.raises(Exception, match="standing provisional"):
        eng.record_review(dp, actor="Wei")
    assert not (tmp_path / "prefs/decisions.jsonl").exists()


def test_unscored_and_gated_n06_do_not_record_fork(project):
    proj, _wf = project
    eng = Engine(proj)
    workdir = proj / "work/n06_creative_direction/a1"
    workdir.mkdir(parents=True)
    concepts_path = workdir / "concepts.json"
    concepts_path.write_text(json.dumps({"concepts": [
        {"id": "C1", "title": "A", "logline": "A", "anchor": "problem", "structure": ["a", "b"], "risks": ["r"]},
        {"id": "C2", "title": "B", "logline": "B", "anchor": "day", "structure": ["a", "b"], "risks": ["r"]},
        {"id": "C3", "title": "C", "logline": "C", "anchor": "system", "structure": ["a", "b"], "risks": ["r"]},
    ]}))
    ctx = NodeContext(node=type("Node", (), {"id": "n06_creative_direction"})(), attempt=1, workdir=workdir,
                      project_dir=proj, pkg_root=PKG, state=eng.state, fingerprint="", dispatch_token="",
                      config={}, rubrics={}, controls=eng.controls,
                      registered={"concepts": [ArtifactRecord(id="concepts", key="concepts", path=str(concepts_path.relative_to(proj)),
                                                               sha256=sha256_file(concepts_path), bytes=1,
                                                               node="n05_narrative_ideation", attempt=1, produced_at=now())]})
    res = NodeResult(outputs={"creative_decision": "creative-decision.json"})
    (workdir / "creative-decision.json").write_text(json.dumps({"selected": "C2"}))
    eng._prepare_scored_fork(ctx, res)
    assert "fork" not in res.outputs


def test_scoring_wrapper_documented_command_smoke(tmp_path):
    fork = {"fork_id": "F-001", "fork_type": "concept", "dims": CONCEPT_DIMS,
            "candidates": [{"id": "C1", "scores": {d["id"]: 5 for d in CONCEPT_DIMS}, "passes_rubric": True}]}
    fp = tmp_path / "fork.json"; fp.write_text(json.dumps(fork))
    result = subprocess.run([
        sys.executable,
        str(PKG / "scoring/vpg_score.py"),
        "--data", str(tmp_path / "prefs"),
        "score", str(fp),
    ], cwd=PKG, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_calibrate_refits_versioned_model_and_reports_metrics(project, tmp_path, monkeypatch, capsys):
    from vpg import scoring
    from vpg.cli import main

    proj, _wf = project
    store = tmp_path / "prefs"
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None: store)
    fork = {
        "fork_id": "F-001",
        "fork_type": "concept",
        "dims": CONCEPT_DIMS,
        "candidates": [
            {"id": "C1", "scores": {d["id"]: 5 for d in CONCEPT_DIMS}, "passes_rubric": True},
            {"id": "C2", "scores": {d["id"]: 1 for d in CONCEPT_DIMS}, "passes_rubric": True},
        ],
    }
    scoring.record_choice(store, fork, ["C1", "C2"], {}, "Synthetic", "clearer", True, False, "test")
    capsys.readouterr()

    assert main(["calibrate", str(proj), "--principal", "Synthetic"]) == 0

    out = json.loads(capsys.readouterr().out)
    current = read_json(store / "current.json")
    model = read_json(store / "models" / f"model-v{current['version']}.json")
    assert model["principal"] == "Synthetic" and model["version"] == 1
    assert {"n", "top1", "pairwise", "brier", "ece_raw", "ece_calibrated", "calibrated"} <= set(out)
