# Product

<!-- impeccable:product-schema 1 -->

> Public capability contract. Observed capabilities describe the implementation;
> inferred statements describe product intent, not measured user outcomes.

## Platform

web

## Users

* The owner and director of AI-produced films. The owner
  opens the app to see what needs his decision, understand why a version passed or failed, watch and compare versions,
    and leave decisions or timecoded notes for the producer. **[inferred]**
* The AI producer agents that build the films read his reactions (through `inbox.py`, the producer inbox API or the
  Markdown export) and answer them: picked up with a plan, then addressed in a version. They are readers of his output,
  not users of the interface. **[observed: the `ack` reaction and export; the inbox and `resolve` since 5.2]**

## Product Purpose

Make AI video production observable and steerable from one local place. For every production job the app shows what
each pipeline node and gate produced, why it passed, failed or waits, and what needs the owner. Its Library shows every
video under explicitly configured library roots, grouped into films and versions. Success: within seconds the owner knows what needs attention and why,
and can record a decision or note the producer picks up. **[inferred from "observe how/what your subagent working on",
"a place where you can see and react", "load all videos ... with all versions"]**

The interface leads with decisions and keeps supporting evidence one step away. **[inferred: design intent]**

## Positioning

It is a glass box over the VPG workflow graph, not a generic media library or dashboard: it reads the engine's own run
state, ledger, gates, reviews, telemetry and candidate folders, maps every piece of evidence to the node that produced
it, and returns the owner's reactions to the producer. **[observed]**

## Operating Context

* Runs locally on macOS or Linux as a Python server at `127.0.0.1:4420`, opened in a desktop browser.
  Embedding is not supported by the current framing policy. **[observed]**
* Reads production jobs under explicit `--root` folders and the bundled or explicitly selected VPG engine.
  Library indexing requires `--library-root`; no home folders are scanned by default. **[observed]**
* Used between production rounds: after a producer agent finishes a version, before a release decision, and when
  comparing a regression against an earlier version or a benchmark film. **[inferred]**

## Capabilities and Constraints

Every capability below exists today and must survive any redesign (owner, 2026-10-27: "don't lose the capabilities").
**[observed]**

* Needs you: decisions for the owner across all jobs (human gates, release decisions, P1 and P2 defects, rework
  verdicts), and an FYI list of what the producer is still working on, with the reason for each item. A gate the
  owner already decided while the engine run waits reads "Decided by you · engine waits" and is not counted.
* Jobs: per job, the workflow's pipeline map (25 nodes, or 27 in the quality workflow) with each node's status and
  provenance (governed run, direct run recorded or not), the versions table, the governed run card with the ledger and
  telemetry coverage, and the job README head.
* Node detail: status with its reason, the node's contract, evidence files and previews, automatic checks, reviews and
  scores, defects, telemetry evaluations and spans, and reactions.
* Version: player with captions, a timeline of defects, key frames, caption cues and notes, a key-frame filmstrip,
  verdicts per reviewer and for automatic gates, the release decision (N23) with its caveat, findings with filters and
  agree, disagree, note and details, reviews, work notes, and reactions; the owner's own verdict on each version
  (rail, contact sheet, versions table, room sentence) and its runtime against the brief's cap.
* Gates: human gates with their state and the recorded decision, automatic gates and reviewer gates.
* Compare: two versions in sync, "what changed" with better or worse readings, runtime targets per version and
  measurement-basis warnings, score deltas, verdicts, and a preference (A, B or tie).
* Activity: every reaction with its thread (picked up, addressed, reopened), the pickup summary, Waiting, Picked up
  and Addressed, filters, a link to each reaction's target and a permalink to it, Copy for chat and Download .md.
* Producer channel: each job's producer inbox (`inbox.py` and `GET /api/inbox`) listing the owner's waiting notes
  verbatim with links; the producer's pickup and resolve threaded back under each note; the owner's Reopen; a
  Producers line on the Inbox and one line per note (its state, the target in words and the producer's first
  sentence) linking to its thread, with Copy for the producer.
* Library: videos under the configured roots grouped into films and versions, search, filters, sort, other-video collections,
  film pages with variants, files and copies, compare of two versions, and notes; Rescan.
* Reactions: approve, request changes, agree, disagree, note, flag, prefer, the producer's acknowledgement and resolve,
  and the owner's reopen, on versions, nodes, gates, findings, moments and library videos. A library film or file that
  belongs to a job opens in that job's Files.
* Keyboard: Space play or pause, Left and Right seek (Shift for 5 s), N note at the current time, G gates, C compare,
  Esc close, ? help.

Constraints:

* Local only: bound to `127.0.0.1`, never online, no CDNs or downloaded web fonts; production content is under NDA.
  **[observed]**
* Read-only over every project. Server writes are the owner's reactions and library index, in the private data
  folder. The browser also caches thumbnails in IndexedDB; the README describes removal and temporary profiles.
  Approvals are reactions for the producer, not engine decisions. **[observed]**
* Python standard library server and vanilla HTML, CSS and ES modules; no build step and no dependencies to install.
  **[observed]**

## Brand Commitments

* The editorial monochrome interface follows [DESIGN.md](DESIGN.md). No external design-reference file is required.
* No organisation brand applies; the client brands of the films stay inside the films. **[inferred]**

## Evidence on Hand

* Public tests use fictional jobs, reviews, gates, ledgers and locally generated media. No production data is bundled.
* Synthetic test results do not establish real-world usability, creative quality or human approval.

## Product Principles

1. Decision first: what needs the owner leads every entry point; everything else is one step away. **[inferred]**
2. Evidence before verdict: every status links to the artifact, check or review that produced it. **[inferred]**
3. Honest signals: never claim more than the data supports (basis changes, per-version targets, provisional
   approvals, direct runs not in the ledger). **[observed: owner feedback on hidden regressions]**
4. Observe by default, write on purpose: production files are read-only; reactions and caches stay separate. **[observed]**
5. Familiar operation: standard navigation and controls; the brand lives in precise details. **[inferred]**

## Accessibility & Inclusion

Keyboard operation, visible focus, labelled controls, WCAG 2.2 AA contrast and reduced motion are design requirements.
The synthetic capability check exercises navigation and controls; it is not accessibility certification.
