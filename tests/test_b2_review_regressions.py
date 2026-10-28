from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import PKG
from vpg import scoring
from vpg.context import NodeResult
from vpg.engine import CONCEPT_FORK_DIMS, Engine
from vpg.models import Approval, ArtifactRecord, Defect, Feedback, GateResult, NodeStatus as S, Review
from vpg.provenance import Ledger
from vpg.tools import verify
from vpg.util import now, read_json, sha256_file


def _copy_controls(proj: Path) -> None:
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")


def _project(tmp_path: Path, monkeypatch, *, store: Path, humans=("Wei",), privacy_names: list[str] | None = None,
             principal="Wei", scored=True, gated=False, title="A") -> tuple[Path, Engine]:
    proj = tmp_path / f"proj-{len(list(tmp_path.glob('proj-*')))}"
    _copy_controls(proj)
    cfg = {"id": "review", "name": "review", "workflow": "default", "humans": list(humans)}
    if privacy_names is not None:
        (proj / "names.json").write_text(json.dumps({"names": privacy_names}))
        cfg["privacy"] = {"names": "names.json"}
    (proj / "vpg.project.yaml").write_text(json.dumps(cfg))
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None, _s=store: _s)
    eng = Engine(proj)
    eng.state.runtime.accepted_policy = {"humans": list(humans)}
    if not gated:
        eng.state.runtime.accepted_policy["autonomy"] = {"mode": "provisional", "principal": principal}

    n05 = proj / "work/n05_narrative_ideation/a1"
    n05.mkdir(parents=True)
    concepts = []
    for cid, score, anchor in (("C1", 5, "problem"), ("C2", 3, "day"), ("C3", 1, "system")):
        c = {"id": cid, "title": title if cid == "C1" else cid, "logline": f"{title} logline" if cid == "C1" else cid,
             "anchor": anchor, "structure": ["a", "b"], "risks": ["r"]}
        if scored:
            c["scores"] = {d["id"]: score for d in CONCEPT_FORK_DIMS}
            c["score_rationale"] = f"rationale {cid}"
        concepts.append(c)
    cp = n05 / "concepts.json"
    cp.write_text(json.dumps({"concepts": concepts}))
    eng.state.artifacts["concepts@n05#a1"] = ArtifactRecord(
        id="concepts@n05#a1", key="concepts", path=str(cp.relative_to(proj)), sha256=sha256_file(cp),
        bytes=cp.stat().st_size, node="n05_narrative_ideation", attempt=1, produced_at=now())

    n06 = eng.node("n06_creative_direction")
    rt = eng.rt("n06_creative_direction")
    rt.status, rt.attempts = S.RUNNING, 1
    ctx = eng._context(n06, 1, "fp", "tok")
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    (ctx.workdir / "creative-decision.json").write_text(json.dumps({
        "selected": "C2", "rationale": "Agent choice.",
        "rejected": [{"id": "C1", "reason": "initial"}, {"id": "C3", "reason": "initial"}],
        "narrative_arc": ["open", "close"],
    }))
    (ctx.workdir / "narrative.md").write_text("# Narrative\n")
    eng._complete(ctx, NodeResult(outputs={"creative_decision": "creative-decision.json", "narrative": "narrative.md"}))
    return proj, eng


def _release_wait(eng: Engine, proj: Path, *, attempt=1, version=1) -> object:
    master = proj / "renders/master.mp4"
    master.parent.mkdir(exist_ok=True)
    master.write_bytes(f"synthetic master {version}".encode())
    eng.state.runtime.current_version = version
    for aid, art in list(eng.state.artifacts.items()):
        if art.key == "master" and art.status == "current":
            eng.state.artifacts[aid] = art.model_copy(update={"status": "superseded"})
    eng.state.artifacts[f"master@n14#a{attempt}"] = ArtifactRecord(
        id=f"master@n14#a{attempt}", key="master", path=str(master.relative_to(proj)), sha256=sha256_file(master),
        bytes=master.stat().st_size, node="n14_video_assembly", attempt=attempt, produced_at=now(), version=version)
    eng.state.evaluation.gate_results.append(GateResult(id=f"G-{version:03d}", node="n21_feedback_interpretation",
                                                        version=version, outcome="pass", reasons=["clean"], created_at=now()))
    n23 = eng.node("n23_final_verification")
    rt = eng.rt("n23_final_verification")
    rt.status, rt.attempts, rt.started_at = S.RUNNING, attempt, now()
    ctx = eng._context(n23, attempt, "fp", "tok")
    verify.final(ctx)
    for key, filename in {"verification": "verification.json", "review_packet": "review-packet.md",
                          "decision_template": "decision.template.json"}.items():
        p = ctx.workdir / filename
        for aid, art in list(eng.state.artifacts.items()):
            if art.key == key and art.status == "current":
                eng.state.artifacts[aid] = art.model_copy(update={"status": "superseded"})
        eng.state.artifacts[f"{key}@n23#a{attempt}"] = ArtifactRecord(
            id=f"{key}@n23#a{attempt}", key=key, path=str(p.relative_to(proj)), sha256=sha256_file(p),
            bytes=p.stat().st_size, node="n23_final_verification", attempt=attempt, produced_at=now(), version=version)
    rt.status = S.WAITING_HUMAN
    eng._note_review_forks_offered("n23_final_verification")   # as the engine does when the release starts waiting
    eng._save("test_release_wait")
    return ctx


