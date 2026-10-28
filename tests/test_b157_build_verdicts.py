from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import PKG
from tests.test_b2_review_regressions import _decision, _events, _project, _release_wait, _rows, _write
from vpg import scoring
from vpg.engine import Engine
from vpg.models import Review, Score, NodeStatus as S
from vpg.provenance import Ledger
from vpg.tools.verify import human_record
from vpg.util import now, read_json, sha256_file
from vpg.cli import main


def _verdict_rows(store: Path) -> list[dict]:
    return scoring.load_verdicts(store)


def _ledger_data(proj: Path, kind: str) -> list[dict]:
    return [e["data"] for e in _events(proj, kind)]


def _add_review_forecast(eng: Engine, *, version: int = 1, score: float = 5.0) -> None:
    scores = []
    for dim, spec in sorted(eng._review_dimension_specs().items()):
        if spec.get("level", "MUST") != "MUST":
            continue
        value = score if dim == "CRE-HOOK" else 4.0
        scores.append(Score(dimension=dim, score=value, threshold=4.0, level="MUST", passed=value >= 4.0))
    eng.state.evaluation.reviews.append(Review(
        id=f"rev-synthetic-v{version}",
        node="n16_accessibility_review",
        version=version,
        reviewer="agent:synthetic-reviewer",
        rubric_ids=["creative"],
        scores=scores,
        verdict="pass" if score >= 4.0 else "fail",
        created_at=now(),
    ))


def _add_fork_copy(eng: Engine, proj: Path, source_id: str, fork_id: str, *, version: int, node: str = "n06_creative_direction",
                   attempt: int | None = None) -> Path:
    source = proj / eng.state.production.forks_ref[source_id]
    fork = read_json(source)
    fork["fork_id"] = fork_id
    fork["version"] = version
    fork["node"] = node
    dest = source.with_name(f"{fork_id}.fork.json")
    dest.write_text(json.dumps(fork))
    eng.state.production.forks_ref[fork_id] = str(dest.relative_to(proj))
    source_art = next(a for a in eng.state.artifacts.values() if a.key == "fork" and a.item == source_id)
    att = source_art.attempt if attempt is None else attempt
    art_id = f"fork@{node}#a{att}[{fork_id}]"
    eng.state.artifacts[art_id] = source_art.model_copy(update={
        "id": art_id, "path": str(dest.relative_to(proj)), "sha256": sha256_file(dest),
        "bytes": dest.stat().st_size, "node": node, "attempt": att, "item": fork_id,
    })
    return dest


def _file_hashes(root: Path) -> dict[str, str]:
    import hashlib
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_rejection_without_fork_ranking_writes_build_verdict_not_decision_row(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="The build is not coherent yet.")
    d["forks"] = {}

    before_decisions = (store / "decisions.jsonl").read_bytes() if (store / "decisions.jsonl").exists() else b""
    eng.record_review(_write(proj, d), actor="Wei")

    after_decisions = (store / "decisions.jsonl").read_bytes() if (store / "decisions.jsonl").exists() else b""
    assert after_decisions == before_decisions                  # verdicts never enter the fork-choice store
    rows = _verdict_rows(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "build_verdict" and row["schema"] == 1
    assert row["outcome"] == "changes_requested"
    assert row["words"] == "The build is not coherent yet."
    assert row["forecast"]["CRE-HOOK"] == 5.0 and row["forecast"]["NAR-CLARITY"] == 4.0
    assert row["forecast_pass"] is True
    assert row["verdict_ratings"] == {}
    assert row["forks_ranked"] == 0
    assert row["review_id"].startswith(f"{eng.state.runtime.run_id}:v1:n23_final_verification:a1")

    events = _ledger_data(proj, "build_verdict")
    assert len(events) == 1
    assert events[0]["outcome"] == "changes_requested"
    assert "words" not in events[0]
    assert events[0]["forecast_pass"] is True and events[0]["forks_ranked"] == 0


def test_approval_writes_build_verdict(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)

    eng.record_review(_write(proj, _decision(ctx, release="approve", feedback="Approved for release.")), actor="Wei")

    rows = _verdict_rows(store)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "approved"
    assert rows[0]["words"] == "Approved for release."
    assert rows[0]["forks_ranked"] == 1


@pytest.mark.parametrize("ratings", [{"UNKNOWN": 4}, {"CRE-HOOK": 0}, {"CRE-HOOK": 6}])
def test_invalid_verdict_ratings_refuse_atomically(tmp_path, monkeypatch, ratings):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="Not yet.")
    d["forks"] = {}
    d["verdict_ratings"] = ratings
    state_before = (proj / ".vpg/state.json").read_bytes()
    ledger_before = (proj / ".vpg/ledger.jsonl").read_bytes()
    decisions_before = (store / "decisions.jsonl").read_bytes() if (store / "decisions.jsonl").exists() else b""
    verdicts_before = (store / "verdicts.jsonl").read_bytes() if (store / "verdicts.jsonl").exists() else b""

    with pytest.raises(Exception, match="verdict_ratings"):
        eng.record_review(_write(proj, d), actor="Wei")

    assert (proj / ".vpg/state.json").read_bytes() == state_before
    assert (proj / ".vpg/ledger.jsonl").read_bytes() == ledger_before
    assert ((store / "decisions.jsonl").read_bytes() if (store / "decisions.jsonl").exists() else b"") == decisions_before
    assert ((store / "verdicts.jsonl").read_bytes() if (store / "verdicts.jsonl").exists() else b"") == verdicts_before


