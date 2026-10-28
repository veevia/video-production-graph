---
spec: {family: vpg-spec/v1, doc: story, version: 1}
runtime_s: {target: 18, max: 20}
beats:
  - {id: problem, share: 0.33, idea: "Every plant reports, but nobody sees the pattern in time", chapters: [ch1]}
  - {id: signal, share: 0.33, idea: "A signal fires when a plant leaves its own range", chapters: [ch2]}
  - {id: outlook, share: 0.34, idea: "The outlook lets the team act first", chapters: [ch3]}
beat_tolerance: 0.06
must_say: ["Northwind Water Monitor", "expected range", "seven-day outlook"]
banned_phrases: ["game-changing", "revolutionary"]
rules:
  - id: ST-TERMS-001
    level: MUST
    statement: Every must-say term is spoken.
    rationale: The three terms are the product's promise in the brief.
    source: brief
    enforcement: {type: tool, check: "specchecks:story_terms"}
    examples:
      do: ["its own expected range"]
      dont: ["its normal levels"]
  - id: ST-BAN-001
    level: MUST
    statement: No banned phrase is spoken or shown.
    rationale: Calm and precise means no hype.
    source: brief (tone)
    enforcement: {type: tool, check: "specchecks:story_terms"}
    examples:
      do: ["Say what the signal does"]
      dont: ["A game-changing monitor"]
  - id: ST-IDEA-001
    level: MUST
    statement: One idea per scene.
    rationale: Three scenes, three ideas.
    source: R-DEN-001
    enforcement: {type: judge}
    examples:
      do: ["The outlook scene only shows the outlook"]
      dont: ["The signal and the outlook in one scene"]
---
# Story spec

## Intent
A plant manager ends knowing the monitor watches each plant against its own range and looks a week ahead.

## Beats
Problem, signal, outlook: one scene each.

## Terms
The product name, its own expected range and the seven-day outlook are the promise.