def _decision(ctx, *, release="approve", confirm=None, reject=None, feedback="") -> dict:
    d = read_json(ctx.workdir / "decision.template.json")
    for fid in d["forks"]:
        d["forks"][fid]["ranking"] = ["C1", "C2", "C3"]
        d["forks"][fid]["ratings"] = {"C1": {"CON-FIT": 5}}
        d["forks"][fid]["why"] = "Clearer story."
    d.update(release=release, ack_warnings="all", signoffs=[], confirm_provisional=confirm,
             reject_provisional=reject or {}, feedback=feedback)
    return d


def _write(proj: Path, d: dict, name="decision.json") -> Path:
    p = proj / name
    p.write_text(json.dumps(d))
    return p


def _rows(store: Path) -> list[dict]:
    f = store / "decisions.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def _events(proj: Path, kind: str | None = None) -> list[dict]:
    ev = list(Ledger(proj / ".vpg/ledger.jsonl").events())
    return [e for e in ev if kind is None or e["type"] == kind]


def test_calibrate_between_packet_and_record_keeps_frozen_prediction(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    fork = read_json(proj / eng.state.production.forks_ref["F-001"])
    frozen = fork["scored"]
    for i in range(6):
        scoring.record_choice(store, {**fork, "fork_id": f"H-{i}"}, ["C3", "C2", "C1"], {}, "Wei", "past", source="seed")
    scoring.fit_store(store)
    eng.record_review(_write(proj, _decision(ctx)), actor="Wei")
    row = _rows(store)[-1]
    assert row["model_version"] == frozen["model_version"]
    assert row["predicted"] == frozen["predicted"]
    assert _events(proj, "human_choice")[-1]["data"]["prediction"]["predicted"] == frozen["predicted"]


def test_option_label_rankings_map_to_ids_and_mixed_rankings_refuse(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    tmpl = read_json(ctx.workdir / "decision.template.json")
    option_ids = tmpl["forks"]["F-001"]["option_map"]
    d = _decision(ctx)
    d["forks"]["F-001"]["ranking"] = list(option_ids)
    eng.record_review(_write(proj, d), actor="Wei")
    assert _rows(store)[-1]["ranking"] == [option_ids[k] for k in option_ids]

    proj2, eng2 = _project(tmp_path, monkeypatch, store=tmp_path / "prefs2")
    ctx2 = _release_wait(eng2, proj2)
    d2 = _decision(ctx2)
    first = next(iter(read_json(ctx2.workdir / "decision.template.json")["forks"]["F-001"]["option_map"]))
    d2["forks"]["F-001"]["ranking"] = [first, "C2", "C3"]
    with pytest.raises(Exception, match="either option labels or candidate ids"):
        eng2.record_review(_write(proj2, d2), actor="Wei")


def test_fork_drift_refused_even_without_answer_sha(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    fp = proj / eng.state.production.forks_ref["F-001"]
    fork = read_json(fp)
    fork["scored"]["predicted"] = {"C1": 0.01, "C2": 0.01, "C3": 0.98}
    fp.write_text(json.dumps(fork))
    d = _decision(ctx)
    d["forks"]["F-001"].pop("sha256")
    with pytest.raises(Exception, match="sha256"):
        eng.record_review(_write(proj, d), actor="Wei")


def test_edited_registered_template_is_refused(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    template = ctx.workdir / "decision.template.json"
    data = read_json(template)
    data["forks"]["F-001"]["option_map"] = {"Option 1": "C3", "Option 2": "C2", "Option 3": "C1"}
    template.write_text(json.dumps(data))
    with pytest.raises(Exception, match="decision.template.json changed"):
        eng.record_review(_write(proj, _decision(ctx)), actor="Wei")


def test_second_version_omits_answered_forks_and_writes_no_duplicate_rows(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx1 = _release_wait(eng, proj, attempt=1, version=1)
    eng.record_review(_write(proj, _decision(ctx1), "d1.json"), actor="Wei")
    eng = Engine(proj)
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None, _s=store: _s)
    ctx2 = _release_wait(eng, proj, attempt=2, version=2)
    assert read_json(ctx2.workdir / "decision.template.json")["forks"] == {}
    eng.record_review(_write(proj, _decision(ctx2), "d2.json"), actor="Wei")
    assert [r["fork_id"] for r in _rows(store)].count("F-001") == 1


def test_decision_digest_is_numbered_masked_and_mapped_to_template(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    listed = "Jordan Sample"
    proj, eng = _project(tmp_path, monkeypatch, store=store, privacy_names=[listed])
    eng.state.history.approvals.append(Approval(node="n04_product_understanding", decision="approved", actor="agent:x",
                                                human=False, at=now(), provisional=True, principal="Wei", instruction="go"))
    eng.state.evaluation.feedback.append(Feedback(id="F001", text=f"{listed} said the ending needs proof", actor="Wei",
                                                  at="42", version=1, created_at=now(),
                                                  status="resolved_pending_confirmation", defect_ids=["D001"],
                                                  no_action_reason=f"{listed} confirmed it was fixed"))
    ctx = _release_wait(eng, proj)
    digest = (ctx.workdir / "decision-digest.md").read_text()
    template = read_json(ctx.workdir / "decision.template.json")
    for section in ("## 0. TL;DR", "## 1. Watch", "## 2. Decide", "## 3. Confirm", "## 4. Acknowledge",
                    "## 5. Your feedback", "## 6. How to answer"):
        assert section in digest
    assert "0:42" in digest
    assert listed not in digest and "[list entry #1]" in digest
    for item_id in ("D1", "D2", "P1", "P2", "F1"):
        assert f"- {item_id}:" in digest
        assert item_id in template["item_map"]
    assert "Option 1" in digest and "release or iterate" in digest


def test_review_record_confirms_and_rejects_feedback_items(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    eng.state.evaluation.feedback.append(Feedback(id="F001", text="the ending needs proof", actor="Wei",
                                                  at="42", version=1, created_at=now(),
                                                  status="resolved_pending_confirmation", no_action_reason="fixed"))
    ctx = _release_wait(eng, proj)
    template = read_json(ctx.workdir / "decision.template.json")
    item = next(k for k, v in template["item_map"].items() if v.get("target") == "feedback.F001")
    d = _decision(ctx)
    d["confirm_items"] = [item]
    eng.record_review(_write(proj, d), actor="Wei")
    assert eng.state.evaluation.feedback[0].status == "confirmed"

    proj2, eng2 = _project(tmp_path, monkeypatch, store=tmp_path / "prefs2")
    eng2.state.evaluation.feedback.append(Feedback(id="F001", text="the ending needs proof", actor="Wei",
                                                   at="42", version=1, created_at=now(),
                                                   status="resolved_pending_confirmation", no_action_reason="fixed"))
    ctx2 = _release_wait(eng2, proj2)
    template2 = read_json(ctx2.workdir / "decision.template.json")
    item2 = next(k for k, v in template2["item_map"].items() if v.get("target") == "feedback.F001")
    d2 = _decision(ctx2)
    d2["reject_items"] = [item2]
    eng2.record_review(_write(proj2, d2), actor="Wei")
    fb = eng2.state.evaluation.feedback[0]
    assert (fb.status, fb.defect_ids, fb.no_action_reason) == ("open", [], "")


def test_review_record_reject_item_forces_changes_and_relay_needs_time(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store, humans=("Wei",), principal="Wei")
    eng.state.evaluation.feedback.append(Feedback(id="F001", text="needs proof", actor="Wei",
                                                  at="42", version=1, created_at=now(),
                                                  status="resolved_pending_confirmation", no_action_reason="fixed"))
    ctx = _release_wait(eng, proj)
    item = next(k for k, v in read_json(ctx.workdir / "decision.template.json")["item_map"].items()
                if v.get("target") == "feedback.F001")
    d = _decision(ctx)
    d["reject_items"] = [item]
    eng.record_review(_write(proj, d), actor="Wei")
    assert eng.rt("n23_final_verification").status == S.STALE

    proj2, eng2 = _project(tmp_path, monkeypatch, store=tmp_path / "prefs2", humans=("Wei",), principal="Wei")
    eng2.state.evaluation.feedback.append(Feedback(id="F001", text="needs proof", actor="Wei",
                                                   at="42", version=1, created_at=now(),
                                                   status="resolved_pending_confirmation", no_action_reason="fixed"))
    ctx2 = _release_wait(eng2, proj2)
    item2 = next(k for k, v in read_json(ctx2.workdir / "decision.template.json")["item_map"].items()
                 if v.get("target") == "feedback.F001")
    d2 = _decision(ctx2)
    d2["confirm_items"] = [item2]
    with pytest.raises(Exception, match="instruction-at"):
        eng2.record_review(_write(proj2, d2), actor="agent:relay", on_behalf_of="Wei", instruction="confirm")


def test_k_item_ack_counts_and_p1_end_review_all_is_not_default(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    eng.ledger.append("privacy_text_warning", node="n07", warning="partial [list entry #1]")
    eng.state.evaluation.defects.append(Defect(id="D009", signature="X|n13|tool-x", severity="P1",
                                               dimension="ACC-CONTRAST", description="contrast",
                                               source="rev-n18-v1", traced_node="n13_motion_scenes",
                                               first_seen_version=1, last_seen_version=1))
    eng.state.runtime.end_review_items.append({"id": "ERI-001", "node": "n21", "version": 1, "gate": "n21",
                                               "gate_result": "G-001", "reasons": ["blocked"], "defects": ["D009"],
                                               "severities": {"D009": "P1"}, "at": now()})
    ctx = _release_wait(eng, proj)
    item_map = read_json(ctx.workdir / "decision.template.json")["item_map"]
    k = next(k for k, v in item_map.items() if str(v.get("target")).startswith("ack_warnings."))
    eri = next(k for k, v in item_map.items() if str(v.get("target")).startswith("end_review_items."))
    assert "default" not in item_map[eri]
    d = _decision(ctx, confirm="all")
    d["confirm_items"] = [k]
    d["ack_warnings"] = []
    with pytest.raises(Exception, match="explicit decision required"):
        eng.record_review(_write(proj, d), actor="Wei")
    assert eng.state.runtime.end_review_items[0].get("disposition") != "acknowledged"


@pytest.mark.parametrize("count,relayed", [(1, False), (2, False), (1, True), (2, True)])
def test_changes_with_provisional_rejections_never_passes_release_or_spends_release_loop(tmp_path, monkeypatch, count, relayed):
    store = tmp_path / f"prefs-{count}-{relayed}"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    nodes = ["n04_product_understanding", "n09_visual_system"][:count]
    for node in nodes:
        eng.state.history.approvals.append(Approval(node=node, decision="approved", actor="agent:x", human=False,
                                                    at=now(), provisional=True, principal="Wei", instruction="go"))
    ctx = _release_wait(eng, proj)
    reject = {node: f"fix {node}" for node in nodes}
    kwargs = {}
    if relayed:
        kwargs = {"actor": "agent:chat", "on_behalf_of": "Wei", "instruction": "request changes",
                  "instruction_at": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()}
    else:
        kwargs = {"actor": "Wei"}
    eng.record_review(_write(proj, _decision(ctx, release="changes", reject=reject, feedback="more energy")), **kwargs)
    eng = Engine(proj)
    assert eng.rt("n23_final_verification").status == S.STALE


def test_decide_release_confirm_reject_parity_table(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    eng.state.history.approvals.append(Approval(node="n04_product_understanding", decision="approved", actor="agent:x",
                                                human=False, at=now(), provisional=True, principal="Wei", instruction="go"))
    _release_wait(eng, proj)
    eng.decide("n23_final_verification", "changes_requested", actor="Wei", confirm_provisional="all", feedback=["revise"])
    eng = Engine(proj)
    assert eng._standing_provisional() == [] and len(_events(proj, "provisional_confirmed")) == 1
    assert eng.rt("n23_final_verification").status == S.STALE

    proj2, eng2 = _project(tmp_path, monkeypatch, store=tmp_path / "prefs2")
    eng2.state.history.approvals.append(Approval(node="n04_product_understanding", decision="approved", actor="agent:x",
                                                 human=False, at=now(), provisional=True, principal="Wei", instruction="go"))
    eng2.rt("n06_creative_direction").status = S.WAITING_HUMAN
    with pytest.raises(Exception, match="only allowed at the release"):
        eng2.decide("n06_creative_direction", "approved", actor="Wei", reject_provisional="n04_product_understanding",
                    rejection_feedback="x")

    proj3, eng3 = _project(tmp_path, monkeypatch, store=tmp_path / "prefs3")
    _release_wait(eng3, proj3)
    with pytest.raises(Exception, match="no standing provisional"):
        eng3.decide("n23_final_verification", "approved", actor="Wei", confirm_provisional="n04_product_understanding",
                    ack_warnings=["all"])


def test_changes_can_confirm_provisional_without_release_pass(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    eng.state.history.approvals.append(Approval(node="n04_product_understanding", decision="approved", actor="agent:x",
                                                human=False, at=now(), provisional=True, principal="Wei", instruction="go"))
    ctx = _release_wait(eng, proj)
    eng.record_review(_write(proj, _decision(ctx, release="changes", confirm=["n04_product_understanding"],
                                             feedback="revise")), actor="Wei")
    eng = Engine(proj)
    assert eng._standing_provisional() == []
    assert len(_events(proj, "provisional_confirmed")) == 1
    assert eng.rt("n23_final_verification").status == S.STALE


@pytest.mark.parametrize("case", ["standing", "ack", "signoff", "not-waiting", "early-relay", "missing-relay-time"])
def test_review_record_refusals_are_atomic(tmp_path, monkeypatch, case):
    store = tmp_path / f"prefs-{case}"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    if case == "standing":
        eng.state.history.approvals.append(Approval(node="n04_product_understanding", decision="approved", actor="agent:x",
                                                    human=False, at=now(), provisional=True, principal="Wei", instruction="go"))
    if case == "signoff":
        eng.state.evaluation.reviews.append(Review(id="R", node="n12_voice_audio", version=1, reviewer="r",
                                                   rubric_ids=[], verdict="pass", pending_human=["VOI-ADULT"], created_at=now()))
    ctx = _release_wait(eng, proj)
    d = _decision(ctx)
    if case == "ack":
        report = read_json(ctx.workdir / "verification.json")
        report["warnings"] = ["must ack"]
        (ctx.workdir / "verification.json").write_text(json.dumps(report))
        art = eng.state.artifacts["verification@n23#a1"]
        eng.state.artifacts[art.id] = art.model_copy(update={"sha256": sha256_file(ctx.workdir / "verification.json"),
                                                             "bytes": (ctx.workdir / "verification.json").stat().st_size})
        d["ack_warnings"] = []
    if case == "signoff":
        d["signoffs"] = []
    if case == "not-waiting":
        eng.rt("n23_final_verification").status = S.STEP_PASS
    kwargs = {"actor": "Wei"}
    if case in {"early-relay", "missing-relay-time"}:
        eng.state.history.approvals.append(Approval(node="n04_product_understanding", decision="approved", actor="agent:x",
                                                    human=False, at=now(), provisional=True, principal="Wei", instruction="go"))
        d["confirm_provisional"] = "all"
        kwargs = {"actor": "agent:chat", "on_behalf_of": "Wei", "instruction": "confirm"}
        if case == "early-relay":
            kwargs["instruction_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    before = len(_events(proj))
    with pytest.raises(Exception):
        eng.record_review(_write(proj, d), **kwargs)
    assert _rows(store) == []
    assert len(_events(proj)) == before
    assert not (store / "models").exists()


def test_privacy_name_masked_in_packet_html_and_rows_use_ids_only(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    listed = "Jordan Sample"
    proj, eng = _project(tmp_path, monkeypatch, store=store, privacy_names=[listed], title=f"{listed} morning")
    ctx = _release_wait(eng, proj)
    assert listed not in (ctx.workdir / "review-packet.md").read_text()
    assert listed not in (ctx.workdir / "review.html").read_text()
    eng.record_review(_write(proj, _decision(ctx)), actor="Wei")
    assert listed not in json.dumps(_rows(store)[-1])


def test_non_principal_declared_human_records_to_principal_store_with_actor(tmp_path, monkeypatch):
    store = tmp_path / "wei-store"
    proj, eng = _project(tmp_path, monkeypatch, store=store, humans=("Wei", "Pat Example"), principal="Wei")
    ctx = _release_wait(eng, proj)
    eng.record_review(_write(proj, _decision(ctx)), actor="Pat Example")
    assert _rows(store) == []
    choice = _events(proj, "human_choice")[-1]["data"]
    assert choice["principal"] == "Pat Example" and choice["actor"] == "Pat Example" and choice["learned"] is False

    proj2, eng2 = _project(tmp_path, monkeypatch, store=tmp_path / "relay-store", humans=("Wei", "Pat Example"), principal="Wei")
    ctx2 = _release_wait(eng2, proj2)
    eng2.record_review(_write(proj2, _decision(ctx2)), actor="agent:chat", on_behalf_of="Pat Example",
                       instruction="rank for me", instruction_at=(datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat())
    choice2 = _events(proj2, "human_choice")[-1]["data"]
    assert choice2["principal"] == "Pat Example" and choice2["actor"] == "agent:chat" and choice2["learned"] is False


def test_calibration_map_uses_metric_subset_and_concurrent_refits_get_distinct_versions(tmp_path):
    store = tmp_path / "prefs"
    fork = {"fork_id": "F", "fork_type": "concept", "dims": CONCEPT_FORK_DIMS,
            "candidates": [{"id": "C1", "scores": {d["id"]: 5 for d in CONCEPT_FORK_DIMS}},
                           {"id": "C2", "scores": {d["id"]: 1 for d in CONCEPT_FORK_DIMS}}]}
    for i in range(12):
        scoring.record_choice(store, {**fork, "fork_id": f"F-{i}"}, ["C1", "C2"], {}, "Wei",
                              equal_fidelity=(i % 2 == 0), source="seed")
    model = scoring.fit_store(store)
    assert model["metrics"]["n_blind"] == 12 and model["metrics"]["n_calibration"] == 6
    assert model["calibration"] is None

    out = []
    threads = [threading.Thread(target=lambda p=p: out.append(scoring.fit_store(store, principal=p)["version"]))
               for p in ("A", "B")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(out) == [2, 3]


def test_refit_after_rollback_and_corrupt_store_fallback_do_not_break_n06(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    fork = {"fork_id": "F-001", "fork_type": "concept", "dims": CONCEPT_FORK_DIMS,
            "candidates": [{"id": "C1", "scores": {d["id"]: 5 for d in CONCEPT_FORK_DIMS}, "passes_rubric": True},
                           {"id": "C2", "scores": {d["id"]: 1 for d in CONCEPT_FORK_DIMS}, "passes_rubric": True}]}
    for i in range(3):
        scoring.record_choice(store, {**fork, "fork_id": f"R-{i}"}, ["C1", "C2"], {}, "Wei", source="seed")
        scoring.fit_store(store)
    v2 = (store / "models/model-v2.json").read_text()
    (store / "current.json").write_text(json.dumps({"version": 1}))
    scoring.record_choice(store, {**fork, "fork_id": "R-9"}, ["C2", "C1"], {}, "Wei", source="seed")
    model = scoring.fit_store(store)
    assert model["version"] == 4 and (store / "models/model-v2.json").read_text() == v2
    (store / "current.json").write_text('{"version": ')
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    assert eng.rt("n06_creative_direction").status == S.WAITING_HUMAN


def test_record_fork_refuses_duplicate_missing_dimension_empty_prediction_and_rerecord(project):
    proj, _wf = project
    eng = Engine(proj)
    fork = {"fork_id": "F-001", "fork_type": "scene", "node": "n13_motion_scenes", "version": 1,
            "dims": [{"id": "PAC-READ", "measured": True}, {"id": "CRE-FOCUS", "measured": False}],
            "candidates": [{"id": "A", "label": "A", "scores": {"PAC-READ": 4, "CRE-FOCUS": 3},
                            "passes_rubric": True, "rationale": "x"},
                           {"id": "B", "label": "B", "scores": {"PAC-READ": 3, "CRE-FOCUS": 4},
                            "passes_rubric": True, "rationale": "y"}],
            "scored": {"model_version": 0, "predicted": {"A": 0.4, "B": 0.6}, "confidence": 0.5,
                       "band": "refuse", "build": ["A", "B"]}}
    p = proj / "fork.json"
    p.write_text(json.dumps({**fork, "candidates": [fork["candidates"][0], {**fork["candidates"][1], "id": "A"}]}))
    with pytest.raises(Exception, match="unique"):
        eng.record_fork(p, actor="agent:test")
    bad = json.loads(json.dumps(fork))
    bad["candidates"][1]["scores"].pop("CRE-FOCUS")
    p.write_text(json.dumps(bad))
    with pytest.raises(Exception, match="cover every dimension"):
        eng.record_fork(p, actor="agent:test")
    bad = json.loads(json.dumps(fork)); bad["scored"]["predicted"] = {}
    p.write_text(json.dumps(bad))
    with pytest.raises(Exception, match="predicted"):
        eng.record_fork(p, actor="agent:test")
    p.write_text(json.dumps(fork))
    eng.record_fork(p, actor="agent:test")
    with pytest.raises(Exception, match="already recorded"):
        eng.record_fork(p, actor="agent:test")


def test_concurrent_append_survives_and_undo_removes_only_own_review_rows(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    fork = read_json(proj / eng.state.production.forks_ref["F-001"])
    orig_pass = Engine._pass

    def interleaved(self, node, rt, outcome, data):
        scoring.record_choice(store, {**fork, "fork_id": "B", "run": "project-B"}, ["C2", "C1", "C3"], {}, "Wei",
                              "B row", source="B")
        sp = self.project.state_path
        time.sleep(0.01)
        sp.write_bytes(sp.read_bytes())
        os.utime(sp, None)
        return orig_pass(self, node, rt, outcome, data)

    monkeypatch.setattr(Engine, "_pass", interleaved)
    with pytest.raises(Exception):
        eng.record_review(_write(proj, _decision(ctx)), actor="Wei")
    assert [(r["run"], r["why"]) for r in _rows(store)] == [("project-B", "B row")]
    monkeypatch.setattr(Engine, "_pass", orig_pass)
    Engine(proj).record_review(_write(proj, _decision(ctx), "d2.json"), actor="Wei")
    assert {r["why"] for r in _rows(store)} == {"B row", "Clearer story."}


def test_retry_replaces_orphan_row_for_same_review_and_fork(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    review_id = f"{eng.state.runtime.run_id}:v{eng.state.runtime.current_version}:n23_final_verification:a1"
    fork = read_json(proj / eng.state.production.forks_ref["F-001"])
    scoring.append_decision_atomic(store, {"review_id": review_id, "fork_id": "F-001", "ranking": ["C1", "C2", "C3"],
                                           "why": "orphan"})
    assert len(_rows(store)) == 1
    d2 = _decision(ctx)
    d2["forks"]["F-001"]["ranking"] = ["C3", "C2", "C1"]
    Engine(proj).record_review(_write(proj, d2, "second.json"), actor="Wei")
    rows = _rows(store)
    assert len(rows) == 1 and rows[0]["ranking"] == ["C3", "C2", "C1"]
    assert _events(proj, "human_choice")[-1]["data"]["ranking"] == ["C3", "C2", "C1"]


def test_model_pin_fingerprint_and_midrun_calibrate(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    n06 = eng.node("n06_creative_direction")
    fp1 = eng.fingerprint(n06)
    pin = dict(eng.state.runtime.preference_model_pins)
    fork = read_json(proj / eng.state.production.forks_ref["F-001"])
    scoring.record_choice(store, {**fork, "fork_id": "H-1"}, ["C3", "C2", "C1"], {}, "Wei", source="seed")
    scoring.fit_store(store)
    assert eng.state.runtime.preference_model_pins == pin
    assert eng.fingerprint(n06) == fp1
    key = next(iter(pin))
    eng.state.runtime.preference_model_pins[key] = pin[key] + 1
    assert eng.fingerprint(n06) != fp1


def test_first_n06_dispatch_fingerprint_includes_pin(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    fork = {"fork_id": "seed", "fork_type": "concept", "dims": CONCEPT_FORK_DIMS,
            "candidates": [{"id": "C1", "scores": {d["id"]: 5 for d in CONCEPT_FORK_DIMS}},
                           {"id": "C2", "scores": {d["id"]: 1 for d in CONCEPT_FORK_DIMS}}]}
    scoring.record_choice(store, fork, ["C1", "C2"], {}, "Wei", source="seed")
    scoring.fit_store(store)
    proj = tmp_path / "dispatch-pin"
    _copy_controls(proj)
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "pin", "name": "pin", "workflow": "default", "humans": ["Wei"]}))
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None, _s=store: _s)
    eng = Engine(proj)
    eng.state.runtime.accepted_policy = {"humans": ["Wei"], "autonomy": {"mode": "provisional", "principal": "Wei"}}
    n05 = proj / "work/n05_narrative_ideation/a1"; n05.mkdir(parents=True)
    cp = n05 / "concepts.json"
    cp.write_text(json.dumps({"concepts": [
        {"id": "C1", "title": "A", "logline": "A", "anchor": "problem", "structure": ["a", "b"], "risks": ["r"],
         "scores": {d["id"]: 5 for d in CONCEPT_FORK_DIMS}, "score_rationale": "r"},
        {"id": "C2", "title": "B", "logline": "B", "anchor": "day", "structure": ["a", "b"], "risks": ["r"],
         "scores": {d["id"]: 3 for d in CONCEPT_FORK_DIMS}, "score_rationale": "r"},
        {"id": "C3", "title": "C", "logline": "C", "anchor": "system", "structure": ["a", "b"], "risks": ["r"],
         "scores": {d["id"]: 1 for d in CONCEPT_FORK_DIMS}, "score_rationale": "r"},
    ]}))
    eng.state.artifacts["concepts@n05#a1"] = ArtifactRecord(
        id="concepts@n05#a1", key="concepts", path=str(cp.relative_to(proj)), sha256=sha256_file(cp),
        bytes=cp.stat().st_size, node="n05_narrative_ideation", attempt=1, produced_at=now())
    ctx = eng._prepare("n06_creative_direction")
    assert eng.state.runtime.preference_model_pins == {"wei": 1}
    assert ctx.fingerprint == eng.rt("n06_creative_direction").fingerprint
    assert ctx.fingerprint == eng.fingerprint(eng.node("n06_creative_direction"))


def test_packet_shows_calibrated_metrics_after_calibrate(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    monkeypatch.setattr(scoring, "preference_store", lambda _root, _principal: store)
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    fork = read_json(proj / eng.state.production.forks_ref["F-001"])
    scoring.record_choice(store, {**fork, "fork_id": "H-1"}, ["C1", "C2", "C3"], {}, "Wei", source="seed")
    scoring.fit_store(store)
    ctx = _release_wait(eng, proj)
    packet = (ctx.workdir / "review-packet.md").read_text()
    assert '"n": 1' in packet and "ece_calibrated" in packet and "Current model: v1" in packet


def test_blind_packet_and_html_before_reveal_have_no_scores_picks_probabilities_or_paths(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    packet = (ctx.workdir / "review-packet.md").read_text()
    blind = packet.split("## 9. Reveal", 1)[0]
    html = (ctx.workdir / "review.html").read_text()
    visible_html = html.split("<script id=\"reveal\"", 1)[0]
    import re
    raw = re.search(r'<script id="reveal" type="application/json">(.*?)</script>', html, re.S).group(1)
    reveal_text = json.loads(raw)
    assert "&quot;" not in reveal_text and '"C1"' in reveal_text
    assert "<pre></pre>" in html
    forbidden = ["Predicted", "probabilities", "creative-decision", "concepts.json", "candidate `", "model top",
                 '"scores"', "scores {"]
    assert not any(term in blind for term in forbidden)
    assert not any(term in visible_html for term in forbidden)
    lines = [line for line in blind.splitlines() if line.startswith("- Option")]
    assert lines and not lines[0].startswith("- Option 1: C1")


def test_metrics_exclude_non_equal_fidelity_rows_but_weights_still_train():
    decisions = []
    for i in range(8):
        decisions.append({"fork_id": f"F-{i}", "fork_type": "concept", "dims": CONCEPT_FORK_DIMS,
                          "candidates": [
                              {"id": "A", "scores": {d["id"]: 5 for d in CONCEPT_FORK_DIMS}},
                              {"id": "B", "scores": {d["id"]: 1 for d in CONCEPT_FORK_DIMS}},
                          ],
                          "ranking": ["B", "A"], "blind": True, "equal_fidelity": False})
    metrics, _rows_ = scoring.evaluate(decisions, epochs=20)
    assert metrics["n"] == 8 and metrics["n_calibration"] == 0
    assert metrics["top1"] is None and metrics["pairwise"] is None and metrics["ece"] is None
    model = scoring.fit(decisions, epochs=20)
    weights = scoring.weights(model, "concept", [d["id"] for d in CONCEPT_FORK_DIMS])
    assert set(weights) == {d["id"] for d in CONCEPT_FORK_DIMS}


def test_malformed_none_score_row_is_skipped_by_fit(tmp_path):
    store = tmp_path / "prefs"
    fork = {"fork_id": "ok", "fork_type": "concept", "dims": CONCEPT_FORK_DIMS,
            "candidates": [{"id": "C1", "scores": {d["id"]: 5 for d in CONCEPT_FORK_DIMS}},
                           {"id": "C2", "scores": {d["id"]: 1 for d in CONCEPT_FORK_DIMS}}]}
    scoring.record_choice(store, fork, ["C1", "C2"], {}, "Wei", source="seed")
    row = _rows(store)[0]
    row["fork_id"] = "bad"
    row["candidate_scores"]["C1"]["CON-FIT"] = None
    with open(store / "decisions.jsonl", "a") as fh:
        fh.write(json.dumps(row) + "\n")
    model = scoring.fit_store(store)
    assert model["trained_on"]["decisions"] == 1 and model["trained_on"]["skipped"] == 1


def test_pdg_shared_cli_sequence_accepts_open_ended_fork_types(tmp_path):
    work = tmp_path / "prototype-design"
    data = work / "knowledge/preferences/wei"
    work.mkdir(parents=True)
    fork = {
        "fork_id": "F-001",
        "fork_type": "refinement",
        "version": 1,
        "dims": [
            {"id": "DES-TASTE", "measured": False},
            {"id": "DES-CONSISTENCY", "measured": False},
            {"id": "REF-PARITY", "measured": True},
        ],
        "candidates": [
            {"id": "A", "label": "Reference-faithful", "scores": {"DES-TASTE": 4, "DES-CONSISTENCY": 3, "REF-PARITY": 5},
             "passes_rubric": True, "rationale": "Closest to the approved reference."},
            {"id": "B", "label": "System-clean", "scores": {"DES-TASTE": 5, "DES-CONSISTENCY": 5, "REF-PARITY": 3},
             "passes_rubric": True, "rationale": "Cleaner system fit."},
            {"id": "C", "label": "Dense", "scores": {"DES-TASTE": 2, "DES-CONSISTENCY": 2, "REF-PARITY": 4},
             "passes_rubric": False, "rationale": "Fails the floor."},
        ],
    }
    fork_path = work / "F-001.json"
    fork_path.write_text(json.dumps(fork))
    script = PKG / "scoring/vpg_score.py"
    py = sys.executable

    score = subprocess.run([py, str(script), "--data", str(data), "score", str(fork_path)],
                           cwd=work, text=True, capture_output=True)
    assert score.returncode == 0, score.stderr
    scored = json.loads(score.stdout)
    assert scored["fork_type"] == "refinement" and scored["dropped_by_rubric"] == ["C"]

    record = subprocess.run([py, str(script), "--data", str(data), "record", str(fork_path),
                             "--rank", "B,A", "--why", "cleaner system fit"],
                            cwd=work, text=True, capture_output=True)
    assert record.returncode == 0, record.stderr
    rows = _rows(data)
    assert len(rows) == 1 and rows[0]["fork_type"] == "refinement"
    assert rows[0]["equal_fidelity"] is True and "candidates" in rows[0]

    fit = subprocess.run([py, str(script), "--data", str(data), "fit"], cwd=work, text=True, capture_output=True)
    assert fit.returncode == 0, fit.stderr
    report = subprocess.run([py, str(script), "--data", str(data), "report"], cwd=work, text=True, capture_output=True)
    assert report.returncode == 0, report.stderr
    out = json.loads(report.stdout)
    assert out["metrics"]["n"] == 1 and out["metrics"]["n_calibration"] == 1
    assert sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file()) == [
        ".decisions.lock", ".model.lock", "current.json", "decisions.jsonl", "models/model-v1.json"
    ]