def test_relayed_non_principal_records_ledger_but_no_verdict_row(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store, humans=("Wei", "Synthetic Principal"), principal="Wei")
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)

    eng.record_review(_write(proj, _decision(ctx, release="changes", feedback="Relay says revise.")), actor="agent:relay",
                      on_behalf_of="Synthetic Principal", instruction="request changes", instruction_at=now())

    assert _verdict_rows(store) == []
    events = _ledger_data(proj, "build_verdict")
    assert len(events) == 1
    assert events[0]["principal"] == "Synthetic Principal"
    assert events[0]["learned"] is False


def test_undone_review_record_removes_verdict_row(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    orig_pass = Engine._pass

    def fail_after_rows(self, node, rt, outcome, data):
        raise RuntimeError("synthetic failure after verdict row")

    monkeypatch.setattr(Engine, "_pass", fail_after_rows)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        eng.record_review(_write(proj, _decision(ctx, release="approve", feedback="Approved.")), actor="Wei")
    assert _verdict_rows(store) == []

    monkeypatch.setattr(Engine, "_pass", orig_pass)
    Engine(proj).record_review(_write(proj, _decision(ctx, release="approve", feedback="Approved."), "second.json"), actor="Wei")
    assert len(_verdict_rows(store)) == 1


def test_calibrate_reports_reviewer_false_pass_and_bias(tmp_path, monkeypatch, capsys):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="Scores were too generous.")
    d["forks"] = {}
    d["verdict_ratings"] = {"CRE-HOOK": 2, "NAR-CLARITY": 3}
    eng.record_review(_write(proj, d), actor="Wei")

    assert main(["calibrate", str(proj), "--principal", "Wei"]) == 0

    out = json.loads(capsys.readouterr().out)
    rc = out["reviewer_calibration"]
    assert rc["n_verdicts"] == 1
    assert rc["n_changes_requested"] == 1
    assert rc["false_pass"] == 1
    assert rc["false_pass_rate"] == 1.0
    assert rc["per_dimension"]["CRE-HOOK"]["mean_forecast_changes"] == 5.0
    assert rc["per_dimension"]["CRE-HOOK"]["bias_vs_principal"] == 3.0


