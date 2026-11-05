---
spec: {family: vpg-spec/v1, doc: story, version: 1}
runtime_s: {target: 18, max: 20}
beats:
  - {id: group, share: 0.33, idea: "Introduce three geometric cards", chapters: [ch1]}
  - {id: select, share: 0.33, idea: "Outline Card B with a selection marker", chapters: [ch2]}
  - {id: inspect, share: 0.34, idea: "Read the matching detail panel", chapters: [ch3]}
beat_tolerance: 0.06
must_say: ["Shape Board", "card group", "detail panel"]
banned_phrases: ["game-changing", "revolutionary"]
rules:
  - id: ST-TERMS-001
    level: MUST
    statement: Every must-say term is spoken.
    rationale: The three terms identify the generated fixture's visible elements.
    source: brief
    enforcement: {type: tool, check: "specchecks:story_terms"}
    examples:
      do: ["a selection marker on Card B"]
      dont: ["an invented interactive feature"]
  - id: ST-BAN-001
    level: MUST
    statement: No banned phrase is spoken or shown.
    rationale: Calm and precise means no hype.
    source: brief (tone)
    enforcement: {type: tool, check: "specchecks:story_terms"}
    examples:
      do: ["Describe the visible selection"]
      dont: ["An unsupported product claim"]
  - id: ST-IDEA-001
    level: MUST
    statement: One idea per scene.
    rationale: Three scenes, three ideas.
    source: R-DEN-001
    enforcement: {type: judge}
    examples:
      do: ["The detail panel scene only shows the detail panel"]
      dont: ["The signal and the detail panel in one scene"]
---
# Story spec

## Intent
A viewer can identify Card B and read the matching label in its detail panel.

## Beats
Group, select, inspect: one scene each.

## Terms
The fixture name, card group and detail panel are literal labels, not business claims.
