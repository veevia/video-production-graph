# Typed state model

> **TL;DR:** One pydantic model (`vpg/models.py` → [`schemas/state.schema.json`](../schemas/state.schema.json)) travels
> through the graph, saved atomically to `.vpg/state.json` after every step and checkpointed on every pass or failure.

## The six separations (plus runtime control)

| Section | Category | Mutability and who writes it |
|---|---|---|
| `inputs` | **Immutable project inputs**: brief, sources (sha256, evidence tag), constraints, and gap waivers | Written by N01/N02 only; **frozen** by N02 (hash recorded). Any later change blocks the run. Only an intake loop unfreezes it, and the ledger records that. `waivers` are added only by a named human (`vpg unblock … n02_source_validation --waive <gap> --note …`) before the freeze. |
| `production` | **Mutable production state**: objective, audience, business and product context, assumptions, open questions, and refs to the brief, concepts, creative decision, narrative, script, storyboard, shot list, scenes, design system, asset plan and registry, audio spec, voice, timings, master and captions | Only through declared outputs (`state_path`, `state_fields`) that match the node's `state_write`, and re-validated against the typed model after every write. |
| `artifacts` | **Derived artifacts**: content-addressed records (sha256, bytes, node, attempt, item, lineage inputs, tool, version, current/superseded) | The engine at fan-in. Artifacts are never deleted; older ones are superseded. |
| `evaluation` | **Evaluation state**: reviews (scores, tool findings, overridden agent passes, pending-human dimensions), defects (signature, severity, trace, smallest fix, status), feedback, gate results, before/after comparisons | The engine, from review merges and the interpreter's output. |
| `history` + `.vpg/ledger.jsonl` | **Historical memory**: versions, decisions, approvals (human, dry-run or under a grant, with the outputs decided on), retries, checkpoints, change requests (open, approved, rejected, deferred with their revisit trigger), delegation grants; the hash-chained event log, where every event of an act under a grant carries the grant and its grantor | Append-only. |
| `knowledge` | **Reusable cross-project knowledge**, by reference: rule-base path, version, sha256, enforced and advisory rule ids, proposals | Read-only during a run. Changed only through `vpg memory apply` by a human. |
| `runtime` | Execution control: node status, attempts, failure streak, fingerprints, dispatch tokens, traversals, batches, rework cycles, escalations | The engine. |
| `controls` | Registry of the control documents: status, version, accepted and approved hashes, snapshot. A quality-workflow run also registers the spec family (`spec-design` … `spec-gates`, kept in `spec/`) | The engine; see [control access](control-access.md) and [quality control access](quality-control-access.md). |

## Resumability

- `Engine(project)` resumes from `.vpg/state.json`. State is saved at four points: when a batch is dispatched, before a command bound at submit runs (the node is RUNNING), when each execution returns (`NodeRuntime.executed`), and when a node starts being judged (VALIDATING).
  - On resume, RUNNING and VALIDATING nodes return to READY, and the interrupted attempt does not count against the retry budget.
  - Interruptions are counted, and the node escalates at `retry.max_attempts` consecutive interruptions. A node is charged when the process died while it was executing or running its bound command (RUNNING, not `executed`), or while it was being judged (VALIDATING). The reset records why in `NodeRuntime.failures`, so the next attempt's packet explains the restart and names any discarded edit. A node whose execution had returned before another node's judging crashed is not charged.
  - An interrupted control-document writer re-runs from scratch. Resume therefore restores each document it may write to the version the saved state accepted, and keeps the discarded text in `.vpg/controls/<doc>.interrupted-<node>-r<n>.md` (ledger: `interrupted_edit_undone`).
  - Every accepted version is kept by hash, so this works even when the crash landed between accepting an edit and saving the state.
- `runtime.config_sha256` fingerprints the workflow, rule base, rubrics, default thresholds, artifact schemas, prompts, agent role files, project config and the engine's own code (`engine/<file>`) at run creation. Any later change blocks the run until a named human runs `vpg accept-config`.
- A node with a bound command also fingerprints that command's code (`command_code`: the scripts it names and their top-level
  project directory, or `command_deps`), so a pipeline edit re-runs it. Other nodes' fingerprints do not include it.
- A node's fingerprint also covers `files:<pattern>` inputs: N01 fingerprints `files:sources/**`, so adding a source re-opens intake.
- A review's fingerprint includes `runtime.config_sha256` (`judged_under`), so accepting a configuration change re-runs
  every review instead of carrying its verdict forward, and so does a rollback to a checkpoint judged under another one (v1.5).
