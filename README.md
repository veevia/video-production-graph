# Video Production Graph (vpg)

Deterministic CI/CD and governance for AI-assisted product demo films.

Creative agents can write scripts, build motion scenes and critique cuts, but left unchecked they invent
product claims, overwrite approved scenes during revisions, miss broadcast audio and legibility targets,
and grade their own work. **vpg** wraps your agents and local media tools in one declarative graph
([`graph/workflow.yaml`](graph/workflow.yaml): 25 nodes, 49 edges, 7 phases) so every cut is source-grounded,
measured on disk, surgically reworked, and signed off by named people.

![The Video Production Graph: 25 steps in 7 phases. A brief moves through discovery, definition, planning and production; six reviews judge each version in parallel; defects return only to the step that made them; a person approves at five human gates before packaging and delivery.](docs/images/vpg-overview.gif)

## Why vpg?

| Without vpg (ad-hoc agent chat) | With vpg (governed production graph) |
|---|---|
| Fixing Scene 4 regenerates and breaks Scene 2 | **Surgical rework:** defects route only to the node that caused them; unchanged work is memo-skipped by SHA-256 fingerprint |
| Agents invent metrics, alter terminology or fake UI | **Frozen grounding:** source evidence is hashed at intake; every script claim and product term is checked against locked specs |
| Agents self-approve clipped audio or unreadable text | **Tools beat agents:** `ffmpeg` loudness, WCAG contrast, caption sync and OCR privacy checks cannot be overridden by an agent score |
| Long chat context rots or crashes mid-production | **Stateless packets & checkpoints:** file-based task packets, atomic resume and a tamper-evident hash-chained ledger |

### How vpg fits into the ecosystem

| Category | Examples | How vpg differs or composes |
|---|---|---|
| **Prompt & SDLC instruction kits** | `microsoft/hve-core`, `.instructions.md` / skills | Prompt kits guide agents *inside* the context window. `vpg` enforces state, spec locks and completion checks *outside* the LLM—and dispatches those agents as task-packet workers. |
| **General workflow engines** | LangGraph, Temporal, Dagster, Prefect | General engines need a server/daemon or in-process SDK calls and lack media evaluation primitives. `vpg` is zero-daemon, file-based, and gives deterministic tools precedence over agent scores ([research](docs/research/graph-engineering-and-tools.md)). |
| **Programmatic renderers & TTS** | Remotion, Motion Canvas, Playwright, Qwen3-TTS | Renderers and TTS turn code or text into pixels and audio. `vpg` binds them at `N11–N14` and wraps them in source grounding, 6-lens QA and surgical rework. |
| **End-to-end generative video repos** | OpenMontage, one-shot prompt-to-video tools | One-shot pipelines optimize for autonomous generation. `vpg` is built for enterprise product accuracy: zero invented UI, SHA-256 memo-skipping, OCR privacy scans and 5 human sign-off gates. |

## Division of labor: agents create, tools measure, people approve

The engine makes no model or network calls. It coordinates three roles across the graph:

| 1. Agents create (via task packets) | 2. Deterministic tools measure (hard `MUST` gates) | 3. Named humans commit (5 gates) |
|---|---|---|
| Works with Claude Code, GitHub Copilot, Codex, Cursor, Gemini CLI, OpenCode or human workers | **Technical:** `-16 LUFS ±1`, `≤ -1 dBTP` true peak, H.264/AAC, silence `> 1.5s`<br>**Legibility:** `≥ 22px` critical text, WCAG 4.5:1 contrast, safe areas, dwell time<br>**Captions:** word-locked sync, `≤ 20 chars/s`<br>**Accuracy & privacy:** verified claim IDs, design token match, native-FPS OCR scan | **N03:** Objective & runtime cap (`evaluation.md`)<br>**N04:** Product truth & claims (`product.md`)<br>**N06:** Creative direction (`creative.md`)<br>**N09:** Visual system tokens (`design.md`)<br>**N23:** Final listening & release sign-off |

## How it works

1. **Ground & lock (`N01–N06`, `N09`):** Validate and freeze source evidence into `.vpg/sources/`. Lock the six
   control contracts (`evaluation.md`, `product.md`, `creative.md`, `design.md`, `production.md`, `memory.md`)
   at human gates. Downstream agents cannot edit locked specs without an approved change request.
