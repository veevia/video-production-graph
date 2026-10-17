# Video Production Graph (vpg)

A local, graph-based production workflow for product demo films.

- **25 nodes, 49 edges, 7 subgraphs:** one YAML graph drives planning, production, review and targeted rework.
- **Agents create; tools measure; people approve.** External workers use file-based task packets.
- **Resumable and auditable:** checkpoints, content fingerprints, artifact lineage and a hash-chained ledger.
- **No hosted service or model account required.** The engine itself makes no network or model API calls.

Status: **beta**. The fictional sample proves workflow mechanics, not creative quality, human listening
approval, privacy certification or production readiness. [Known limitations](docs/gaps-and-limitations.md).

## Install and verify

Python **3.11+** and **ffmpeg/ffprobe** are required. Tesseract is required when using OCR privacy scans.
The sample uses macOS `say` when available; elsewhere it explicitly reports a degraded tone fallback.
The fallback is not a human voice or a listening approval.

```sh
git clone https://github.com/veevia/video-production-graph.git
cd video-production-graph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# macOS: brew install ffmpeg tesseract
# Debian/Ubuntu: sudo apt-get install ffmpeg tesseract-ocr
python -m vpg validate
python -m vpg docs --check
python -m pytest -q
python -m vpg dry-run
```

Commands run from this checkout; an editable package installation is not required.
See [dependencies and provenance](docs/dependencies.md). No private workspace files are needed.

## Make a film

```sh
python -m vpg init /absolute/path/to/my-film --name "My product demo"
# Add sources/brief.md, product evidence and brand authority.
python -m vpg run /absolute/path/to/my-film
python -m vpg status /absolute/path/to/my-film
python -m vpg tasks /absolute/path/to/my-film
```

1. Fill the brief and register authorized product and brand sources.
2. Bind renderers, narration and assembly as project commands in `vpg.project.yaml`.
3. Give each task packet to an external agent or human worker. Submit the packet's exact outputs and token.
4. At `WAITING_HUMAN`, the named human decides; never invent their approval.
5. At `BLOCKED`, read the escalation report. Correct the cause before a human unblocks it.

```sh
python -m vpg submit /absolute/path/to/my-film <node> --token <token> \
  --actor agent:<worker> --output key=path
python -m vpg approve /absolute/path/to/my-film <node> \
  --actor "<human name>" --reason "<their decision>"
python -m vpg ledger-verify /absolute/path/to/my-film
```

Resume with `run` after interruption. A configuration or engine change during a run requires a human's
`accept-config`; this deliberately prevents silently continuing under different rules.
Review files must name the exact bytes they judged. Human listening, taste and release cannot be delegated.

## Project map

| Directory | Purpose |
|---|---|
| `graph/` | Workflow definition, the single graph authority |
| `vpg/` | Scheduler, CLI, controls, evaluation, memory, provenance and deterministic tools |
| `agents/`, `prompts/`, `templates/` | Portable role specifications, node prompts and six control-document templates |
| `rubrics/`, `config/`, `schemas/` | Quality gates, thresholds and artifact/state contracts |
| `knowledge/` | Reusable rules; original private case-study evidence is not distributed |
| `examples/sample-demo/` | Fictional fixtures, synthetic artwork, licensed font and real-media sample pipeline |
| `scoring/` | Optional preference scorer, separate from engine gate decisions |
| `tests/` | Portable regression and real-media tests |
| `docs/` | Architecture, catalogs, operational guidance and limitations |

Start with the [operator guide](docs/operator-guide.md), [extension guide](docs/extension-guide.md),
[graph specification](docs/graph-spec.md) and [graph diagram](docs/graph.svg).
See [worker capabilities](docs/skill-bindings.md), [evaluation and retry](docs/evaluation-and-retry.md),
[self-improvement](docs/self-improvement.md) and [state model](docs/state-model.md).

The original private production history, personal preference records, customer sources, historical
workspace build reports and optional third-party skill implementations are intentionally not included.
Historical rule evidence IDs remain references to withheld evidence, not publicly reproducible claims.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md). Maintainers can continue working in the original workspace and
share reviewed updates with the [guarded synchronization workflow](docs/standalone-sync.md).
There is no automatic push, force-push, service restart or copying of production projects.

## Non-goals

- Not a renderer, TTS service, editor or model gateway; bind your own authorized tools.
- Not an autonomous approver or a substitute for listening, taste, licensing or privacy review.
- Not a distributed scheduler; this is a single-machine file-based engine.

## License

Original project code and documentation: [MIT](LICENSE).
The bundled Mona Sans font retains its [SIL Open Font License](examples/sample-demo/assets/fonts/OFL-MonaSans.txt).
Optional services, worker skills and renderers retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
