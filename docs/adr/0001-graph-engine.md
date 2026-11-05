# 0001 - A standalone graph engine

## Context

A reusable media workflow needs bounded rework, conditional routing, parallel
reviews, content-addressed reuse, human gates and durable provenance. It must run
locally without requiring a hosted scheduler or model account.

## Design

Keep the default graph in `graph/workflow.yaml`. Flow edges form a DAG; bounded
loop edges express rework; escalation stops with evidence for a named operator.
Use typed state, project-local checkpoints, a hash-chained ledger and deterministic
tools. Agents operate through files and task packets rather than in-process model
calls.

General orchestration frameworks remain possible integration layers. The toolkit
does not depend on a private operations workspace or reproduce its project history.
See the [public research references](../research/graph-engineering-and-tools.md).

## Consequences

The engine is portable and inspectable but owns its scheduler semantics. It is
single-machine, and callers must respect project locking and human-only decisions.
Moving to another scheduler requires mapping the graph, replaying state and
verifying equivalent gating behavior.
