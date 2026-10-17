# Extension guide

> **TL;DR:** Everything is data plus small functions. Change the YAML or add a function, run
> `python -m vpg validate`, `python -m vpg docs` and `python -m pytest -q`, and the validator and tests tell you
> what's missing. Never hand-edit generated files (catalogs, graph.mmd/svg, state/workflow schemas, design-principles.md).

## Start a new production

```bash
# From the standalone checkout, or 02_Workflows/video-production in the workspace:
python -m vpg init /absolute/path/to/film --name "<film>"   # 6 control docs, brief, vpg.project.yaml
# fill sources/brief.md, then:
python -m vpg run <project>          # dispatches task packets / runs tools until it waits for an agent or a human
```

- Put product evidence (a spec, prototype capture or recording) and brand authority in `sources/`. N01 must register them, or N02 loops back with gaps.
- Bind the renderers, TTS and assembly in `vpg.project.yaml → commands:` (node id → shell command).
- Changing `vpg.project.yaml`, the workflow, `knowledge/rules.json`, `rubrics/`, `config/defaults.yaml` or the engine's own code (`vpg/**/*.py`) while a run is in flight blocks that run. A named human then accepts the change with `vpg accept-config <project> --actor <name> --reason "…"`, and the reviews re-run under it.
- `{python}` in a command is replaced with the running interpreter.

## Add a project command (renderer, TTS, assembly…)

1. The command runs in the project directory. `VPG_CONTEXT` holds the path of `<workdir>/context.json`, which contains:
   - `node`, `attempt`, `workdir`;
   - `producer`: the node, attempt, code file hashes and `code_sha256` for the command producer;
   - `inputs` (paths per artifact key) and `inputs_items` (per map item);
   - `agent_outputs`, `controls` (paths), `defects` (the rework defects to fix) and `items`.
2. It writes `<workdir>/outputs.json`: `{"outputs": {key: relative path or list}, "data": {...}, "findings": [...], "outcome": "pass"}`.
   A finding about one scene, unit or item should carry `element` (its id). The defect lists the items its latest review
   named (`elements` in `defects`), so a command can re-render only those.
3. A non-zero exit fails the attempt; the retry carries stderr into the next attempt. `command.log` keeps stdout and stderr.
4. Honour `defects`: re-render only the scenes, units or items they name. That keeps regeneration minimal; the engine can only skip whole nodes.
5. Make it reproducible: identical inputs must give byte-identical outputs. Downstream nodes memo-skip by content hash, so a command that
   drifts by a few bits re-runs every reviewer after it.
   - Don't embed timestamps, random seeds or absolute paths.
   - ffmpeg filters with two audio inputs (`sidechaincompress`, `amix`) vary with frame interleaving. Give both inputs fixed frames with
     `asetnsamples=n=1024:p=0`, as `assemble.py` does.
6. Its code is part of the node's fingerprint: each script file the command names, plus every file under that script's
   top-level project directory (all of `pipeline/` for `{python} pipeline/scenes.py`), caches excluded. Editing a scene
   engine the script loads therefore re-runs the node instead of memo-skipping it.
   - **A file the pipeline generates into that tree is output, not code.** List it in
     `command_code_exclude: [pipeline/film/data.js]`, or every node bound to the tree re-runs whenever it is rewritten.
     vpg ledgers `command_code_changed` when a node's code changed while it was in flight, and `vpg status` warns.
     An edit to real code during an attempt raises the same warning and costs one extra re-run; don't exclude real code
     to silence it.
   - To narrow or widen that for one node, list globs in `vpg.project.yaml`, for example
     `command_deps: {n12_voice_audio: [pipeline/voice.py, pipeline/_ctx.py]}`. The list replaces the directory rule, a
     glob starting with `!` excludes, and the named scripts always count.
   - Settings with the wrong shape (a string where a list belongs, an absolute or `..` glob, an unknown node) are refused
     when the run opens.
   - `vpg status` also warns when a command names no script in the project (for example `cd pipeline && …`), so its
     code isn't fingerprinted: list its files in `command_deps`.
   - Code outside the project, installed packages and the interpreter version are not hashed.
   - If the command reads an earlier attempt's output (for example `work/<node>/a1/outputs.json` or `a*` attempt
     directories), use `from vpg.reuse import stamp, may_reuse`. Save records with `stamp(record, ctx_payload,
     schema="your-output/v1")`, and reuse only when `may_reuse(record, ctx_payload, schema="your-output/v1")` is true.
     The key covers the node, its command line and its code, but not your inputs: compare those yourself. `vpg status`
     warns, per file, when a code file reads earlier attempts (`work/<node>/a<N>`, or a glob over `a*` folders) without
     importing `vpg.reuse`. It is advisory because the engine can see source text, not intent.
