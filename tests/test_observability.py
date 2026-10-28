"""P2: observability. Spans and evaluation events, the direct-run event path, provisional approvals with an evidence
tag and an expiry.

Acceptance (handoff brief, P2): `ledger-verify` passes; every node and gate has an event with evidence; the glass-box
data contract is documented (docs/observability.md, checked here for every artifact path it promises).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta

import pytest
import yaml

from tests.conftest import PKG, FnExecutor
from tests.test_hardening import make_project
from vpg import cli, telemetry
from vpg.engine import Engine, EngineError
from vpg.models import NodeStatus as S
from vpg.provenance import Ledger


def _cfg(proj, **extra):
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg.update(extra)
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))


def _run_to_end(proj, human_b=False):
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=300)
    if human_b:
        eng.decide("b_plan", "approved", actor="Wei", reason="looks right")
        eng.run(max_steps=300)
    return eng


def _verify(proj):
    ok, n, msg = Ledger(proj / ".vpg/ledger.jsonl", read_only=True).verify()
    return ok, n, msg


def test_opted_in_runs_write_evaluation_events_and_the_ledger_verifies(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    _cfg(proj, observability={"evaluation_events": True})
    eng = _run_to_end(proj, human_b=True)
    assert eng.state.runtime.status.value == "RUN_PASS"
    events = list(Ledger(proj / ".vpg/ledger.jsonl", read_only=True).events())
    evals = [e["data"] for e in events if e["type"] == "evaluation"]
    passed = {e["data"]["node"] for e in events if e["type"] == "node_passed"}
    assert {d["node"] for d in evals if d["name"] == "vpg.node.completion"} == passed
    assert any(d["name"] == "vpg.gate" for d in evals) and any(d["name"] == "vpg.human_decision" for d in evals)
    assert any(d["name"].startswith("vpg.review.") for d in evals)
    for d in evals:
        assert d["gen_ai.evaluation.name"] == d["name"] and d["gen_ai.evaluation.score.label"]
        assert d["evidence_tag"] in ("observed", "reported", "assumed")
    completion = [d for d in evals if d["name"] == "vpg.node.completion"]
    for d in completion:   # a node that registered outputs names each by sha256; every event says what was checked
        made = [a for a in eng.state.artifacts.values() if a.node == d["node"] and a.attempt == d["attempt"]]
        kinds = {x["kind"] for x in d["evidence"]}
        assert "check" in kinds, d["node"]
        if made:
            assert {x["sha256"] for x in d["evidence"] if x["kind"] == "artifact"} == {a.sha256[:16] for a in made}, d["node"]
    ok, _, msg = _verify(proj)
    assert ok, msg
    tel = telemetry.derive(proj)
    assert tel["coverage"]["ok"], tel["coverage"]
    assert tel["coverage"]["orphan_events"] == []      # every engine-written event links to a derived span
    decision = next(d for d in evals if d["name"] == "vpg.human_decision")
    assert decision["evidence_tag"] == "observed" and decision["evaluator"] == "Wei"


def test_default_runs_write_no_new_events_but_still_derive_full_coverage(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    _run_to_end(proj, human_b=True)
    events = list(Ledger(proj / ".vpg/ledger.jsonl", read_only=True).events())
    assert not [e for e in events if e["type"] in ("evaluation", "direct_run", "provisional_expired")]
    tel = telemetry.derive(proj)
    cov = tel["coverage"]
    assert cov["ok"] and cov["nodes_run"] == cov["nodes_with_evidence"] > 0 and cov["gates"] == cov["gate_events"]
    names = {s["name"].split(" ")[0] for s in tel["spans"]}
    assert {"vpg.run", "invoke_agent", "execute_tool", "vpg.await_human"} <= names
    agent = next(s for s in tel["spans"] if s["name"].startswith("invoke_agent"))
    assert agent["attributes"]["gen_ai.operation.name"] == "invoke_agent" and agent["attributes"]["gen_ai.provider.name"] == "vpg"
    assert re.fullmatch(r"[0-9a-f]{32}", tel["trace_id"]) and all(re.fullmatch(r"[0-9a-f]{16}", s["span_id"]) for s in tel["spans"])
    reviews = [e for e in tel["evaluations"] if e["name"].startswith("vpg.review.")]
    assert reviews and {e["evidence_tag"] for e in reviews} <= {"observed", "reported"}


def test_telemetry_is_deterministic_and_read_only(tmp_path):
    proj = make_project(tmp_path)
    _run_to_end(proj)
    before = {p: p.read_bytes() for p in (proj / ".vpg").rglob("*") if p.is_file()}
    a, b = telemetry.derive(proj), telemetry.derive(proj)
    assert a == b
    assert {p: p.read_bytes() for p in (proj / ".vpg").rglob("*") if p.is_file()} == before


def test_otlp_export_parents_evaluations_on_their_spans(tmp_path):
    proj = make_project(tmp_path)
    _run_to_end(proj)
    tel = telemetry.derive(proj)
    otlp = telemetry.to_otlp(tel, "t")
    spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == len(tel["spans"])
    events = [e for s in spans for e in s["events"]]
    assert events and all(e["name"] == "gen_ai.evaluation.result" for e in events)
    keys = {a["key"] for e in events for a in e["attributes"]}
    assert {"gen_ai.evaluation.name", "gen_ai.evaluation.score.label", "vpg.evidence"} <= keys
    assert all(int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"]) for s in spans)


def test_cli_telemetry_write_and_check(tmp_path, capsys):
    proj = make_project(tmp_path)
    _run_to_end(proj)
    assert cli.main(["telemetry", str(proj), "--check"]) == 0
    assert not (proj / ".vpg/telemetry").exists()       # read-only unless --write
    assert cli.main(["telemetry", str(proj), "--write", "--otlp", str(tmp_path / "otlp.json")]) == 0
    files = {p.name for p in (proj / ".vpg/telemetry").iterdir()}
    assert files == {"spans.jsonl", "evaluations.jsonl", "summary.json"}
    summary = json.loads((proj / ".vpg/telemetry/summary.json").read_text())
    assert summary["schema"] == telemetry.SCHEMA and summary["coverage"]["ok"]
    assert json.loads((tmp_path / "otlp.json").read_text())["resourceSpans"]
    ok, _, msg = _verify(proj)
    assert ok, msg


# ------------------------------------------------------------------------------------------------ direct runs
def test_direct_runs_are_recorded_never_registered(tmp_path, capsys):
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)                                   # waits at the human gate, as the motivating run did
    assert eng.rt("b_plan").status == S.WAITING_HUMAN
    out = proj / "candidates/v2/film.txt"
    out.parent.mkdir(parents=True)
    cmd = f"{sys.executable} -c \"import os, pathlib; pathlib.Path('candidates/v2/film.txt').write_text(os.environ['VPG_DIRECT_RUN'])\""
    rc = cli.main(["direct-run", str(proj), "d_build", "--actor", "agent:builder", "--reason", "the gate waited",
                   "--label", "v2", "--output", "film=candidates/v2/film.txt", "--run", cmd])
    assert rc == 0, capsys.readouterr()
    assert out.read_text() == "DR-001"
    eng = Engine(proj, executor=FnExecutor())
    dr = eng.state.history.direct_runs[-1]
    assert dr.id == "DR-001" and dr.exit_code == 0 and dr.outputs["film"]["path"] == "candidates/v2/film.txt"
    assert (proj / dr.log).is_file() and dr.label == "v2"
    assert not [a for a in eng.state.artifacts.values() if a.path.startswith("candidates/")]   # never a governed artifact
    assert any("direct run" in w and "never judged" in w for w in eng.status()["warnings"])
    ev = list(Ledger(proj / ".vpg/ledger.jsonl", read_only=True).events("direct_run"))[-1]["data"]
    assert ev["governed"] is False and ev["outputs"]["film"]["sha256"] and ev["actor"] == "agent:builder"
    ok, _, msg = _verify(proj)
    assert ok, msg
    tel = telemetry.derive(proj)
    assert any(s["name"] == "vpg.direct_run d_build" for s in tel["spans"])
    assert any(e["name"] == "vpg.direct_run" and e["gen_ai.evaluation.score.label"] == "ungoverned" for e in tel["evaluations"])
    eng.decide("b_plan", "approved", actor="Wei", reason="go on")   # the governed path still works afterwards
    eng.run(max_steps=300)
    assert eng.state.runtime.status.value == "RUN_PASS"


def test_a_rollback_keeps_direct_runs_and_numbers_never_repeat(tmp_path):
    proj = make_project(tmp_path, human_b=True)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    first = eng.state.history.checkpoints[0].seq
    cmd = [sys.executable, "-c", "print('first build')"]
    eng.record_direct_run("d_build", "agent:builder", "the gate waited", command=cmd)
    log1 = (proj / ".vpg/direct-runs/DR-001.log").read_text()
    eng.rollback(first, actor="Wei")
    assert [d.id for d in eng.state.history.direct_runs] == ["DR-001"]      # a build outside the graph stays on record
    assert any("direct run" in w for w in eng.status()["warnings"])
    eng.record_direct_run("d_build", "agent:builder", "again", command=[sys.executable, "-c", "print('second build')"])
    assert [d.id for d in eng.state.history.direct_runs] == ["DR-001", "DR-002"]
    assert (proj / ".vpg/direct-runs/DR-001.log").read_text() == log1 and "second build" in (proj / ".vpg/direct-runs/DR-002.log").read_text()
    ids = [e["data"]["id"] for e in Ledger(proj / ".vpg/ledger.jsonl", read_only=True).events("direct_run")]
    assert ids == ["DR-001", "DR-002"]


def test_a_decision_is_observed_only_when_a_named_human_made_it():
    tag = telemetry.decision_tag
    assert tag(False, False) == "observed"                                  # the human's own act
    assert tag(False, True, human=False) == "reported"                      # relayed: the human's instruction, quoted
    assert tag(True, True, human=False) == "assumed"                        # provisional
    assert tag(False, False, human=True, grant="G-001") == "assumed"        # under a delegation grant
    assert tag(False, False, human=False) == "assumed"                      # a dry-run auto-approval
    ev = telemetry.payload_decision("r", "n03", 1, "approved", "dry-run-auto", "", False, False, {}, human=False)
    assert ev["evidence_tag"] == "assumed" and ev["vpg.human"] is False
    assert ev["gen_ai.evaluation.explanation"].startswith("not a human's decision: recorded by dry-run-auto")


@pytest.mark.parametrize("kwargs, match", [
    ({"actor": "", "reason": "x"}, "--actor"),
    ({"actor": "a", "reason": " "}, "--reason"),
    ({"actor": "a", "reason": "x", "outputs": {"f": "/etc/hosts"}}, "outside the project"),
    ({"actor": "a", "reason": "x", "outputs": {"f": "missing.txt"}}, "not a file"),
])
def test_direct_run_refusals(tmp_path, kwargs, match):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=5)
    with pytest.raises(EngineError, match=match):
        eng.record_direct_run("d_build", **kwargs)
    with pytest.raises(EngineError, match="unknown node"):
        eng.record_direct_run("nope", "a", "x")


# ------------------------------------------------------------------------------------------------ provisional approvals
def _provisional_project(tmp_path, monkeypatch, expiry_h=None):
    import vpg.engine as ve
    proj = make_project(tmp_path, human_b=True, lock_b=("design",))
    autonomy = {"mode": "provisional", "principal": "Wei", **({"provisional_expiry_h": expiry_h} if expiry_h else {})}
    _cfg(proj, humans=["Wei"], privacy={"names": "none", "reason": "synthetic"},
         reviews={"binding": "require", "independence": "require"},
         loop_budget={"on_exhaust": "extend_if_converging"}, autonomy=autonomy)
    monkeypatch.setattr(ve, "PROVISIONAL_GATES", {"b_plan"})
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="agent:runner", on_behalf_of="Wei", instruction="go ahead while I am away",
               reason="provisional", provisional=True)
    return proj, eng


def test_provisional_approvals_are_tagged_assumed(tmp_path, monkeypatch):
    proj, eng = _provisional_project(tmp_path, monkeypatch)
    a = eng.state.history.approvals[-1]
    assert a.provisional and a.evidence == "assumed" and a.expires_at == ""      # no expiry unless the project sets one
    data = list(Ledger(proj / ".vpg/ledger.jsonl").events("decision"))[-1]["data"]
    assert data["evidence"] == "assumed" and "expires_at" not in data
    assert not any("assumed, not decided" in w for w in eng.status()["warnings"])   # unchanged without an expiry


def test_an_expired_provisional_approval_is_never_silent(tmp_path, monkeypatch):
    import vpg.engine as ve
    proj, eng = _provisional_project(tmp_path, monkeypatch, expiry_h=24)
    a = eng.state.history.approvals[-1]
    expires = datetime.fromisoformat(a.expires_at)
    assert timedelta(hours=23.9) < expires - datetime.fromisoformat(a.at) <= timedelta(hours=24.1)
    assert any("assumed, not decided" in w and "expires" in w for w in eng.status()["warnings"])
    later = (expires + timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(ve, "now", lambda: later)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=1)
    eng.run(max_steps=1)                                    # noted once, not on every run
    expired = list(Ledger(proj / ".vpg/ledger.jsonl").events("provisional_expired"))
    assert len(expired) == 1 and expired[0]["data"]["node"] == "b_plan" and expired[0]["data"]["evidence"] == "assumed"
    assert any("EXPIRED" in w for w in eng.status()["warnings"])
    ok, _, msg = _verify(proj)
    assert ok, msg


# ------------------------------------------------------------------------------------------------ the data contract
def test_the_glass_box_data_contract_names_every_artifact():
    doc = (PKG / "docs/observability.md").read_text()
    for path in (".vpg/ledger.jsonl", ".vpg/state.json", ".vpg/telemetry/spans.jsonl", ".vpg/telemetry/evaluations.jsonl",
                 ".vpg/telemetry/summary.json", ".vpg/direct-runs/", "spec/GATES.yaml", "spec/DESIGN.tokens.json",
                 "benchmarks/registry.json", "schemas/benchmark-registry.schema.json", "schemas/measurements.schema.json",
                 "gen_ai.evaluation.result", "provisional_expired", "direct_run", "evidence_tag"):
        assert path in doc, path
