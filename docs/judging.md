# Judging

**TL;DR:** Judges compare versions in pairs instead of scoring them 1 to 5. Every pair is shown in both orders, each
batch hides a known-good reference and a known-bad anchor, and versions are ranked with Bradley-Terry and bootstrap
intervals. A judge's verdicts gate nothing until its agreement with a named human's picks is measured, and an alarm
fires when its pointwise scores saturate.

## Why

In a recent 90-second case-study film (NDA), the critic scored 4 or 5 on all 20 dimensions for good and regressed
versions alike (reported). The same pattern shows in review scores recorded by earlier productions on this engine:
read-only, the saturation alarm below fires for most reviewers in most runs, and in one run a reviewer gave the
maximum to every score across all of its versions (observed). Absolute scores saturate; "is A better than B on this criterion?"
against the previous best and a benchmark keeps separating versions, provided position bias and judge drift are
controlled (reported: see [the research basis](research/quality-evaluation.md)).

## Tiers

| Tier | What | Gates |
|---|---|---|
| T0 | Deterministic craft metrics on every frame and window ([craft metrics](craft-metrics.md)) | yes, through [GATES.yaml](spec-family.md#gates) |
| T1 | Checklists that cite rule ids | defects only, never a quality score |
| T2 | Pairwise judging against the previous best and a benchmark (this page) | only a calibrated judge |
| T3 | A named human's pairwise picks and timecoded notes | the human decides |

## The protocol

1. `vpg judge pack` builds a blinded batch: the candidates, a hidden reference (a benchmark or the previous best) and a
   degraded anchor, labelled S01, S02 and so on in a seeded random order. Every pair appears twice, once in each order.
   The key that unblinds the batch is written separately and never given to the judge.
   * Pack copies each film's media under a neutral name beside the packet (`<packet>-media/S03/master.mp4`), so no file
     name gives a film away.
   * It remuxes audio and video losslessly without their container metadata (a title tag can name the film). The
     packet carries a remuxed copy's sha256.
   * Stills and text are copied as they are, with their own metadata. The packet gives no sha256 for them, since it
     would equal the original's, which the items and a benchmark registry list.
   * Any other file type is refused, as is a file whose sha256 no longer matches the items.
2. The judging agent answers each presentation with `first`, `second` or `tie`, citing spec rule ids and timecodes.
3. `vpg judge score` unblinds the verdict and merges the two orders of each pair. A pair has a winner only when both
   orders name the same film; a disagreement (position bias) is a tie, never a win.
4. Anchor check: the reference must beat the anchor. Otherwise the batch is not trusted (exit 1), whatever it says
   about the candidates. A candidate that loses to the anchor is listed.
5. Ranking: Bradley-Terry strengths (minorisation-maximisation, ties count half each way, a weak prior keeps an unbeaten
   film finite) with 95% intervals from 200 seeded bootstrap resamples. Adjacent films whose intervals overlap are not
   separated by this batch.

```bash
python -m vpg judge pack --items items.json --reference BEST-3 --anchor ANCHOR-SAT \
  --criterion "the ground stays quiet behind the content" --rule DS-GROUND-001 --out packet.json --key key.json
python -m vpg judge score --key key.json --verdict verdict.json --out result.json
```

### Contracts

| File | Schema | Who writes it |
|---|---|---|
| items | a JSON list of `{id, media: {key: {path, sha256}}}`; use the same media keys for every film | the orchestrator |
| packet | [`schemas/judge-packet.schema.json`](../schemas/judge-packet.schema.json) (`vpg-judge-packet/v1`) | `vpg judge pack` |
| key | `vpg-judge-key/v1`: the batch, reference, anchor, candidates, labels, each presentation's order and the staged copies' origins | `vpg judge pack`; keep it from the judge |
| verdict | [`schemas/judge-verdict.schema.json`](../schemas/judge-verdict.schema.json) (`vpg-judge-verdict/v1`) | the judging agent |
| result | `vpg-judge-result/v1`: validity, anchors, position statistics, consolidated comparisons, ranking, the raw verdicts | `vpg judge score` |
| labels | JSON lines, `vpg-judge-label/v1`: `{pair, pick, by, at, note?, t?, version?}` | `vpg judge label`, named humans only |

A verdict line:

```json
{"presentation": "J-3f9a01c2de-P007", "pick": "second", "rules": ["DS-GROUND-001"],
 "rationale": "S04's ground stays charcoal; S02 sits on a saturated gradient", "evidence": [{"t": 12.0, "item": "first", "note": "gradient behind the chart"}]}
```

## Calibration

`vpg judge label` records a named human's pick for a pair (agents are refused: an agent's pick is a verdict, not a
label), optionally with a timecoded note. `vpg judge agreement --result <r.json> --labels labels.jsonl` compares a
judge's consolidated picks with the latest human pick for the same pairs.

| Labels | Krippendorff's alpha | Status | The judge's verdicts |
|---|---|---|---|
| fewer than 30 | any | insufficient | advisory |
| 30 or more | below 0.667 | unreliable | advisory (the brief's rollback trigger) |
| 30 or more | 0.667 to 0.8 | tentative | inform, with the interval |
| 30 or more | 0.8 or more | reliable | may gate |

Cohen's kappa is reported beside alpha. The alpha implementation reproduces Krippendorff's published worked example
(nominal 0.743, ordinal 0.815, interval 0.849; observed, `tests/test_judging.py`).

## Saturation alarm

`vpg judge saturation <project>` reads a run's recorded review scores (read-only); `--scores` reads `{version: [scores]}`.

* Ceiling: more than 15% of a judge's scores at the maximum (the ceiling-effect criterion of Terwee and colleagues).
* Collapse: with two or more versions, the standard deviation of the per-version mean below 0.15, so the scores no
  longer tell versions apart.

The alarm is a calibration signal, not a gate: high scores can be deserved. When it fires, move that judge to pairwise
comparison against the previous best and a benchmark.

## Evidence and limits

* observed: order swap, ties, the ranking, intervals, anchors, kappa, alpha and both alarms are unit-tested on
  synthetic judges, including a judge that always picks the first film shown (it cannot pass the anchor check).
* observed, on real films (2026-10-27):
  * A batch packed before this fix showed each film's own path. Its file names named the version or the benchmark,
    and every candidate's container title named the project while the benchmark had none.
  * Packed now, the packet names no film. Each copy's streams are bit-identical to its original.
  * On that film's recorded reviews, the alarm fired for every reviewer with scores at each version the investigation
    examined, when run on the history up to that version. On the four versions the investigation cites, it fired for
    the creative critic on both counts.
  * It cannot see a reviewer rank a regressed version above a good one (one did). Anchors and labels catch that.
* Blinding covers names, order, hashes and container metadata. It cannot hide what is in the film itself: its length,
  its resolution, a burned-in title or a watermark. Stills and text keep their own metadata.
* assumed: 200 bootstrap rounds, the 0.5 prior and the 0.15 collapse threshold are first choices.
* The engine never calls a model: judging is a task packet for an agent. Whether a judge's taste matches Wei's is
  exactly what the labels measure; nothing here claims it.
