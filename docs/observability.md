# Observability and the glass-box data contract

**TL;DR:** Every run, old ones included, can be read as OpenTelemetry-GenAI-shaped spans and evaluation events with
evidence, derived from the hash-chained ledger and the saved state. A run that opts in also writes the evaluation events
into its ledger as they happen. Builds made outside the graph get an event path (`vpg direct-run`), and a provisional
approval is labelled `assumed`, can expire, and is never silent. This page is the contract a local viewer reads.

## Why

In a recent 90-second case-study film (NDA), the run waited at its first human gate while candidates were built by
running the production nodes directly (reported). Nothing reached the ledger or the engine's evaluation, so no view of
the run could explain what was built, judged or decided. Observability here means: every node, gate and human decision
has an event that says what was judged, by whom, with what evidence, and how sure the record is.

## Spans

`vpg telemetry <project>` derives them; `--write` refreshes `.vpg/telemetry/`; `--otlp <file>` writes OTLP/JSON for a
local Phoenix or Langfuse. Nothing calls the network.

| Span | Name | When |
|---|---|---|
| Run | `vpg.run <run id>` | `run_created` to the last event |
| Agent or review node attempt | `invoke_agent <agent>` | `node_dispatched` to `node_passed`, `node_failed`, `escalated` or `invalidated` |
| Tool node attempt | `execute_tool <module:function>` | the same events |
| Memo hit | `vpg.memo <node>` | the `memo_hit` event (zero length) |
| Wait on a human | `vpg.await_human <node>` | `awaiting_human` to its `decision` (child of the attempt) |
| Direct run | `vpg.direct_run <node>` | the run's start to its `direct_run` event |

Attributes follow the OpenTelemetry GenAI conventions (`gen_ai.operation.name`, `gen_ai.provider.name` = `vpg`,
`gen_ai.agent.name` or `gen_ai.tool.name`) plus `vpg.node.id`, `vpg.node.num`, `vpg.attempt`, `vpg.fingerprint`,
`vpg.executor`, `vpg.actor`, `vpg.outcome` and, on waits, `vpg.human.decision`, `vpg.human.actor`,
`vpg.human.provisional` and `vpg.evidence_tag`. The trace id is derived from the run id, and a span id from the run, the
node and the attempt, so an engine-written event and a derived span always link.

## Evaluation events

Each follows the conventions' `gen_ai.evaluation.result` event, parented to the span it evaluates.

| Event name | From | Label | Evidence |
|---|---|---|---|
| `vpg.node.completion` | each pass or failure | the outcome, or `fail` | the attempt's artifacts (key, path, sha256) and the completion checks, or the failure messages |
| `vpg.review.<DIMENSION>` | each scored rubric dimension | `pass` or `fail`, with the score as value | the reviewer's note and the tool findings for that dimension |
| `vpg.finding.<DIMENSION>` | a tool finding on an unscored dimension | `fail` or `nit` | the finding (check, severity, evidence, element, traced node) |
| `vpg.gate` | each review-gate decision | `pass`, `rework` or `blocked` | the version and the open blocking defects |
| `vpg.human_decision` | each human-gate decision | the decision | the outputs decided on (sha256), the grant if one was used, and, for a provisional one, its expiry; `vpg.human` is false when no named human made it, and the explanation says who did |
| `vpg.node.memo` | each memo hit | `pass` | the fingerprint |
| `vpg.direct_run` | each recorded direct run | `ungoverned` | the outputs (path, sha256) |

Every event carries `evidence_tag`, using the workspace's evidence tags:

* `observed`: a tool measured it, or the engine recorded a named human's own decision;
* `reported`: an agent said it (an agent reviewer's 1 to 5 score, or a decision an agent relayed with the human's
  quoted instruction);
* `assumed`: no human judged that output: a provisional approval made while the human was away, a decision made under
  a delegation grant, or a dry-run auto-approval.

### Opting in

```yaml
# vpg.project.yaml
observability: {evaluation_events: true}
```