2. **Build in parallel (`N07–N14`):** Fan out conflict-free work (script $\parallel$ visual system; per-asset
   generation $\parallel$ voice synthesis) while respecting single-flight hardware locks (`tts`, `renderer`),
   then assemble the master cut.
3. **Audit through six parallel lenses (`N15–N20`):** Judge every assembled version across Technical QA,
   Creative Critique, Design Compliance, Accessibility & Legibility, Product Accuracy, and Narrative & Pacing
   (80 rubric dimensions). Reviews must echo the exact SHA-256 bytes they judged, and an author agent never
   reviews its own work.
4. **Gate, rework & learn (`N21–N25`):**
   - **Pass:** advance to human release verification (`N23`), evidence packaging (`N24`) and governed rule
     learning (`N25`).
   - **Rework:** trace each defect to the responsible node (`N07–N14`), invalidate only that node and its
     dependents, memo-skip unchanged work, and record a before/after comparison.
   - **Blocked:** stop with an escalation report in `.vpg/escalations/` when a loop budget is spent without
     convergence or a defect needs a human decision—never looping silently.

See the generated [graph specification](docs/graph-spec.md) and [graph diagram](docs/graph.svg).

## Quickstart (60-second live demo)

Requires Python **3.11+** and **ffmpeg/ffprobe** on `PATH` (plus **tesseract** when OCR privacy scanning is
enabled).

```sh
git clone https://github.com/veevia/video-production-graph.git
cd video-production-graph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# macOS: brew install ffmpeg tesseract
# Debian/Ubuntu: sudo apt-get install ffmpeg tesseract-ocr

python -m vpg validate       # verify the 25-node workflow graph
python -m vpg docs --check   # verify generated docs and schemas match the graph
python -m vpg dry-run        # run the fictional sample end to end on real 1080p media
python -m pytest -q          # run the regression suite
```

`python -m vpg dry-run` builds a real 1920×1080 H.264/AAC film offline, exercises a script-accuracy retry,
runs all six parallel reviews, executes a targeted rework cycle with memo-skipping, and verifies the
hash-chained ledger. (The sample proves workflow mechanics, not creative craft or human listening approval;
see [known limitations](docs/gaps-and-limitations.md).)

## Make a film

`vpg run` advances the project until it needs a worker, a human decision or an escalation fix:

```text
python -m vpg run <project>
  ├─► WAITING_RUNTIME  → Worker completes .vpg/tasks/<node>-a<n>.md  → vpg submit
  ├─► WAITING_HUMAN    → Named human reviews evidence at gate        → vpg approve
  ├─► BLOCKED          → Resolve root cause in .vpg/escalations/     → vpg unblock
  └─► RUN_PASS         → Verify ledger & hand off package
```

1. **Initialize a project** outside this checkout and add your brief, product evidence (spec, prototype
   capture or recording) and brand authority under `sources/`:

   ```sh
   python -m vpg init /absolute/path/to/my-film --name "My product demo"
   ```

2. **Bind your tools** (`commands:` for renderer, narration and assembly) in `vpg.project.yaml`, following
   the [sample project](examples/sample-demo/vpg.project.yaml) and [extension guide](docs/extension-guide.md).

3. **Drive the loop:**

   ```sh
   python -m vpg run /absolute/path/to/my-film
   python -m vpg status /absolute/path/to/my-film   # node states, waits and escalations
   python -m vpg tasks /absolute/path/to/my-film    # open task packets and human gates
   ```

4. **Submit work or record decisions, then `run` again:**

   ```sh
   python -m vpg submit /absolute/path/to/my-film <node> --token <token> \
     --actor agent:<worker> --output key=path
   python -m vpg approve /absolute/path/to/my-film <node> \
     --actor "<human name>" --reason "<their decision>"
   python -m vpg ledger-verify /absolute/path/to/my-film
   ```

## Review films with Glassbox

[Glassbox](apps/glassbox/README.md) is the optional local review app included with vpg. It runs on
macOS/Linux with the Python environment above, without a frontend build or model account.

```sh
python -B apps/glassbox/server.py --root /absolute/path/to/my-film
# Open http://127.0.0.1:4420/
```