7. A recorded walkthrough is one planned asset with `spec.format: take`: a JSON manifest in which every entry with
   `file` and `sha256` (at any depth, paths relative to the manifest) must exist inside the project and match. Its
   hash changes with every new take, so nothing downstream memo-skips onto an old one. A video asset takes an
   mp4/mov/webm/mkv/m4v `spec.format` and is checked with ffprobe (size, optional `duration_s`).
8. A voice command's `timings` may carry word times, `words: [{unit, i, word, start, end, measured}]`, in the same
   time base as `units` (seconds from the start of the voice track). Caption sync (N18) then judges a unit's cues on
   them when the entries are its words in order (split as vpg splits spoken text: a run of letters or digits, with
   inner hyphens and apostrophes; an entry with no letter or digit, such as a lone dash, is ignored), at least one is
   `measured: true`, and every measured time is finite, ordered and inside the unit's span, give or take 0.1 s.
   Measured words keep their times. The time of an entry not marked measured is never read: such a word is given the
   whole gap between the measured words around it (or the unit's start or end, widened by the 0.1 s tolerance when the
   measured neighbour lies outside the span; across both neighbours when they overlap). Under the model that a unit's
   words follow one another inside its span, the no-lag and early-clear checks then flag every cue that could lag or
   clear early, and a cue that starts or ends on such a word may be flagged although it is in sync (the finding says
   the word was not measured). Any other unit keeps the character-length estimate over its whole
   span, which at a clause break can be most of a second off real speech. Chunk captions on the same measured onsets,
   and start and end cues on measured words.
9. Worked examples: `examples/sample-demo/pipeline/`.

## Declare live windows for privacy scans

`text-layers.json` may add:

```json
{"live_windows": [{"start": 12.4, "end": 16.9, "what": "screen-capture scroll"}]}
```

A live window is a time range where product UI moves and a sampled privacy scan is unsafe. When `privacy.names` is
declared, N23 decodes the whole master at the scan's rate: never below 10 fps, a pinned `scan_fps` or the stream's stated
rates. ffmpeg's fps filter must also report no dropped frame, or the scan fails closed. It OCRs every frame of each live
window in full, skipping only exact repeats of a frame already read in full.

`privacy-report.json` records only counts, per window: its number, start/end, frames in the window, frames decoded, OCR
reads, repeats and the sampled fraction. The frames a window covers are one integer range, [ceil(start × rate),
ceil(end × rate)), used both to test membership and to count. A window running past the last frame is checked up to it.
One that starts after the video ends, or isn't fully decoded, holds the release closed, named by its number. Reports
never include listed name text. Each window frame costs one full-frame OCR (about 0.3–1 s), so size
`privacy.time_budget_s` to your windows: an exceeded budget blocks the release instead of passing it.

## Publish versions for human review

Publishing is opt-in in `vpg.project.yaml`:

```yaml
publish:
  dir: outputs
  keys: [master, captions, voice, music, text_layers]
  auto: true
```

`python -m vpg publish <project> [--version N] [--check]` publishes every unpublished version (or one version).

- **Checks before copying:** the version's files must still be the bytes it registered. When `privacy.names` is declared,
  the release's own scan then runs on the version's master, using the live windows of the version's `text_layers` even
  when `keys` doesn't list them.
- **Held:** a readable listed name, an unreadable video, missing OCR, an incomplete live window, a changed file, or a
  different file already at the destination records `version_held` and copies nothing.
  - A held version isn't re-scanned unless it is asked for with `--version`.
- **Published:** a passing version is copied to `<dir>/v<N>/`, each copy is checked against the registered hash, and it
  is listed in `<dir>/_vpg-index.json` and `<dir>/_vpg-README.md`.
- **Auto:** `auto: true` publishes the version just cut, after the run saves it; a failure is ledgered as `publish_failed`.
- **Check:** `--check` writes nothing and scans nothing. It reads without the project lock, and lists what would be
  published, what is held, and published copies edited or removed since (`out_of_sync`). `vpg status` lists unpublished
  and held versions.

## Add a deterministic tool

Every command that changes a run imports the graph's tools, mid-run as well as at the start. A tool module outside `vpg/tools/` must therefore be importable by every such command: install it, or set the same `PYTHONPATH` for all of them. Otherwise the engine refuses to open the run.