The run then writes each event into `.vpg/ledger.jsonl` as an `evaluation` event when a node passes or fails, the
review gate decides and a human decides, so the events are hash-chained with everything else and `vpg ledger-verify`
covers them. Without the setting, the ledger is exactly as before and `vpg telemetry` derives the same events from the
ledger and the state. Either way, `vpg telemetry <project> --check` exits 1 unless every node that ran, every gate
decision and every human decision has an event with evidence.

## Direct runs

```bash
python -m vpg direct-run <project> n13_motion_scenes --actor agent:motion --reason "the gate waited" \
  --label v26 --run "python pipeline/scenes.py" --output film=candidates/v26/scenes.mp4
```

`direct-run` records a build made outside the governed path, optionally running it (in the project folder, with
`VPG_DIRECT_RUN` set, its output kept in `.vpg/direct-runs/<id>.log`). It hashes the named outputs, records the node's
command code and current inputs, ledgers `direct_run` and keeps the record in `history.direct_runs`. The outputs are
never registered as artifacts, so no gate or review ever judges them, and `vpg status` keeps saying so. A rollback
keeps the records; a number is never reused, and a log is never overwritten. The look and
animatic gates in the [quality workflow](quality-workflow.md) remove most reasons to build outside the graph.

## Provisional approvals

A provisional approval (autonomy mode, v1.8) is recorded with `evidence: assumed`. With
`autonomy: {provisional_expiry_h: 72}` it also gets `expires_at`. While it stands, `vpg status` names it and its expiry;
the first command that runs after it expires ledgers `provisional_expired` once, and the status says EXPIRED until a
named human confirms or rejects it at the release, as before. An expiry never approves or rejects anything by itself.

## The data contract

Paths are relative to a project folder unless they start with `schemas/` (the engine package). Existing paths are
unchanged; the viewer may rely on every row.

| Path | Format | Written by | Notes |
|---|---|---|---|
| `vpg.project.yaml` | YAML | a person | `workflow:` is `default`, `quality` or a path; `observability`, `autonomy.provisional_expiry_h` and `autonomy.provisional_gates_extra` are new and optional |
| `.vpg/state.json` | `schemas/state.schema.json` | the engine | new: `history.direct_runs[]`, `history.approvals[].evidence`, `.expires_at`, `.expiry_noted_at`; a quality run also has `controls.spec-*` (path, status, version, snapshot) and `production.refs.look_frames`, `.animatic` |
| `.vpg/ledger.jsonl` | one event per line: `seq`, `ts`, `type`, `data`, `prev`, `hash` | the engine | new types: `evaluation` (opt-in), `direct_run`, `provisional_expired`; `decision` events gain `evidence` and an optional `expires_at` |
| `.vpg/telemetry/spans.jsonl` | one span per line (below) | `vpg telemetry --write` | derived; safe to delete and rebuild |
| `.vpg/telemetry/evaluations.jsonl` | one evaluation event per line (below) | `vpg telemetry --write` | derived |
| `.vpg/telemetry/summary.json` | `{schema, run_id, trace_id, coverage}` | `vpg telemetry --write` | `coverage.ok`, `missing`, counts |
| `.vpg/direct-runs/<DR-NNN>.log` | text | `vpg direct-run --run` | the command's output |
| `benchmarks/registry.json` | `schemas/benchmark-registry.schema.json` | `vpg benchmark` | a convention; GATES.yaml names the actual path |
| measurements (any path) | `schemas/measurements.schema.json` | `vpg measure --out` | timecoded evidence per metric |
| `spec/DESIGN.md`, `spec/MOTION.md`, `spec/STORY.md`, `spec/VOICE.md`, `spec/MUSIC.md`, `spec/MIX.md` | YAML front matter and Markdown | a person; in the quality workflow N26 (DESIGN, MOTION), N06 (STORY), N09 (VOICE, MUSIC, MIX) | rules with ids ([spec family](spec-family.md)) |
| `spec/DESIGN.tokens.json` | W3C Design Tokens 2025.10 | a person; in the quality workflow N26 | exact values |
| `spec/GATES.yaml` | YAML | a person; in the quality workflow N03 | gates by metric id and version; the registry pinned as `{path, sha256}`; `waivers` |
| `.vpg/controls/spec-<doc>.accepted.<ext>`, `spec-<doc>@v<N>.<ext>` | the document's format | the engine (quality runs) | the accepted copy and each approved version, as for the six documents |
| `work/n26_look_development/a<N>/look-frames.json`, `look-report.json` | `schemas/artifacts/look-frames.schema.json`; report JSON | N26 and its check | frames by role, motion test, spec hash; per-role gate rows with value and envelope |
| `work/n27_animatic/a<N>/animatic.json`, `animatic-report.json` | `schemas/artifacts/animatic.schema.json`; report JSON | N27 and its check | timed beats, scratch read, music hits; gates, beat shares, terms, hits |
| `work/n15_technical_qa/a<N>/quality-gates.json` | JSON | N15 (quality runs) | build-gate rows, ratchet rows, the baseline id, measurements |
| `work/n21_feedback_interpretation/a<N>/interpretation.json` → `spec_proposals[]` | `{doc, change, rule, proposal, because}` | N21 (optional) | listed by `vpg status`; filed only by a person |
| `judging/` (a convention) | packet, key, verdict and result JSON; `labels.jsonl` | `vpg judge` | [judging](judging.md); keep `key.json` away from the judge |

