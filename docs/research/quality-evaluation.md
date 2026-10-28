# Research basis for the quality layer

**TL;DR:** Judge versions in pairs with the order swapped and anchors hidden, rank them with Bradley-Terry, calibrate
every judge against a named human before it gates anything, measure craft deterministically on a fixed and versioned
basis, describe the creative intent as versioned specs with tokens, check looks and timing before full builds, and keep
every decision observable. None of this is a learned "beauty" score.

**Provenance.** The sources below were collected for the 2026-10-26 quality upgrade (v1.9) and are cited as published
(reported). The motivating evidence is a local investigation of a recent 90-second case-study film (NDA): it compared
the film's last four versions with two benchmark films and with its own earlier best, using one set of tools on all
films (reported; its records stay with that project and are not part of this package). Statements about this engine
are observed in its tests. Nothing here authorises calling a model, a cloud service or publishing media.

## What the investigation found (reported)

Measured with one set of tools on all films:

* a saturated gradient as every frame's ground raised whole-frame colourfulness several-fold over the benchmark films
  and the film's own earlier best;
* frames in motion rose well above the benchmark films' share, and the share of the spoken span left as pauses fell to
  about half;
* narration got faster than both the benchmark films and the earlier best;
* the voice-over-music margin (10th percentile) fell well below the benchmark films' margin and below the roughly 10 LU
  at which speech over music stays intelligible, while a change of loudness basis (+3 dB for the voice) hid the fall;
* the narration spoke about half of the design-approach terms the earlier best had covered.

The measured values stay with that project, under its NDA; they are not part of this package.

Every gate and all five reviewers passed the regressed versions. The causes it named: the governed path was bypassed
while the run waited at its first human gate; the art direction lived in unversioned notes; there was no cheap look
check, no reference and no pairwise judging, and the 1 to 5 scores saturated; the checks missed transitions, ground
saturation, UI legibility at player width and breathing room; a loudness-basis change hid a falling margin; and human
reactions arrived as free text.

## Judging

| Finding | Source | Used for |
|---|---|---|
| LLM judges prefer the first (or second) answer; swapping the order and treating disagreement as a tie controls it | MT-Bench and Chatbot Arena, <https://arxiv.org/abs/2306.05685>; "Large language models are not fair evaluators", <https://arxiv.org/abs/2305.17926> | order swap in `judging.consolidate` |
| Form-filling with explicit criteria and probabilities beats free scoring | G-Eval, <https://arxiv.org/abs/2303.16634> | criteria and rule ids in every packet |
| Pairwise preference ranking is more reliable than pointwise scores for generation quality | PairS, <https://arxiv.org/abs/2403.16950>; pairwise or pointwise, <https://arxiv.org/abs/2504.14716> | T2 is pairwise |
| Bradley-Terry with bootstrap intervals is the robust way to rank from pairwise votes; online Elo is order-dependent | Chatbot Arena, <https://arxiv.org/abs/2403.04132>; Elo uncovered, <https://arxiv.org/abs/2311.17295> | `judging.rank` |
| Separability and agreement with humans are the measures of a benchmark judge | Arena-Hard, <https://arxiv.org/abs/2406.11939> | intervals, `separated_from_next` |
| Judges' agreement with humans varies widely by task; measure it per judge | Judging the judges, <https://arxiv.org/abs/2406.12624> | `judge_status` |
| Evaluation criteria drift as humans see outputs; calibrate judges against human grades | Who validates the validators, <https://arxiv.org/abs/2404.12272> | T3 labels |
| Decomposed checklists are more consistent than holistic scores | CheckEval, <https://arxiv.org/abs/2403.18771> | T1 checklists cite rule ids and gate defects only |
| A panel of diverse judges beats one large judge | Replacing judges with juries, <https://arxiv.org/abs/2404.18796> | several judges may score one batch |
| Multimodal judges are reliable in pairwise mode and weaker in scoring | MLLM-as-a-judge, <https://arxiv.org/abs/2402.04788>; video language models as judges, <https://arxiv.org/abs/2503.05977> | video judging is pairwise |
| Optimising against a proxy reward overfits it | Scaling laws for reward overoptimisation, <https://arxiv.org/abs/2210.10760> | judges never become targets; envelopes come from benchmarks |
| A model's stated reasoning need not be why it decided | Unfaithful chain of thought, <https://arxiv.org/abs/2305.04388> | evidence must be timecodes and frames, not prose alone |

## Saturation, anchors and agreement

