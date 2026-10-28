---
spec: {family: vpg-spec/v1, doc: voice, version: 1}
persona: {adjectives: [calm, precise, warm], reference_read: ""}
pace_wpm: {min: 150, max: 185}
pause_budget: {min_share: 0.12}
emphasis: [own, seven-day, first]
lexicon:
  - {term: Northwind, say: "NORTH-wind", tts: {rate: 1.0}}
rules:
  - id: VO-PACE-001
    level: MUST
    statement: Narration stays inside the pace band.
    rationale: A faster read would crowd an 18-second teaser.
    source: production.md narration_wps 2.8
    enforcement: {type: tool, metric: audio.pace_wpm, gate: GT-PACE-001}
    examples:
      do: ["Cut a word to fit"]
      dont: ["Speed up the read (R-PRO-006)"]
  - id: VO-PAUSE-001
    level: MUST
    statement: Pauses keep their share of the spoken span.
    rationale: The viewer reads the chip in the pause after each claim.
    source: production.md unit_pause_s 0.45
    enforcement: {type: tool, metric: audio.pause_share, gate: GT-PAUSE-001}
    examples:
      do: ["A breath between units"]
      dont: ["Units butted together"]
  - id: VO-PERSONA-001
    level: MUST
    statement: The read is calm, precise and warm.
    rationale: Only a listener can judge it (R-VOI-001).
    source: R-VOI-001
    enforcement: {type: human}
    examples:
      do: ["An adult, even read"]
      dont: ["A youthful or hyped read"]
---
# Voice spec

## Intent
An operations colleague explaining something useful, unhurried.

## Pace and pauses
Each unit ends with a pause long enough to read its chip.

## Lexicon
Northwind is stressed on the first syllable.