- `NodeRuntime.fingerprint_scheme` and `last_pass_scheme` record the fingerprint scheme (`engine.FINGERPRINT_SCHEME`) a
  dispatch and a pass were fingerprinted under; none means before v1.5. `NodeRuntime.settled_fingerprint` is the
  dispatch fingerprint without the rework defects it was sent to fix, which is what a pass records, so an unchanged
  node memo-skips after a fix. `vpg accept-config`, and a rollback to a
  checkpoint judged under another configuration, re-derive only older passes.
- A `defects:rework` input covers the defects routed to the node, which are all its context holds; `defects:open` and
  `defects:all` cover every defect (v1.5). `vpg accept-config` carries passes recorded under an earlier scheme over when
  their checkpoint reproduces them (`passes_carried_over`); `--adopt-code` keeps a pass that predates code fingerprints.
- At the freeze, each local source that still matches its registered hash is copied, read-only, to `.vpg/sources/<sha256>`
  (`sources_kept`). `vpg status` checks frozen sources and warns, by id, on a missing, changed, hard-linked or symbolically
  linked one. It re-hashes a file only when its stat identity changed (`.vpg/sources/verified.json`). The store's own
  `.gitignore` ("*") keeps the copies out of any commit of `.vpg/`; `git add -f` adds one on purpose.
- A node's fingerprint also covers every control document it reads, through `controls.read` or a `control:` input. A re-checked reader therefore re-runs when the document changed, instead of memo-skipping.
- `NodeRuntime.rechecks` is the depth of the control-document re-check chain that sent a node back (0 for any other invalidation). A depth above the number of control writers escalates the writer, and unblocking resets every depth to 0.
- `NodeRuntime.force_rerun` is set by an explicit `vpg invalidate`, and on the loop target when a spent loop is unblocked. It makes the next dispatch skip the memo once.
- STEP_PASS nodes are never re-dispatched. STALE nodes re-run only when their fingerprint changed, so approved outputs are not regenerated.
- The ledger and `state.json` are two files. A human action's ledger events commit inside its state save (`runtime.pending_ledger`, write-ahead). If they don't reach the ledger, the next command that changes the run writes them, skipping any already there.
- A control document that is deleted, or replaced by a pipe or device, counts as a change. A node that does it fails its attempt and the accepted version is put back. Outside the workflow it blocks the run until `vpg restore-control` recreates the document. Every accepted version is kept by hash (`.vpg/controls/<doc>@<sha16>.md`).
- Every state save records the ledger's last event (`runtime.ledger_tail`). A ledger that no longer contains that event (deleted, truncated or replaced after the save) is refused in three places:
  - `vpg ledger-verify` and N23 report it.
  - The next command that changes the run refuses to open it until the ledger is restored, so nothing new hides the loss.
  - Only a named human may accept the loss (`vpg accept-ledger-break`). The break is ledgered, kept in `runtime.ledger_breaks` and listed in the release summary.
  - While a human action's events are pending (`runtime.pending_ledger`), `ledger-verify` reports them as pending, not as a loss.
- A ledger write that was cut short leaves a last line without its newline. Before the next append, the engine moves that fragment to `.vpg/ledger.jsonl.torn-*`, so events are never glued together.
- An engine writes `state.json` and the ledger only when each file is exactly as it last read or wrote it (same inode, size and mtime). An engine that fell behind another process raises `ConcurrentChange` (exit 4) instead of losing that process's update or breaking the hash chain. Reopen the run and retry.
- `vpg rollback <project> --checkpoint N --actor <name>` restores any checkpoint (time travel). It needs a named human, and it is ledgered.
  - Budgets never go backwards: loop traversals, rework cycles and repeated-defect counts carry forward.
  - Control documents go back to the versions the checkpoint accepted (kept by hash), and the current text is kept as `.vpg/controls/<doc>.before-rollback-*.md`.
  - Open escalations stay open. Unblocking one whose node passed at the checkpoint re-checks that node and its dependents; it never resets the node.
- An escalation of a loop bound records the first spent loop (`edge`) and every spent loop of the same result (`edges`); unblocking resets each. An open escalation whose node stops being BLOCKED (an invalidation, a rollback) records why (`reopened`), which the unblock's ledger entry repeats.
