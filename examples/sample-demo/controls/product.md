---
doc: product
terminology:
  - {term: "water-use signal", source: "S02 product-notes"}
  - {term: "expected range", source: "S02 product-notes"}
  - {term: "seven-day outlook", source: "S02 product-notes"}
prohibited:
  - {term: "predicts", use_instead: "the seven-day outlook projects", source: "S02: the outlook is a planned range, not a forecast"}
  - {term: "forecast", use_instead: "seven-day outlook", source: "S02"}
verified_claims:
  - {id: C01, text: "flags unusual water use against each plant's own expected range", source: "S02 product-notes, bullet 1"}
  - {id: C02, text: "shows a seven-day outlook derived from the production plan", source: "S02 product-notes, bullet 3"}
---
# Product — Northwind Water Monitor (fictional)

> Access: written by N04; locked when approved.

## Product truth
Northwind Water Monitor compares each plant's daily water use with its own expected range and raises a water-use signal when use leaves it (observed, S02). It shows a seven-day outlook derived from the production plan (observed, S02).

## User problem
Plant managers see their own meters but not the pattern: unusual use is found late, after water is wasted (reported, S01).

## Audience
Plant managers and the operations director. Afterwards they should believe the signal is specific to their plant and early enough to act on.

## Value proposition
Unusual water use becomes a plant-specific signal early enough for the plant team to act.

## Capabilities
- Water-use signal against the plant's expected range (S02).
- Seven-day outlook card (S02).

## Required interactions
Home view (plant list) → open a plant → the signal and its expected range → the seven-day outlook card.

## Demo scenarios
A plant's water use leaves its expected range; the team opens the plant and reads the outlook. Synthetic data.

## Claims that must be verified
C01 and C02 (front-matter) only; both are sourced to S02.

## Accuracy constraints
Tag every product frame "synthetic data". The outlook is a planned range: never "predicts" or "forecast".

## Content that must not be invented
Savings figures, customer names, integrations, accuracy percentages and any UI not in the supplied capture.
