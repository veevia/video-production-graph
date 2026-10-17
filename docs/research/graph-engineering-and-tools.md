# Research — graph engineering and production tools for the video workflow

> **TL;DR:** Adopt a Pregel-style graph: typed shared state, conditional edges, bounded loops, fan-out/fan-in, checkpoints
> and human interrupts. Make evaluation nodes first-class and deterministic-first, borrow content-addressed caching from
> Bazel and Dagster, and gate learning behind human approval. No framework researched provides evaluation nodes or governed
> learning, and the ones that need no running service (LangGraph OSS, Burr, CrewAI Flows) were mirrored in vocabulary
> rather than adopted as a dependency (see [ADR 0001](../adr/0001-graph-engine.md)).

**Provenance.** A source-verified research pass, 2026-09-26 (a research agent with web access; every non-trivial claim is
cited below and anything unverifiable is marked UNVERIFIED). The "Observed in production" notes come from the 2026-09
platform-film case study (`knowledge/case-studies/platform-film-2026-09/`), not from vendor descriptions. Evidence tags: cited docs = *reported*;
production notes = *observed*; recommendations in the last section = *introduced by this task*. Nothing here authorises
installing a tool, calling a provider or publishing media.

## 1. Graph-engineering concepts: verdicts for creative video production

| Concept | How frameworks implement it (cited) | Verdict | Why |
|---|---|---|---|
| Directed graphs vs DAGs | LangGraph StateGraph=Pregel BSP engine, not DAG-restricted, cycles first-class[1]. Dagster's asset graph *is* a DAG (lineage only)[21]. | **Adopt** Pregel graph | Rework loops need cycles; DAG tools forbid them. |
| Cyclic/iterative workflows,bounded loops | LangGraph bounds via recursion_limit/GraphRecursionError+RemainingSteps[1]; Burr via `expr('epochs>100')` transition conditions[12]. | **Adopt** | Hard, inspectable termination guarantee. |
| Nodes/edges/conditional routing | `add_conditional_edges(source,path_fn,path_map)`→next node(s)/END[1]; `Command(update=,goto=)` fuses write+route[10]. | **Adopt** | Fits scene/shot branching and QC pass/fail routing. |
| State machines/statecharts | Burr: Action(reads/writes)+`Transition(condition)` over immutable State[11][12]. SK Process: event-driven Steps/Events, cycles+nesting[43]. | **Adapt** | Flat FSMs confirmed; full Harel statecharts unconfirmed. |
| Shared typed state+reducers | `Annotated[T,reducer_fn]`; default overwrites, `operator.add`/add_messages merge parallel writes; Overwrite bypasses once[1][10]. | **Adopt** | Confirmed safe way to merge concurrent fan-out writes. |
| Hierarchical/nested graphs,subgraphs | Compiled subgraph=node if state keys overlap, else wrap+translate[5]. Burr RunnableGraph substitutes an Action w/ a sub-graph[14]. MAF nests Workflows as Executors[41]. | **Adopt** | Matches scene→shot→render nesting. |
| Checkpoints, persistence, resumability | BaseCheckpointSaver keyed by thread_id(+checkpoint_id); SqliteSaver/PostgresSaver[2][3]. Time-travel: get_state_history+update_state[6]. Burr `fork_from_app_id/_sequence_id`[13]. | **Adopt** | Zero-external-service resumability. |
| Human-in-the-loop interrupts/resume | `interrupt(value)` raises GraphInterrupt; resume via `Command(resume=...)`; node re-executes from top (pre-interrupt effects must be idempotent)[4]. Prefect pause_flow_run[27]. Temporal Signals/Updates[18]. | **Adopt** | Best fit for shot-approval gates; forces idempotent nodes. |
| Multi-agent orchestration(supervisor/handoff/hierarchical) | OpenAI SDK: `handoff()`(mesh)+`Agent.as_tool()`(supervisor)[35]. AutoGen: Swarm(handoff)/SelectorGroupChat(supervisor)/nested chats[38][39]. MAF: 5 patterns incl. Magentic[41][42]. | **Adapt** | Borrow the taxonomy; engine's "agents" are tool-calling nodes, not chat. |
| Fan-out/fan-in(map-reduce) | `Send(node,arg)` dispatches N parallel calls w/ distinct input; fan-in=automatic reducer merge on shared key[1][10]. Prefect `task.map()`[26]. Burr MapStates/MapActions+`.reduce()`[14]. | **Adopt** | Cleanest pattern: parallel shot render, merged into one manifest. |
| Dependency management | Dagster assets declare deps via params or `deps=[...]`, auto-building lineage[21]. Bazel's action graph declares inputs/outputs explicitly[29]. | **Adapt** | Borrow the dependency-declaration style for the asset layer. |
| Failure recovery, retry(idempotency/backoff/compensation) | `RetryPolicy(max_attempts=3,backoff_factor=2.0,jitter=True,retry_on=...)` per node; `error_handler=` post-exhaustion=compensation[10][7]. Temporal Activity default(1s/2.0x/∞)[17]. | **Adopt** | Matches the brief's retry/idempotency/compensation language. |
| Versioning/provenance/lineage;content-addressed caching | Dagster code_version+auto DataVersion hash skips unchanged rematerialization[22]. Bazel action key=hash(command+env)+Merkle hash of inputs→CAS[29][30]. Nix content-addressed derivations remain experimental[32][33]. | **Adopt pattern, not tool** | hash(inputs+recipe)→skip-render is sound; reimplement in stdlib hashlib+SQLite. |
| Evaluation nodes/quality gates | No framework has a dedicated "evaluation node" primitive — LangGraph confirms it's an ordinary node, usually delegated to LangSmith[1]. | **Adapt** | Wrap deterministic checks (ffmpeg loudnorm/silencedetect/blackdetect[57-60], WCAG contrast[86]) + optional LLM-judge, gated by `interrupt()`. |
| Memory(short-term vs long-term) | Checkpointer=per-thread short-term state[2]; Store=cross-thread KV+optional embedding search, `put/get/search/list_namespaces`[8]. Docs name semantic/episodic/procedural memory on Store[1]. | **Adopt** | Store's procedural-memory pattern is the closest governed-learning-loop analogue. |
| Observability(traces/logs/artifacts) | OpenAI SDK: built-in Traces/Spans, hosted dashboard, disable-able[37]. Burr LocalTrackingClient logs graph+per-step state to `~/.burr`, local UI port 7241[11]. | **Adopt (local)** | Burr's filesystem tracker needs zero external service. |
| Reflection/self-improvement(Reflexion, self-refine) | LangGraph Store pattern: read prompt→model revises from feedback→write back[1] — documented pattern, not a built-in API. No framework guards against drift. | **Adapt** | Universal failure mode: unbounded self-editing drifts/reward-hacks; gate learned-prompt writes behind `interrupt()`+provenance. |