def test_pdg_score_fit_report_ignore_verdicts_jsonl(tmp_path):
    data = tmp_path / "knowledge/preferences/synthetic"
    fork = {
        "fork_id": "F-001",
        "fork_type": "refinement",
        "dims": [{"id": "DES-TASTE", "measured": False}, {"id": "REF-PARITY", "measured": True}],
        "candidates": [
            {"id": "A", "scores": {"DES-TASTE": 4, "REF-PARITY": 5}, "passes_rubric": True},
            {"id": "B", "scores": {"DES-TASTE": 5, "REF-PARITY": 3}, "passes_rubric": True},
        ],
    }
    fork_path = tmp_path / "F-001.json"
    fork_path.write_text(json.dumps(fork))
    script = PKG / "scoring/vpg_score.py"

    assert subprocess.run([sys.executable, str(script), "--data", str(data), "score", str(fork_path)],
                          cwd=tmp_path, text=True, capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, str(script), "--data", str(data), "record", str(fork_path),
                           "--rank", "B,A", "--why", "Synthetic preference"],
                          cwd=tmp_path, text=True, capture_output=True).returncode == 0
    scoring.append_verdict_atomic(data, {"type": "build_verdict", "schema": 1, "principal": "Synthetic", "run": "r1",
                                         "version": 1, "review_id": "r1:v1:n23:a1", "at": now(),
                                         "outcome": "changes_requested", "words": "Synthetic rejection.",
                                         "forecast": {"DES-TASTE": 5.0}, "forecast_pass": True,
                                         "verdict_ratings": {"DES-TASTE": 2}, "forks_ranked": 0})
    fit = subprocess.run([sys.executable, str(script), "--data", str(data), "fit"], cwd=tmp_path, text=True, capture_output=True)
    assert fit.returncode == 0, fit.stderr
    report = subprocess.run([sys.executable, str(script), "--data", str(data), "report"], cwd=tmp_path, text=True, capture_output=True)
    assert report.returncode == 0, report.stderr
    out = json.loads(report.stdout)
    assert out["metrics"]["n"] == 1
    assert "verdicts.jsonl" in {p.name for p in data.iterdir()}


def test_v2_approval_must_answer_unanswered_v1_fork(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx1 = _release_wait(eng, proj, attempt=1, version=1)
    reject = _decision(ctx1, release="changes", feedback="Needs another version.")
    reject["forks"] = {}
    eng.record_review(_write(proj, reject, "reject-v1.json"), actor="Wei")

    eng = Engine(proj)
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None, _s=store: _s)
    _add_review_forecast(eng, version=2)
    ctx2 = _release_wait(eng, proj, attempt=2, version=2)
    approve = _decision(ctx2, release="approve", feedback="Now works.")
    approve["forks"] = {}
    with pytest.raises(Exception, match="missing \\['F-001'\\]"):
        eng.record_review(_write(proj, approve, "approve-missing.json"), actor="Wei")

    approve["forks"] = {"F-001": read_json(ctx2.workdir / "decision.template.json")["forks"]["F-001"]}
    approve["forks"]["F-001"]["ranking"] = ["C1", "C2", "C3"]
    approve["forks"]["F-001"]["ratings"] = {}
    approve["forks"]["F-001"]["why"] = "Chosen after version two."
    eng.record_review(_write(proj, approve, "approve-answered.json"), actor="Wei")
    assert eng.rt("n23_final_verification").status == S.STEP_PASS


