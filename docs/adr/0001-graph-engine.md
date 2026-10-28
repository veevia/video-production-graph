---
type: decision
schema_version: decision-record/v2
record_type: adr
id: vpg-0001
status: accepted
light: yellow
trigger: "The first real production run needs something the engine lacks: execution across machines, a durable remote queue, or cycles in Workflow OS job contracts. Re-decide then."
expiry: 2026-12-31
owner: Wei Wei
consultation: solo
created: 2026-09-26
updated: 2026-09-26
slug: standalone-graph-engine
tags: [type/decision, record/adr, status/accepted, light/yellow, scope/workflow, purpose/video-production]
---

# 0001 — A standalone graph engine for video production

**TL;DR:** Video production runs on a small, standalone Python graph engine (`vpg`: the standard library, pydantic, PyYAML and jsonschema; no service) that borrows LangGraph's vocabulary and Workflow OS's state names. It does not extend Workflow OS or depend on an orchestration framework. 🟡 Yellow until a real production calibrates it.

## Context

The 2026-09 platform film (the case study) took 31 recovered iterations across seven rounds. Its loop needed several things together:
- cycles (rework after critique), bounded so the loop can't run forever;
- conditional routing on gate outcomes;
- fan-out and fan-in (six parallel reviewers, per-asset work);
- content-addressed skipping of approved work;
- human gates;
- an audit trail.

Everything had to run locally, because customer media must not leave the machine. [Research](../research/graph-engineering-and-tools.md) found that no framework provides evaluation nodes or governed learning, and that Temporal, Dagster and Prefect need a running service.

## Alternatives

1. **Extend Workflow OS** (`02_Workflows/workflow-os/`). Its job contracts must be acyclic (every `depends_on` sits "inside the same acyclic job graph"), with per-step retry only. There are no rework loops, dynamic fan-out or memoisation, and it is a qualified, security-hardened product: extending it would need re-qualification.
2. **Adopt LangGraph as a dependency.** It has the right model (StateGraph, `Send`, interrupts, checkpointers), but adds a moving dependency for a local, file-based workflow that agents drive through task packets rather than in-process LLM calls.
3. **Temporal, Dagster or Prefect.** Each needs a server or daemon for production use.
4. **A standalone engine (chosen).** Measured 2026-09-26: about 3,000 lines of core Python (3,800 with the CLI, docs generator and dry-run driver) plus about 1,300 lines of deterministic tools, with typed pydantic state, JSON checkpoints, a hash-chained ledger and deterministic tools. Agents are external workers reached through task packets.

## Decision

Build `vpg`, and keep the graph in `graph/workflow.yaml` as the single source of truth:
- Flow edges form a DAG with join = all; loop edges are bounded; escalate edges stop the run.
- Node states follow Workflow OS's names (STEP_PASS, ITERATE, WAITING_HUMAN, BLOCKED …), so a later adapter can expose a vpg subgraph as a Workflow OS job.
- Workflow OS, W1–W6 and existing SOPs are untouched. This workflow is added beside them as a new SOP and prompt.

## Consequences

- **Good:**
  - The whole workflow runs offline in about 20 s on the example, and 78 tests cover its guarantees.
  - The rules are data (`rubrics/`, `knowledge/rules.json`), so learning changes rules, not code.
- **Cost:** vpg owns its scheduler. It is single-machine; parallelism means threads plus concurrently open task packets. There is no distributed queue.
- **Risk:** its semantics could drift from LangGraph's. Mitigation: the vocabulary is mapped in the research doc.

## Consultation

Solo. The workflow was built at Wei's request; no reviewer dissent was sought or recorded. The independent critic reviewed the implementation, not this choice.

## Reversal

- Engine state is plain JSON, and the graph is YAML validated by pydantic. Moving to LangGraph means mapping nodes to functions, `when:` to conditional edges and `map_over` to `Send`, then replaying from checkpoints.
- To integrate with Workflow OS, wrap a subgraph (for example N23–N24) as a job contract.
- To revert entirely, remove `02_Workflows/video-production/` plus its SOP, prompt and README row; nothing else depends on it.
