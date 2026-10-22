# Agent hosts

> **TL;DR:** vpg works with any coding agent that can read files, write inside the production project and
> run one shell command, including Claude Code, Codex, GitHub Copilot, OpenCode, Gemini CLI and Cursor.
> Agents load [AGENTS.md](../AGENTS.md). Block the human-only commands in each host's permission settings.

## What an agent needs

| Need | Why |
|---|---|
| Read the vpg checkout and the project | Packets cite prompts and schemas in the checkout, and inputs in the project |
| Write inside the project | vpg writes nothing outside the project directory |
| Run `python -m vpg submit` from the checkout | With the interpreter that has `requirements.txt` installed |

vpg itself needs no network, model API or MCP server. Skills and tools named in a packet are capability
hints; see [worker capabilities](skill-bindings.md). Start the agent from a shell where the virtual
environment is active, so `python` resolves to it.

## Verified

Checked in fresh projects built from the fictional sample sources:

- **Any shell-capable agent.** A worker using only file writes and the packet's Submit command passed N01,
  and N02 then passed. The same steps passed in a macOS sandbox that allowed writes only inside the project
  and blocked the network; the checkout stayed read-only.
- **GitHub Copilot CLI.** Given only the packet path, it completed N01 in about a minute and
  submitted as `agent:context-research`; N02 then passed. With `--allow-tool 'shell(python:*)'`, the rule
  `--deny-tool 'shell(python -m vpg approve:*)'` blocked `vpg approve` and still let `vpg status` run.
  Without the trailing `:*`, the rule did not match.
- **AGENTS.md with Copilot CLI.** Started in the checkout, it loaded AGENTS.md with no tool calls. As
  operator of a project at the N03 human gate, with nothing blocking `approve`, it ran only `vpg run` and
  `vpg tasks`, stopped, and reported the decision, approver and evidence. This was one run on one model.

Claude Code, Codex, OpenCode, Gemini CLI and Cursor were not run. Their rows below follow each vendor's
documentation; check them against your host environment.

## Two ways to launch an agent

- **Worker, one packet.** Start the agent in the project directory and give it read access to the checkout.
  Ask it to complete `.vpg/tasks/<packet>.md` exactly as instructed and to run the packet's Submit command.
  The packet is self-contained, so the agent does not need AGENTS.md.
- **Operator, a whole run.** Start the agent in the checkout, so it loads AGENTS.md, and give it access to
  the project directory. It runs `vpg run`, works or hands out packets, and stops at `WAITING_HUMAN` and
  `BLOCKED`.

The verified Copilot CLI worker launch, run in the project directory:

```sh
copilot -p "Complete the task packet .vpg/tasks/<packet>.md exactly as it instructs, then run its Submit command." \
  --add-dir /path/to/video-production-graph --allow-tool write --allow-tool 'shell(python:*)' \
  --deny-tool 'shell(python -m vpg approve:*)'
```

## Host reference

| Host | Reads AGENTS.md | Headless run | Extra directory | Block `vpg approve` |
|---|---|---|---|---|
| Copilot CLI | Yes | `copilot -p` | `--add-dir` | `--deny-tool 'shell(python -m vpg approve:*)'` (verified) |
| Copilot in VS Code | Yes | — | Multi-root workspace | `chat.tools.terminal.autoApprove` entry `"/^python -m vpg approve\\b/": false` forces a confirmation |
| Copilot cloud agent | Yes | Assign an issue | — | A `preToolUse` hook; there is no built-in rule |
| Claude Code | Through [CLAUDE.md](../CLAUDE.md) | `claude -p` | `--add-dir` | `permissions.deny`: `Bash(python -m vpg approve *)`; older releases use `approve:*` |
| Codex CLI | Yes | `codex exec` | `--add-dir` | A `.rules` file: `prefix_rule(pattern=["python", "-m", "vpg", "approve"], decision="forbidden")` |
| OpenCode | Yes | `opencode run` | `permission.external_directory` | `permission.bash`: `"*": "ask"` first, then `"python -m vpg approve*": "deny"` |
| Gemini CLI | With `"context": {"fileName": ["AGENTS.md", "GEMINI.md"]}` in `.gemini/settings.json` | `gemini -p` | `--include-directories` | A Policy Engine rule, or the deprecated `tools.exclude: ["run_shell_command(python -m vpg approve)"]` |
| Cursor | Yes | `cursor-agent -p` | `/add-dir` | `.cursor/cli.json` `permissions.deny`: `Shell(python:-m vpg approve*)` |

Block every human-only command the same way, not only `approve`: also `unblock`, `resolve`,
`restore-control`, `accept-config`, `accept-ledger-break`, `rollback`, `grant` and `memory`.
[AGENTS.md](../AGENTS.md) says which of them a human can delegate with `vpg grant`.

- **Precedence differs.** Deny wins in Claude Code, Copilot CLI and Cursor; Codex applies the most
  restrictive matching rule; OpenCode applies the last matching rule. Copy a rule set between hosts with care.
- **Rules match command text.** `python3 -m vpg approve` or `.venv/bin/python -m vpg approve` slips past a
  rule written for `python -m vpg approve`. These rules prevent mistakes; they are not a security boundary
  (see [limitation 16](gaps-and-limitations.md)).
- **Test each rule on a scratch project.** A command the host blocks never reaches vpg, so
  `.vpg/ledger.jsonl` is unchanged. A command that reaches vpg appends a `resumed` event, even when vpg
  refuses it. Codex can also check its rules with `codex execpolicy check`. The Cursor and Codex rules
  above were not run here.
- **Sandboxes.** `codex exec` starts read-only: pass `--sandbox workspace-write` and add the project
  directory. A cloud agent's environment also needs ffmpeg and ffprobe.

## Independent reviews

vpg refuses a review that names bytes other than the ones on disk. With
`reviews: {binding: require, independence: require}` in `vpg.project.yaml`, it also refuses a review
submitted by the identity that produced the work. Give reviews their own agent identity, ideally on another
host or model, so the critic doesn't share the producer's context.

## Vendor documentation

- Claude Code: [memory](https://code.claude.com/docs/en/memory), [permissions](https://code.claude.com/docs/en/permissions)
- Codex: [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
- GitHub Copilot: [custom instructions](https://docs.github.com/en/copilot/reference/custom-instructions-support),
  [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference),
  [VS Code approvals](https://code.visualstudio.com/docs/agents/run/approvals), [hooks](https://docs.github.com/en/copilot/concepts/agents/hooks)
- OpenCode: [rules](https://opencode.ai/docs/rules/), [permissions](https://opencode.ai/docs/permissions/)
- Gemini CLI: [context files](https://geminicli.com/docs/cli/gemini-md/), [shell restrictions](https://geminicli.com/docs/tools/shell/)
- Cursor: [rules](https://cursor.com/docs/rules), [CLI permissions](https://cursor.com/docs/cli/reference/permissions)
- The [AGENTS.md](https://agents.md/) convention
