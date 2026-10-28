---
spec: {family: vpg-spec/v1, doc: motion, version: 1}
durations: {enter: "{duration.base}", exit: "{duration.fast}", hold_min: "{duration.hold}"}
easing: {standard: "{easing.standard}"}
transitions: [cut, line-carry, match-cut, fade]   # the vocabulary; one per seam
holds: {min_s: 1.0, s_per_word: 0.24}             # R-LEG-003
reduced_motion: "<fill: the still alternative for every move>"
motion_test: {duration_s: 5, shows: [enter, hold, exit, one transition]}   # rendered at the look gate
rules:
  - id: MO-SHARE-001
    level: SHOULD
    statement: Frames move about as much as the benchmark films do, and no more.
    rationale: Constant motion leaves no room to read; regressed versions moved far more than the benchmark films.
    source: "<fill>"
    enforcement: {type: tool, metric: video.motion_share, gate: GT-MOTION-001}
    examples:
      do: ["Move on the spoken cue, then hold"]
      dont: ["A drifting background under every scene"]
  - id: MO-SEAM-001
    level: MUST
    statement: Each seam uses one transition from the vocabulary.
    rationale: One motion grammar reads as intent; a new effect per seam reads as a template.
    source: "<fill>"
    enforcement: {type: judge}
    examples:
      do: ["A line carried from one scene into the next"]
      dont: ["A zoom, a spin and a wipe on one seam"]
---
# Motion spec

> Read by the look gate (motion test), the animatic, N13 and N14.

## Intent
<fill: how motion serves comprehension in this film>

## Holds
<fill: when and how long things hold still>

## Transitions
<fill: the vocabulary and where each is used>

## Reduced motion
<fill: what a viewer with reduced motion sees>