def test_superseded_fork_is_not_asked_but_successor_is_across_versions(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    f1_path = proj / eng.state.production.forks_ref["F-001"]
    f2 = read_json(f1_path)
    f2["fork_id"] = "F-002"
    f2["version"] = 2
    f2_path = f1_path.with_name("F-002.json")
    f2_path.write_text(json.dumps(f2))
    eng.state.production.forks_ref["F-002"] = str(f2_path.relative_to(proj))
    _add_review_forecast(eng, version=3)
    ctx = _release_wait(eng, proj, attempt=3, version=3)

    forks = read_json(ctx.workdir / "decision.template.json")["forks"]
    assert set(forks) == {"F-002"}


def test_forecast_pass_none_and_unscored_must_when_reviews_missing(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="No reviewer forecast yet.")
    d["forks"] = {}

    eng.record_review(_write(proj, d), actor="Wei")

    row = _verdict_rows(store)[0]
    assert row["forecast"] == {}
    assert row["forecast_pass"] is None
    assert "CRE-HOOK" in row["unscored_must"]
    assert main(["calibrate", str(proj), "--principal", "Wei"]) == 0


def test_forecast_uses_minimum_latest_review_score_per_dimension(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    eng.state.evaluation.reviews.extend([
        Review(id="rev-low", node="n20_final_quality_review", version=1, reviewer="agent:q", rubric_ids=["creative"],
               scores=[Score(dimension="CRE-HOOK", score=2.0, threshold=4.0, level="MUST", passed=False)],
               verdict="fail", created_at=now()),
        Review(id="rev-high", node="n16_accessibility_review", version=1, reviewer="agent:a", rubric_ids=["creative"],
               scores=[Score(dimension="CRE-HOOK", score=5.0, threshold=4.0, level="MUST", passed=True)],
               verdict="pass", created_at=now()),
    ])
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="Pronunciation-like disagreement.")
    d["forks"] = {}

    eng.record_review(_write(proj, d), actor="Wei")

    row = _verdict_rows(store)[0]
    assert row["forecast"]["CRE-HOOK"] == 2.0
    assert row["forecast_pass"] is False


def test_forecast_pass_uses_score_passed_flag_not_threshold(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    eng.state.evaluation.reviews.append(Review(
        id="rev-overridden", node="n16_accessibility_review", version=1, reviewer="agent:a", rubric_ids=["creative"],
        scores=[Score(dimension="CRE-HOOK", score=5.0, threshold=4.0, level="MUST", passed=False)],
        verdict="fail", created_at=now()))
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="Tool override should fail.")
    d["forks"] = {}

    eng.record_review(_write(proj, d), actor="Wei")

    row = _verdict_rows(store)[0]
    assert row["forecast"] == {"CRE-HOOK": 5.0}
    assert row["forecast_pass"] is False


def test_build_verdict_is_not_a_human_record_row_or_shared_reason(tmp_path):
    events = [
        {"type": "decision", "ts": "2026-10-02T00:00:00Z",
         "data": {"node": "n23_final_verification", "decision": "changes_requested", "actor": "Wei",
                  "reason": "Same release words.", "human": True}},
        {"type": "build_verdict", "ts": "2026-10-02T00:00:01Z",
         "data": {"actor": "Wei", "outcome": "changes_requested", "words": "Same release words."}},
    ]

    rows = human_record(events)

    assert [r["action"] for r in rows] == ["decision"]
    assert "shared" not in rows[0]["codes"]


def test_verdict_words_are_masked_in_store(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    listed = "Jordan Sample"
    proj, eng = _project(tmp_path, monkeypatch, store=store, privacy_names=[listed])
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback=f"{listed} appears in the ending.")
    d["forks"] = {}

    eng.record_review(_write(proj, d), actor="Wei")

    words = _verdict_rows(store)[0]["words"]
    assert listed not in words
    assert "[list entry #1]" in words


@pytest.mark.parametrize("ratings", [{"CON-FIT": 5}, {"CRE-HOOK": 3.5}, {"LEG-SIZE": 5}])
def test_verdict_ratings_accept_only_review_dimensions_and_integer_scores(tmp_path, monkeypatch, ratings):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="Invalid build-level rating.")
    d["forks"] = {}
    d["verdict_ratings"] = ratings
    state_before = (proj / ".vpg/state.json").read_bytes()
    ledger_before = (proj / ".vpg/ledger.jsonl").read_bytes()

    with pytest.raises(Exception, match="verdict_ratings"):
        eng.record_review(_write(proj, d), actor="Wei")

    assert (proj / ".vpg/state.json").read_bytes() == state_before
    assert (proj / ".vpg/ledger.jsonl").read_bytes() == ledger_before
    assert _verdict_rows(store) == []


