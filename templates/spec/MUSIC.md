---
spec: {family: vpg-spec/v1, doc: music, version: 1}
role: "Supports the voice from underneath; never competes with a spoken phrase"
sections: [{id: open, at_s: 0}, {id: build, at_s: 20}, {id: resolve, at_s: 70}]
hit_points: [{at_s: 0.0, what: "<fill: the visual event the music lands on>"}]
energy_curve: [[0.0, 0.3], [0.5, 0.6], [1.0, 0.4]]   # [share of runtime, relative energy]
bpm: 96
key: "<fill>"
references: [{path: "<fill: a reference cue>", note: "<fill: what to take from it>"}]
rules:
  - id: MU-ROLE-001
    level: MUST
    statement: Music supports the voice and never competes with a phrase.
    rationale: Only a listener can judge it; the mix floor in MIX.md catches the measurable part.
    source: "<fill>"
    enforcement: {type: human}
    examples:
      do: ["Duck under speech; let phrases land in pauses"]
      dont: ["A melodic line over a spoken claim"]
---
# Music spec

> Read by N12 and N14, and by the animatic's music sketch.

## Intent
<fill: the music's job in this film>

## Structure
<fill: sections, hit points and the energy curve>