1. Write `def fn(ctx) -> CheckResult` (a completion check or pre-check) or `-> NodeResult` (a tool node) in a `vpg/tools/<module>.py`.
2. Report problems as `finding(check, dimension, severity, message, evidence, traced, key, fix)` from `vpg/tools/_common.py`. `dimension` must be a rubric dimension id, and `traced` is the node that must fix it.
3. Read thresholds with `thresholds(ctx)`: `config/defaults.yaml`, overridden by the project's `evaluation.md` front-matter.
4. Reference it as `"<module>:<fn>"` in a node's `completion`/`run.tool`, or as the `check.fn` of a rubric dimension.
5. Tests enforce it: `test_tool_dimensions_match_what_tools_emit`, plus add a unit test in `tests/test_tools.py`.

## Add or change a node

1. Add it in `graph/workflow.yaml` with all 19 fields:
   - id, num, name, phase, subgraph, kind, purpose;
   - inputs, context, state_read, state_write;
   - agent, skills, tools, instructions, prompt;
   - outputs, completion, rubric, failure_conditions, retry, escalation, human_review, provenance, controls.

   Input refs are:
   - `artifact:<key>`, `control:<doc>` and `state:<path>`;
   - `rubric:<id>`, `reviews:current`, `feedback:open`, `defects:open|rework|all` and `knowledge:rules`
     (`defects:rework` is the node's own rework defects; `open` and `all` are every defect);
   - `files:<pattern>` (project files; a trailing `**` means every file below).
   Each ref is part of the node's fingerprint, so a change to it re-runs the node instead of memo-skipping it.
   A node whose tool reads a control document (`ctx.fm("<doc>")`, or `thresholds(ctx)` for evaluation) must list it in
   `controls.read`; the engine refuses the graph otherwise.

   For stage numbering, the validator enforces the 25 brief stages; a new stage needs `REQUIRED_STAGES` in `vpg/graph.py` extended too.
2. Wire its edges: every outcome needs an outgoing edge, join predecessors need loop or escalate edges for non-joining outcomes, and loops need `max_traversals`.
3. Add `prompts/<node>.md` (placeholders are checked) and bind a rubric.
4. Run `python -m vpg validate`, `python -m vpg docs` and `python -m pytest -q`.

## Add an agent

`agents/<id>.yaml` holds id, name, responsibility, nodes, inputs_allowed, controls, skills, tools, outputs, decision_authority, constraints, evaluation, escalation and runtime_binding. Bind it from nodes with `agent: <id>`. `test_agents_cover_18_roles` keeps the roster complete.

## Add a rubric dimension

In `rubrics/<id>.yaml`, each dimension needs:
- `id`, `name` and `brief_dimension` (one of the 18);
- `level` (MUST or SHOULD);
- `check.type` of `tool` (with `fn`), `review` (scored 1–5) or `human`;
- `threshold` and `target`;
- `rule` (an id in `knowledge/rules.json`);
- concrete `cases.never`/`cases.always`.

A dimension whose rule isn't enforced is downgraded automatically ([self-improvement](self-improvement.md)).

Placement (enforced by `vpg validate`): a MUST `review` dimension belongs on a review node, where an independent reviewer scores it. A MUST `human` dimension belongs on a human-gated node, where the named human's decision covers it, or on a review node, which records it as pending for release. On any other node, use `tool` for a deterministic check. A self-check for the node's own author is a SHOULD dimension that names its independent MUST counterpart.

## Add a rule

Never hand-edit `knowledge/rules.json`. Propose it through N25, or with `python -m vpg memory promote --rule <id> --to <state> --by <name> --human --evidence <ref>` for lifecycle moves. Every write archives the previous version and appends to the changelog.

## Swap the graph for a different film type

Set `workflow: path/to/other.yaml` in `vpg.project.yaml`; it must pass `python -m vpg validate --workflow path/to/other.yaml`.

- **Engine core:** hard-codes no node ids. Engine behaviour attaches through four `gate:` hooks, and the validator enforces them:
  - `freeze_inputs` — at most one node;
  - `review_gate` — exactly one node;
  - `regeneration_plan` — exactly one node;
  - `package` — at most one node.
- **Shipped domain tools:** they *do* trace findings to the default graph's node ids — the script (N07), storyboard (N08), asset plan and assets (N10–N11), voice (N12), scenes (N13) and assembly (N14).
  - Either keep those ids for the nodes that own that work, or give your graph its own tools.
  - A finding traced to an unknown node fails N21's `defects_valid` completion check. It is never silently dropped.