Glassbox adds a human-facing review loop around the file-based workflow:

1. **Observe the work.** It reads the project's graph, saved run state, ledger, reviews and artifacts.
   Inbox shows what needs your attention; Pipeline and Files let you trace each node's status to its evidence.
2. **Review the film.** Watch versions with captions, key frames and findings, or compare two versions
   side by side with their scores, runtime targets and measurement-basis warnings.
3. **Send feedback to the producer.** Record timecoded notes, requested changes or decisions. These go
   into a separate private log, not the project files. Producers read them with the included CLI:

   ```sh
   python -B apps/glassbox/inbox.py --project /absolute/path/to/my-film
   ```

   The producer acknowledges a note with a plan, then marks it addressed in a new version. You can
   follow that thread in Glassbox and reopen it if the change did not land.

**Glassbox does not advance the graph or approve a production.** Its Approve button records a message
for the producer; the engine's named-human gates still require their own governed decisions.

Repeat `--root` to include more project folders. Video-library scanning is off unless you explicitly
add `--library-root /path/to/videos`. See the [app guide](apps/glassbox/README.md) for producer commands
and [privacy and local storage](apps/glassbox/README.md#privacy-and-local-storage).

## Repository map

| Directory | Purpose |
|---|---|
| `graph/` | Declarative workflow definition (`workflow.yaml`), the single graph authority |
| `vpg/` | Scheduler, CLI, controls, evaluation, memory, provenance and deterministic tools |
| `apps/glassbox/` | Local film review app, evidence browser and producer feedback channel |
| `agents/`, `prompts/`, `templates/` | 18 role specs, node prompts and 6 control-document templates |
| `rubrics/`, `config/`, `schemas/` | 80 rubric dimensions, thresholds and JSON schemas |
| `knowledge/` | Governed cross-project rules and design principles |
| `examples/sample-demo/` | Fictional fixtures, synthetic artwork, licensed font and real-media sample pipeline |
| `scoring/` | Optional preference scorer for ranking candidates, separate from engine gates |
| `tests/` | Regression and real-media test suite |
| `docs/` | Architecture, generated catalogs, operational guides and limitations |

## Documentation

| Guide | Covers |
|---|---|
| [Operator guide](docs/operator-guide.md) | Step-by-step production operation, privacy scanning and local publishing |
| [Glassbox](apps/glassbox/README.md) | Local review, producer feedback, setup and privacy |
| [Agent hosts](docs/agent-hosts.md) | Using vpg with Claude Code, Copilot, Codex, OpenCode, Gemini CLI or Cursor |
| [AGENTS.md](AGENTS.md) | Rules coding agents load automatically, including the human-only boundary |
| [Extension guide](docs/extension-guide.md) | Binding renderers and TTS; adding tools, nodes, agents, rubrics and rules |
| [Graph specification](docs/graph-spec.md) | Nodes, edges, subgraphs and validation rules, with [graph.svg](docs/graph.svg) |
| [Architecture](docs/architecture.md) | Engine layers, design principles and provenance records |
| [Orchestration](docs/orchestration.md) | How the 18 agent roles are dispatched in conflict-free parallel batches |
| [Evaluation and retry](docs/evaluation-and-retry.md) | The tri-state review gate, seven-step failure procedure and rework budgets |
| [Worker capabilities](docs/skill-bindings.md) | Capability contracts for external agents and human workers |
| [State model](docs/state-model.md) | Typed state separations, checkpoints and resumability |
| [Self-improvement](docs/self-improvement.md) | Post-production learning proposals and the governed rule base |
| [Gaps and limitations](docs/gaps-and-limitations.md) | Current boundaries and what requires human judgment |

## Scope & license

- **Non-goals:** Not a renderer, TTS service, video editor, model gateway or distributed scheduler; not a
  substitute for human listening, taste, licensing or privacy review.
- **Contributing:** See [CONTRIBUTING.md](CONTRIBUTING.md) and [standalone-sync.md](docs/standalone-sync.md).
- **License:** [MIT](LICENSE) for original code and documentation. The bundled Mona Sans font retains its
  [SIL Open Font License](examples/sample-demo/assets/fonts/OFL-MonaSans.txt); see
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
