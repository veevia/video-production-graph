# Private learning and reusable policy

The public policy pack contains generic defaults, not observations from earlier
productions. Each rule has a stable ID, an executable check or review cases, and
an explicit `default_enforcement`: enforced, advisory or inactive.

## Built-in policy is not approval history

Shipped rules use `lifecycle.state: builtin`, with empty evidence and history.
They establish the toolkit's initial behavior; they do not claim that a person
approved a real film or that a threshold was calibrated on private footage.
Project-specific rules and personal preference records are not bundled.

The engine preserves enforcement when loading built-in defaults. It never makes
an advisory check blocking merely because private provenance was removed.

## Project storage

`python -m vpg init <project>` copies the generic pack to `.vpg/rules.json` inside
that private project and sets the project's `rules:` path. The file is independent
of the shipped copy. Preference models and decisions live under
`.vpg/preferences/<principal>/`, also inside the private project.

Use `--human "<name>"` when initializing, or configure `humans:` before scoring
alternatives and recording preferences. No personal identity is assumed.
The standalone scorer requires an explicit `--data` directory outside the
toolkit checkout.

## Governed changes

N25 produces a learning proposal; it never applies it automatically. A named
human can move a copied built-in rule into `candidate`, or revise or retire it.
Candidate rules then follow the ordinary lifecycle:

```text
candidate -> observed_again -> validated -> approved -> adopted -> monitored
```

Revising or retiring an enforced policy is a human decision. Evidence references,
observations and approval history created by these transitions remain private.

```sh
python -m vpg memory status --rules /path/to/private-film/.vpg/rules.json
# Human-only actions, after reviewing the evidence:
python -m vpg memory promote --rules /path/to/private-film/.vpg/rules.json \
  --rule R-EXAMPLE --to candidate --by "<name>" --human --evidence "<project evidence>"
python -m vpg memory apply --rules /path/to/private-film/.vpg/rules.json \
  --proposal /path/to/private-film/learning-proposal.json --approved-by "<name>"
```

The shipped rule file is read-only to memory commands. Never use a private
production as a source of public test records. A reusable improvement is authored
as a new generic change with synthetic tests and reviewed before publication.
