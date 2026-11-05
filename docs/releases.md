# Starter-kit compatibility

The public toolkit contains the default workflow, opt-in quality workflow and
optional Glassbox viewer. It ships reusable defaults and synthetic fixtures,
not any project's completed controls, evaluations or approval history.

## Initializing a private project

```sh
python -m vpg init /path/to/private-film --name "My film" --human "<reviewer>"
```

Initialization copies blank controls and a private rule file. The quality
workflow also copies the spec family; complete it and select licensed references
before production. Preference storage is project-local, not under the toolkit.

## Existing installations

Do not silently migrate a production or move its private stores. Preserve any
existing private policy and preference data until its operator chooses the new
paths and accepts the engine/configuration change. Never replace private learned
rules with the shipped defaults as an implicit upgrade.

A saved run retains its workflow. Use a new run for the quality graph. Metric
versions and measurement bases must agree before comparisons; re-measure old
reference data where necessary.

Glassbox reactions remain separate from engine decisions. Changing source files
does not authorize restarting a server, transferring notes or approving a gate.

## Rollback

Use a reviewed compatible code revision. A run that accepted different engine
code may require a new named-human configuration acceptance. A quality run cannot
continue on an engine that lacks its graph. Git history rewriting is separate
from production rollback and never erases copies held by other users.
