# Craft metrics and benchmarks

**TL;DR:** `vpg measure` turns a film and its stems into versioned craft metrics, a benchmark registry turns
reference films into metric envelopes, and the ratchet stops a candidate from getting worse than the accepted
baseline unless a named human waives it. Envelopes come from the benchmark films, never from a guess in this package.

Evidence tags follow [gaps and limitations](gaps-and-limitations.md): observed (a test or run in this package),
reported (an external source or a production chat) and assumed (a design judgement not yet tested).

## Why

A recent 90-second case-study film (NDA) passed every gate and all five reviewers while it regressed against its own
earlier best and two benchmark films (reported). The reviews scored 4 or 5 on every dimension for good and bad
versions alike, and nothing measured the ground's saturation, motion share, pauses, or the voice-over-music margin on
one fixed basis. These metrics measure exactly those things, deterministically, on every frame or window.

## Metrics

`vpg measure --catalog` prints the full catalog with each basis and version. All are version 1 except the two
voice-over-music metrics and `text.overlap_all.count`, which are version 2 (see the real-film evaluation below).

| Metric | Unit | Defect side | What it measures |
|---|---|---|---|
| `video.colourfulness.median` | M | both | Hasler and Suesstrunk colourfulness of whole frames sampled every 2 s, median |
| `video.ground_colourfulness.median` | M | both | Colourfulness of the ground only: flat blocks connected to the frame border, so a card's flat inside does not count |
| `video.coverage.median` | share | both | Share of 16-pixel blocks that carry drawn content (an edge above 40 luma levels) |
| `video.motion_share` | share | upper | Frame pairs in motion (mean grey difference above 0.15 at 320x180) |
| `video.hold_share` | share | lower | Time spent in still holds of at least 1 s (visual breathing room) |
| `audio.pause_share` | share | lower | Gaps of at least 0.3 s as a share of the spoken span |
| `audio.pace_wpm` | wpm | both | Spoken words per minute over the voiced span |
| `audio.vo_over_music.p10` | LU | lower | Voice minus music momentary loudness, 10th percentile over windows where the voice speaks (v2: windows paired by time) |
| `audio.vo_over_music.median` | LU | lower | The same margin, median (v2); the better separator when the benchmarks' quietest moments differ |
| `text.overlap_all.count` | count | upper | Text layers whose boxes cross by more than 2 px both ways at any moment, fades included (v2; R-LAY-001 everywhere, not only on held text) |
| `text.min_px_at_player` | px | lower | The smallest essential text as seen in a 960-pixel-wide player |
| `text.ocr_small_share` | share | upper | Read words under a fixed 8 px at player width (needs tesseract). It cannot tell 3 px UI text from 6 px |
| `text.ocr_word_px.dense_min` | px | lower | Product-UI legibility: on every text-dense frame (20 or more confidently read words) the median word size at player width, and the smallest of those (needs tesseract) |
| `still.*` | | | The look gate's versions on style frames: colourfulness, ground, coverage, `still.ocr_small_share` and `still.ocr_word_px.median` (word size per role; a frame with fewer than 3 confidently read words gives none, so judge it on the roles that show UI: `roles: [product_ui]` in GATES.yaml) |

Each record carries timecoded evidence: the most colourful frames, the longest motion runs, every pause, the quietest
voice-over-music windows and every overlapping pair, so a reviewer or the glass-box viewer can jump to the moment.

A metric that cannot be measured (no stem, an unreadable file, no OCR) is recorded as `not_measured` with the reason.
It is never a pass.

## Versions and bases

Every measurement names its metric, the metric's version and the 16-character hash of its basis: the method and every
parameter, including how stems are downmixed (`vpg/metrics.py`).

* Two measurements compare only when metric, version and basis all match.
* A different basis is never compared. `vpg benchmark measure` re-measures stale records from the registered media
  first, and a check against a stale envelope reports `not_comparable`.
* The voice-over-music basis downmixes both stems to the mean of their channels before metering. Metered as-is, a
  dual-mono stem reads 3 dB louder than its mono source (observed, `test_the_mix_basis_is_fixed_across_channel_layouts`),
  which is how a basis change once hid a falling margin (reported).
* Changing a method or a parameter, wording included, is a new basis; bump the version when the method changes.
* Momentary windows are placed by their own timestamps. ffmpeg reports 400 ms of digital zeros after loud audio as
  `M: nan`; version 1 dropped those lines and paired the voice with music from seconds earlier, so version 2 refuses
  to compare with it.

## Benchmark registry

A registry is a JSON file (`vpg-benchmarks/v1`, [schema](../schemas/benchmark-registry.schema.json)). Media paths are
relative to it, stay inside its folder (an absolute path or `..` is refused) and carry their sha256. Keep a production's
registry, and its films, inside the project (the quality workflow requires it), not in this package: benchmark films are
usually confidential.

| Entry kind | Role |
|---|---|
| `reference` | A film whose craft defines what is in character; envelopes are built from these |
| `degraded_anchor` | A deliberately degraded copy of a reference: a negative control that must fall outside at least one envelope |
| `previous_best` | The ratchet's accepted baseline. Only a named human may set one, with a reason; a new one supersedes the old |

