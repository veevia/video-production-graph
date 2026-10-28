# Quality workflow

**TL;DR:** `workflow: quality` adds two human gates before anything is built: a look gate (N26) that measures 3 to 5
style frames against the approved spec and a pinned set of benchmark films, and an animatic gate (N27) that checks the
timing on a scratch read. The spec family becomes control documents, technical QA measures the spec's build gates and a
ratchet, and N21 can propose spec changes. It is opt-in: the default graph and every run on it are unchanged.

- Graph: [`graph/workflow-quality.yaml`](../graph/workflow-quality.yaml), generated from the default graph and the
  overlay in [`vpg/quality.py`](../vpg/quality.py) by `python -m vpg docs`. Never edit it by hand: `vpg docs --check`
  fails when it drifts, so a change to a default node reaches the quality graph too.
- Catalogs: [nodes](quality-node-catalog.md), [edges](quality-edge-catalog.md),
  [control access](quality-control-access.md), [diagram](quality-graph.mmd).
- Decision: [ADR 0002](adr/0002-quality-workflow.md).

## Why

A recent 90-second case-study film (NDA) went through several full builds in which reviewers kept finding problems
that no gate measured: how saturated the frames were, how much moved, how readable product UI was in an embedded
player, how the voice sat over the music. Each was found in a finished master, where it costs a rebuild.

