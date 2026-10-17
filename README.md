# Video Production Graph (vpg)

A local, graph-based production workflow for product demo films. One YAML graph takes a brief through
planning, production, review and targeted rework to a packaged film.

![The Video Production Graph: 25 steps in 7 phases. A brief moves through discovery, definition, planning and production; six reviews judge each version in parallel; defects return only to the step that made them; a person approves at five human gates before packaging and delivery.](docs/images/vpg-overview.gif)

- **One graph drives the work.** 25 nodes, 49 edges and 7 subgraphs in [`graph/workflow.yaml`](graph/workflow.yaml)
  define every step, review and rework path.
- **Agents create; tools measure; people approve.** External agents or human workers receive file-based
  task packets, deterministic tools measure what can be measured, and named people make the commitments.
- **Resumable and auditable:** checkpoints, content fingerprints, artifact lineage and a hash-chained ledger.
- **No hosted service or model account required.** The engine itself makes no network or model API calls.

> **Status: beta.** The fictional sample proves workflow mechanics, not creative quality, human listening
> approval, privacy certification or production readiness. See the [known limitations](docs/gaps-and-limitations.md).

## How it works

A brief moves through seven phases: discovery and grounding, definition, planning, production, review,
iteration, and delivery and learning.

- **Six reviews judge every version in parallel** after assembly (N14): technical QA, creative critique,
  design compliance, accessibility and legibility, product accuracy, and narrative and pacing (N15–N20).
- **Defects go back only to the step that made them.** Feedback interpretation (N21) traces each defect to
  its source, and targeted regeneration (N22) sends the work back to that step. Content fingerprints let
  unchanged work be reused instead of redone.
- **People approve at five human gates:** objective (N03), product truth (N04), creative direction (N06),
  visual system (N09) and release (N23).
- **Every loop is bounded.** Rejections, retries and rework cycles have budgets. A budget spent without
  convergence stops the run with an escalation report instead of looping silently.
- **Blocking problems escalate to the human lead.** Unresolvable source gaps (N02) and a blocked review
  gate (N21) stop the run and report why.

The diagram is an overview. The [graph specification](docs/graph-spec.md) and the generated
[graph diagram](docs/graph.svg) are rebuilt from the YAML and checked in CI.

## Install and verify

You need Python **3.11+** and **ffmpeg/ffprobe** on `PATH`. Tesseract is required only for OCR privacy
scans. The sample narrates with macOS `say` when available; elsewhere it explicitly reports a degraded
tone fallback, which is not a human voice or a listening approval.

```sh
git clone https://github.com/veevia/video-production-graph.git
cd video-production-graph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# macOS: brew install ffmpeg tesseract
# Debian/Ubuntu: sudo apt-get install ffmpeg tesseract-ocr

python -m vpg validate       # check the workflow graph
python -m vpg docs --check   # confirm the generated docs match the graph
python -m pytest -q          # regression and real-media tests (a few minutes)
python -m vpg dry-run        # run the fictional sample end to end with scripted agents
```

Commands run from this checkout; an editable package installation is not required, and no private
workspace files are needed. See [dependencies and provenance](docs/dependencies.md).

## Make a film

1. **Create a project.**

   ```sh
   python -m vpg init /absolute/path/to/my-film --name "My product demo"
   ```

   Fill in `sources/brief.md`, then add authorized product evidence (a spec, prototype capture or
   recording) and brand authority to `sources/`. A brief alone does not pass source validation.

2. **Bind your tools.** Register renderer, narration and assembly commands under `commands` in
   `vpg.project.yaml`, as the [sample project](examples/sample-demo/vpg.project.yaml) does. The
   [extension guide](docs/extension-guide.md#add-a-project-command-renderer-tts-assembly) describes the
   command contract.

3. **Run until the graph stops** for a worker (`WAITING_RUNTIME`), a human (`WAITING_HUMAN`) or an
   escalation (`BLOCKED`), or until the film passes (`RUN_PASS`).

   ```sh
   python -m vpg run /absolute/path/to/my-film
   python -m vpg status /absolute/path/to/my-film   # node states, waits and escalations
   python -m vpg tasks /absolute/path/to/my-film    # open task packets and human gates
   ```

4. **Resolve the stop, then `run` again.** Submitting work or recording a decision does not advance the
   graph by itself.
   - `WAITING_RUNTIME`: give each task packet to an external agent or human worker, then submit the
     packet's exact outputs with its token.
   - `WAITING_HUMAN`: the named human decides. Record their actual decision; never invent their approval.
   - `BLOCKED`: read the escalation report in `.vpg/escalations/` and correct the cause before a human
     unblocks it.

   ```sh
   python -m vpg submit /absolute/path/to/my-film <node> --token <token> \
     --actor agent:<worker> --output key=path
   python -m vpg approve /absolute/path/to/my-film <node> \
     --actor "<human name>" --reason "<their decision>"
   ```

5. **Before handoff, verify the ledger.** The [operator guide](docs/operator-guide.md) lists the other
   handoff checks.

   ```sh
   python -m vpg ledger-verify /absolute/path/to/my-film
   ```

Resume with `run` after an interruption; approved work is kept. A configuration or engine change during a
run requires a human's `accept-config`; this deliberately prevents silently continuing under different
rules. Review files must name the exact bytes they judged. Human listening, taste and release cannot be
delegated.

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

## Documentation

| Guide | Covers |
|---|---|
| [Operator guide](docs/operator-guide.md) | Running a production step by step, privacy scanning and publication |
| [Agent hosts](docs/agent-hosts.md) | Using vpg with Claude Code, Codex, GitHub Copilot, OpenCode, Gemini CLI or Cursor |
| [AGENTS.md](AGENTS.md) | The rules coding agents load automatically, including the human-only boundary |
| [Extension guide](docs/extension-guide.md) | Binding renderers and TTS; adding tools, nodes, agents, rubrics and rules |
| [Graph specification](docs/graph-spec.md) | Nodes, edges, subgraphs and policies, with the [generated diagram](docs/graph.svg) |
| [Architecture](docs/architecture.md) | Engine layers and design principles |
| [Orchestration](docs/orchestration.md) | The 18 agent roles and how work is dispatched in parallel |
| [Evaluation and retry](docs/evaluation-and-retry.md) | Review gates, the failure procedure, retries and budgets |
| [Worker capabilities](docs/skill-bindings.md) | What external agents and human workers must provide |
| [State model](docs/state-model.md) | Typed state, checkpoints and resumability |
| [Self-improvement](docs/self-improvement.md) | Learning proposals and the governed rule base |
| [Gaps and limitations](docs/gaps-and-limitations.md) | What the beta does and does not prove |

## What is not included

The original private production history, personal preference records, customer sources, historical
workspace build reports and optional third-party skill implementations are intentionally not included.
Historical rule evidence IDs remain references to withheld evidence, not publicly reproducible claims.

## Contributing

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
