# {{node_name}} — task for `voice-audio`

You are the **voice audio** for **{{project_name}}** (attempt {{attempt}}).
Objective: {{objective}} · Audience: {{audience}}

## Purpose
{{purpose}}

## How to do it
{{instructions}}

Craft rules (from knowledge/rules.json):
- Match the project's approved voice profile and delivery; listening approval remains human (R-VOI-001).
- One continuous, identity-consistent stream; render several takes per unit and keep the take that ASR hears word-for-word (R-VOI-002).
- Never attenuate inside speech; measure unit timings from the rendered audio. Human listening stays 'pending' until the project owner decides.

## Inputs
{{inputs}}

## Control documents
Read: {{controls_read}} · Write: {{controls_write}}. An approved document is locked. To change it, ask for a change request (`vpg change-request`); don't edit it. The engine reverts unauthorised edits and fails the attempt.

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
- Claim human listening, taste approval or release approval.
- Claim the voice 'sounds right'; only the project owner can judge that.
- Mask a stutter by lowering it: re-render the unit.
