---
spec: {family: vpg-spec/v1, doc: motion, version: 1}
durations: {enter: "{duration.base}", exit: "{duration.fast}", hold_min: "{duration.hold}"}
easing: {standard: "{easing.standard}"}
transitions: [cut, line-carry, fade]
holds: {min_s: 1.0, s_per_word: 0.24}
reduced_motion: "Every move resolves to its end frame; the signal chip appears without travel"
motion_test: {duration_s: 5, shows: [enter, hold, exit, one transition]}
rules:
  - id: MO-SHARE-001
    level: SHOULD
    statement: Frames move about as much as the benchmark does, and no more.
    rationale: The plant data needs still time to be read.
    source: synthetic benchmark SYN-REF
    enforcement: {type: tool, metric: video.motion_share, gate: GT-MOTION-001}
    examples:
      do: ["Move on the spoken cue, then hold"]
      dont: ["A drifting background under every scene"]
  - id: MO-SEAM-001
    level: MUST
    statement: Each seam uses one transition from the vocabulary.
    rationale: One motion grammar reads as intent.
    source: R-MOT-002
    enforcement: {type: judge}
    examples:
      do: ["The trend line carried into the outlook card"]
      dont: ["A spin and a wipe on one seam"]
---
# Motion spec

## Intent
Motion points at the signal and then gets out of the way.

## Holds
Each claim holds for at least a second after it lands.

## Transitions
Cuts between plants, a line carry into the outlook, a fade on the close.

## Reduced motion
Every move resolves to its end frame.
