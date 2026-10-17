# Evaluation and retry framework

> **TL;DR:**
> - Every node has completion checks, and every version faces six review gates.
> - Deterministic tools measure; agents judge only what tools cannot; humans decide listening, taste and release.
> - A defect is fixed at the node that caused it, compared before and after, and escalated after three failed corrections.

## Levels

| Level | What is checked | Where |
|---|---|---|
| Node | The completion criteria for its outputs: schema-valid artifacts, tool checks (accuracy, runtime, storyboard, palette, assets, voice, scenes, master) | `completion:` in workflow.yaml |
| Version | Six reviews of each assembled version: technical, creative, design compliance, accessibility/legibility, product accuracy, narrative/pacing | N15–N20, then the gate at N21 |
| Release | Every release condition recomputed from disk, plus the human decision | N23 |

## The 18 evaluation dimensions

All 18 brief dimensions are covered by 80 rubric dimensions in `rubrics/*.yaml`. Each dimension has:
- an id;
- a level (MUST or SHOULD);
- a check type: `tool` (deterministic), `review` (scored 1–5 by an agent, threshold 4) or `human` (decided by Wei and never scored by an agent);
- a threshold or concrete never/always cases;
- the rule it enforces (`knowledge/rules.json`).

## Gate (N21) — tri-state