```json
{
  "schema": "vpg-benchmarks/v1",
  "id": "film-benchmarks",
  "entries": [
    {"id": "REF-A", "kind": "reference", "evidence": "observed",
     "media": {"master": {"path": "refs/a/master.mp4", "sha256": "..."}, "voice": {"path": "refs/a/voice.wav", "sha256": "..."}},
     "style_frames": [{"role": "title", "path": "refs/a/title.png", "t": 2.0}],
     "measurements": {"video.motion_share": {"metric": "video.motion_share", "version": 1, "basis": "...", "unit": "share",
                                             "value": 0.19, "evidence": {"pairs": 2249, "fps": 25.0}}}}
  ],
  "envelope_policy": {"audio.vo_over_music.p10": {"floor": 10.0, "why": "speech over music stays intelligible"}}
}
```

### Envelopes

For each metric that every reference measured under the current basis, the envelope is the references' range widened
by the metric's tolerance, judged on the metric's defect side. `envelope_policy` may change a metric's side or
tolerance, or add an absolute floor or ceiling.

`vpg benchmark verify` is the registry's self-check, and a failing verify means the envelopes must not gate anything:

* no reference falls outside an envelope (a floor set above a reference fails here);
* every degraded anchor falls outside at least one envelope;
* no measurement is stale, and every registered file is still its registered bytes.

### Ratchet

`benchmarks.ratchet(baseline, candidate, envelopes, waivers)` lists the metrics on which a candidate is worse than the
accepted baseline beyond the metric's tolerance: lower on a lower-side metric, higher on an upper-side metric, or
further outside the envelope on a two-sided one. A regression stays `regressed` unless a named human waived that
metric with a reason; an agent's waiver is ignored. Only a named human replaces the baseline
(`vpg benchmark add <registry> --id <id> --kind previous_best --by <human> --reason "<why>"`), and the old baseline is kept as superseded.

## Commands

```bash
python -m vpg measure --master film.mp4 --voice voice.wav --music music.wav \
  --text-layers text-layers.json --narration script.json [--ocr] --out measurements.json
python -m vpg benchmark init benchmarks/registry.json
python -m vpg benchmark add benchmarks/registry.json --id REF-A --kind reference --by agent:producer \
  --master benchmarks/refs/a/master.mp4 --voice benchmarks/refs/a/voice.wav --music benchmarks/refs/a/music.wav
python -m vpg benchmark add benchmarks/registry.json --id ANCHOR-SAT --kind degraded_anchor --degrades REF-A \
  --how "saturated ground" --by agent:producer --master benchmarks/anchors/sat.mp4
python -m vpg benchmark verify benchmarks/registry.json
python -m vpg benchmark check benchmarks/registry.json --measurements measurements.json [--waivers waivers.json]
python -m vpg benchmark measure benchmarks/registry.json   # re-measure stale records after a basis change
```

`check` exits 1 when a value is outside an envelope, not comparable, or regressed against the baseline. None of these
commands changes a production run.

## Evidence and limits

* observed: on synthetic films the metrics separate a good film from a degraded one, three references never flag
  their own envelopes, fresh re-measurements stay inside, and the anchor is caught on six or more metrics
  (`tests/test_craft_metrics.py`).
* reported: the metric choice and the v1 parameters reproduce the investigation's method on the case-study film.
* assumed: tolerances are first guesses, to calibrate against Wei's judgements; envelopes, not tolerances, carry the
  benchmark's character.
* Colourfulness, coverage and OCR read frames every 2 s, so a two-second event can fall between samples; motion and
  loudness read every frame or window.
* Aesthetic scores are deliberately absent: no learned "beauty" number gates anything.

### Real-film evaluation (2026-10-27)

The metrics were run, read-only, on a real production's six versions (a recent 90-second case-study film, NDA) and on
two benchmark films, and compared with an independent set of gates another team wrote for the same films.

* observed: colourfulness, motion share and pause share reproduce the independent measurements to within 0.1 on every
  film.
* observed: four defects were found and fixed:
  * the voice-over-music windows drifted after digital silence;
  * pace read 0 on the engine's own timings files;
  * touching line boxes of one headline counted as overlaps;
  * a plural ("sub-agents") did not count for a must-say term.
* observed: the fixed 8 px share could not tell 3 px UI text from 6 px, so `text.ocr_word_px.dense_min` and
  `still.ocr_word_px.median` were added.
  * On the masters of the five versions the independent gate judged for UI legibility, `text.ocr_word_px.dense_min`
    agreed on all five.
  * Its look-gate counterpart, `still.ocr_word_px.median`, is tested on synthetic frames only: these versions had no
    look stage.
* observed: with envelopes from the benchmark films, the engine agreed with the independent verdicts on 29 of the 32
  checks both could make (the landed version, before these fixes: 23). It would also have failed the two good versions
  on pace and overlap.
  * The three differences are all the engine being stricter: a tinted ground on two earlier versions, and 28% motion
    on one.
  * The independent tool had no input for overlap on three versions; the engine measured them.
  * The ratchet against the accepted earlier version flagged every regressed version and neither good one, on the
    metrics the template ratchets.
* Envelopes depend on which films are references, and for which media: registering a film's stems without its master
  keeps it out of the visual envelopes.
* The overlap count is only as good as the renderer's boxes: a renderer that reports container boxes reports overlaps
  that are not there (`box_height_vs_type` in the evidence shows it).
* OCR cannot read the very smallest text, so text too small to read confidently drops out of
  `text.ocr_word_px.dense_min`; a film with no readable dense frame is not measured.
