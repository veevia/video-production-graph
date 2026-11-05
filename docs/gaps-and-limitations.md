# Gaps and limitations

VPG verifies workflow mechanics and measurable contracts. It does not certify
creative quality, human perception, licensing or confidentiality.

## Evaluation boundaries

- Built-in policies are generic defaults, not records of human approval or
  production calibration. Configure thresholds for the project and document the
  basis in its private controls. Advisory defaults stay advisory until explicitly
  governed in that project's rule file.
- Voice identity, music suitability, taste and final listening require named humans.
  Scripted fixture decisions are always marked as dry-run decisions, never human.
- Legibility relies on accurate renderer-provided boxes and timings. Container boxes
  can report false overlaps. Text inside an image needs OCR or an independent review.
- OCR can miss small, stylized or partially obscured text. A configured name list
  covers only those names; it does not prove that every confidential fact is absent.
- Flash detection is a whole-frame luma proxy, not a certified area-based analyser.
- Measurements compare only when metric versions and bases match. Benchmark
  envelopes and ratchets depend on the licensed references selected for the project.
- Pairwise ranks remain advisory unless calibrated against actual human labels.
  Blinding cannot hide visible titles, watermarks or recognizable film content.

## Runtime boundaries

- One machine and one writer per project; there is no distributed queue or model
  gateway. External workers execute the file-based task packets.
- Regeneration is node-level. Scene/unit reuse depends on each bound renderer's
  implementation and content fingerprints.
- Actor names are asserted, not authenticated. Agent callers must respect the
  human-only boundary; real approvals are not inferred from a UI reaction.
- Review hashes bind a verdict to bytes, not to reviewer attention or judgment.
- Direct runs remain outside the governed graph even when their provenance is
  recorded. Do not treat them as equivalent to passed gates.
- A run retains its selected workflow. Start a new run to adopt the quality graph;
  do not change node sets in flight.
- Accepted configuration changes can require a named human's explicit acceptance
  and fresh reviews. This is an integrity guard, not an automatic migration.
- A learning proposal does not apply itself. Shipped policy files are read-only;
  observations and preference models belong in private project storage.

## Sample and viewer

The sample is a generated geometric animation. Its checks exercise retries,
rework, resume, media measurement and packaging, not product capabilities. The
tone narration fallback is intentionally degraded and cannot establish listening
approval.

Glassbox is a single-user loopback viewer for macOS/Linux. It observes projects
and records feedback outside them; it does not advance the engine or approve a
production. Browser thumbnail caching is separate from HTTP caching. See its
[privacy guidance](../apps/glassbox/README.md#privacy-and-local-storage).

## Publication boundary

Fixtures and documentation must be generic, not renamed copies of real project
data. The file allowlist and automated scanner cannot establish confidentiality
by themselves. Review complete examples and provenance before publication.
See [maintainer guidance](standalone-sync.md).