def test_malformed_store_lines_do_not_block_review_record_and_are_reported(tmp_path, monkeypatch, capsys):
    store = tmp_path / "prefs"
    store.mkdir()
    (store / "decisions.jsonl").write_text("{not-json\n")
    (store / "verdicts.jsonl").write_text("{not-json\n")
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)

    eng.record_review(_write(proj, _decision(ctx, release="approve", feedback="Approved.")), actor="Wei")
    assert sum(1 for line in (store / "decisions.jsonl").read_text().splitlines() if '"fork_id": "F-001"' in line) == 1
    assert len(_verdict_rows(store)) == 1

    assert main(["calibrate", str(proj), "--principal", "Wei", "--reviewers"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped_lines"]["verdicts.jsonl"] == 1


def test_failed_review_undo_removes_verdicts_file_created_by_action(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)

    def fail_after_rows(self, node, rt, outcome, data):
        raise RuntimeError("synthetic failure after verdict write")

    monkeypatch.setattr(Engine, "_pass", fail_after_rows)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        eng.record_review(_write(proj, _decision(ctx, release="approve", feedback="Approved.")), actor="Wei")

    assert not (store / "verdicts.jsonl").exists()


def test_calibrate_reviewers_is_read_only(tmp_path, monkeypatch, capsys):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="Reviewers passed a bad build.")
    d["forks"] = {}
    eng.record_review(_write(proj, d), actor="Wei")
    before = sorted(p.relative_to(store).as_posix() for p in store.rglob("*") if p.is_file())

    assert main(["calibrate", str(proj), "--principal", "Wei", "--reviewers"]) == 0

    after = sorted(p.relative_to(store).as_posix() for p in store.rglob("*") if p.is_file())
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"reviewer_calibration", "skipped_lines"}
    assert before == after


def test_the_engine_ships_no_active_nested_ignore_rules():
    """The workspace forbids nested ignore layers (its TEST_GIT_013): exclusions live in the root policy, and any nested
    .gitignore the engine ships is a comment-only note, as the source store's is. Store lock files stay untracked. The
    workspace copy carries publication.json, which is never exported, so a checkout without it is the standalone
    repository, whose own top-level .gitignore is that repository's root policy."""
    standalone_root_policy = None if (PKG / "publication.json").exists() else PKG / ".gitignore"
    active = {}
    for p in PKG.rglob(".gitignore"):
        rel = p.relative_to(PKG)
        if {".venv", "node_modules", ".pytest_cache"} & set(rel.parts) or p == standalone_root_policy:
            continue
        active[str(rel)] = [x for x in p.read_text().splitlines() if x.strip() and not x.lstrip().startswith("#")]
    assert {k: v for k, v in active.items() if v} == {}


def test_an_approval_must_still_answer_every_fork(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="approve", feedback="Approved.")
    d["forks"] = {}
    state, ledger = (proj / ".vpg/state.json").read_bytes(), (proj / ".vpg/ledger.jsonl").read_bytes()
    with pytest.raises(Exception, match="missing"):
        eng.record_review(_write(proj, d), actor="Wei")
    assert (proj / ".vpg/state.json").read_bytes() == state and (proj / ".vpg/ledger.jsonl").read_bytes() == ledger
    assert _verdict_rows(store) == []


