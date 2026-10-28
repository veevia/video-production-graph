---
type: decision
schema_version: decision-record/v2
record_type: adr
id: vpg-0002
status: proposed
light: yellow
trigger: "Wei reviews this record, or the first production on `workflow: quality` reaches its look gate (N26). Re-decide the envelopes and whether the gates stay human-only then."
expiry: 2026-12-31
owner: Wei Wei
consultation: solo
created: 2026-10-26
updated: 2026-10-26
slug: quality-workflow
tags: [type/decision, record/adr, status/proposed, light/yellow, scope/workflow, purpose/video-production]
---

# 0002 — An opt-in quality workflow with look and animatic gates

**TL;DR:** Craft quality is measured before the build, not discovered after it: an opt-in `workflow: quality` adds a
look gate (N26) and an animatic gate (N27) in front of asset generation, makes the spec family control documents, and
measures the spec's build gates and a ratchet at technical QA. The default graph and its runs are untouched. 🟡 Yellow
until a real production calibrates the envelopes.

## Context

- reported: a recent 90-second case-study film (NDA) went through several full builds in which reviewers kept finding
  look and pacing problems that no gate measured; each was found in a finished master.
- observed: in this workspace's finished runs, reviewer scores sat at or near the ceiling across versions (the
  [saturation alarm](../judging.md#saturation-alarm)), so the reviews could not separate versions.
- Constraints: nine productions run on the default graph, several finished and one waiting at N03. The package is
  published. `accept_config` cannot add nodes to a saved run, and engine code is part of every run's accepted
  configuration.

## Alternatives

1. **Add the gates to the default graph.** Every production would get them, but a saved run cannot gain nodes, the
   nine projects would diverge from their recorded graph, and the default's 25 stages are a validated contract.
2. **Advisory checks only (no new nodes).** Measure the spec at N15 and report. Cheap, but the problems are still found
   in a master, which is the cost this decision exists to remove.
3. **A hand-maintained second graph.** Opt-in, but it drifts from the default as soon as a default node changes.
4. **A generated, opt-in workflow (chosen).** `graph/workflow-quality.yaml` is generated from the default graph and a
   declared overlay (`vpg/quality.py`) by `python -m vpg docs`; `vpg docs --check` and a drift test fail when they part.

## Decision

- `workflow: quality` names the generated graph; `default` and paths behave as before. A run cannot switch workflows.
- N26 (look) and N27 (animatic) are agent nodes with human gates and deterministic completion checks; they are named
  humans' decisions unless a project opts them into provisional approval (`autonomy.provisional_gates_extra`).
- The control store takes an exact document registry: the six documents, plus the eight spec documents a workflow's
  nodes use. Each spec document has one writer that validates and locks it. GATES.yaml, with its pinned benchmark
  registry, is a bar document like evaluation.md.
- The build gates and the ratchet cite R-REG-001 and stay advisory until a named human adopts that rule.

## Consequences

- **Good:** a look or a timing that breaks the approved spec stops before any asset is built, with the gate, rule, role,
  value and envelope in the agent's next packet (observed: `tests/test_quality_workflow.py`).
- **Cost:** engine code changed, so every run in flight needs one `vpg accept-config` between versions. A quality
  production must assemble a benchmark registry with style frames per role before N03 can approve the bar.
- **Risk:** uncalibrated envelopes could block a look Wei likes. Mitigation: the opt-in provisional fallback, the rollback
  triggers in [the workflow doc](../quality-workflow.md#rollback-triggers), and calibration through
  [judging](../judging.md#calibration).

## Consultation

Solo: Wei asked for the work; no person reviewed this choice. An independent design-review agent reviewed the design
before it was built and raised three blockers and five important issues; all were accepted and changed the design:

- Writers lint only their own documents (the first draft had N26 and N27 lint documents other nodes own).
- The registry is pinned in GATES.yaml and treated as part of the bar (the draft read whatever registry file was there).
- The look and animatic gates are not provisional by default (the draft allowed it); the fallback is opt-in.
- Still metrics are judged per style-frame role at one canonical width, against the same role in the references.
- Mid-run workflow switches are refused, the control-document registry is exact, and the generated graph has a drift
  guard.

## Reversal

Delete `graph/workflow-quality.yaml`, its catalogs, `vpg/quality.py` and the `quality` entry in `WORKFLOWS`. Default
runs are unaffected; a quality run would have to finish on an engine that still has the workflow.
