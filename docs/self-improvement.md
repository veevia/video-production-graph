# Self-improvement and memory

> **TL;DR:** Each production ends with a learning proposal (N25). A named human applies it with `vpg memory apply`.
> Rules move through an evidence-gated lifecycle and are versioned with an append-only changelog. Protected scopes
> (objective, product truth, brand constraints, approved design principles) can never be changed by learning.

## Where memory lives

| Store | Scope | Changes by |
|---|---|---|
| `knowledge/rules.json` (+ `rules-changelog.jsonl`, `history/rules-v*.json`) | Cross-project rules | `vpg memory apply` / `vpg memory promote`, by a human |
| `knowledge/design-principles.md` | Human-readable library, generated from the rules and the case study | `python -m vpg docs` |
| `knowledge/case-studies/<case>/` (`*_TENANT`) | The evidence base for the rules | Curated reconstructions |
| `controls/memory.md` (per project) | Project-specific lessons | N25 (the only node allowed to write it) |

## Lifecycle (vpg/memory.py)

```
candidate → observed_again → validated → approved → adopted → monitored → revised | retired
```

| Step | Gate |
|---|---|
| observed_again | ≥ 2 distinct evidence refs (iterations or productions) |
| validated | an executable check or concrete never/always cases, plus validation evidence (a test id or a run) |
| approved | a named human (`--human`; agent and dry-run actors are refused) |
| adopted | already approved, plus the rubric or config path the rule now lives in |
| monitored | ≥ 1 production observation after adoption |
| revised | a reason and evidence; for an approved or enforced rule, a named human. The prior version is kept in `previous_versions`, and the rule goes back through validation. |
| retired | a reason and evidence; for an approved or enforced rule, a named human |

**Enforcement follows the lifecycle.** An adopted or monitored rule is enforced: its rubric dimensions block at their declared level. An approved or validated rule is advisory: its dimensions are downgraded to SHOULD and its findings to nits, reported but never blocking. Earlier states are inactive. This is how an unreviewed lesson is prevented from corrupting stable production rules.

## The learning loop after each production

1. N25's tool builds a proposal from the run's defects and comparisons. It holds:
   - observations of existing rules (whether the rule fired, and whether it helped);
   - candidates for new failure patterns;
   - project-specific items, kept apart from reusable ones.
2. The Learning and Memory agent writes `lessons.json`, each lesson classed reusable or project-specific with evidence, and updates `controls/memory.md`.
3. Wei reviews the proposal and applies it: `python -m vpg memory apply --proposal …/learning-proposal.json --approved-by Wei`.
4. Wei promotes a rule once its evidence is sufficient: `python -m vpg memory promote --rule R-… --to approved --by Wei --human --evidence "…"`.
5. Every write bumps the rule-base version, archives the previous file and appends to the changelog.

## Initial rule state

46 rules from the case study: 38 adopted, 3 approved (project-specific, deliberately never defaults), 4 validated (critic-derived, advisory until Wei approves), 1 candidate (found by this workflow's own dry run). Every adopted rule's approval cites the message in which Wei stated the requirement. The numeric thresholds are marked "calibration pending".
