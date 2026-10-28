# Dependencies and project boundary

## Required

| Dependency | Role | Source |
|---|---|---|
| Python 3.11+ | Local CLI, scheduler, checkpoints and tools | System/user Python |
| pydantic 2 | Typed workflow and state | `requirements.txt` |
| PyYAML | Workflow, rubric and configuration loading | `requirements.txt` |
| jsonschema | Artifact contract validation | `requirements.txt` |
| NumPy | Audio measurement and fictional sample audio | `requirements.txt` |
| Pillow | Frame/image measurement and fictional sample rendering | `requirements.txt` |
| pytest | Regression tests, including optional scorer tests | `requirements.txt` |
| ffmpeg / ffprobe | Video/audio assembly and media checks | System packages; must be on PATH |

The existing requirement ranges are retained; this extraction does not silently pin, upgrade or replace
the workspace environment. Python 3.11 is the locally verified version. CI also exercises 3.12.
The library files, YAML, JSON schemas, prompts, rules and sample assets are loaded relative to
`vpg/util.py`'s checkout root, not relative to a private workspace.

## Conditional and optional

- **Tesseract:** required when OCR privacy scanning is enabled. Missing OCR fails closed.
- **macOS `say`:** sample narration only. Without it, the sample reports a degraded tone fallback;
  a real production binds its own TTS and obtains listening sign-off.
- **Renderers and TTS:** project commands, not engine imports. Supply licensed renderers, approved
  source assets and authorized provider access in the production project.
- **Workers:** any human or agent satisfying the [capability contracts](skill-bindings.md).
  Named workspace agents and skills are hints, not executable dependencies.
- **Workflow OS:** an optional workspace operations adapter discovers the existing workflow folder.
  vpg does not import it. Keeping that folder intact preserves the adapter.
- **GitHub CLI:** maintainer publication only; never required to produce a film.
- **Glassbox:** the optional local viewer uses Python's standard library on macOS/Linux, with
  optional PyYAML. Its POSIX file locking does not support Windows. It uses no Copilot API,
  frontend build, CDN or model service. Project and library roots are configured explicitly.
- **Glassbox tests:** Playwright/Chromium and ffmpeg exercise the browser with fictional media;
  Node.js 22+ runs the router test. These are not viewer runtime dependencies.

## Included project files

The standalone repository contains the complete runtime/tool source, graph, role YAML, node prompts,
six control templates, rubrics, default configuration, schemas, reusable rules, generated documentation,
fictional sample (fixtures, pipeline, synthetic images, licensed font), portable tests, and optional
preference scorer with synthetic tests. Generated docs are rebuilt against the public files.
It also includes the quality-workflow overlay, spec templates, measurement/judging/telemetry tools,
and the complete current Glassbox backend, frontend, producer CLI and synthetic tests.
Historical UI snapshots and private design/review records remain in the authoring workspace.

## Deliberately not redistributed

- Customer-labelled `_TENANT` case-study files, customer media and source documents.
- Personal preference stores and learned models; start a separate store for your own principal.
- Historical workspace-only build loop, reviewer outputs and validation receipts.
- History-fidelity tests that need those private recordings and absolute citation paths.
- Private SOP/prompt implementations, custom agents, optional third-party skills, MCP setups,
  the workspace's environment, caches and OS metadata.

The operator guide replaces the private workspace-specific invocation instructions. No customer
production directory is needed or copied. Seed rules preserve their original historical evidence IDs;
withheld history is not a public verification claim. The bundled font retains its
[SIL license](../examples/sample-demo/assets/fonts/OFL-MonaSans.txt). Optional tools are installed
separately and retain their own terms; no renderer, cloud service or third-party skill is relicensed.