**How vpg applies them** (see `docs/graph-spec.md`): flow edges form a DAG with join = all; rework is modelled with bounded loop
edges (`max_traversals`) plus workflow budgets (`max_rework_cycles`, `max_same_defect_failures`). Routing is on node outcomes
(`when:`) and on regeneration targets (`target:<node>`). The state is a typed pydantic model with engine-managed append-only
collections, which play the role of reducers for fan-in. Checkpoints go to JSON files with a hash-chained ledger. Human gates
are WAITING_HUMAN states resumed with `vpg approve`. Fan-out comes from conflict-free batches and `map_over`. Evaluation
nodes are REVIEW nodes, where deterministic tools beat agent scores. Learning is proposal-only, with the lifecycle in
`vpg/memory.py`.

## 2. Frameworks compared

| Framework | Capabilities | Limitations | Integration | Fit |
|---|---|---|---|---|
| LangGraph | Pregel graph, reducers, Send, subgraphs, checkpointers(Memory/Sqlite/Postgres), interrupt/Command, RetryPolicy/TimeoutPolicy, Store+semantic search[1-10] | Checkpoints unbounded, unpruned[3]; `stream_events(v3)` experimental[1]; LangSmith Deployment=separate paid product[9] | Python≥3.10, `pip install langgraph`; zero external services (OSS core); Postgres/Redis only for durable backends[1-3] | **Adopt** as kernel |
| Temporal | Durable execution via Event-History replay; Activity RetryPolicy+heartbeats; Signals/Queries/Updates; Continue-As-New[15-18] | Workflow code must be deterministic; no Python embedded-server(Go-only,not-for-prod)[20]; workflows not retried by default | Needs a running Temporal Service(dev/self-hosted/Cloud)+Cassandra/MySQL/Postgres[19][20] | **Avoid**—needs a service; borrow retry/Signal vocabulary |
| Dagster | Software-Defined Assets; auto lineage graph; code_version+DataVersion memoization; op/run RetryPolicy incl.FROM_ASSET_FAILURE[21-24] | Needs dagster-webserver+dagster-daemon for schedules/sensors; SQLite dev storage ephemeral | Python 3.9–3.14; assets callable w/o full deployment; prod needs Postgres[24] | **Adapt**—asset-lineage/caching model, not the daemon |
| Prefect | @flow/@task; `task.map()` fan-out; cache_policy(DEFAULT/INPUTS/TASK_SOURCE); `retries=`/retry_condition_fn; HITL pause_flow_run[25-28] | OSS quickstart instructs `prefect server start` first; multi-worker needs Postgres+Redis[28] | Python only; flows callable as plain functions; zero-process mode unconfirmed(UNVERIFIED) | **Adapt**—caching taxonomy reusable |
| Burr | Explicit FSM (Actions/Transitions/State); fork_from_app_id branch-from-checkpoint; MapStates/MapActions fan-out/reduce; LocalTrackingClient UI[11-14] | Tracking limited to local filesystem client; "Apache Burr(incubating)"—maturity signal | Pure Python, dependency-free core, no server required; opt-in SQLite persister[11] | **Adopt/Adapt**—best lightweight FSM reference |
| OpenAI Agents SDK | Agent/Runner; `handoff()`; input/output Guardrails(parallel or blocking); built-in Tracing w/ Spans[34-37] | Tracing posts to hosted `platform.openai.com/traces` by default; unavailable under Zero Data Retention[37] | Python 3.10+, MIT, provider-agnostic (100+ LLMs)[34] | **Adapt**—guardrail/handoff vocabulary only |
| AutoGen | RoundRobinGroupChat/SelectorGroupChat(supervisor); Swarm(handoff); GraphFlow(experimental graph); nestable Group-Chat-Manager hierarchies[38-39] | Officially in maintenance mode — "new users should start with Microsoft Agent Framework"[38] | Python 3.10+, MIT+CC-BY-4.0 | **Avoid** direct adoption; reference only |
| Microsoft Agent Framework | 5 orchestrations(Sequential/Concurrent/Handoff/Group-Chat/Magentic); graph Workflows w/ checkpointing+time-travel[40-42] | Still Preview, no confirmed GA date; Magentic "untested outside original design"[42] | Python/.NET/Go(preview); `pip install agent-framework`; no mandatory service | **Adapt**—most complete pattern taxonomy found |
| Semantic Kernel Process Framework | Event-driven graph (Steps/Events/ProcessBuilder); cycles+nested sub-processes; local runtime default, optional Dapr[43] | .NET-first; Python HITL "coming soon," not implemented[44]; marked experimental in one official doc | .NET/Python(MIT); local by default | **Avoid** for Python-only engine |
| CrewAI Flows | @start/@listen/@router/or_/and_ event-graph DSL; @persist→SQLite default; fork via restore_from_state_id; @human_feedback HITL[45] | @human_feedback needs CrewAI≥1.8.0; token-usage reporting has a known gotcha | Python≥3.10,<3.14, MIT, no external service by default | **Adapt**—closest stdlib-friendly analogue |
| Bazel/Make/Nix(reference) | Bazel: content-hash action key+CAS[29][30]. Make: mtime only, no hashing[31]. Nix: input-addressed by default; content-addressed=experimental[32][33] | Not orchestration engines; adopting directly adds needless overhead | N/A | Reference-only—reimplement hash-key idea in hashlib |

