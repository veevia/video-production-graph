"""Governed learning: lifecycle gates, human-only approval, protected scopes, versions and the audit trail."""
from __future__ import annotations

import json
import shutil

import pytest

from tests.conftest import PKG
from vpg.memory import ALLOWED, GovernanceError, RuleBase, apply_proposal, enforcement


@pytest.fixture
def rules(tmp_path):
    p = tmp_path / "rules.json"
    shutil.copy2(PKG / "knowledge/rules.json", p)
    return p


def _candidate(rb, rid="R-TEST-001", scope="production_default", check=None, evidence=("R07-v10",)):
    return rb.add_candidate({"id": rid, "statement": "test rule", "category": "craft_quality", "scope": scope,
                             "check": check or {"type": "tool", "fn": "legibility:check"}, "evidence": list(evidence)}, by="agent:learning")


def test_lifecycle_requires_evidence(rules):
    rb = RuleBase(rules)
    _candidate(rb)
    with pytest.raises(GovernanceError, match="≥2 distinct evidence"):
        rb.transition("R-TEST-001", "observed_again", by="agent:learning", evidence=[])
    rb.transition("R-TEST-001", "observed_again", by="agent:learning", evidence=["R07-v13"])
    with pytest.raises(GovernanceError, match="validation evidence"):
        rb.transition("R-TEST-001", "validated", by="agent:learning", evidence=[])
    rb.transition("R-TEST-001", "validated", by="agent:learning", evidence=["tests/test_tools.py::test_density_limit"])
    with pytest.raises(GovernanceError, match="not an allowed lifecycle step"):
        rb.transition("R-TEST-001", "monitored", by="Wei", evidence=["x"], human=True)


def test_only_humans_approve(rules):
    rb = RuleBase(rules)
    _candidate(rb, evidence=("R07-v10", "R07-v13"))
    rb.transition("R-TEST-001", "observed_again", by="agent:learning", evidence=["R07-v13"])
    rb.transition("R-TEST-001", "validated", by="agent:learning", evidence=["run:x"])
    for by, human in (("agent:critic", True), ("Wei", False)):
        with pytest.raises(GovernanceError, match="named human"):
            rb.transition("R-TEST-001", "approved", by=by, evidence=["ok"], human=human)
    rb.transition("R-TEST-001", "approved", by="Wei", evidence=["review 2026-09-26"], human=True)
    with pytest.raises(GovernanceError, match="must name the rubric"):
        rb.transition("R-TEST-001", "adopted", by="Wei", evidence=["x"], human=True)
    rb.transition("R-TEST-001", "adopted", by="Wei", evidence=["x"], human=True, adopted_in="rubrics/accessibility.yaml")
    assert enforcement(rb.get("R-TEST-001")) == "enforced"
    with pytest.raises(GovernanceError, match="production observation"):
        rb.transition("R-TEST-001", "monitored", by="Wei", evidence=["x"], human=True)
    rb.observe("R-TEST-001", "run-1", fired=True, helped=True)
    rb.transition("R-TEST-001", "monitored", by="Wei", evidence=["run-1"], human=True)
    for to in ("retired", "revised"):   # an agent cannot switch off or rewrite an enforced rule
        with pytest.raises(GovernanceError, match="only a named human"):
            rb.transition("R-TEST-001", to, by="agent:learning", evidence=["run-2"], reason="looked redundant", human=True)
    assert enforcement(rb.get("R-TEST-001")) == "enforced"
    rb.transition("R-TEST-001", "retired", by="Wei", evidence=["review 2026-09-27"], reason="superseded by R-TEST-002", human=True)


def test_protected_scope_refused(rules):
    rb = RuleBase(rules)
    _candidate(rb, rid="R-TEST-PT", scope="product_truth", evidence=("R07-v10", "R07-v13"))
    rb.transition("R-TEST-PT", "observed_again", by="agent", evidence=["R07-v13"])
    rb.transition("R-TEST-PT", "validated", by="agent", evidence=["t"])
    with pytest.raises(GovernanceError, match="protected"):
        rb.transition("R-TEST-PT", "approved", by="Wei", evidence=["x"], human=True)


def test_revision_preserves_previous_version(rules):
    rb = RuleBase(rules)
    rid = "R-LEG-001"
    before = json.loads(json.dumps(rb.get(rid)))
    with pytest.raises(GovernanceError, match="reason and evidence"):
        rb.transition(rid, "revised", by="Wei", evidence=[], human=True)
    rb.transition(rid, "revised", by="Wei", evidence=["calibration round 1"], human=True, reason="Wei wants 24 px")
    r = rb.get(rid)
    assert r["previous_versions"][-1]["statement"] == before["statement"] and r["rule_version"] == 2
    assert r["lifecycle"]["state"] == "revised" and enforcement(r) == "inactive"


def test_changelog_appends_and_version_bumps(rules):
    rb = RuleBase(rules)
    v0 = rb.data["version"]
    _candidate(rb)
    rb2 = RuleBase(rules)
    assert rb2.data["version"] != v0
    log = (rules.parent / "rules-changelog.jsonl").read_text().splitlines()
    assert json.loads(log[-1])["rule"] == "R-TEST-001"
    assert (rules.parent / "history" / f"rules-v{v0}.json").exists()


def test_apply_proposal_requires_human(rules):
    proposal = {"run": "r1", "observations": [{"rule": "R-CAP-001", "fired": True, "helped": True, "defect": "D001"}],
                "candidates": [{"defect": "D002", "proposed_rule": {"id": "R-CAND-X", "statement": "s", "category": "craft_quality",
                                                                   "scope": "production_default", "check": {"type": "review", "never": ["x"]}, "evidence": ["r1:D002"]}}],
                "project_specific": []}
    for bad in ("agent:learning", "dry-run-auto", ""):
        with pytest.raises(GovernanceError):
            apply_proposal(proposal, approved_by=bad, rules_path=rules)
    assert apply_proposal(proposal, approved_by="Wei", rules_path=rules) == {"observations": 1, "candidates": 1}
    rb = RuleBase(rules)
    assert rb.get("R-CAND-X")["lifecycle"]["state"] == "candidate" and rb.get("R-CAP-001")["observations"][-1]["run"] == "r1"


def test_seed_rules_reference_existing_tests():
    import re
    tests = {f"{f.relative_to(PKG).as_posix()}::{m}" for f in (PKG / "tests").glob("test_*.py")
             for m in re.findall(r"^def (test_\w+)", f.read_text(), re.M)}
    rules = json.loads((PKG / "knowledge/rules.json").read_text())["rules"]
    for r in rules:
        t = r["check"].get("test")
        if t:
            assert t in tests, (r["id"], t)


def test_rule_histories_follow_allowed_transitions():
    rules = json.loads((PKG / "knowledge/rules.json").read_text())["rules"]
    for r in rules:
        states = [h["state"] for h in r["lifecycle"]["history"]]
        assert states and states[0] == "candidate", r["id"]
        for a, b in zip(states, states[1:]):
            assert b in ALLOWED[a], (r["id"], a, b)
        assert states[-1] == r["lifecycle"]["state"], r["id"]


def test_advisory_rules_downgrade_rubric_dimensions(project):
    """A dimension whose rule is not yet adopted is advisory (SHOULD), so its findings cannot block the gate."""
    from vpg.engine import Engine
    from tests.conftest import FnExecutor
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    ring = next(d for d in eng.rubrics["accessibility"]["dimensions"] if d["id"] == "LEG-RING")
    assert ring["level"] == "SHOULD" and "not yet adopted" in ring["advisory_reason"]      # R-UI-002 is validated, not adopted