A span line:

```json
{"trace_id": "<32 hex>", "span_id": "<16 hex>", "parent_span_id": "<16 hex>", "kind": "CLIENT",
 "name": "invoke_agent visual-design", "start": "2026-10-26T15:00:01+00:00", "end": "2026-10-26T15:04:40+00:00",
 "status": "OK", "attributes": {"gen_ai.operation.name": "invoke_agent", "gen_ai.provider.name": "vpg",
 "gen_ai.agent.name": "visual-design", "vpg.node.id": "n09_visual_system", "vpg.attempt": 2, "vpg.outcome": "approved"},
 "source": {"ledger_seq": 118, "ledger_hash": "<16 hex>"}}
```

An evaluation event line:

```json
{"name": "vpg.review.CRE-PACING", "gen_ai.evaluation.name": "vpg.review.CRE-PACING",
 "gen_ai.evaluation.score.value": 3.0, "gen_ai.evaluation.score.label": "fail",
 "gen_ai.evaluation.explanation": "CRE-PACING scored 3.0 against 4 (MUST)", "node": "n16_creative_critique",
 "attempt": 1, "evaluator": "agent:creative-critic", "evidence_tag": "reported",
 "evidence": [{"kind": "reviewer_note", "text": "the second chapter has no pause after the claim at 0:41"}],
 "span_id": "<16 hex>", "trace_id": "<32 hex>", "time": "2026-10-26T15:20:11+00:00", "source": {"state": "evaluation.reviews[3]"}}
```

`source` names where the event came from: a ledger event (`ledger_seq`, `ledger_hash`), which `vpg ledger-verify`
protects, or a state record. Paths the viewer reads today, such as `candidates/<v>/`, `qa/qa-direct.json`, `reviews/`,
`key-frames/` and `work/film-<v>/`, are a project's own conventions; the engine neither writes nor moves them.

## Evidence and limits

* observed: derived telemetry for two finished runs in this workspace covered every node that ran, every gate decision
  and every human decision with no orphan event (25 of 25 nodes, 24 gates and 16 decisions in the larger), in about 0.1
  s each, read-only.
* observed: opted-in runs ledger the events and `ledger-verify` passes; default runs ledger nothing new; direct runs and
  expiries are recorded once and shown in the status (`tests/test_observability.py`).
* A review's attempt is inferred from when its record was created; a review merged out of order could attach to the
  neighbouring attempt of the same node.
* Telemetry is derived, not authoritative: the ledger and the state remain the record. Delete `.vpg/telemetry/` at any
  time; `vpg telemetry --write` rebuilds it.
