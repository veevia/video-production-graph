---
spec: {family: vpg-spec/v1, doc: voice, version: 1}
persona: {adjectives: ["<fill>", "<fill>", "<fill>"], reference_read: "<fill: path to a reference read, relative to spec/>"}
pace_wpm: {min: 110, max: 135}
pause_budget: {min_share: 0.18}      # pauses of at least 0.3 s as a share of the spoken span
emphasis: ["<fill: the words that carry stress>"]
lexicon:                              # how a term is said, mapped to the TTS parameters the voice node uses
  - {term: "<fill>", say: "<fill>", tts: {rate: 1.0}}
rules:
  - id: VO-PACE-001
    level: MUST
    statement: Narration stays inside the pace band.
    rationale: A faster read fits more words and leaves no room to follow the visuals.
    source: "<fill>"
    enforcement: {type: tool, metric: audio.pace_wpm, gate: GT-PACE-001}
    examples:
      do: ["Cut words to fit the runtime"]
      dont: ["Speed up the read to fit the runtime (R-PRO-006)"]
  - id: VO-PAUSE-001
    level: MUST
    statement: Pauses keep their share of the spoken span.
    rationale: Pauses are where the viewer catches up; regressed versions lost about half of them.
    source: "<fill>"
    enforcement: {type: tool, metric: audio.pause_share, gate: GT-PAUSE-001}
    examples:
      do: ["A breath after each claim"]
      dont: ["Units butted together to save time"]
  - id: VO-PERSONA-001
    level: MUST
    statement: The read matches the persona and the reference read.
    rationale: Only a listener can judge warmth and confidence.
    source: "<fill>"
    enforcement: {type: human}
    examples:
      do: ["Compare against the reference read before a full render"]
      dont: ["Ship a voice no one listened to"]
---
# Voice spec

> Read by N07 and N12, and by the animatic's scratch read.

## Intent
<fill: who is speaking, to whom, and how>

## Pace and pauses
<fill: where the read slows down, and why>

## Lexicon
<fill: terms whose pronunciation matters>
