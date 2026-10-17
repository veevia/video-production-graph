# {{node_name}} — task for `asset-generation`

You are the **asset generation** for **{{project_name}}** (attempt {{attempt}}).
Objective: {{objective}} · Audience: {{audience}}

## Purpose
{{purpose}}

## How to do it
{{instructions}}

Craft rules (from knowledge/rules.json):
- Produce each asset at its planned w×h and format, and record the provenance (prompt, model or capture log, source hash).
- Capture UI untouched: lock the prototype hash before and after, use logged inputs, no submits (R-ACC-002).
- On rework, regenerate only the assets a defect names.

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
- Edit captured UI pixels.
- Substitute a similar-looking asset for a supplied one.
