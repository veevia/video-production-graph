# Architecture

> **TL;DR:** One YAML graph (25 nodes, 49 edges) is executed by a small deterministic engine. Agents do the creative
> work through task packets; tools do the measuring. Every output is content-addressed and every transition is
> ledgered. The engine decides when the work is done, from explicit gates, not from an agent's sense of completion.

```
            graph/workflow.yaml ──► vpg.graph (load · validate · index)
                                          │
controls/*.md (6 contracts) ──┐           ▼
sources/  (immutable inputs) ─┼──► vpg.engine ── batch ──► executors ──► agents (task packets)  /  scripted fixtures
knowledge/rules.json ─────────┘      │   ▲                     │           tools (vpg.tools.*)   /  project commands
rubrics/*.yaml, config/*.yaml        │   └── fan-in ◄──────────┘
                                     ▼
          .vpg/state.json · .vpg/checkpoints/ · .vpg/ledger.jsonl · .vpg/tasks/ · .vpg/escalations/ · work/<node>/a<n>/
```

## Layers

| Layer | Files | Responsibility |
|---|---|---|
| Definition | `graph/workflow.yaml`, `schemas/workflow.schema.json` | Nodes (all 19 fields), edges (7 fields), subgraphs, policies. The single source of truth. |
| Model | `vpg/models.py` → `schemas/state.schema.json` | The typed workflow and the typed shared state, with its six separations ([state model](state-model.md)). |
| Engine | `vpg/engine.py`, `vpg/graph.py` | Readiness, conflict-free batching, fingerprints and memo-skips, completion checks, routing, invalidation, human gates, checkpoints. |
| Source provenance | `vpg/source_store.py` | Content-addressed copies of the frozen sources in `.vpg/sources/`, and the drift, hard-link and symlink warnings. |
| Execution | `vpg/executors.py`, `vpg/context.py` | Task packets (real agents), scripted fixtures (dry runs), project commands (renderers, TTS, assembly). |
| Evaluation | `vpg/evaluation.py`, `rubrics/`, `config/defaults.yaml`, `vpg/tools/` | Deterministic checks first; reviews merged, with tools taking precedence; the tri-state gate; before/after comparison ([evaluation and retry](evaluation-and-retry.md)). |
| Controls | `vpg/controls.py`, `templates/control/` | The six control documents: write permissions, approval locks and change requests ([access](control-access.md)). |
| Provenance | `vpg/provenance.py` | Append-only, hash-chained ledger; artifact lineage. |
| Publishing | `vpg/publish.py` | Optional human-facing copies of every version, privacy-held when a scan cannot pass, never blocking the graph. |
| Learning | `vpg/memory.py`, `knowledge/` | Governed rule lifecycle and proposals ([self-improvement](self-improvement.md)). |
| Interface | `vpg/cli.py`, SOP, `/video-production` prompt | How humans and agents drive a production. |

## Design principles of the engine

1. **Verification over self-report.** A node passes only when its completion checks recompute from disk, never on an agent's say-so.
2. **Tools beat agents on MUST checks.** An agent cannot pass a dimension a deterministic tool failed.
3. **Minimal regeneration.** Fingerprints (inputs + definition + prompt + command + rework context) skip approved work. Defects route to the node that produced them.
4. **Bounded everything.** Loop traversals, rework cycles and repeats of the same defect all have limits. A project's `loop_budget` can extend a spent rework budget while the run is converging (the ledger records why). Repeats of the same defect, and `max_hours` when set, are hard runaway guards. A limit reached without convergence means BLOCKED plus a report, never a silent loop.
5. **Humans own commitments.** Objective, product truth, creative direction, visual system and release are named human decisions. Dry-run auto-approvals are labelled as not human.
6. **Nothing is lost.** Outputs are superseded, never deleted; state is checkpointed after every step; the ledger is tamper-evident.
7. **Refuse or block; never hang.** The engine refuses to run a graph that fails structural validation. A run that cannot progress is BLOCKED with a report, never left RUNNING. So is a run whose rules, rubrics, workflow or project config changed underneath it, or whose input artifacts no longer match their hashes.

## Relationship to existing workspace systems

- **Workflow OS** (`02_Workflows/workflow-os/`): not modified. vpg reuses its state names and HITL principles; [ADR 0001](adr/0001-graph-engine.md) records why vpg is standalone and how to bridge the two.
- **W1–W6 / SOPs:** vpg is callable as the `video-production` SOP and the `/video-production` prompt; it doesn't replace the design loop.
- **Skills and agents:** nodes name worker capabilities, not Python dependencies. The portable [skill bindings](skill-bindings.md) describe each capability. Tests also check the workspace index when it exists. Agents map to custom agents where one fits ([agent catalog](agent-catalog.md)); a community user can supply an equivalent worker without installing the private workspace.

## Logging and provenance

| Record | Where | What it proves |
|---|---|---|
| Ledger | `.vpg/ledger.jsonl` | An append-only event log; each event is hash-chained to the previous one. See the event types below. `vpg ledger-verify` detects edited, inserted or deleted events (limit: [gaps](gaps-and-limitations.md) #10). |
| Artifact registry | `state.artifacts` | Each output's sha256, bytes, node, attempt, map item, tool, version and input lineage. `vpg lineage <id>` walks it; superseded artifacts are kept. |
| Checkpoints | `.vpg/checkpoints/NNNNN-<event>-<node>.json` | The full state after every pass or failure, for resume and `vpg rollback`. |
| Per-attempt work | `work/<node>/a<n>/` | Outputs, the command's `context.json` and `command.log` (stdout/stderr), and the agent's review files. |
| Task packets | `.vpg/tasks/<node>-a<n>.{md,json}` | Exactly what each agent was asked, with its dispatch token. |
| Escalations | `.vpg/escalations/<node>-<n>.md` | What failed, what was tried (rework history) and what unblocks it. |
| Published versions | `<publish.dir>/v<N>/`, `<publish.dir>/_vpg-index.json`, `<publish.dir>/_vpg-README.md` | Optional copies for humans to inspect between graph versions. Each is checked against the version's registered hash, and nothing is overwritten. |
| Delivery evidence | `evidence/` in the package | Reviews, gates, defects, decisions (human or not), comparisons and a copy of the ledger. |

The ledger records these event types:
- run: `run_created`, `resumed`, `resume_reset`, `run_pass`;
- scheduling and outcomes: `batch`, `node_dispatched`, `task_issued`, `node_submitted`, `node_passed`, `node_failed`, `memo_hit`;
- review and rework: `gate`, `regeneration_plan`, `feedback`, `invalidated`;
- inputs and controls: `inputs_frozen`, `sources_kept`, `inputs_unfrozen`, `control_locked`, `change_request`, `change_request_approved`, `change_request_rejected`;
- human decisions and recovery: `awaiting_human`, `decision`, `escalated`, `unblocked`, `rollback`, `checkpoint`, `config_accepted`,
  `passes_carried_over`;
- publishing: `version_published`, `version_held`.