- reported: the reviews of that film named the look and the pacing more often than anything a gate checked.
- observed: in the finished runs in this workspace, the reviewer scores sat at or near the ceiling across versions (the
  [saturation alarm](judging.md#saturation-alarm)), so the reviews could not tell a better version from a worse one.
- assumed: proving the look on a few stills and the timing on a scratch read is cheaper than finding either in the
  master. The [research notes](research/quality-evaluation.md) cite the practice this borrows (style frames, animatics,
  measured references).

## The flow

```text
N06 direction ─┬─ N07 script ───────────────────────────────┐
               └─ N09 visual system ─ N26 LOOK (human) ──────┴─ N08 storyboard ─ N27 ANIMATIC (human) ─┬─ N10 asset plan ─ N11 …
                                       ↺ rejected (max 2)                        ↺ rejected → N07 (max 2) └─ N12 voice …
```

Three default edges are replaced: N09 → N08, N07 → N12 and N08 → N10 (`REMOVED_EDGES` in `vpg/quality.py`). Nothing
downstream of N10 or N12 runs before both gates are approved. N22 can route a defect back to N26 or N27 like any other
producer.

## The two gates

| Gate | The agent produces | The completion check (`vpg/tools/`) | Then |
|---|---|---|---|
| N26 Look development | `look-frames.json`: one style frame per role in DESIGN.md `style_frames`, a motion test of MOTION.md's length, and the spec hash they were rendered from | `look:check`: the hash is current; DESIGN.md, its tokens and MOTION.md lint clean; 3 to 5 frames, one per role; every look-stage gate in GATES.yaml passes **for each role** (or for the roles the gate names) against the same role in the pinned registry, at 1920 px; the motion test is the right length and flashes at most 3 times a second. Plus `design:check_palette` | Wei approves the look; approval locks design.md and the visual spec |
| N27 Animatic | `animatic.json`: the storyboard beats timed to a scratch read (contiguous from 0 s to the read's end), a music sketch, and the spec hash | `animatic:check`: the hash is current; the **measured** runtime (the read, or the board video if longer) within STORY.md, with the beats covering it; each story beat within its share; pace, pauses and voice over music inside their animatic gates; every must-say term spoken, no banned phrase; every MUSIC.md hit point on a **measured** onset in the music sketch (no sketch: not measured, so it fails); and the loudness pre-check | Wei approves the timing |

Every file a manifest names must sit in that attempt's work folder (no `..`, no absolute path or link out of it), and
none may copy a reference or degraded-anchor file of the benchmark registry: the same bytes, or the same decoded
content (a still's pixels, audio's samples), so a re-saved copy counts. Similar is not a copy: a frame inside the
references' envelopes is expected to share their ground. The gates judge what was made for this production, never the
references. The accepted baseline (`previous_best`) is the production's
own work and may be reused. A re-encoded or edited copy is not caught; the human gate still sees it. Where a beat starts
and ends inside the read is the agent's declaration; everything else is measured.

The **loudness pre-check** mixes the scratch read with the music sketch at unity, for the length of the read, both as
stereo at 48 kHz (a mono read as dual-mono, as it plays in the master) in 32-bit float, and meters it as technical QA
meters the master (ffmpeg `ebur128`, BS.1770). It fails when the mix clips (true peak above 0 dBTP), is silent, or would
need more than `loudness.precheck_max_limiting_db` (MIX.md, default 6 dB) of limiting to reach MIX.md's integrated
target within its true-peak ceiling. N09's check keeps MIX.md's loudness equal to the approved bar (evaluation.md over
the defaults), the values N15 judges the master against.

A **hit point** lands on an accent in the music sketch: an onset (a rise of at least 6 dB in short-term energy within
30 ms) within 0.15 s of the MUSIC.md time whose strength is at least 3 dB above every other onset within 0.5 s, not
counting onsets that land other declared hit points. Strength is how far the onset's peak rises above the music's median
level over the surrounding second, ignoring silence, so the music coming in is not an accent. A steady pulse has onsets
everywhere and accents nowhere. These thresholds are assumed (`vpg/tools/animatic.py`).

A failing check fails the attempt. The agent's next packet lists each failure with the gate, the rule ids, the role,
the value and the envelope; three failures escalate to Wei. A gate that cannot be judged (no envelope, not measured,
the registry missing, unpinned or changed) blocks like a failure: nothing passes unmeasured. A passing check is
evidence for Wei's decision, never the decision (R-PRO-002).

Both gates are named humans' decisions by default. If they stall a run, a project may let a relaying agent pass them
provisionally like N03, N04, N06 and N09:
`autonomy: {mode: provisional, principal: Wei, provisional_gates_extra: [n26_look_development, n27_animatic]}`. No other
node can be added that way.

## Who writes each spec document

Each document has exactly one writer, which validates it (a writer never lints another node's document) and locks it
when its gate is approved. After that it changes only through `vpg change-request`; an edit outside the workflow blocks
the run until `vpg restore-control` puts the accepted version back.

| Document (control id) | Written, validated and locked by | Read by |
|---|---|---|
| `spec/GATES.yaml` (`spec-gates`) | N03, with the objective: `qualitygates:check_bar` | N26, N27, N15, N21 |
| `spec/STORY.md` (`spec-story`) | N06, with the direction: `qualitygates:check_spec` | N07 (`specchecks:story_terms`), N08 (`qualitygates:check_beats`), N27, N21 |
| `spec/VOICE.md`, `MUSIC.md`, `MIX.md` (`spec-voice`, `spec-music`, `spec-mix`) | N09, with the visual-system direction | N12, N14, N27, N21 |
| `spec/DESIGN.md`, `DESIGN.tokens.json`, `MOTION.md` (`spec-design`, `spec-tokens`, `spec-motion`) and `controls/design.md` | N26, with the look (N09 drafts design.md and leaves it open) | N13, N21 |

GATES.yaml is a bar document, like evaluation.md: before the release review only a named human approves a change to
it, and neither a grant nor a relay covers it. The registry is part of the bar: GATES.yaml pins it by path and sha256,
and a changed registry (its baseline included) is refused until a change request re-pins it.

N03's `check_bar` proves the bar can judge before anyone builds against it: GATES.yaml lints clean; the registry is
there, matches its pin and passes its own check (no reference outside its own envelope, every degraded anchor caught,
every registered file intact); every blocking gate with a benchmark envelope has one; and every reference shows a style
frame for each look role. A changed approved document must bump its `spec.version` (`check_spec` compares it with the
approved snapshot).

## Technical QA and the ratchet

In the quality workflow N15 runs `qualitygates:technical_qa`: the default technical QA, then every build-stage gate in
GATES.yaml on the master, its stems and its text layers (GAT-SPEC), and the ratchet (GAT-RATCHET): a metric whose gate
says `ratchet: true` may not get worse than the registry's accepted baseline (`previous_best`) beyond its tolerance,
unless a named human waived it in GATES.yaml. Each finding is traced to the node that made the measured artifact.
Its report is `quality-gates.json`.

Both dimensions cite R-REG-001, which is not yet adopted, so their findings are nits that block nothing. **Wei decides
when they block:** `vpg memory promote --rule R-REG-001 --to adopted --human --by <name> --adopted-in
rubrics/quality-gates.yaml …` makes both MUST, from the next accepted configuration.

## Spec proposals

When feedback shows that the spec itself is wrong, not the build, N21 may add `spec_proposals` to `interpretation.json`:

```json
{"doc": "story", "change": "modify", "rule": "ST-TERMS-001", "proposal": "say 'own range' instead of 'normal levels'",
 "because": "F003"}
```

`qualitygates:check_proposals` refuses a malformed one (an unknown document or rule, a missing reason, two conflicting
proposals on one rule). None is filed automatically. `vpg status` lists each with the `vpg change-request` command a
person runs; an approved request re-runs the document's writer, and the change re-opens every gate that reads it (a
DESIGN.md change re-opens the look, a STORY.md change the animatic). `vpg spec diff` shows what changed.

## Adopting it

New production:

```bash
python -m vpg init <project> --workflow quality                      # templates in spec/, workflow: quality
python -m vpg benchmark init <project>/benchmarks/registry.json --id <registry id>
# copy each reference's media under <project>/benchmarks/ first: the registry stores paths relative to itself
python -m vpg benchmark add <project>/benchmarks/registry.json --id REF-A --kind reference --master … --voice … \
  --music … --narration … --ocr --style-frame title=… --style-frame product_ui=… --style-frame data_callout=… \
  --style-frame closing=… --by <name>
python -m vpg benchmark verify <project>/benchmarks/registry.json
shasum -a 256 <project>/benchmarks/registry.json                    # pin it in spec/GATES.yaml: benchmarks: {path, sha256}
python -m vpg validate --workflow quality
python -m vpg run <project>
```

- Register each reference with all of its media in one `add`; an entry cannot gain media later.
  * `--narration` (timings.json, script.json or the spoken words) gives the pace envelope.
  * `--ocr` (tesseract) gives the UI text-size envelopes. `vpg benchmark measure --ocr` adds them to entries that
    were registered without it.
  * Pin the registry after the last `add` or `measure`; a later change re-opens the bar.
  * N03 refuses a gate that has no envelope, and says which reference lacks what.

- A run stays on the workflow it started with. Changing `workflow:` mid-run is refused (`vpg status` says so); finish
  the run, or move `.vpg` aside and start a new one.
- An existing run on the default workflow is unaffected, except that engine code changed: it needs one
  `vpg accept-config` by a named human between versions, as after every engine release
  ([releases](releases.md)).
- The benchmark films are the project's choice: they must be films Wei would accept, measured on the same basis.

## Non-goals

- No taste verdict: the gates measure properties of frames and audio; Wei decides whether the look and timing are right.
- No change to the default graph, its nodes, prompts or rubrics, and no migration of a running production.
- No automatic spec edits: proposals are listed, a person files them.
- No new dependency: the checks use the engine's numpy, Pillow, ffmpeg and (for the UI-text metric) tesseract.

## Rollback triggers

- A look or animatic gate blocks a production whose frames Wei approves on sight, twice in one run: set
  `provisional_gates_extra` for that run and re-calibrate the envelope (references or tolerance) before the next one.
- A metric's envelope flags one of its own references after a re-measure (`vpg benchmark verify` fails): stop using
  that gate until its basis is fixed.
- The generated graph fails `vpg validate --workflow quality` after a default-graph change: fix the overlay in
  `vpg/quality.py` before any project starts on it.
- To remove the workflow: delete `graph/workflow-quality.yaml`, its catalogs, `vpg/quality.py` and the `quality` entry
  in `WORKFLOWS`; default runs are unaffected.

## Evidence and limits

- observed: a production whose style frames sit on a saturated ground fails N26 three times and is escalated; no
  storyboard, animatic, asset, voice or scene node is dispatched. With frames like the references, the same production
  runs to `RUN_PASS` with both gates approved first, every spec document locked by its writer, the build gates and
  ratchet measured as nits, and telemetry covering every node (`tests/test_quality_workflow.py`).
- observed: a rejected animatic re-runs the script and comes back before anything is built; a writer that edits
  another node's spec document is reverted and fails the attempt; an edit outside the workflow blocks the run.
- assumed: per-role envelopes from two to five references, with the metric tolerances in `vpg/metrics.py`, separate
  acceptable frames from regressed ones; they are not yet calibrated on Wei's judgements
  ([judging](judging.md#calibration)).
- observed, on real films (2026-10-27):
  * On the motivating film's versions, with the two benchmark films as references, the build-stage metrics agreed with
    an independent set of gates on 29 of 32 checks. The three differences were all stricter.
    [Craft metrics](craft-metrics.md#real-film-evaluation-2026-10-27) has the detail.
  * Following "Adopting it" literally on a real benchmark film surfaced three gaps, now fixed: media outside the
    registry folder, a missing `--narration` or `--ocr`, and a `measure --ocr` that skipped what it should fill.
  * With the commands above, N03's bar check passes with 16 envelopes.
- The look gate's UI text size is the median word height that tesseract reads confidently on a style frame; the
  template judges it on the product-UI frame only (`roles: [product_ui]`). On real footage it read words down to about
  3 px at player width. Words it cannot read confidently are left out, so the very smallest text can go unseen, and a
  UI frame with fewer than 3 such words cannot pass.
- The look gate measures stills and a short motion test; motion quality across the whole film is still judged at N16
  and by Wei.
