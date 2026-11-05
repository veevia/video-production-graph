# 0002 - An opt-in quality workflow

## Context

Look and timing can be evaluated before a full production build. Adding gates to
an existing run would change its node set, so additional checks need an explicitly
selected workflow rather than an implicit upgrade.

## Design

Generate `graph/workflow-quality.yaml` from the default graph and `vpg/quality.py`.
Add N26 look development and N27 animatic before asset and voice production.
Treat the spec family as control documents, with one writer per document.
Pin the benchmark registry in the quality bar and keep each gate's measurement
basis explicit. Deterministic evidence informs, but never replaces, human judgment.

The default remains unchanged by opting in elsewhere. Existing runs cannot switch
workflows. Advisory policy defaults remain advisory; a project must govern its own
rule changes rather than borrowing another production's approvals.

## Consequences

The quality workflow requires a completed spec family and licensed references.
Style-frame and animatic tests can catch invalid outputs before expensive builds.
Synthetic regression tests verify the mechanics; they do not prove a real
production's creative quality.

A rollback to an engine without this workflow cannot resume a quality run.
Finish on the compatible engine or start a separate project.
