# Spec family

**TL;DR:** A production can describe its look, motion, story, voice, music, mix and quality gates as eight versioned
spec files with rule ids and design tokens. `vpg spec lint` keeps them honest, `vpg spec diff` names the rules that
changed and the earliest gate a change re-opens, and the files are opt-in: the default workflow never reads them and
the six control documents keep working.

## Why

In a recent 90-second case-study film (NDA), the art direction lived in notes without rule ids or versions (reported).
Every feedback round appended rules, nobody retired any, and the reviewers graded compliance with whatever the notes
said that day. A spec family gives each rule an identity, a reason, a source, an enforcement and a boundary, so a change
is a diff a human can confirm rather than another appended sentence.

## The files

The family lives in `<project>/spec/`, never in `controls/`: on a case-insensitive file system `DESIGN.md` and the
control document `design.md` would be one file.

| File | Holds | Read by | Checked by |
|---|---|---|---|
| `DESIGN.md` and `DESIGN.tokens.json` | Ground and figure policy, colour roles, type scale, grid, product-UI scale, components, Do and Don't | N09, look gate, N13, N17 | lint, frame metrics, reviewers citing rule ids |
| `MOTION.md` | Durations, easing, transitions, holds, motion share, reduced motion, the 5 s motion test | look gate, animatic, N13, N14 | motion share, dwell and flash checks |
| `STORY.md` | Beats as a share of runtime, one idea per scene, must-say terms, banned phrases | N05, N07, N08, animatic | `specchecks:story_terms`, beat timing, the narrative judge |
| `VOICE.md` | Persona, pace band, pause budget, emphasis, lexicon mapped to TTS parameters | N07, N12, animatic | pace and pause metrics, human listening |
| `MUSIC.md` | Role under the voice, sections, hit points, energy curve, tempo and key, references | N12, N14, animatic | human listening |
| `MIX.md` | Loudness, true peak, the voice-over-music floor on a fixed basis, ducking | N14, animatic | voice-over-music metric, BS.1770 meter |
| `GATES.yaml` | Per gate: metric id and version, envelope, severity, stage, rule ids | look gate, animatic, N15 to N23 | every build |

Templates are in [templates/spec](../templates/spec/); a filled example for the fictional sample is in
[examples/sample-demo/spec](../examples/sample-demo/spec/).

### Front matter and rules

Each Markdown file starts with `spec: {family: vpg-spec/v1, doc: <doc>, version: <n>}`, then its machine contract,
then `rules:`. The prose below holds intent; `DESIGN.tokens.json` holds exact values in the W3C Design Tokens format
(2025.10), and the Markdown files refer to tokens as `{color.ground}`.

```yaml
rules:
  - id: DS-GROUND-001          # DS, MO, ST, VO, MU, MX or GT, a name, a number
    level: MUST                # MUST or SHOULD
    statement: The ground is quiet; saturated colour appears only as one accent per frame.
    rationale: A saturated ground behind every frame competes with the content.
    source: F003               # the feedback, benchmark or brief line it answers
    enforcement: {type: tool, metric: video.ground_colourfulness.median, gate: GT-GROUND-001}
    examples:
      do: ["A charcoal ground with the accent on one chip"]
      dont: ["A full-frame saturated gradient behind every scene"]
```

`enforcement.type` is `tool` (a metric from [craft metrics](craft-metrics.md) or a check `module:function`), `judge`
(a reviewer cites the rule id) or `human`. A rule is never deleted: set `status: retired` and `retired_reason`.

### Gates

```yaml
spec: {family: vpg-spec/v1, doc: gates, version: 1}
benchmarks: {path: ../benchmarks/registry.json, sha256: <the registry's sha256>}   # path relative to this file
waivers: []      # {metric, by: <named human>, reason, at}: a ratchet regression a person let through
gates:
  - {id: GT-VOM-001, metric: audio.vo_over_music.p10, version: 2, envelope: benchmark, severity: P1,
     stage: animatic, rules: [MX-VOM-001], ratchet: true}
  - {id: GT-OVERLAP-001, metric: text.overlap_all.count, version: 2, envelope: {hi: 0}, severity: P1,
     stage: build, rules: [DS-TEXT-001]}
```

