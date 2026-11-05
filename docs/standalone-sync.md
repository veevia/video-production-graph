# Maintaining the public starter

This repository owns the reusable toolkit. It is not a mirror of a production
workspace. The `.vpg-sync.json` boundary marker deliberately rejects the former
snapshot-export format so an older exporter cannot restore private context.

## What belongs here

- Engine/tool source, workflow contracts, schemas and blank control/spec templates.
- Generic policy defaults without production observations or human approval history.
- Neutral generated fixtures and regression tests.
- Public technical references, operation guides and original app design documentation.

## What stays in private projects

Briefs, captures, client facts, completed controls, benchmark media, decisions,
feedback, learned rules, preference models, logs and output media stay outside the
toolkit checkout. `vpg init` creates a private project rule file; the engine's
preference store is also project-local. Do not copy production material into tests.

## Updating

1. Implement a generalized change here, using invented fixtures to reproduce behavior.
2. Update the explicit public file inventory in `publication.json` when adding or
   removing a distributable file. It is an allowlist, not permission to publish
   arbitrary content inside a directory.
3. Regenerate derived contracts with `python -m vpg docs`.
4. Run `python -m vpg publication-check`, both graph validations, the test suite,
   the synthetic dry run and Glassbox's isolated browser check.
5. Review the exact staged content. Commit and publish only when authorized.

An optional private term list may be supplied to `publication-check` from outside
the checkout. Never put customer identifiers into a public denylist or test.
Automated checks complement a human content review; they cannot decide whether a
new example reveals confidential business context.

Existing private installations are not updated automatically. A named operator
accepts engine/configuration changes at the appropriate production boundary.
Do not move production folders, restart services or rewrite their approval history
as part of a source update.

## History

Deleting content in a new commit does not erase older Git objects. A history cleanup
requires separately authorized ref rewriting, collaborator coordination and a
fresh-clone verification. Hosting-provider caches, old clones and backups are
outside a normal Git push's reach; do not claim they have been erased.
