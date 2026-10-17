# AGENTS.md

Guidance for coding agents (Claude Code, Codex, GitHub Copilot, OpenCode, Gemini CLI, Cursor and others)
working with this repository. People should start with [README.md](README.md); to set up an agent tool with
vpg, see [agent hosts](docs/agent-hosts.md).

vpg is a file-based production workflow. The engine makes no model or network calls: agents create through
task packets, deterministic tools measure, and named people approve.

## Setup

- Use Python 3.11+ with `requirements.txt` installed (normally in `.venv/`), and ffmpeg/ffprobe on `PATH`.
- Run vpg as `python -m vpg …` from this checkout, with that environment active. Keep that spelling:
  hosts' permission rules match it. If `python` lacks the dependencies, ask the operator to activate the
  environment.
- Production projects live outside this checkout. vpg writes only inside the project directory.

## Work a task packet

A packet, `<project>/.vpg/tasks/<node>-a<attempt>.md`, is the complete assignment.

- Read the inputs it lists. Write only its declared outputs, in its work directory.
- Validate each output against the schema the packet names, and respect its control-document access.
- Submit with the exact command under **Submit**, keeping its token and `--actor agent:<role>`.
- A refused submission (unknown key, missing file, path outside the project) uses no attempt. Fix it and resubmit.
- Skills and tools a packet lists, including MCP servers, are capability hints. Use local equivalents.
- A review must echo the sha256 of every artifact it judged. Never review an attempt you produced.

## Drive a run

1. `python -m vpg run <project>` continues until the run waits, blocks or passes. Submitting does not
   advance the graph, so run it again after each submission.
2. `WAITING_RUNTIME`: work each open packet (`python -m vpg tasks <project>`) or hand it to another agent.
3. `WAITING_HUMAN`: stop. Tell the named human what the gate decides and where its evidence is.
4. `BLOCKED`: stop. Report the escalation in `<project>/.vpg/escalations/`.

## Never act as a human

The engine treats any `--actor` that is not `agent:<id>` (or a dry-run actor) as a person, so it cannot
detect impersonation. Hold this boundary yourself.

- Always use `--actor agent:<role>`, for example `agent:context-research`. Never use a person's name or the
  name of your agent product.
- Never run `approve`, `unblock`, `resolve`, `restore-control`, `feedback --triage`, a change-request
  decision, or an `invalidate` that re-opens a human decision, unless a named human gave you a live grant
  for it (`python -m vpg grant <project> --list`).
- Never run `accept-config`, `accept-ledger-break`, `rollback`, `grant`, `memory apply`, or a
  `memory promote` that approves, revises or retires a rule. These cannot be delegated, and neither can
  approval at a gate with human-judged dimensions or at release.
- Never claim human listening, taste or release approval. Never invent sources, product facts or rights.

## Change this repository

- `graph/workflow.yaml` is the single graph authority. Edit it, `vpg/`, rules, rubrics or prompts, then run
  `python -m vpg docs`. Never hand-edit the files that command generates (catalogs, `docs/graph.*`,
  `knowledge/design-principles.md` and generated schemas).
- Before finishing, run `python -m vpg validate`, `python -m vpg docs --check`, `python -m pytest -q`
  (a few minutes) and `python -m vpg dry-run`.
- Add regression tests under `tests/`. Tests must not need a model API or a paid service.
- Never commit credentials, customer sources, production media, name lists or preference records.