`envelope: benchmark` takes the metric's envelope from the [benchmark registry](craft-metrics.md#benchmark-registry);
`{lo, hi}` states one (either bound may be omitted). A gate pins the metric version it was calibrated on; when the
metric's basis changes, lint refuses the gate until the benchmarks and baseline are re-measured and the gate re-pinned.
`spec.evaluate_gates` returns, per gate, `pass`, `fail`, `not_measured`, `not_comparable` or `no_envelope`, with the
measurement's timecoded evidence. Only `pass` passes. A `still.*` gate with a benchmark envelope is judged once per
style-frame role, against the same role in the references, at 1920 px wide.

`roles: [product_ui]` limits a `still.*` gate to the roles it concerns. The gate is judged on exactly those, with a
benchmark envelope or a stated one. A listed role with no frame, or a frame that gives no value, cannot pass.

- Lint refuses `roles` on any other gate, and any role DESIGN.md's `style_frames` does not list.
- The template judges UI text size (GT-UI-001) on the product-UI frame only. A title card has too few words for a
  median word size, and its type is not UI.

The registry is part of the approved bar: pinning its sha256 means a changed registry, its accepted baseline included,
is refused (`spec.registry_problem`) until a change request on GATES.yaml re-pins it. The registry must sit inside the
project folder (the folder holding `spec/`), and every file it names inside the registry's own folder. A plain path (`benchmarks:
../benchmarks/registry.json`) is accepted while drafting; the quality workflow's N03 requires the pin.

## Lint

`vpg spec lint <project>/spec [--previous <older copy>] [--require-all]` exits 1 on any error:

* front matter, rule ids (prefix by document, unique across the family), MUST or SHOULD, statement, rationale and source
  present, a resolvable enforcement (a known metric, a check that imports, a gate that exists), Do or Don't on every
  MUST rule, and no unfilled `<fill: ...>`;
* tokens: an explicit or inherited `$type` on every token, values valid for color, dimension, duration, number,
  fontFamily, fontWeight, cubicBezier and typography, and every `{alias}` resolvable without cycles;
* gates: known metric at its current version, severity, stage, envelope, rule ids that exist, a pin of 64 hex
  characters, and waivers that name a human and a reason;
* mix: `loudness` with numeric `integrated_lufs` and `true_peak_max_dbtp` (and `precheck_max_limiting_db`, 0 or more, if
  given);
* story: beat ids distinct, shares that add up to 1 (plus or minus 0.02), and a runtime cap not below the target. A beat
  covers the script chapter named like it, or the chapters it lists (`chapters: [ch1, ch2]`);
* with `--previous`: a changed document must bump `spec.version`; a deleted rule is an error (retire it); more than five
  rules added with none retired is a warning that rules only accumulate.

## Diff

`vpg spec diff <older> <newer> [--json]` names what changed:

```text
rule DS-GROUND-001: modified (statement)
rule DS-CHIP-001: added
rule MO-SEAM-001: retired
token color.ground: {"colorSpace": "srgb", ...} -> {"colorSpace": "srgb", ...}
design: v1 -> v2
re-opens from the look gate (look)
```

A change to `DESIGN.md`, its tokens or `MOTION.md` re-opens the look gate; `STORY.md`, `VOICE.md`, `MUSIC.md` and
`MIX.md` re-open the animatic; a gate change re-opens its own stage. The [quality workflow](quality-workflow.md) uses
this to regenerate from the earliest affected gate.

## Adopting it

```bash
python -m vpg spec init <project>/spec --from-controls <project>/controls
python -m vpg spec lint <project>/spec --require-all
python -m vpg spec hash <project>/spec
```

`--from-controls` carries design.md's palette and fonts into tokens and production.md's `narration_wps` into the
voice pace band (plus or minus 10%), and leaves everything else as a placeholder for a person to fill. In the default
workflow nothing reads the family; in the [quality workflow](quality-workflow.md#who-writes-each-spec-document) its
documents become control documents (ids `spec-design`, `spec-tokens`, `spec-motion`, `spec-story`, `spec-voice`,
`spec-music`, `spec-mix`, `spec-gates`), each written, validated and locked by one node, and protected like the six
existing ones. `vpg init <project> --workflow quality` copies the templates into `spec/`.

## Evidence and limits

* observed: the example family lints clean, the templates refuse approval until filled, lint catches each listed
  problem, and a diff names added, modified and retired rules and the stage they re-open (`tests/test_spec_family.py`).
* assumed: the growth warning at five new rules with none retired is a first guess.
* The family describes intent and gates measurable properties; it cannot certify taste. Human-enforced rules stay
  Wei's decision.
