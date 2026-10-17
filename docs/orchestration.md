# Multi-agent orchestration

> **TL;DR:** 18 specialised agents work the nodes. A deterministic orchestrator dispatches only conflict-free work in
> parallel, fans the results in on one thread, rejects stale or low-quality work, and routes rework to the exact node
> that caused a defect. Roles and bindings: [agent catalog](agent-catalog.md).

## Roles by phase

| Phase | Agents |
|---|---|
| Understand | Context Research (N01–N02), Product Understanding (N04), Production Orchestrator (N03) |
| Design | Creative Director (N05–N06), Narrative and Script (N07), Storyboard (N08), Visual Design (N09) |
| Build | Asset Generation (N10–N11), Voice and Audio (N12), Motion Design (N13), Composition (N14) |
| Review | Technical QA (N15), Creative Critic (N16, N20), Design-System Reviewer (N17), Accessibility Reviewer (N18), Product Accuracy Reviewer (N19) |
| Learn | Production Orchestrator (N21–N22), Final Approval (N23–N24), Learning and Memory (N25) |

Independence rule: a reviewer never reviews its own work. The Creative Critic scores the film, not the script it didn't write, and the script's author is not a reviewer.

## How the orchestrator keeps parallel agents coherent

| Guarantee | Mechanism |
|---|---|
| Distribute independent work | `select_batch` dispatches every ready node whose writes don't overlap (state paths, control documents) and whose single-flight resources (tts, renderer, capture, image-api) are free, up to `parallel_limit`. Examples: N08 ∥ N12, and the six reviewers. |
| Track dependencies | A node is ready only when every incoming flow edge is satisfied (join = all). |
| Prevent conflicting changes | Write-set disjointness per batch. Agents never write state directly: the engine applies declared outputs only. A node that may write a control document runs **alone** (N03, N04, N06, N09, N25), so every control edit is attributable and no reader sees a half-written document. An edit a node may not make is reverted: it is charged to that node when it ran alone; with siblings in flight the run blocks as `control-integrity` for a human, and no sibling is blamed. |
| Maintain state integrity | Typed re-validation after every write, frozen inputs, approved-document hashes and a hash-chained ledger. Frozen sources are kept by content in `.vpg/sources/`, and `vpg status` warns when one changes on disk. Before dispatch, each input artifact is re-hashed (for the packaging node, every deliverable), and one changed on disk blocks the run. N23 re-checks every deliverable too. `vpg invalidate` forces its target to re-run once, so the documented recovery regenerates the artifact. When a writer changes a control document, nodes that already passed while reading it (and are not upstream of the writer) are re-checked. Their fingerprints cover the documents they read, so a re-checked node re-runs rather than memo-skipping. The validator rejects graphs whose re-checks can cycle back to a writer, and a run-time chain bound escalates any cycle that slips through. A mid-run change to the workflow, rule base, rubrics, thresholds or project config blocks the run until a human accepts it (`vpg accept-config`). The engine refuses to run a graph that fails structural validation. Every read, hash and ledger append of a project file opens it non-blocking and refuses anything but a regular file. That covers sources, outputs, artifacts, control documents, the project config, the ledger and the lock file. A directory, pipe or device there is refused (exit 4), or blocks the run with a reason; it never hangs a command while it holds the project lock. |
| Critique node outputs | Completion checks on every node; six parallel REVIEW nodes on every version. |
| Reject low-quality outputs | A failed check means ITERATE, with the reasons carried into the next attempt. After `max_attempts` consecutive failures the node is BLOCKED and a report is written. |
| Trigger targeted rework | The gate routes each blocking defect to its traced node through N22's bounded loop edges; descendants re-run or are memo-skipped. |
| Integrate approved artifacts | Content-addressed registration with lineage; superseded artifacts are kept. |
| Preserve decisions and lineage | `history.decisions` and `history.approvals` (human or not), `vpg lineage <artifact>`, and the ledger. |
| Keep the whole coherent | Every version passes the same six reviews. N21 compares before and after. Release waits for a named human. |
| Stale or duplicate work | Each dispatch carries a token. A submission with an old token, or to a node that has no open task, is rejected. So is an unknown output key, a missing file, a path outside the project or a missing required output. Refusals exit 4 and use no attempt. |
| Parallel processes | Every command that changes a run holds the project's exclusive OS lock (`.vpg/lock`, `vpg/lock.py`), so simultaneous `vpg submit` calls queue instead of overwriting each other's state. The OS frees the lock if a process dies. `status`, `tasks`, `lineage` and `ledger-verify` open the run read-only, and they never wait or write. |

## Driving a production with real agents

1. Run `python -m vpg run <project>`. Agent nodes get task packets in `.vpg/tasks/` and the run waits (WAITING_RUNTIME).
2. Several packets can be open at once. Hand each to its agent (see `runtime_binding` in `agents/*.yaml`; for example the FDE Design Reviewer for N16/N17/N20).
3. The agent writes its outputs into the packet's work directory and runs `python -m vpg submit <project> <node> --token … --actor <agent> --output key=path [--review review.json]`.
4. The engine validates the submission, then continues; the next `vpg run` dispatches whatever became ready. If another command is changing the run, the submission waits for up to `--lock-timeout` seconds (default 900), then fails with exit code 4 and names the lock holder. Errors never print a traceback; retry the same command.
5. At human gates, Wei runs `python -m vpg approve <project> <node> --actor Wei --reason "<Wei's words>" [--reject --feedback "…"]`.
