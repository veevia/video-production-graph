# Workflow and app updates

## 2026-10-27: quality workflow and Glassbox

This update incorporates the authoring workflow's end-review and quality-layer
improvements and adds the complete optional Glassbox app. Publication snapshots
are identified by the file hashes in `.vpg-sync.json`; package version strings
alone are not compatibility checks.

### Engine

- End-review decision digests, scored alternatives and reviewer calibration.
- Regression checks for layout, captions, voice seams and accepted baselines.
- Rework adoption and artifact-reference freshness fixes.
- Versioned craft measurements, benchmark envelopes and a versioned spec family.
- Ledger-derived telemetry, direct-run records and explicit provisional-decision
  evidence, including optional expiry.
- Pairwise judging with reference/anchor comparisons and saturation checks.
- An opt-in quality workflow: 27 nodes and 55 edges, adding measured look and
  animatic gates before production. The default graph remains 25 nodes and 49
  edges; its contracts also include the earlier end-review improvements.

### Glassbox

The local app includes film/version browsing, playback and captions, comparison,
pipeline evidence, gate/review inspection, telemetry and producer feedback
threads. The current interface is included; historical UI snapshots remain in
the authoring workspace, outside the public bundle.
No private productions, feedback stores or library indexes are bundled.

Run `python -B apps/glassbox/server.py --root /path/to/projects` from the checkout.
Library scanning requires explicit `--library-root` arguments. The server is
macOS/Linux only and binds to loopback. Its reaction log is not the VPG ledger:
recording a reaction neither advances the graph nor grants approval authority.
See the [app guide](../apps/glassbox/README.md).

### Adoption and rollback

- Keep existing production paths and private stores unchanged. Installing new
  source files does not authorize a server restart or production migration.
- Engine code is part of the accepted run configuration. A named human must
  accept an upgrade between versions when VPG requests `accept-config`; reviews
  then run again under the accepted configuration.
- Start a **new run** for the quality workflow. Complete the spec family and pin
  a benchmark registry before building. Never switch an in-flight run.
- Re-measure old benchmarks where metric versions or bases differ. Voice-over-
  music and text-overlap measurements were corrected and re-versioned; old and
  new measurements must not silently compare.
- A rollback is a reviewed code revert, not a force-push or deletion of production
  state. A run that accepted changed code may need a fresh human configuration
  acceptance after rollback. A quality run cannot continue on an engine without
  that workflow.

See [quality workflow](quality-workflow.md), [craft metrics](craft-metrics.md),
[observability](observability.md), and the [guarded update procedure](standalone-sync.md).