| Finding | Source | Used for |
|---|---|---|
| A ceiling effect is present when more than 15% of responses sit at the maximum | Terwee et al. 2007, <https://doi.org/10.1016/j.jclinepi.2006.03.012> | the saturation alarm |
| Listening tests hide a reference and a low anchor in every trial and screen out assessors who miss them | ITU-R BS.1534-3 (MUSHRA), <https://www.itu.int/rec/R-REC-BS.1534-3-201510-I/en> | hidden reference and degraded anchor per batch |
| Alpha of 0.8 or more is reliable, 0.667 the lowest for tentative conclusions | Krippendorff, <https://repository.upenn.edu/asc_papers/43> | the calibration bands |

## Observability

| Finding | Source | Used for |
|---|---|---|
| Agent invocations are `invoke_agent` spans and evaluations `gen_ai.evaluation.result` events | OpenTelemetry GenAI semantic conventions, <https://github.com/open-telemetry/semantic-conventions-genai> | `vpg/telemetry.py` |
| Never replace a baseline automatically; compare experiments against a fixed one | Langfuse, <https://langfuse.com/docs/evaluation/experiments/compare-experiments> | only a named human sets `previous_best` |
| A trace store can run fully offline | Phoenix self-hosting, <https://arize.com/docs/phoenix/self-hosting> | OTLP/JSON export, no network |
| Keep every score with its scorer and allow re-scoring | Inspect AI scoring workflow, <https://inspect.aisi.org.uk/scoring-workflow.html> | evaluation events carry the evaluator |
| Human interrupts and replay from checkpoints | LangGraph interrupts, <https://docs.langchain.com/oss/python/langgraph/interrupts> | human gates, provisional approvals |
| A blocking check stops materialisation downstream | Dagster asset checks, <https://docs.dagster.io/guides/test/asset-checks> | the look gate's completion check |
| Timecoded, frame-accurate review notes | Frame.io review, <https://frame.io/features/review-and-approval> | timecoded labels |
| Make clear what the system can do and how well; support efficient correction | Microsoft HAX guidelines G2, G11, G15, <https://www.microsoft.com/en-us/haxtoolkit/> | evidence tags, the data contract |
| Explain with the right level of detail and confidence | Google PAIR, <https://pair.withgoogle.com/chapter/explainability-trust/> | evidence tags, intervals |

## Specs

| Finding | Source | Used for |
|---|---|---|
| A design file pairs exact tokens with prose intent and Do and Don't boundaries | Google Labs DESIGN.md, <https://github.com/google-labs-code/design.md> | the spec family |
| Tokens need explicit types, aliases and exact value formats | W3C Design Tokens Format 2025.10, <https://www.designtokens.org/tr/2025.10/format/> | `DESIGN.tokens.json`, `spec.token_issues` |
| Brand guidelines become machine-checked rules | Adobe GenStudio guidelines, <https://experienceleague.adobe.com/en/docs/genstudio-for-performance-marketing/user-guide/guidelines/overview> | rule ids with enforcement |
| Style frames and an animatic come before animation | Explainer production process, <https://www.yumyumvideos.com/blog/explainer-video-production-process/> | the look and animatic gates |

## Craft standards

| Standard | Source | Used for |
|---|---|---|
| Loudness by BS.1770 (integrated, momentary, true peak) | EBU R128, <https://tech.ebu.ch/publications/r128>; short-form supplement, <https://tech.ebu.ch/files/live/sites/tech/files/shared/r/r128s1.pdf> | the momentary meter, MIX.md |
| Speech over music needs about 10 LU or more to stay intelligible | Torcoli et al. 2019, <https://publica.fraunhofer.de/entities/publication/c3e93d0f-d985-4f3d-bcbe-b39824edcec1> | the voice-over-music floor |
| Subtitle reading rates and timing | BBC subtitle guidelines, <https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/>; Netflix timed text, <https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617> | existing caption checks |
| No more than three flashes a second | WCAG 2.3.1, <https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html> | existing flash check |
| Faster narration lowers engagement in instructional video | edX video study 2014, <https://pg.ucsd.edu/publications/edX-MOOC-video-production-and-engagement_LAS-2014.pdf> | the pace band |

## Regression and visual metrics

| Source | Used for |
|---|---|
| Playwright snapshot comparison and a controlled clock, <https://playwright.dev/docs/test-snapshots>, <https://playwright.dev/docs/clock> | deterministic proof renders at the look gate |
| LPIPS perceptual distance, <https://arxiv.org/abs/1801.03924> | not adopted (needs a model); a candidate for style-frame similarity |
| VMAF, <https://github.com/Netflix/vmaf> | not adopted; it measures compression fidelity, not design |
| Aesthetic predictors, <https://github.com/christophschuhmann/improved-aesthetic-predictor> | advisory only, never a gate (a non-goal) |
