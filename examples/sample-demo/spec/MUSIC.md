---
spec: {family: vpg-spec/v1, doc: music, version: 1}
role: "A soft pad under the voice; it never carries a melody over a phrase"
sections: [{id: open, at_s: 0}, {id: resolve, at_s: 12}]
hit_points: [{at_s: 11.0, what: "the outlook card lands"}]
energy_curve: [[0.0, 0.3], [0.6, 0.5], [1.0, 0.35]]
bpm: 90
key: "D major"
references: []
rules:
  - id: MU-ROLE-001
    level: MUST
    statement: Music supports the voice and never competes with a phrase.
    rationale: Only a listener can judge it; the mix floor catches the measurable part.
    source: R-AUD-002
    enforcement: {type: human}
    examples:
      do: ["A pad that swells only in pauses"]
      dont: ["A melodic hook over the product name"]
---
# Music spec

## Intent
Quiet confidence under a short teaser.

## Structure
Open, then resolve as the outlook card lands.
