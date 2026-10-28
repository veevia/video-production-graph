---
spec: {family: vpg-spec/v1, doc: story, version: 1}
runtime_s: {target: 90, max: 95}
beats:                       # each beat's share of the runtime (shares add up to 1); one idea each. N07 names the script
                             # chapters after the beat ids, or a beat lists its chapters: [ch1, ch2]
  - {id: hook, share: 0.12, idea: "<fill>"}
  - {id: problem, share: 0.20, idea: "<fill>"}
  - {id: approach, share: 0.40, idea: "<fill>"}
  - {id: outcome, share: 0.20, idea: "<fill>"}
  - {id: close, share: 0.08, idea: "<fill>"}
beat_tolerance: 0.05         # a beat may drift this much of the runtime from its share
must_say: ["<fill: a term the film must speak>"]
banned_phrases: ["<fill: a phrase the film must never use>"]
rules:
  - id: ST-TERMS-001
    level: MUST
    statement: Every must-say term is spoken.
    rationale: The terms carry the approach; a version that drops half of them tells a thinner story.
    source: "<fill>"
    enforcement: {type: tool, check: "specchecks:story_terms"}
    examples:
      do: ["Name the approach in the words the experts use"]
      dont: ["Replace a named step with 'and more'"]
  - id: ST-BAN-001
    level: MUST
    statement: No banned phrase is spoken or shown.
    rationale: Banned phrases were rejected for a reason the feedback records.
    source: "<fill>"
    enforcement: {type: tool, check: "specchecks:story_terms"}
    examples:
      do: ["Show the step concretely instead"]
      dont: ["A slogan the review rejected"]
  - id: ST-IDEA-001
    level: MUST
    statement: One idea per scene.
    rationale: Two ideas in one scene means neither lands.
    source: "<fill>"
    enforcement: {type: judge}
    examples:
      do: ["One claim, one visual, one cue"]
      dont: ["A claim and a feature tour in the same beat"]
---
# Story spec

> Read by N05, N07, N08 and the animatic.

## Intent
<fill: what the viewer should understand and feel at the end>

## Beats
<fill: the arc, beat by beat>

## Terms
<fill: why each must-say term matters>
