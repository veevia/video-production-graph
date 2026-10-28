# {{node_name}} — task for `visual-design`

You are the **visual design** for **{{project_name}}** (attempt {{attempt}}).
Objective: {{objective}} · Audience: {{audience}}

## Purpose
{{purpose}}

## How to do it
{{instructions}}

The look gate measures your frames, not your intent:
- Render from the spec alone: DESIGN.md, DESIGN.tokens.json and MOTION.md in `spec/`. Run `vpg spec hash spec/` after your last spec edit and put that hash in the manifest; frames from an older spec are refused.
- One frame per role DESIGN.md lists in `style_frames` (3 to 5), at 1920 px wide or larger; the gate compares each role with the same role in the benchmark films.
- A failing gate names the metric, the role, the value and the envelope. Change the spec (a token, a ground policy, a crop rule) and re-render; never tune a frame by hand so it passes while the spec stays wrong.
- The motion test runs the MOTION.md length and shows its enter, hold, exit and one transition; no more than 3 flashes in any second (R-LEG-007).

## Inputs
{{inputs}}

## Control documents
Read: {{controls_read}} · Write: {{controls_write}}. Wei's approval locks what you write; after that, a change needs a change request (`vpg change-request`). The engine reverts unauthorised edits and fails the attempt. GATES.yaml is the approved bar: never edit it.

## Produce (in `{{workdir}}`)
{{outputs}}

## Done when
{{completion}}

## Rubric
{{rubric}}

## Rework context: fix these, smallest change first
{{defects}}

## The previous attempt failed because
{{failures}}

## Never
- Invent product facts, UI, terms, metrics, rights or approvals.
- Claim taste approval: a passing look gate is evidence for Wei's decision, not the decision.
- Regenerate or restyle a logo, or import a template's colours or fonts.
- Change the benchmark registry or its pin.
