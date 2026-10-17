---
doc: product
# Machine contract read by vpg.tools.accuracy. Every entry cites a source id from the source registry.
terminology:            # the only words the film uses for the product
  - {term: "<fill: exact product term>", source: "<fill: source id + locator>"}
prohibited:             # words the film must not use, and what to say instead
  - {term: "<fill: term>", use_instead: "<fill: source term>", source: "<fill: why>"}
verified_claims:        # the only claims a script unit may make
  - {id: C01, text: "<fill: claim>", source: "<fill: source id + locator>"}
---
# Product — <fill: product name>

> Access: written by N04 Product Understanding; read by N01, N05–N07, N19. Locked when Wei approves N04. Changes go through `vpg change-request product`.

## Product truth
<fill: what the product is and does today, in the engineers' words; tag each statement observed/reported/assumed>

## User problem
<fill: the business problem, whose problem it is, and what it costs today>

## Audience
<fill: who watches, and what each review lens needs to believe afterwards>

## Value proposition
<fill: the change the product makes, stated as an outcome, not a feature>

## Capabilities
<fill: capabilities to show, each with its source>

## Required interactions
<fill: the exact UI path the walkthrough must follow, including the detail pages to open>

## Demo scenarios
<fill: the scenario, its data (synthetic or real) and the decision it shows>

## Claims that must be verified
<fill: every claim in the front-matter with its evidence; anything unverified stays out of the script>

## Accuracy constraints
<fill: boundary tags the film must carry, e.g. synthetic data, target architecture, seeded decisions>

## Content that must not be invented
<fill: unknowns — metrics, UI, integrations, customers, outcomes — that the film must not fill in>
