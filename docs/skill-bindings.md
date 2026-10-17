# Worker capabilities

The graph's `skills` fields describe capabilities for external workers; they are not imports, bundled
skills, or required agent vendors. Read the node prompt, role YAML, output schemas and task packet.
Use your own agent or a human with equivalent capabilities. The engine never calls a model API.

| Skill identifier | Capability expected of the worker |
|---|---|
| `goal-harness` | State the goal, constraints, acceptance checks and stop conditions. |
| `context-intake` | Register source material with provenance and access boundaries. |
| `workiq-grounding` | Retrieve authorized enterprise sources, if that source is available. Local files also work. |
| `evidence-tagging` | Distinguish observed, reported and assumed claims and cite sources. |
| `extreme-clarity` | Give a concise conclusion with measurable evidence. |
| `traffic-light-decisions` | Record a named decision; a conditional choice needs a trigger and expiry. |
| `competitive-landscape` | Research comparable approaches without copying protected material. |
| `concept-creator` | Develop a coherent, audience-specific concept. |
| `storyboard` | Specify shots, timing and transitions. |
| `content-writing` | Write specific, source-grounded narration and screen copy. |
| `demo-master` | Structure a demonstration into clear narration beats. |
| `design-system-as-infrastructure` | Use the production's approved design tokens and components. |
| `design-system-author` | Specify a reproducible visual system. |
| `frontend-design` | Implement visual compositions with clear hierarchy. |
| `azure-foundry-image` | Optional image generation through an authorized provider; supplied assets also work. |
| `asset-collection` | Gather licensed, approved assets before rendering. |
| `remotion` | Optional programmatic scene composition; check its license independently. |
| `video-shotcraft` | Choose motivated camera, motion and transition recipes. Not redistributed here. |
| `video-prototype` | Produce narration, motion, audio and assembly through project commands. |
| `design-review` | Independently critique rendered craft, hierarchy and design fidelity. |
| `accessibility-verification` | Verify contrast, reading time, captions and legibility. |
| `prototype-qa` | Verify output behavior and states with reproducible evidence. |
| `grounding-critic-designer` | Check product claims against cited evidence. |
| `research-synthesis` | Synthesize sourced findings without inventing evidence. |
| `act-on-feedback` | Interpret feedback into specific defects and acceptance checks. |
| `code-review-and-quality` | Independently review changes for correctness and maintainability. |
| `debugging-and-error-recovery` | Reproduce a failure, localize its cause and verify the correction. |
| `provenance-ledger-designer` | Preserve artifact lineage and an auditable decision record. |
| `shipping-and-launch` | Verify the release package and obtain the named human's decision. |
| `wiki-maintenance` | Maintain reusable knowledge with source citations. |
| `decision-capture` | Preserve a durable choice and its rationale. |
| `alignment-playbook` | Surface conflicting stakeholder positions without inventing consensus. |
| `brand-guidelines` | Apply only the brand authority supplied for the current production. |
| `browser-testing-with-devtools` | Verify browser-rendered output and inspect console/network evidence. |
| `context-pull` | Retrieve the smallest relevant set of authorized knowledge sources. |
| `design-criteria` | Define explicit criteria before evaluating concepts. |
| `design-principles` | Define production-specific principles that constrain creative choices. |
| `drift-check` | Compare implementation and source authorities for unintended changes. |
| `excalidraw-diagramming` | Create source-backed diagrams when required by the storyboard. |
| `impeccable` | Critique and refine visual craft within the approved design system. |
| `jobs-to-be-done` | Explain the audience's task and desired outcome. |
| `observability-and-instrumentation` | Measure and report operational behavior with traceable evidence. |
| `persona-critic` | Challenge audience assumptions against evidence. |
| `problem-framing` | State the problem, audience and boundaries. |
| `rubric-authoring` | Write measurable, falsifiable quality criteria. |
| `speed-critique` | Critique concepts against agreed criteria. |
| `success-metrics` | Define the measurements that indicate production success. |
| `webapp-testing` | Capture and verify an authorized product interface in a browser. |
| `whiteboard-storyboard` | Sketch the visual sequence before rendering. |
| `wiki-ingest` | Turn authorized source evidence into cited reusable knowledge. |
| `wiki-query` | Retrieve cited knowledge without treating inference as fact. |

Private workspace skills and custom agents are optional integrations. Their names in role YAML do not
grant tool access or permission. This project does not vendor their implementations or licenses.
