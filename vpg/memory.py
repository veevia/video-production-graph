"""Governed self-improvement: the cross-project rule base (knowledge/rules.json).

Lifecycle:  candidate → observed_again → validated → approved → adopted → monitored → revised | retired

Gates (enforced by `transition`):
  observed_again  ≥2 distinct evidence refs (different iterations or productions)
  validated       an executable check (tool) or ≥1 concrete never/always review case, plus validation evidence
                  (a test id or a run id that shows the check catching the failure)
  approved        a named human approver (agents cannot approve)
  adopted         already approved, plus the rubric or config path where the rule now lives
  monitored       already adopted, plus ≥1 production observation recorded after adoption
  revised         a reason and evidence; the prior rule version is preserved and the revision re-enters `validated`
  retired         a reason and evidence
Protected scopes (a project's objective, product truth, brand constraints and approved design principles) are
project controls. The learning loop may never adopt a rule into them; it can only propose a change request.
Every write bumps the rule-base version, archives the previous file and appends to the changelog.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .util import PKG_ROOT, is_human_name, now, read_json, sha256_file, write_json_atomic

LIFECYCLE = ["candidate", "observed_again", "validated", "approved", "adopted", "monitored", "revised", "retired"]
ALLOWED = {
    "candidate": {"observed_again", "retired"},
    "observed_again": {"validated", "retired"},
    "validated": {"approved", "retired"},
    "approved": {"adopted", "retired"},
    "adopted": {"monitored", "revised", "retired"},
    "monitored": {"revised", "retired"},
    "revised": {"validated", "retired"},
    "retired": set(),
}
PROTECTED_SCOPES = {"product_truth", "objective", "brand_constraints", "approved_design_principles"}
ENFORCED = {"adopted", "monitored"}
ADVISORY = {"validated", "approved"}
DEFAULT_RULES = PKG_ROOT / "knowledge" / "rules.json"


class GovernanceError(Exception):
    pass


def enforcement(rule: dict[str, Any]) -> str:
    state = rule["lifecycle"]["state"]
    return "enforced" if state in ENFORCED else "advisory" if state in ADVISORY else "inactive"


class RuleBase:
    def __init__(self, path: Path = DEFAULT_RULES):
        self.path = Path(path)
        self.data = read_json(self.path)
        self.history_dir = self.path.parent / "history"
        self.changelog = self.path.parent / "rules-changelog.jsonl"

    @property
    def rules(self) -> list[dict[str, Any]]:
        return self.data["rules"]

    def get(self, rule_id: str) -> dict[str, Any]:
        for r in self.rules:
            if r["id"] == rule_id:
                return r
        raise KeyError(rule_id)

    def by_state(self, *states: str) -> list[dict[str, Any]]:
        return [r for r in self.rules if r["lifecycle"]["state"] in states]

    def transition(self, rule_id: str, to: str, by: str, evidence: list[str], human: bool = False,
                   reason: str = "", adopted_in: str = "") -> dict[str, Any]:
        rule = self.get(rule_id)
        cur = rule["lifecycle"]["state"]
        if to not in ALLOWED[cur]:
            raise GovernanceError(f"{rule_id}: {cur} → {to} is not an allowed lifecycle step")
        if rule.get("scope") in PROTECTED_SCOPES and to in {"approved", "adopted", "monitored"}:
            raise GovernanceError(f"{rule_id}: scope '{rule['scope']}' is protected; propose a change request to the project owner instead")
        refs = set(rule.get("evidence", [])) | set(evidence)
        if to == "observed_again" and len(refs) < 2:
            raise GovernanceError(f"{rule_id}: needs ≥2 distinct evidence refs to count as observed again")
        if to == "validated":
            check = rule.get("check", {})
            has_check = check.get("type") == "tool" and check.get("fn") or (
                check.get("type") == "review" and (check.get("never") or check.get("always")))
            if not has_check or not evidence:
                raise GovernanceError(f"{rule_id}: validation needs an executable check or concrete cases plus validation evidence")
        if to == "approved" and (not human or not is_human_name(by)):
            raise GovernanceError(f"{rule_id}: only a named human can approve a rule")
        if to == "adopted" and not adopted_in:
            raise GovernanceError(f"{rule_id}: adoption must name the rubric or config path the rule now lives in")
        if to == "monitored" and not rule.get("observations"):
            raise GovernanceError(f"{rule_id}: monitoring needs ≥1 production observation after adoption")
        if to in {"revised", "retired"} and (not reason or not evidence):
            raise GovernanceError(f"{rule_id}: {to} needs a reason and evidence")
        # Weakening a stable rule is a human decision: revising or retiring an approved or enforced rule changes what
        # every future production is judged by, so an agent may propose it but never do it.
        if to in {"revised", "retired"} and cur in {"approved", "adopted", "monitored"} and (not human or not is_human_name(by)):
            raise GovernanceError(f"{rule_id}: only a named human can {'revise' if to == 'revised' else 'retire'} a rule that is {cur}")

        self._archive()
        if to == "revised":
            rule.setdefault("previous_versions", []).append({k: v for k, v in rule.items() if k != "previous_versions"})
            rule["rule_version"] = rule.get("rule_version", 1) + 1
        rule["evidence"] = sorted(refs)
        if adopted_in:
            rule["adopted_in"] = adopted_in
        rule["lifecycle"]["state"] = to
        rule["lifecycle"]["history"].append({"state": to, "at": now(), "by": by, "human": human, "evidence": evidence, "reason": reason})
        self._save({"rule": rule_id, "from": cur, "to": to, "by": by, "human": human, "evidence": evidence, "reason": reason})
        return rule

    def observe(self, rule_id: str, run_id: str, fired: bool, helped: bool | None, note: str = "") -> None:
        rule = self.get(rule_id)
        self._archive()
        rule.setdefault("observations", []).append({"run": run_id, "fired": fired, "helped": helped, "note": note, "at": now()})
        self._save({"rule": rule_id, "observation": run_id, "fired": fired, "helped": helped})

    def add_candidate(self, rule: dict[str, Any], by: str) -> dict[str, Any]:
        if any(r["id"] == rule["id"] for r in self.rules):
            raise GovernanceError(f"{rule['id']} already exists")
        rule = dict(rule)
        rule["lifecycle"] = {"state": "candidate", "history": [{"state": "candidate", "at": now(), "by": by, "human": False,
                                                                "evidence": rule.get("evidence", []), "reason": "proposed by the learning node"}]}
        self._archive()
        self.rules.append(rule)
        self._save({"rule": rule["id"], "added": "candidate", "by": by})
        return rule

    def _archive(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        dest = self.history_dir / f"rules-v{self.data['version']}.json"
        if not dest.exists():
            shutil.copy2(self.path, dest)

    def _save(self, entry: dict[str, Any]) -> None:
        major, minor = str(self.data["version"]).split(".")
        self.data["version"] = f"{major}.{int(minor) + 1}"
        self.data["updated"] = now()
        write_json_atomic(self.path, self.data)
        with self.changelog.open("a") as f:
            f.write(json.dumps({"version": self.data["version"], "at": now(), **entry}, ensure_ascii=False) + "\n")


def summarize(path: Path = DEFAULT_RULES) -> dict[str, Any]:
    rb = RuleBase(path)
    counts: dict[str, int] = {}
    for r in rb.rules:
        counts[r["lifecycle"]["state"]] = counts.get(r["lifecycle"]["state"], 0) + 1
    return {"version": rb.data["version"], "sha256": sha256_file(path), "rules": len(rb.rules), "by_state": counts,
            "enforced": [r["id"] for r in rb.rules if enforcement(r) == "enforced"],
            "advisory": [r["id"] for r in rb.rules if enforcement(r) == "advisory"]}


def propose_from_run(state: Any, rules_path: Path = DEFAULT_RULES) -> dict[str, Any]:
    """Build a learning proposal from a finished run. It only proposes and never applies anything.

    Each resolved or persisting defect becomes either an observation of an existing rule (matched by rule id in the
    defect dimension's rubric link) or a new candidate. Project-specific items are kept apart from reusable ones.
    """
    rb = RuleBase(rules_path)
    by_dim: dict[str, str] = {}
    for r in rb.rules:
        for dim in r.get("rubric_dimensions", []):
            by_dim[dim] = r["id"]
    observations, candidates, project_specific = [], [], []
    for d in state.evaluation.defects:
        rid = by_dim.get(d.dimension)
        entry = {"defect": d.id, "dimension": d.dimension, "status": d.status, "severity": d.severity,
                 "traced_node": d.traced_node, "fix": d.smallest_fix}
        if d.source.startswith("feedback:") and not rid:
            project_specific.append({**entry, "reason": "human feedback without a matching reusable rule; keep in the project memory.md until seen again"})
        elif rid:
            observations.append({**entry, "rule": rid, "fired": True, "helped": d.status == "resolved"})
        else:
            candidates.append({**entry, "proposed_rule": {"id": f"R-CAND-{d.dimension}", "statement": d.description,
                               "category": "recurring_failure_patterns", "scope": "production_default",
                               "check": {"type": "review", "never": [d.description]}, "evidence": [f"{state.runtime.run_id}:{d.id}"]}})
    corrections = [c.model_dump() for c in state.evaluation.comparisons]
    return {"schema": "vpg-learning-proposal/v1", "run": state.runtime.run_id, "created": now(),
            "rules_version": rb.data["version"], "observations": observations, "candidates": candidates,
            "project_specific": project_specific, "corrections": corrections,
            "note": "Proposal only. Apply with `vpg memory apply <proposal> --approved-by <human>`; protected scopes are refused."}


def apply_proposal(proposal: dict[str, Any], approved_by: str, rules_path: Path = DEFAULT_RULES) -> dict[str, int]:
    if not is_human_name(approved_by):
        raise GovernanceError("a learning proposal can only be applied by a named human")
    rb = RuleBase(rules_path)
    n_obs = n_cand = 0
    for ob in proposal["observations"]:
        rb.observe(ob["rule"], proposal["run"], fired=ob["fired"], helped=ob["helped"], note=ob["defect"])
        n_obs += 1
    existing = {r["id"] for r in rb.rules}
    for c in proposal["candidates"]:
        rule = c["proposed_rule"]
        if rule["id"] in existing:
            rb.observe(rule["id"], proposal["run"], fired=True, helped=None, note=c["defect"])
            continue
        if rule.get("scope") in PROTECTED_SCOPES:
            continue
        rb.add_candidate(rule, by=f"learning-node (applied by {approved_by})")
        existing.add(rule["id"])
        n_cand += 1
    return {"observations": n_obs, "candidates": n_cand}