def test_same_node_same_version_offers_every_unanswered_fork(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_fork_copy(eng, proj, "F-001", "F-002", version=1)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    template_forks = read_json(ctx.workdir / "decision.template.json")["forks"]
    assert set(template_forks) == {"F-001", "F-002"}

    d = _decision(ctx, release="approve", feedback="Approved.")
    d["forks"] = {"F-001": d["forks"]["F-001"]}
    with pytest.raises(Exception, match="missing \\['F-002'\\]"):
        eng.record_review(_write(proj, d), actor="Wei")


def test_answered_successor_does_not_resurrect_superseded_fork(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_fork_copy(eng, proj, "F-001", "F-002", version=2)
    _add_review_forecast(eng, version=2)
    ctx2 = _release_wait(eng, proj, attempt=2, version=2)
    d2 = _decision(ctx2, release="approve", feedback="Answer the successor.")
    d2["forks"] = {"F-002": d2["forks"]["F-002"]}
    eng.record_review(_write(proj, d2), actor="Wei")

    eng = Engine(proj)
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None, _s=store: _s)
    _add_review_forecast(eng, version=3)
    ctx3 = _release_wait(eng, proj, attempt=3, version=3)

    assert read_json(ctx3.workdir / "decision.template.json")["forks"] == {}
    d3 = _decision(ctx3, release="approve", feedback="No old fork should return.")
    d3["forks"] = {}
    eng.record_review(_write(proj, d3), actor="Wei")
    assert eng.rt("n23_final_verification").status == S.STEP_PASS


def test_removed_node_fork_is_not_required_forever(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    old = proj / eng.state.production.forks_ref["F-001"]
    fork = read_json(old)
    fork["node"] = "n99_removed"
    old.write_text(json.dumps(fork))
    eng.state.artifacts = {k: v.model_copy(update={"node": "n99_removed"}) if v.key == "fork" else v
                           for k, v in eng.state.artifacts.items()}
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)

    assert read_json(ctx.workdir / "decision.template.json")["forks"] == {}
    d = _decision(ctx, release="approve", feedback="Removed node has no current fork decision.")
    d["forks"] = {}
    eng.record_review(_write(proj, d), actor="Wei")
    assert eng.rt("n23_final_verification").status == S.STEP_PASS


def test_node_produced_stale_attempt_fork_is_not_offered(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    eng.rt("n06_creative_direction").attempts = 2
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)

    assert read_json(ctx.workdir / "decision.template.json")["forks"] == {}


def test_cli_recorded_attempt_zero_fork_stays_offered_across_versions(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    for key, art in list(eng.state.artifacts.items()):
        if art.key == "fork" and art.item == "F-001":
            eng.state.artifacts[key] = art.model_copy(update={"attempt": 0})
    _add_review_forecast(eng)
    ctx1 = _release_wait(eng, proj, attempt=1, version=1)
    d1 = _decision(ctx1, release="changes", feedback="Leave CLI fork open.")
    d1["forks"] = {}
    eng.record_review(_write(proj, d1), actor="Wei")

    eng = Engine(proj)
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None, _s=store: _s)
    _add_review_forecast(eng, version=2)
    ctx2 = _release_wait(eng, proj, attempt=2, version=2)
    assert set(read_json(ctx2.workdir / "decision.template.json")["forks"]) == {"F-001"}


def test_reoffered_fork_choice_is_not_blind(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx1 = _release_wait(eng, proj, attempt=1, version=1)
    d1 = _decision(ctx1, release="changes", feedback="Show me another version.")
    d1["forks"] = {}
    eng.record_review(_write(proj, d1), actor="Wei")

    eng = Engine(proj)
    monkeypatch.setattr(Engine, "_preference_store", lambda self, principal=None, _s=store: _s)
    _add_review_forecast(eng, version=2)
    ctx2 = _release_wait(eng, proj, attempt=2, version=2)
    d2 = _decision(ctx2, release="approve", feedback="Now rank it.")
    eng.record_review(_write(proj, d2), actor="Wei")

    row = scoring.load_decisions(store)[0]
    metrics, rows = scoring.evaluate(scoring.load_decisions(store))
    assert row["blind"] is False
    assert metrics["n_blind"] == 0
    assert rows[0]["blind"] is False


def test_calibrate_reviewers_does_not_touch_project_state_or_resume(tmp_path, monkeypatch, capsys):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    d = _decision(ctx, release="changes", feedback="Reviewer report only.")
    d["forks"] = {}
    eng.record_review(_write(proj, d), actor="Wei")
    eng = Engine(proj)
    eng.rt("n06_creative_direction").status = S.RUNNING
    eng._save("test-running-node")
    before_vpg = _file_hashes(proj / ".vpg")
    before_store = _file_hashes(store)

    assert main(["calibrate", str(proj), "--principal", "Wei", "--reviewers"]) == 0

    assert json.loads(capsys.readouterr().out)["reviewer_calibration"]["n_verdicts"] == 1
    assert _file_hashes(proj / ".vpg") == before_vpg
    assert _file_hashes(store) == before_store
    assert Engine(proj, read_only=True).rt("n06_creative_direction").status == S.RUNNING


def test_pdg_fit_still_fails_on_corrupt_decisions_jsonl(tmp_path):
    data = tmp_path / "prefs"
    data.mkdir()
    (data / "decisions.jsonl").write_text("{not-json\n")

    with pytest.raises(json.JSONDecodeError):
        scoring.fit_store(data)


def test_remove_review_rows_preserves_unparseable_lines(tmp_path):
    data = tmp_path / "prefs"
    data.mkdir()
    (data / "decisions.jsonl").write_text('{not-json\n{"review_id":"R","fork_id":"F-001"}\n')
    (data / "verdicts.jsonl").write_text('{not-json\n{"review_id":"R","type":"build_verdict"}\n')

    scoring.remove_review_rows(data, "R")

    assert (data / "decisions.jsonl").read_text() == "{not-json\n"
    assert (data / "verdicts.jsonl").read_text() == "{not-json\n"


def test_the_packet_and_the_validator_agree_after_a_node_leaves_the_graph(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    old = proj / eng.state.production.forks_ref["F-001"]
    fork = read_json(old)
    fork["node"] = "n99_removed"
    old.write_text(json.dumps(fork))
    eng.state.artifacts = {k: v.model_copy(update={"node": "n99_removed"}) if v.key == "fork" else v
                           for k, v in eng.state.artifacts.items()}
    eng.state.runtime.nodes["n99_removed"] = eng.rt("n06_creative_direction").model_copy()   # runtime entry outlives the graph
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)
    packet_forks = set(read_json(ctx.workdir / "decision.template.json")["forks"])
    assert packet_forks == set(eng._load_review_forks()) == set()                  # the packet asks what the validator asks
    del eng.state.runtime.nodes["n99_removed"]   # a stale runtime entry also breaks status() (pre-existing, out of scope)
    d = _decision(ctx, release="approve", feedback="Nothing to rank.")
    eng.record_review(_write(proj, d), actor="Wei")
    assert eng.rt("n23_final_verification").status == S.STEP_PASS


def _wait_again_and_rank(eng, proj, store, *, attempt: int):
    d2_ctx = _release_wait(eng, proj, attempt=attempt, version=2)
    eng.record_review(_write(proj, _decision(d2_ctx, release="approve", feedback="Ranked now.")), actor="Wei")
    return scoring.load_decisions(store)[0]


def test_a_fork_seen_before_a_rollback_is_not_blind(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    _release_wait(eng, proj, attempt=1, version=1)                     # the packet (and its reveal) reached the principal
    for aid, art in list(eng.state.artifacts.items()):                 # a rollback restores artifacts, not the ledger
        if art.key in ("decision_template", "review_packet", "verification"):
            del eng.state.artifacts[aid]
    eng.rt("n23_final_verification").attempts = 0
    _add_review_forecast(eng, version=2)
    row = _wait_again_and_rank(eng, proj, store, attempt=1)            # the same attempt number, a new waiting period
    assert row["blind"] is False


def test_a_release_attempt_that_never_waited_does_not_unblind_a_fork(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_review_forecast(eng)
    with monkeypatch.context() as m:                                    # attempt 1 failed before reaching the principal
        m.setattr(Engine, "_note_review_forks_offered", lambda self, node_id: None, raising=False)
        _release_wait(eng, proj, attempt=1, version=1)
    _add_review_forecast(eng, version=2)
    row = _wait_again_and_rank(eng, proj, store, attempt=2)
    assert row["blind"] is True


def test_one_unreadable_fork_does_not_empty_the_packet(tmp_path, monkeypatch):
    store = tmp_path / "prefs"
    proj, eng = _project(tmp_path, monkeypatch, store=store)
    _add_fork_copy(eng, proj, "F-001", "F-002", version=1)
    (proj / eng.state.production.forks_ref["F-002"]).write_text("{not json")
    _add_review_forecast(eng)
    ctx = _release_wait(eng, proj)                                    # the packet skips only the unreadable fork
    template = read_json(ctx.workdir / "decision.template.json")
    assert set(template["forks"]) == {"F-001"}
    assert "F-002" in (ctx.workdir / "review-packet.md").read_text()            # named in the warnings to acknowledge
