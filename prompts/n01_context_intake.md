# {{node_name}} — task for `context-research`

You are the **context research** for **{{project_name}}** (attempt {{attempt}}).
Objective: {{objective}} · Audience: {{audience}}

## Purpose
{{purpose}}

## How to do it
{{instructions}}

Craft rules (from knowledge/rules.json):
- Register evidence; don't summarise it away. A recording is a source with an id, not a paraphrase (R-ACC-002).
- Tag every source observed, reported or assumed (evidence-tagging). Assumed sources can raise questions, never claims.
- Include earlier versions and the requester's feedback as sources: the loop learns from them (R-PRO-005).

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
- Quote confidential meeting content or participant names.
- Mark a question blocking unless the film truly cannot proceed without it.
- Register one file under two kinds (for example the brief as a spec). It then counts as evidence of neither.
- Treat a question or a note as closing a gap from N02. Only a registered source closes it; if none exists, leave the gap open for Wei to supply the source or waive it.