The **Fit** column is the research verdict on each framework taken alone. vpg's actual choice was a standalone engine that borrows LangGraph's vocabulary instead of adopting it as the kernel. That choice, and why LangGraph and Workflow OS were not adopted, are recorded in §5 and [ADR 0001](../adr/0001-graph-engine.md).

## 3. Production tools: capabilities, limits, integration, and the node they fit

Concrete capabilities and limitations come from the cited sources. The last column adds what the case-study production observed.

| Tool | Capabilities | Limitations | Inputs → Outputs | Integration/licence | Pipeline node |
|---|---|---|---|---|---|
| Remotion (+Lambda) | React→frame-by-frame,pinned headless Chromium,deterministic; `renderMedia()`/`npx remotion render`[46][48][50]. Lambda:chunks across AWS invocations[49]. | 5.0 license drafted(PR#3750,unmerged) vs. FAQ inconsistency—UNVERIFIED[46][47]. Lambda:~10GB storage,~5GB/2hr ceiling,no AV1[49]. | React/TSX→MP4; Lambda:bundle→S3+progress | Free:individuals,≤3-employee orgs,non-profits,evaluation; else Company License[46][47]. Lambda:AWS+"Cloud Rendering Units"(rate UNVERIFIED)[49]. | Final render/composition |
| Motion Canvas | Generator-function (yield) animation model; live editor preview[51] | Native output=PNG/frame sequence only, ffmpeg/NLE assembly external per docs[51]; last release 2024-12 (stale) | TS scene code → image sequence | MIT[52] | Vector/diagram animation authoring |
| Revideo | Fork of Motion Canvas; `renderVideo()` w/ parallel workers, WASM exporter, serverless-capable[53][54] | Pre-1.0 (`0.11.0`); org renamed redotvideo→midrender | TS project+variables → MP4 | MIT[53] | Parameterized/templated render |
| Playwright→frames→ffmpeg | `page.clock` drives requestAnimationFrame/Date deterministically; `page.screenshot()`/tick→numbered PNGs→ffmpeg image2 assembly[55][56] | Not an official Playwright recipe; demuxer -framerate flag not in ffmpeg's formats chapter(UNVERIFIED) | HTML/CSS/JS scene → PNG seq → MP4 | Playwright OSS+ffmpeg(LGPL/GPL) | Custom-scene render |
| ffmpeg filters (QC) | ebur128(measure)/loudnorm(normalize,1-/2-pass,`I=`/`TP=`/`LRA=`)/silencedetect(`noise=`,`duration=`)/blackdetect(`d=`,`pic_th=`)/subtitles(libass burn-in; SRT/ASS confirmed, WebVTT unconfirmed)[57-61] | loudnorm 2-pass needs prior measure pass; subtitles forces full re-encode vs. soft-sub copy | Audio/video → loudness metadata/burned captions | ffmpeg(mixed LGPL/GPL) | QC/evaluation node |
| OpenAI Sora 2 | 16–20s(guide) or 4/8/12/16/20s(Cookbook, conflicting)[63]; ≤1920×1080(-pro); native sync audio; Characters API(consented cameo,≤2 refs); Edit/Extend[63][64] | **⚠OpenAI's own API shut down 2026-09-24** ("no 1:1 replacement")[62]; no text/UI-hallucination admission; blocks real people/IP | Text/image/video prompt→MP4+audio | Azure Foundry lists it preview, retiring 2026-10-15—continuity past OpenAI's shutdown UNVERIFIED[65][66] | Generative B-roll/hero shots |
| Google Veo 3.1 | 8s native(+720p-only extension); 720p/1080p/4K; 16:9 or 9:16; native audio; first/last-frame control, ≤3 ref images, seed consistency[67] | Camera-angle control caveated unreliable; official admission: quotation marks trigger unwanted on-screen text[68] | Text/image prompt→MP4+audio | Gemini API / Gemini Enterprise Agent Platform[67] | Generative B-roll |
| Runway(Gen-4.5/Aleph 2.0) | Aleph 2.0:≤30s@1080p video-to-video edit,multi-shot propagation[69]; "Recipes"(product ad video,UGC,ad localization)[69] | Gen-4.5 duration/resolution Cloudflare-blocked—UNVERIFIED; native in-gen audio unconfirmed(separate TTS/SFX endpoints) | Text/image/video→MP4 | Usage policy bars unauthorized likeness/IP/living-artist-style mimicry[70] | Generative B-roll+in-context edits |
| OpenAI gpt-image-1 | "Reliably incorporating text"(official); inpainting via mask; multi-turn edits via Responses API previous_response_id[72] | "Can still struggle with precise text placement"(official)[72]; requires Org Verification | Text/image+mask → PNG/JPEG/WebP | Now labeled "previous" model[71] | Storyboard/still generation |
| OpenAI gpt-image-2 | Confirmed real & GA(resolves brief's core question)—4K, arbitrary WxH, improved editing+face preservation, auto high-fidelity input[73][74] | "ChatGPT Images 2.0" consumer-name link is circumstantial only | Text/image → image | GA on OpenAI API and Azure AI Foundry[74] | Storyboard/still generation |
| FLUX.1 Kontext(Black Forest Labs) | Unified text-to-image+instruction-based editing; character consistency across scenes; competitive typography claimed[75] | BFL now steers new projects to FLUX.2; multi-turn editing can degrade quality(official)[75] | Text+ref image → image | `[dev]`=non-commercial license v2.0[76]; `[pro]/[max]`: BFL API+Azure AI Foundry(BFL-confirmed)[77] | Storyboard/still gen, edit-with-reference |
| Qwen3-TTS | 3-second zero-shot voice clone(Base); text-description VoiceDesign model; CustomVoice styleable presets; 10 languages[78][79] | Voice-cloning consent/ethics is the operator's responsibility, not enforced by the repo | Text(+ref audio) → speech | Apache-2.0, open weights, local: `pip install qwen-tts`, CUDA/CPU, weights via HF/ModelScope[78][79] | Narration synthesis(open-weight,local) |
| **Azure AI Speech (Neural TTS)** | Full SSML: `prosody`/`break`/`phoneme`(ipa/sapi/ups/x-sampa)/`say-as` + `mstts:express-as`/`backgroundaudio`/`viseme` extensions [80] | **Custom Neural Voice is Limited Access** — registration, Microsoft-account-team eligibility, documented voice-talent consent + biometric verification required before any model trains; "Personal voice" similarly gated [81] | Text/SSML(+consented voice sample) → speech | Azure subscription; CNV/Personal-voice need an approved application, not self-service [81] | Narration synthesis (consented/enterprise) |
| **ElevenLabs** | 4 models trading latency/richness (Eleven v3 → Flash v2.5, 75ms); Instant Voice Cloning (<2 min audio) vs Professional (~1–3hr audio, 3–6hr fine-tune, "virtually indistinguishable") [82][83] | PVC requires Creator plan+; mandatory **Voice CAPTCHA** verification before clone use; no singing support [83] | Text(+ref audio) → MP3/PCM/µ-law/Opus | API-key SDK (Python/TS); Prohibited-Use Policy bars non-consensual cloning and CAPTCHA evasion [83] | Narration synthesis, voice cloning |
| **Whisper large-v3** | Word-level timestamps **natively built into the base repo** (`word_timestamps=True`, cross-attention+DTW, per-model `_ALIGNMENT_HEADS`) — not solely a WhisperX/stable-ts add-on; exposes `no_speech_threshold`/`hallucination_silence_threshold` to fight silence hallucination [84] | Model card itself admits hallucination from weakly-supervised training, worse in low-resource languages; independent research (Koenecke et al.) finds ~1% of transcripts contain fully fabricated phrases, correlated with longer non-vocal spans [84][85] | Audio → transcript + word timestamps | **MIT**, local inference, 1550M params, ~10GB VRAM [84] | ASR/QC node (verify narration vs. captions, flag hallucinated silence) |
| **Impeccable** | 24 commands (`critique`/`audit`/`polish`/`harden`/`animate`...), 61 deterministic detector rules + LLM critique, live-browser variant iteration, `PRODUCT.md`/`DESIGN.md` context model [98][104] | **Frontend/UI craft skill only** — no video, render, or voice capability whatsoever; its launcher downloads a small engine binary on first run (SHA-256 verified) | Project code/design docs → design critique or edits | Apache-2.0; upstream `pbakaus/impeccable`, docs at impeccable.style; explicitly descends from Anthropic's `frontend-design` skill [98][99] | Pre-production creative-direction QA (esp. product-UI capture fidelity before it enters a shot) |
| **video-shotcraft** | 12 curated shot/transition recipe families (of upstream's own inconsistently-reported ~152–157 cards / 209–216 previews) — e.g. `depth-layer-moves`, `circle-match-iris`, `spotlight-hero-card` — each with a source card + Remotion (TSX) implementation [100][102] | **Curated reference only** — "does not install or run a renderer, generate voices, launch a workbench or publish media"; adapting the TSX still needs a separately-licensed Remotion project [102] | Brief+brand+asset registry → a bounded shot-selection **plan** (table), not rendered video | Apache-2.0 (upstream `Vincentwei1021/video-shotcraft`, pinned commit `a1ec677c`); Remotion's own license is explicitly separate and not covered [100][102] | Shot-planning layer sitting above a Remotion renderer |
| **OpenMontage** | Self-described "first open-source, agentic video production system" — hybrid pipeline distinguishing anchor footage from generated/designed support; asset-role mapping; voice/style sample-approval gates before batching [101][103] | **AGPL-3.0** — network-service/distribution obligations need dedicated legal review before adoption; default setup installs Python deps + Remotion + Piper with an automatic cloud-TTS fallback if Piper fails, "unsuitable as an unattended repair for a private local voice workflow" [103] | Natural-language brief → researched/scripted/assembled video (full pipeline) | AGPL-3.0 [101]; Python+Node+Remotion+Piper stack; own state/artifact schema and provider selectors | Reference architecture for pipeline design only — **not vendored/adopted** locally [102][103] |

### Observed in the case-study production (local evidence)

| Tool | Where it was used | What was observed | Node in this workflow |
|---|---|---|---|
| Deterministic HTML/SVG renderer (Playwright/Chromium) + ffmpeg | R01–R07 | The picture is a pure function of time. A lossless capture of the untouched prototype went into a 1:1 editorial window. Accepted outputs must never be overwritten. | N13, N14 |
| Qwen3-TTS VoiceDesign/Base (local, identity-locked) | R06–R07 | About 0.28× real time; several seeds per unit, with ASR choosing the take. "Versioned" was heard as "version" in 8 of 8 takes, so the line was rephrased. ASR and levels cannot certify warmth or age; a human listening pass is required. | N12 |
| Whisper large-v3 (MLX, offline) | R02, R05, R07 | Word timings drive captions and cues. Plain ASR split "experts" at the /p/ closure; a zoomed spectrogram plus a local-snippet test were needed to prove the word was intact. | N12 QC, N18 |
| Apple Vision OCR (on-device) | R02, R07 | Terminology and density measurement from frames; uncorrected and approximate. | N19, density |
| FDE Design Reviewer (independent critic agent) | R07 v01–v15 | Pixel-evidenced critique with severities and traced fixes. It is read-only, cannot listen and never approves a release. | N16, N17, N20 |
| video-shotcraft (curated, reference-only) | R06–R07 | Motivated seams, source-locked reveals and restrained real-UI camera attention. It must not bring in its template skin or fictional UI. | N08, N13 |
| Azure AI Foundry media adapters | R01, R03 | Generic image, video and TTS smoke tests only. Private product material may not be uploaded without scope approval. Sora output was 720p, upscaled. | N11 (generic plates only) |
| OpenMontage | R01, R06 | Research-only (AGPL-3.0). Its asset-role mapping and voice-sample gate informed N10 and N12; nothing was vendored. | reference |

## 4. Quality standards that became testable gates

| Standard | Threshold | Source |
|---|---|---|
| WCAG 2.2 SC 1.4.3 (AA) | Normal text ≥ **4.5:1**; large text ≥ **3:1** | W3C [86] |
| WCAG 2.2 "large-scale" (glossary) | ≥18pt regular (≈24px) or ≥14pt bold (≈18.5px) | W3C [86][87] |
| WCAG 2.2 SC 1.2.2 (A) | Captions required for all prerecorded audio in synchronized media | W3C [86] |
| WCAG 2.2 SC 2.3.1 (A) | ≤3 flashes/sec, else below general/red-flash luminance-area thresholds | W3C [86] |
| WCAG 2.2 SC 1.4.11 (AA) | UI components / graphical objects ≥ **3:1** vs. adjacent color | W3C [86] |
| EBU R128 (broadcast) | **−23.0 LUFS** integrated (±1.0 LU; ±0.2 LU for QC tooling); True Peak ≤ **−1 dBTP** | EBU [88] |
| ATSC A/85:2026-07 (US broadcast) | **−24 LKFS** (±2dB); TP ≤ **−2 dBTP**; streaming range −23 to −27 LKFS noted | ATSC [89] |
| Spotify (streaming) | **−14 dB LUFS**; TP < −1 dBTP | Spotify, official [90] |
| Apple Podcasts/Music Sound Check | **−16 dB LKFS** (±1dB); TP ≤ −1 dBFS | Apple, official [91] |
| Netflix Sound Mix | **−27 LKFS** dialogue-gated (±2 LU); TP ≤ −2 dBTP (−2.3 Atmos) | Netflix Partner Help, official [92] |
| YouTube (streaming) | ~−14 LUFS commonly cited | **UNVERIFIED** — no official page located |
| Netflix Timed Text | ≤**20 CPS** adult / ≤**17 CPS** children; ≤**42** chars/line; ≤2 lines; **20-frame** min–**7s** max | Netflix, official [92][93] |
| BBC Subtitles | **160–180 WPM** (≈0.3–0.375s/word); match programme pace, prefer verbatim | BBC, official [94] |
| DCMP Captioning Key | ≤130/140/160 wpm by education level; duration **40 frames(1s10f)** min–**6s** max | DCMP, official [95] |
| SMPTE RP 218:2009 (legacy, 16:9) | Safe Action = 90% W×H; Safe Title = 80% W×H — **self-flagged legacy** | SMPTE, via preview excerpt [96] |
| EBU R95 v1.1 (current, 16:9) | Action Safe = 93% (3.5% inset); Graphics/Title Safe = 90% (5% inset) | EBU, official [97] |

**Where they live:** `config/defaults.yaml` → `vpg/tools/legibility.py`, `captions.py` and `media.py`. The workflow's defaults
(−16 LUFS ±1 / ≤ −1 dBTP; ≤ 20 cps; title-safe 5%) follow the online-delivery and EBU R95 lines above. Broadcast delivery
would override them in `evaluation.md`.

## 5. Recommendations and how vpg applied them

1. **Adopt a Pregel-style kernel, SQLite-only.** Build the control-flow layer directly on LangGraph's proven vocabulary (`StateGraph`, reducers, `add_conditional_edges`, `Send`, `interrupt()`/`Command`, `RetryPolicy`) but pin the checkpointer to `SqliteSaver`/in-memory only [2][3]. This is the one researched framework confirmed to need **zero external services** for its OSS core — Temporal, Dagster, and Prefect all require a server/daemon/DB for production use [19][24][28].
2. **Reimplement content-addressed caching in stdlib, don't depend on Bazel/Nix/Dagster.** Borrow the *pattern* — `cache_key = hash(node_code_version + hash(inputs))`, skip re-render on a hit — proven by Bazel's action-key/CAS model and Dagster's `code_version`+`DataVersion` [22][29][30]. Implement with `hashlib.sha256` + a local SQLite/`.cache/<sha>` directory; this gives incremental rebuilds (don't re-render an unchanged shot) with no daemon or remote cache service.
3. **Make evaluation nodes first-class and deterministic-first.** No framework researched has a dedicated "evaluation node" primitive [1] — build one explicitly: wrap ffmpeg `loudnorm`/`ebur128`/`silencedetect`/`blackdetect` [57-60] and WCAG contrast math [86][87] as reference-based gates, and reserve LLM-as-judge for subjective checks only, since it has no researched deterministic guarantee.
4. **Gate the learning loop behind `interrupt()` + a provenance ledger.** Model the "governed learning loop" on LangGraph's documented (but not built-in) procedural-memory pattern — read a versioned prompt/recipe from a local `Store`-like KV table, propose a revision, **require human approval via `interrupt()`** before writing back, and log every write with `who/why/diff` [1][4][8]. This directly guards the universal failure mode found across Reflexion/self-refine-style loops: unbounded drift with no built-in stop.
5. **Keep it pydantic/stdlib end-to-end.** Use Pydantic `BaseModel` for the validated state schema (LangGraph explicitly supports this, at an acknowledged performance cost vs. `TypedDict` [1]), `sqlite3` (stdlib) for checkpoints/cache/store, and `subprocess` calls to the Remotion CLI / ffmpeg binaries rather than heavyweight SDKs — avoiding any hard dependency on a package that itself mandates a server (Temporal, Dagster, Prefect, Semantic Kernel's Dapr runtime) [19][24][28][43].

| # | Applied? | How |
|---|---|---|
| 1 | Adapted | vpg reuses LangGraph's vocabulary (conditional edges, `Send`-like fan-out, interrupt/resume, `RetryPolicy`) but implements it in its own engine, with no LangGraph dependency. As measured on 2026-09-26, that is about 3,000 lines of core Python (3,800 with the CLI, docs generator and dry-run driver) plus about 1,300 lines of deterministic tools, on the standard library, pydantic, PyYAML and jsonschema. Its checkpoints are JSON files, not SQLite. The reason is in ADR 0001 (Yellow, with a trigger). |
| 2 | Adopted | Node fingerprint = hash(node definition, prompt, command, input artifact hashes, control hashes, rework context). A STALE node with an unchanged fingerprint is memo-skipped. |
| 3 | Adopted | REVIEW nodes run deterministic tools first. A tool failure on a MUST dimension overrides the agent's score, and LLM judgment covers only the review-type dimensions. |
| 4 | Adopted | N25 writes a proposal. `vpg memory apply` requires a named human; every change bumps the version, archives the previous file and appends to the changelog. |
| 5 | Adopted | Pydantic state, `subprocess` calls to ffmpeg and to project commands, and no required service. |

## 6. UNVERIFIED items

- **Sora 2 / Azure Foundry continuity**: OpenAI's own direct API shut down 2026‑09‑24 [62][64]; Azure AI Foundry's retirement schedule (updated 2026‑09‑23) still lists it retiring 2026‑10‑15 — whether Azure's copy runs on independent infrastructure and remains live past OpenAI's shutdown is not confirmed anywhere [65][66].
- **Remotion's operative license today**: `LICENSE.md` (for-profit + ≤3 employees) vs. the live FAQ page (already using unmerged 5.0 wording, org-agnostic headcount) — PR #3750 is open/unmerged; which text is contractually enforced right now is unresolved [46][47].
- Remotion's exact "Cloud Rendering Unit" rate/definition [49].
- YouTube's own official loudness target — no `support.google.com/youtube` page states a LUFS figure; −14 LUFS is secondary/industry-inferred only.
- SMPTE ST 2046‑1 / RP 2046‑2 (the modern safe-area successor standard) exact percentages — paywalled; only legacy RP 218 (90/80%) is primary-verified, with EBU R95 (93/90%) as the closest verified current analogue [96][97].
- WebVTT support in ffmpeg's `subtitles` filter, and exact subtitle mux codec names (`mov_text`/`webvtt`/`srt`/`ass`) — not found in the official filter text fetched [61].
- Whether the Playwright→`image2`-demuxer frame-assembly pattern's input-side flags (e.g. `-framerate`) are documented in a dedicated FFmpeg "Demuxers" chapter — only the muxer-side `%d`-pattern behavior was confirmed [55][56].
- Runway Gen-4.5's exact duration/resolution limits, and whether its video generation (not just its separate audio endpoints) produces native synchronized audio — `help.runwayml.com` was Cloudflare-blocked on every attempt [69].
- Full Harel-statechart semantics (hierarchical + orthogonal regions, e.g. XState) in any researched framework — Burr and Semantic Kernel Process Framework confirmed only flat/graph FSMs with conditions [11][43].
- LangGraph's exact currently-published PyPI version (only a `main`-branch source snapshot, `1.2.12`, was confirmed) [10]; and whether `Send` has any documented hard fan-out-width cap beyond `recursion_limit` [1].
- Microsoft Agent Framework's GA date — one official devblog says only "still in Preview" [40][42].
- A fully server-less "ephemeral" Prefect mode — described in community sources only, not confirmed on an official `docs.prefect.io` page [28].
- Exact dollar pricing for BFL FLUX tiers and LangSmith Deployment plans — intentionally not reported per instructions [9][75][76].
- Whether "ChatGPT Images 2.0" (OpenAI system card) is formally the same model as the `gpt-image-2` API — snapshot-date match is circumstantial only [73].
- ATSC A/85's link to the FCC CALM Act — common industry knowledge, not textually present in the fetched PDF itself [89].

## 7. References (accessed 2026-09-26)

1. https://docs.langchain.com/oss/python/langgraph/graph-api — Graph API overview
2. https://docs.langchain.com/oss/python/langgraph/checkpointers — Checkpointers
3. https://docs.langchain.com/oss/python/langgraph/persistence — Persistence
4. https://docs.langchain.com/oss/python/langgraph/interrupts — Interrupts
5. https://docs.langchain.com/oss/python/langgraph/use-subgraphs — Subgraphs
6. https://docs.langchain.com/oss/python/langgraph/use-time-travel — Use time-travel
7. https://docs.langchain.com/oss/python/langgraph/fault-tolerance — Fault tolerance
8. https://docs.langchain.com/oss/python/langgraph/stores — Stores
9. https://docs.langchain.com/langsmith/deployment — LangSmith Deployment
10. https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/types.py — source: Send/Command/RetryPolicy/Overwrite
11. https://github.com/apache/burr — README, "Apache Burr (incubating)"
12. https://burr.apache.org/docs/concepts/transitions/ — Transitions
13. https://burr.apache.org/docs/concepts/state-persistence/ — State Persistence
14. https://burr.apache.org/docs/concepts/parallelism/ — Parallelism
15. https://docs.temporal.io/evaluate/why-temporal — Why Temporal?
16. https://docs.temporal.io/workflows — Temporal Workflow
17. https://docs.temporal.io/encyclopedia/retry-policies — What is a Temporal Retry Policy?
18. https://docs.temporal.io/evaluate/features/workflow-message-passing — Workflow message passing
19. https://docs.temporal.io/temporal-service — Temporal Service
20. https://docs.temporal.io/self-hosted-guide/embedded-server — Embedding Temporal server as a Go library
21. https://docs.dagster.io/guides/build/assets/defining-assets — Defining assets
22. https://docs.dagster.io/guides/build/assets/asset-versioning-and-caching — Asset versioning and caching
23. https://docs.dagster.io/deployment/execution/run-retries — Configuring run retries
24. https://docs.dagster.io/deployment/execution/dagster-daemon — Dagster daemon
25. https://docs.prefect.io/v3/concepts/caching — Caching
26. https://docs.prefect.io/v3/how-to-guides/workflows/retries — How to automatically rerun your workflow when it fails
27. https://docs.prefect.io/v3/advanced/interactive — How to write interactive workflows
28. https://docs.prefect.io/v3/concepts/server — Prefect server
29. https://bazel.build/remote/caching — Remote Caching
30. https://bazel.build/basics/hermeticity — Hermeticity
31. https://www.gnu.org/software/make/manual/make.html — GNU Make Manual
32. https://nix.dev/manual/nix/2.34/protocols/store-path — Store Path Specification
33. https://nix.dev/manual/nix/2.34/development/experimental-features — Experimental Features
34. https://openai.github.io/openai-agents-python/ — OpenAI Agents SDK
35. https://openai.github.io/openai-agents-python/handoffs/ — Handoffs
36. https://openai.github.io/openai-agents-python/guardrails/ — Guardrails
37. https://openai.github.io/openai-agents-python/tracing/ — Tracing
38. https://github.com/microsoft/autogen — README (maintenance-mode notice)
39. https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html — GraphFlow (Workflows)
40. https://learn.microsoft.com/en-us/agent-framework/overview/ — Microsoft Agent Framework Overview
41. https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/ — Workflow orchestrations in Agent Framework
42. https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic — Workflows Orchestrations - Magentic
43. https://learn.microsoft.com/en-us/semantic-kernel/frameworks/process/process-framework — Process Framework
44. https://learn.microsoft.com/en-us/semantic-kernel/frameworks/process/examples/example-human-in-loop — How-To: Human-in-the-Loop
45. https://docs.crewai.com/concepts/flows — Flows
46. https://github.com/remotion-dev/remotion/blob/main/LICENSE.md — Remotion License
47. https://www.remotion.dev/docs/license/faq — License FAQ
48. https://www.remotion.dev/docs/renderer/render-media — renderMedia()
49. https://www.remotion.dev/docs/lambda — @remotion/lambda
50. https://www.remotion.dev/docs/miscellaneous/chrome-headless-shell — Chrome Headless Shell
51. https://motioncanvas.io/docs/rendering/ — Rendering
52. https://github.com/motion-canvas/motion-canvas/blob/main/LICENSE — LICENSE
53. https://midrender.com/revideo/docs/api-reference/renderer/renderVideo — renderVideo()
54. https://midrender.com/revideo/docs — Welcome to Revideo!
55. https://playwright.dev/docs/screenshots — Screenshots
56. https://playwright.dev/docs/clock — Clock
57. https://ffmpeg.org/ffmpeg-filters.html#ebur128 — ebur128
58. https://ffmpeg.org/ffmpeg-filters.html#loudnorm — loudnorm
59. https://ffmpeg.org/ffmpeg-filters.html#silencedetect — silencedetect
60. https://ffmpeg.org/ffmpeg-filters.html#blackdetect — blackdetect
61. https://ffmpeg.org/ffmpeg-filters.html#subtitles — subtitles
62. https://developers.openai.com/api/docs/deprecations — Deprecations
63. https://developers.openai.com/api/docs/guides/video-generation — Video generation with Sora
64. https://openai.com/index/sora-2-system-card/ — Sora 2 System Card
65. https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/video-generation — Sora 2 video generation overview (preview)
66. https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule — Model retirement schedule
67. https://ai.google.dev/gemini-api/docs/veo — Generate videos with Veo 3.1
68. https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/best-practice — Best practices for generating videos
69. https://runway.com/research/introducing-runway-gen-4.5 — Runway Gen-4.5
70. https://runway.com/safety/usage-policy — Runway's Usage Policy
71. https://developers.openai.com/api/docs/models/gpt-image-1 — GPT-Image-1
72. https://developers.openai.com/api/docs/guides/image-generation — Image generation
73. https://developers.openai.com/api/docs/models/gpt-image-2 — GPT-Image-2
74. https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/dall-e — How to Use Image Generation Models from OpenAI
75. https://bfl.ai/blog/flux-1-kontext — Introducing FLUX.1 Kontext
76. https://bfl.ai/legal/non-commercial-license-terms — FLUX [dev] Non-Commercial License v2.0
77. https://bfl.ai/blog/flux-azure-ai-foundry — FLUX Models Launch on Azure AI Foundry
78. https://github.com/QwenLM/Qwen3-TTS — README
79. https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign — model card
80. https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup — SSML overview
81. https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/speech-service/text-to-speech/limited-access — Limited Access
82. https://elevenlabs.io/docs/overview/capabilities/text-to-speech — Text to Speech
83. https://help.elevenlabs.io/hc/en-us/articles/13313681788305 — Instant vs. Professional Voice Cloning
84. https://github.com/openai/whisper — README / model-card.md / transcribe.py
85. https://arxiv.org/abs/2402.08021 — Careless Whisper: Speech-to-Text Hallucination Harms
86. https://www.w3.org/TR/WCAG22/ — Web Content Accessibility Guidelines (WCAG) 2.2
87. https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html — Understanding SC 1.4.3
88. https://tech.ebu.ch/docs/r/r128.pdf — R 128: Loudness normalisation and permitted maximum level of audio signals
89. https://www.atsc.org/wp-content/uploads/2026/07/A85-2026-07-Annex-M.pdf — A/85:2026-07, Annex M
90. https://support.spotify.com/us/artists/article/loudness-normalization/ — Loudness normalization on Spotify
91. https://podcasters.apple.com/support/893-audio-requirements — Audio requirements
92. https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617 — Timed Text Style Guide: General Requirements
93. https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977 — English (USA) Timed Text Style Guide
94. https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/ — BBC Subtitle Guidelines
95. https://dcmp.org/learn/601-captioning-key---presentation-rate — Captioning Key: Presentation Rate (+ dcmp.org/learn/captioningkey/597, Text/duration)
96. SMPTE RP 218:2009 — official record store.smpte.org (paywalled); text verified via https://www.normsplash.com/Samples/SMPTE/197747783/SMPTE-RP-218-2009-en.pdf
97. https://tech.ebu.ch/docs/r/r095.pdf — R 95: Safe areas for 16:9 television production
98. https://github.com/pbakaus/impeccable — README
99. https://impeccable.style — official documentation site (linked from README)
100. https://github.com/Vincentwei1021/video-shotcraft — README
101. https://github.com/calesthio/OpenMontage — README (license badge: AGPL‑3.0)
102. Local: `03_Agents-and-Skills/Skills/video-production/video-shotcraft/{SKILL.md,NOTICE.md,README.md,references/*,import-manifest.json}` (this workspace)
103. Local: `~/.copilot/session-state/de8afd0b-b951-4087-a1a9-e0bce91e22ae/files/video-production-skills-research.md` (prior research note, reviewed 2026‑09‑25)
104. Local: `03_Agents-and-Skills/Skills/impeccable/README.md` (provenance record: `pbakaus/impeccable` @ commit `9d715cc4`, Apache‑2.0)