- **pass:** every reviewer has a current review, none of those reviews failed, and no P1–P3 defect is open, persisting or in rework.
- **rework:** blocking defects are open, their traced nodes are regeneration targets, and budget remains.
- **blocked:**
  - a defect needs a human (its target is locked product truth or design, or it has no route). This is recomputed at
    every gate from the defect's current trace and the approved change requests, so it never outlives its cause;
  - or the same defect failed `max_same_defect_failures` (3) corrections (a runaway guard: never extended);
  - or `max_rework_cycles` (6, or the project's `loop_budget.rework_cycles`) is spent and the run is not converging
    (see "Budgets that follow quality" below);
  - or a review is missing;
  - or a current review failed but left no blocking defect to route, so rework would have nothing to target;
  - or a human's feedback was closed with no action (`feedback_held`). No version passes until its author or the
    gate's owner accepts that by unblocking the gate (`released_holds`); rework still routes meanwhile. An agent
    may map feedback to defects, but never dismiss a human's feedback on its own authority.
    A feedback that leads only to a closed defect or a nit is held too, and so is one whose defects close at the very
    gate that mapped them. Every gate result names every hold, `vpg status` lists them, and an unblock releases only
    holds its escalation named.
  A BLOCKED gate writes a report with the full rework history.

A finding on a failed MUST dimension is never a nit. Whatever severity a reviewer files, the engine raises it to at least P3, so it blocks and routes.

**A defect is the problem, not its trace.** Its identity is the dimension and the check that found it.
- Findings of one review about the same problem on the same trace share one defect. Every description is kept, and
  `elements` lists the items they name (layer, beat, cue, unit or asset ids), so the rework context loses nothing.
- Elements are information, not identity: an item id that changes between versions can't make a persisting problem look
  new. The cost is that a partial fix (two of three items) counts as a failed correction of the whole check.
- A reviewer that re-traces a persisting problem (for example from N13 to the storyboard, N08) keeps the same defect. Its
  failure count carries, so re-tracing never counts as a fix (`defect_retraced` in the ledger). An unresolved defect is
  always matched before a resolved one on the new trace is reopened, so a problem moving between traces keeps one count.
- A re-trace off a human-gated node (a human gate, or a locked approved document) onto one rework can reach puts the defect
  on hold (`defect_held`). The gate blocks and says why; rework routes it only after a named human unblocks the gate.
- Severity follows the latest review (`defect_severity_changed`); the latest smallest fix replaces the earlier one. The
  rubric sets a finding's level: a reviewer cannot make a MUST dimension's finding a SHOULD-level nit.

## The seven-step failure procedure

| Step | Implementation |
|---|---|
| 1. Identify the exact defect | Findings carry dimension, severity, evidence (timestamp, frame or layer), check id and message. Human feedback is mapped by N21. |
| 2. Trace it to the responsible node | Each finding names `traced_node` and `artifact_key`. The gate refuses traces to unknown nodes, and marks traces to locked or unroutable nodes as needing a human. |
| 3. Determine the smallest correction | `smallest_fix` and `acceptance_check` go into the regeneration plan and the target's task packet. N21 must give every open blocking defect a fix: GAT-TRACE (`regen:check_traces`) fails it otherwise, and a tool finding without one gets its fix under `fixes`. A plan with no routable target escalates. |
| 4. Re-run only the affected node and its invalidated dependents | N22 invalidates the targets plus their flow descendants. Unchanged fingerprints are memo-skipped, and upstream nodes are preserved. |
| 5. Compare the new output with the prior version | The plan snapshots the targets' output hashes; the gate records a `Comparison` (before/after versions, resolved, why, changed hashes). |
| 6. Record whether the correction succeeded | A defect is resolved **only** if the reviewer that found it re-ran on a newer version (not carried forward) and no longer reports it. That holds whether the fix came from rework or from outside it (an approved change request, a rollback), so a fixed defect never holds the gate. A defect from human feedback alone is resolved when N21 lists it as resolved on a later version, or when a named human closes it with `vpg resolve <project> <defect> --evidence "…"`. A defect that was in rework and shows none of this persists, and its failure count rises. |
| 7. Escalate after repeated failure | After 3 failed corrections of the same defect, the gate goes BLOCKED with a report of what was tried and what unblocks it. |

## Retry policy (per node)

- **Consecutive-failure budget:** `retry.max_attempts`. A successful pass resets the streak.
- **Interrupted attempts:** a crash-interrupted attempt does not consume the budget, but interruptions are counted.
  - A crash counts whether it happens while the node executes, while a command bound at submit runs, or while the submission is judged. An example is a completion check that takes the process down.
  - The next attempt's packet says why it restarts, and names any control-document edit that was rolled back.
  - At `retry.max_attempts` consecutive interruptions the node escalates, instead of being re-dispatched forever.
- **Context on retry:** each retry carries the previous attempt's failure messages and any rework defects into the task packet.
- **Exhausted budget:** BLOCKED, with an escalation file naming the attempts, the failures, the open defects and what unblocks it.
- **Unblocking:** only a named human can unblock, and only a node or run-level name with an open escalation. Each escalation needs its own unblock; the run stays BLOCKED while any is open.
  - Unblocking resets the node's retry streak. The node re-runs, so any dependent that already passed is re-checked first: nothing stays built on output it replaces.
  - An escalation can sit on a node that is no longer BLOCKED: a rollback restored a checkpoint at which it was not, or an invalidation (an accepted configuration change, an upstream edit) sent it to re-check. Unblocking it never resets the node; the node and its dependents re-check (re-run, or memo-skip when unchanged), and the ledger names which of the two happened (the escalation records it when it happens).
  - For the loops whose traversal budget ran out, it also resets their counters. `--reset-loops` (only with an open loop escalation) gives every loop of the node a fresh budget in the same decision; the ledger and the release's human record list each loop it reset.
  - An engine before v1.5.1 applied a regeneration plan before checking its loop bounds. Unblocking a loop block such an engine wrote (its escalation records no `edges`) reverts what that plan spent, when the ledger shows the exact sequence it wrote: the rework cycle, the defects' status before the plan (from the checkpoint before it) and the traversal it charged each loop that was not spent. The ledger records `legacy_plan_reverted`, and `vpg status` shows what it will change before the unblock. Otherwise re-judging the same version would count each of those defects as a failed correction. A rollback made after such a block turns the repair off: the rollback restores its own state, and the cycle it carries forward stays spent.
  - It restarts every control-document re-check chain.
  - When the intake loop (E04) runs out, `--waive <gap id> --note "<reason>"` lets the film proceed past a resolvable gap. Only a named human can record the waiver, and only for a gap in the latest gap report, before the inputs freeze. The waiver is frozen with the inputs and ledgered (`gap_waived`). While the gap is open, every later human gate and task packet names it.
  - `--reset-budget` restarts the rework-cycle and repeated-defect counters, and the ledger records it.
  - A rollback (also human-only) never restores a spent budget.
- **Loop budgets are checked before anything moves.** A node whose result needs a loop with no traversal left blocks without traversing any of its loops: a regeneration plan then spends no rework cycle and puts no defect in rework. One escalation names every spent loop and lists the node's loop budgets, and it says when another loop of the node has no traversal left either. `vpg status` prints each traversed loop's count against its bound, and the rework cycles against `max_rework_cycles`.

## Budgets that follow quality (v1.6)

A loop bound measures effort, not quality. On the SRA run, rework loop E43 reached its bound of 3 just after cycle 4 had resolved 15 defects, and the run sat blocked for a day while the film kept improving outside the graph. So budgets are project settings (`vpg/budget.py`):

```yaml
loop_budget:
  profile: quality-first           # rework loops 8 traversals, 15 rework cycles, extend while converging
  # any key overrides the profile: max_traversals, rework_cycles, on_exhaust (block | extend_if_converging), extend_by, max_hours
```

- **Without a `loop_budget` block nothing changes:** the workflow's bounds hold, and a spent budget blocks as before.
- **`max_traversals` applies to the regeneration plan's loops only** (N22 → N07…N14, N09). The intake loop, a human's rejection loops and the release's change loop keep their own bounds: running out of those means a human must act.
- **With `on_exhaust: extend_if_converging`,** a spent rework loop, or the spent rework-cycle budget, gets `extend_by` more when the latest review round made progress on the blocking (P1–P3) defects. Progress means all three of these held:
  - it resolved at least one;
  - it found no more new ones than it resolved;
  - the open count fell, or their severity did (fewer P1, then P2, then P3).

  The ledger records `loop_budget_extended` or `rework_budget_extended` with the reason and the version. The release summary lists every extension, because each one replaces a human unblock.
- **Each budget is extended at most once per version** (`runtime.grant_versions`). A second gate on the same version (after `accept-config`, an unblock, or a rework that left the master unchanged) can't earn a second extension for the same progress. A version re-gated after a human's unblock, and not yet extended, keeps its progress and can still earn its one extension.
- **A profile widens a rework loop; it never narrows one.** The bound is the larger of the profile's `max_traversals` and the edge's own.
- **A human reset starts clean.** `unblock --reset-loops` (or a plain unblock of a spent loop) and `--reset-budget` also clear what converging rounds were granted, and the `unblocked` event lists them (`cleared_grants`).
- **Otherwise the run escalates as before.** The escalation says why it was not extended: nothing was resolved, so the same defects persist; or more new defects than resolved ones; or neither the open count nor their severity fell.
- **Runaway guards never extend.** A defect that failed `max_same_defect_failures` corrections still stops the gate, and `max_hours` (optional, measured from the run's creation) refuses every extension once it is spent.
- **The trend.** There is one row per reviewed version, and all of the version's gates are merged into it.
  - The open count and severity split come from its last gate.
  - Resolved, new and persisting are the version's own: the defects its comparisons resolved, those first seen at it, and those it saw persist.
  - Each gate also records its own round (`trend.round`) for the audit, and each involved defect's severity at the time (`trend.severities`). A defect's severity follows its latest review, so a later downgrade can't rewrite an earlier round.
  - `vpg status` prints the last rounds and whether the latest one converged.
  - Every escalation file has a "Quality trend" section, and its ledger event carries the last two rounds.
  - Rounds recorded before v1.6 are marked "estimated". Their open count is the gate's own; resolved and new are counted from today's defects. Their severity split is taken from the gate's reason, which lists each open defect with its severity at the time ("blocking defects open: D043 P2 …"), when it lists them all.
- **Granted budget is kept.** A rollback never takes back an extension, as it never restores a spent budget.

## Independent pre-flight (v1.6, optional)

`preflight: {nodes: [n14_video_assembly], when: rework}` in `vpg.project.yaml` holds a listed node's finished attempt before the graph spends a review round on it. `when: always` holds the first build too. This is how the SRA side chat took v16 to v19 through an independent reviewer; the graph now does it as a step.

- **Which nodes:** only producers. A review, a gate, the freeze, the package node, a human-gated node or a per-item node is refused, because it has already acted when it finishes, or a human decides it.
- **What is held:** the attempt is judged (outputs registered, completion checks passed), but no version is cut and nothing downstream starts. The node waits (`WAITING_RUNTIME`) on a packet, `.vpg/tasks/<node>-a<N>-preflight.md`. The packet lists:
  - the defects the attempt was meant to fix;
  - every file to judge, with its sha256.
- **The verdict:** `vpg preflight <project> <node> --token <t> --verdict pass|revise --actor <reviewer> [--notes <file>]`. It is one durable action, like a human decision: a failure part-way records nothing. It is refused when:
  - the reviewer is the attempt's producer or the node's bound agent, compared case-free and with or without `agent:`;
  - the reviewer is a placeholder in any spelling: empty, `agent:`, `cli`, or the dry-run actor;
  - a file it was shown changed on disk;
  - the token is stale;
  - it is a revision without notes.
- **Pass:** the attempt passes as it would have (a version is cut, the reviews run), and its result records who checked it.
- **Revise:** a failed attempt with the reviewer's notes as its failure reasons, so the producer's next packet carries them. No version or review round is spent.
  - Revisions escalate after `preflight.max_revisions` (default: the node's `retry.max_attempts`). A revision is a quality iteration, so a project may allow more of them than technical failures; the bound stays as the runaway guard.
  - Since v1.7 the two count apart (`preflight_revisions` and `fail_streak`, both reset by a pass). A technical failure after revisions is charged to the node's retry budget only, and a revised attempt (which completed its checks) ends a run of technical failures.
  - The revised attempt's files stay registered under their attempt number until the next attempt replaces them. Nothing downstream runs on them, because the node has not passed.
  - After a revision, or when an invalidation drops a pending hold, the next dispatch re-runs the node. It never memo-passes on those unjudged files.
- **Records:** the ledger has `preflight_requested`, `preflight_passed`, `preflight_revise`, and `preflight_dropped` (an invalidation sent the attempt back). The release summary lists every verdict.
- **The pre-flight adds a check; it never replaces the six reviews,** which still judge every version.

## Review binding and independence (v1.7)

- A review node's packet lists the files it must judge, the version's registered bytes, and a `"reviewed": {key: sha256}`
  block to copy into review.json. Per-item inputs (e.g. assets) bind as one digest of their sorted hashes.
- A review naming other bytes is refused before an attempt is used (the same rule fails an attempt for executors that
  don't submit). A prefix of at least 12 hex characters is accepted.
- `vpg.project.yaml` `reviews: {binding: warn | require, independence: warn | require}` (default warn). Under require,
  a review without the block is refused, and so is one submitted by an identity that produced what it judges: the
  submitter of those artifacts' attempts, or their node's bound agent.
- Each review records `actor`, `inputs`, `reviewed` and `independent`. N23 warns about unbound, non-independent and
  pre-v1.7 reviews of the released version.

## Agents' notes and triage (v1.7)

- A named human's feedback re-opens a passed gate, and N21 interprets it on the next run.
- An agent's note (`vpg feedback --actor agent:<id>`) re-opens nothing. It is recorded with status `triage` and a hold:
  no version passes the gate while it waits, N21 may not map it, `vpg unblock` never releases it, and N23 refuses the
  release. `vpg feedback --triage F --accept|--dismiss --reason … --actor <human>` rules on it (an agent granted
  `feedback-triage` may, never the note's author). Accepted, it becomes open feedback that counts as the human's (N21 can't
  close it with no action); dismissed, it is closed with the reason.

## Human-review checkpoints

| Node | Decision | Locks |
|---|---|---|
| N03 | Objective, audience, runtime cap, quality bar | evaluation.md |
| N04 | Product truth (terminology, claims, scenarios) | product.md |
| N06 | Creative direction | creative.md |
| N09 | Visual system and production spec | design.md, production.md |
| N23 | Release, or changes requested with feedback | — |

None of these decisions can be delegated (v1.7): each covers human-judged dimensions, or, for N23, the reviewed film.
Some other human-only acts can be, to one named agent until a date, with `vpg grant`, but never accept-config, and never
over a hold or a defect that needs a human; see the SOP.

Every human action is one durable unit: approve, reject, feedback, unblock, a waiver, accept-config, a change request, resolve, invalidate, a grant and rollback. Its state changes stay in memory, and its ledger events are held back. The state is saved once, with the events written ahead inside it (`runtime.pending_ledger`); then the events go to the ledger.
- A failure before the save records nothing, and a rollback also puts back the documents it restored.
- A failure of only the ledger write leaves the action standing. The next command that changes the run writes the pending events, so none is lost or duplicated.
- An approval also locks its documents before it is recorded, all or nothing, and only in the version the workflow accepted: a document edited since is refused. If a document can't be locked (it is missing, or it is not a regular file), the approval is refused (exit 4) and nothing is written. The state and the ledger never hold a decision that didn't take effect.

A MUST dimension must be scored by someone independent of the node's author, and the validator enforces this:
- review-type dimensions sit only on review nodes;
- human-type dimensions sit only on human-gated nodes (the named human's decision covers them, and the ledger records which) or on review nodes (recorded as pending).

The release summary lists every human dimension still awaiting a named human, and every one a dry run merely auto-approved, for example the adult-professional voice and the music supporting it. No agent may claim them. It also lists non-blocking warnings (frozen inputs that changed on disk, declared redactions, reviews submitted within seconds of each other) and a human record of every action under a human name, flagged when it was fast, had no reason, shared a reason, cited delegation or released a hold. When `privacy.names` is declared, a readable listed name makes the version unreleasable.
