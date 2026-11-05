# Contributing

Repository contributor: [veevia](https://github.com/veevia).
Do not add automated-tool authorship, co-author or generation trailers.
Third-party components retain their own [license notices](THIRD_PARTY_NOTICES.md).

Use Python 3.11 or later with ffmpeg and ffprobe on PATH. Install `requirements.txt` in an isolated
environment. Tesseract is needed for OCR; macOS `say` is optional for the fictional sample.

```sh
python -m vpg validate
python -m vpg validate --workflow quality
python -m vpg docs
python -m pytest -q
python -m vpg docs --check
python -m vpg dry-run
```

Make small, focused changes with regression tests. Edit the graph, models or rules, not generated
catalogs and schemas. Re-generate documentation when those authorities change.
The quality graph is derived from the default graph and `vpg/quality.py`; never edit it by hand.

## Glassbox

The complete optional app lives in `apps/glassbox/`; keep its Python/vanilla-JavaScript stack.
The root pytest command includes its unit and engine-contract tests. Node.js 22+ enables
the router regression test; browser verification also needs ffmpeg and the test-only dependencies:

```sh
python -m pip install -r apps/glassbox/requirements-dev.txt
python -m playwright install chromium
python -B apps/glassbox/tests/ui_capabilities.py
```

The browser runner creates and cleans up fictional media, a private temporary store and a loopback
server. Do not point tests at real projects or a live feedback store. Preserve the separation between
owner reactions and authoritative VPG approvals, and keep all project/library roots read-only.

## Publication

Never include credentials, real customer sources, production media, name lists or personal preference
records. Use fictional fixtures. A successful simulated approval is not a real person's approval.
No model API or paid service should be required by tests.

External workers can use any toolchain that satisfies the task packet and artifact schemas.
Names in `skills` and `runtime_binding` are optional capability hints, not required packages.

This repository owns the public starter. Generalize improvements here; never copy
production history or private workspace snapshots into it. Update the explicit
`publication.json` file inventory and run `python -m vpg publication-check` before
publication. See [maintainer guidance](docs/standalone-sync.md).

## Scope

Changes to renderers and services belong in production projects unless they improve a general
workflow contract. Avoid adding a hosted scheduler, private workspace dependency or automatic human
approval. Security-sensitive reports should omit exploitable credentials and customer material.
