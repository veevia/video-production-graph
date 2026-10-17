"""Executors: how agent and review nodes get their work done.

  TaskPacketExecutor  (default, real productions) writes a task packet for an agent and waits for
                      `vpg submit`. Several packets can be open at once: independent nodes are worked in parallel.
  ScriptedExecutor    (dry runs and tests) replays fixture outputs per node and attempt, standing in for agents.
                      It never stands in for a human: approvals come only from `vpg approve` or an explicitly
                      labelled dry-run auto-approval.
Project commands bound to a node (`commands:` in vpg.project.yaml) run after the agent part, under a wall-clock
timeout. Examples are a renderer, the TTS pipeline or the assembly script. The command reads VPG_CONTEXT and writes
<workdir>/outputs.json.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .context import NodeContext, NodeResult
from .models import Finding
from .util import PKG_ROOT, glob_files, open_regular, read_json, rel, sha256_json, write_json_atomic


class Executor:
    name = "executor"

    def execute(self, ctx: NodeContext) -> NodeResult:  # pragma: no cover - interface
        raise RuntimeError("abstract executor")


def minimal_example(schema: dict[str, Any], root: dict[str, Any] | None = None) -> Any:
    """The smallest instance that satisfies a schema's required structure, to show an agent the expected shape."""
    root = root or schema
    if "$ref" in schema and schema["$ref"].startswith("#/"):
        target = root
        for part in schema["$ref"][2:].split("/"):
            target = target[part]
        return minimal_example(target, root)
    for key in ("oneOf", "anyOf"):
        if key in schema:
            return minimal_example(schema[key][0], root)
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for sub in schema["allOf"]:
            merged.update(sub)
        return minimal_example(merged, root)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0])
    if kind == "object" or (kind is None and "properties" in schema):
        props = schema.get("properties", {})
        return {k: minimal_example(props.get(k, {}), root) for k in schema.get("required", [])}
    if kind == "array":
        return [minimal_example(schema.get("items", {}), root) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "x" * max(1, schema.get("minLength", 1))
    if kind in ("number", "integer"):
        low = schema.get("minimum", schema.get("exclusiveMinimum", 0) + (1 if "exclusiveMinimum" in schema else 0))
        return int(low) if kind == "integer" else float(low)
    if kind == "boolean":
        return False
    return None


def output_contracts(ctx: NodeContext) -> str:
    """For each schema-bound output: where the schema lives, its required fields, and a minimal valid example."""
    blocks = []
    for o in ctx.node.outputs:
        if not o.schema_name:
            continue
        path = ctx.pkg_root / "schemas" / "artifacts" / f"{o.schema_name}.schema.json"
        if not path.exists():
            continue
        schema = read_json(path)
        req = schema.get("required", [])

        def resolve(sub: dict[str, Any]) -> dict[str, Any]:
            while isinstance(sub, dict) and "$ref" in sub and sub["$ref"].startswith("#/"):
                target: Any = schema
                for part in sub["$ref"][2:].split("/"):
                    target = target[part]
                sub = target
            return sub
        items = []
        for k, sub in schema.get("properties", {}).items():   # every array of objects, required or not
            item = resolve(sub.get("items", {})) if sub.get("type") == "array" else {}
            if item.get("properties"):
                fields = ", ".join(f + ("*" if f in item.get("required", []) else "") for f in item["properties"])
                strict = "; no other keys" if item.get("additionalProperties") is False else ""
                items.append(f"each `{k}` item: {fields} (* required{strict})")
        blocks.append(f"- `{o.key}` → schema `{path}`\n  - required: {', '.join(req) or 'none'}"
                      + "".join(f"\n  - {x}" for x in items)
                      + f"\n  - minimal valid shape: `{json.dumps(minimal_example(schema), ensure_ascii=False)}`")
    return "\n".join(blocks) or "- no schema-bound outputs"


def render_prompt(ctx: NodeContext) -> str:
    template = (ctx.pkg_root / ctx.node.prompt).read_text() if ctx.node.prompt else ""
    values = prompt_values(ctx)

    def sub(m: re.Match) -> str:
        return str(values.get(m.group(1).strip(), m.group(0)))

    return re.sub(r"\{\{([^}]+)\}\}", sub, template)


PROMPT_VARS = {"project_name", "node_id", "node_name", "purpose", "objective", "audience", "inputs", "controls_read",
               "controls_write", "outputs", "completion", "rubric", "defects", "failures", "workdir", "attempt",
               "instructions", "items"}


def prompt_values(ctx: NodeContext) -> dict[str, Any]:
    n = ctx.node
    inputs = []
    for ref in n.inputs:
        kind, _, name = ref.partition(":")
        if kind == "artifact":
            paths = [rel(p, ctx.project_dir) for p in ctx.artifact_paths(name)]
            inputs.append(f"- `{ref}` → {', '.join(paths) or 'not yet produced'}")
        elif kind == "control":
            inputs.append(f"- `{ref}` → controls/{name}.md")
        elif kind == "files":
            found = [rel(f, ctx.project_dir) for f in glob_files(ctx.project_dir, name)]
            inputs.append(f"- `{ref}` → {len(found)} file(s): {', '.join(found[:12]) or 'none yet'}{' …' if len(found) > 12 else ''}")
        else:
            inputs.append(f"- `{ref}`")
    dims = []
    for rid in n.rubric:
        for d in ctx.rubrics.get(rid, {}).get("dimensions", []):
            dims.append(f"- {d['id']} [{d.get('level', 'MUST')}] {d['name']} ({d['check']['type']}): {d.get('target', '')}")
    return {
        "project_name": ctx.state.project.name, "node_id": n.id, "node_name": f"N{n.num:02d} {n.name}", "purpose": n.purpose,
        "objective": ctx.state.production.objective or "(not yet defined)",
        "audience": ", ".join(ctx.state.production.audience) or "(not yet defined)",
        "inputs": "\n".join(inputs), "controls_read": ", ".join(n.controls.read) or "none",
        "controls_write": ", ".join(n.controls.write) or "none",
        "outputs": "\n".join(f"- `{o.key}` ({o.kind}{', schema ' + o.schema_name if o.schema_name else ''}{'' if o.required else ', optional'}): {o.description}"
                              + (" — already written by this node's tool check; don't submit it" if o.key in tool_outputs(ctx) else "")
                              for o in n.outputs),
        "completion": "\n".join(f"- {c.description}" for c in n.completion),
        "rubric": "\n".join(dims) or "- (none)",
        "defects": "\n".join(f"- {d.id} [{d.severity}] {d.dimension}{f' @ ' + ', '.join(d.elements) if d.elements else ''}: {d.description} | smallest fix: {d.smallest_fix or 'propose one'} | accept when: {d.acceptance_check}" for d in ctx.defects) or "- none",
        "failures": "\n".join(f"- {f}" for f in ctx.failures) or "- none",
        "workdir": rel(ctx.workdir, ctx.project_dir), "attempt": ctx.attempt, "instructions": n.instructions,
        "items": ", ".join(ctx.items or []) or "n/a",
    }


def tool_outputs(ctx: NodeContext) -> set[str]:
    """Output keys this attempt's deterministic tool check already wrote; the agent never submits them."""
    return set((ctx.pre_result.outputs if getattr(ctx, "pre_result", None) else {}) or {})


class TaskPacketExecutor(Executor):
    name = "task-packet"

    def execute(self, ctx: NodeContext) -> NodeResult:
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        tasks = ctx.project_dir / ".vpg" / "tasks"
        tasks.mkdir(parents=True, exist_ok=True)
        stem = f"{ctx.node.id}-a{ctx.attempt}"
        pre = "\n".join(f"- [{f.severity}] {f.dimension}: {f.message} ({f.evidence})" for f in ctx.pre_findings) or "- none"
        submit = (f"python -m vpg submit {rel(ctx.project_dir, Path.cwd())} {ctx.node.id} --token {ctx.dispatch_token} --actor agent:{ctx.node.agent} "
                  + " ".join(f"--output {o.key}=<path>" for o in ctx.node.outputs if o.key not in tool_outputs(ctx))
                  + (" --review <review.json>" if ctx.node.kind.value == "review" else ""))
        budget = ctx.node.retry.max_attempts
        from .tools.sources import waivers_in_effect
        waived = waivers_in_effect(ctx.state.inputs.waivers, ctx.load("gaps"))
        waived_line = ("\n- **Waived intake gaps:** " + "; ".join(f"{w.gap}, waived by {w.by}: {w.reason}" for w in waived)
                       + ". The film proceeds without these sources. Never fill one by invention; say what is missing."
                       if waived else "")
        bind = ""
        if ctx.node.kind.value == "review":   # v1.7 (#62/#67): the review names the bytes it judged
            from .evaluation import review_bindings
            bound = review_bindings(ctx.node, ctx.current)
            if bound:
                files = "\n".join(f"- `{k}` → {', '.join(rel(p, ctx.project_dir) for p in ctx.artifact_paths(k))} (sha256 {v[:16]}…)"
                                  for k, v in bound.items())
                echo = json.dumps({k: v[:16] for k, v in bound.items()})
                bind = f"""
## Judge exactly this build
Judge these files, the version's registered bytes, never a copy or a newer dev build:
{files}

Put this in your review.json. A review that names other bytes is refused without using an attempt, and the release lists
any review that doesn't name them:
```json
"reviewed": {echo}
```
Submit as your own identity (`--actor agent:<you>`): a reviewer that produced what it judges is not independent.
"""
        text = f"""# Task packet — N{ctx.node.num:02d} {ctx.node.name} (attempt {ctx.attempt})

- **Attempt:** {ctx.attempt}. The node escalates to a human after {budget} consecutive failed attempts. A submission with an
  unknown key, a missing file or a path outside the project is refused without using an attempt.
- **Agent role:** `{ctx.node.agent}` (see `agents/{ctx.node.agent}.yaml`)
- **Skills:** {', '.join(ctx.node.skills) or 'none'} · **Tools:** {', '.join(ctx.node.tools) or 'none'}
- **Dispatch token:** `{ctx.dispatch_token}`. A submission with any other token is rejected as stale.
- **Work directory:** `{rel(ctx.workdir, ctx.project_dir)}`
- **Control documents:** read {', '.join(ctx.node.controls.read) or 'none'}; write {', '.join(ctx.node.controls.write) or 'none'}. Any other edit is reverted and fails the attempt.{waived_line}

{render_prompt(ctx)}

## Output contracts
{output_contracts(ctx)}

## Deterministic findings already measured for this attempt
{pre}
{bind}
## Submit
Relative `--output` and `--review` paths are resolved against the work directory (`{rel(ctx.workdir, ctx.project_dir)}`); absolute
paths must be inside the project. Outputs this node's tool check already wrote are not submitted. Run the command from
the vpg checkout (`{PKG_ROOT}`).
```
{submit}
```
"""
        path = tasks / f"{stem}.md"
        path.write_text(text)
        write_json_atomic(tasks / f"{stem}.json", {"node": ctx.node.id, "attempt": ctx.attempt, "token": ctx.dispatch_token,
                                                   "workdir": rel(ctx.workdir, ctx.project_dir),
                                                   "outputs": [o.key for o in ctx.node.outputs]})
        return NodeResult(pending=True, task_packet=rel(path, ctx.project_dir), executor=self.name)


def _copy_regular(src, dst) -> None:
    """Copy a fixture that is a regular file. A symlink to a device or pipe raises NotRegularFile (no disk-filling copy)."""
    with open_regular(src) as fin, open(dst, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    shutil.copystat(src, dst)


class ScriptedExecutor(Executor):
    """Replays fixtures/<node>/attempt-<k>/outputs.json (falls back to the highest attempt ≤ k, then `default/`)."""

    name = "scripted"

    def __init__(self, fixtures: Path):
        self.fixtures = Path(fixtures)

    def locate(self, node_id: str, attempt: int) -> Path | None:
        base = self.fixtures / node_id
        for k in range(attempt, 0, -1):
            if (base / f"attempt-{k}" / "outputs.json").exists():
                return base / f"attempt-{k}"
        return base / "default" if (base / "default" / "outputs.json").exists() else None

    def execute(self, ctx: NodeContext) -> NodeResult:
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        src = self.locate(ctx.node.id, ctx.attempt)
        if src is None:
            return NodeResult(outcome="pass", executor=self.name, data={"fixture": None})
        spec = read_json(src / "outputs.json")
        for f in src.iterdir():
            if f.name == "outputs.json":
                continue
            if f.is_dir() and not f.is_symlink():
                shutil.copytree(f, ctx.workdir / f.name, copy_function=_copy_regular)
            else:
                _copy_regular(f, ctx.workdir / f.name)
        review = read_json(ctx.workdir / spec["review"]) if spec.get("review") else None
        return NodeResult(outcome=spec.get("outcome", "pass"), outputs=spec.get("outputs", {}), data=spec.get("data", {}),
                          review=review, executor=f"{self.name}:{rel(src, self.fixtures)}")


def run_command(ctx: NodeContext, command: str, result: NodeResult, timeout_s: int) -> NodeResult:
    """Run a project-bound production command for this node; it reports outputs through <workdir>/outputs.json."""
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    code = dict(ctx.producer_code)
    payload = {
        "node": ctx.node.id, "attempt": ctx.attempt, "workdir": str(ctx.workdir), "project_dir": str(ctx.project_dir),
        "items": ctx.items, "agent_outputs": {k: str(ctx.output_path(k, v)) if isinstance(v, str) else v for k, v in result.outputs.items()},
        "inputs": {ref.split(":", 1)[1]: [str(p) for p in ctx.artifact_paths(ref.split(":", 1)[1])]
                   for ref in ctx.node.inputs if ref.startswith("artifact:")},
        "inputs_items": {ref.split(":", 1)[1]: {(a.item or a.id): str(ctx.project_dir / a.path) for a in ctx.current(ref.split(":", 1)[1])}
                         for ref in ctx.node.inputs if ref.startswith("artifact:")},
        "controls": {doc: str(ctx.control_path(doc)) for doc in ctx.node.controls.read},
        "defects": [d.model_dump() for d in ctx.defects],
        "producer": {"node": ctx.node.id, "attempt": ctx.attempt, "command": command, "code": code,
                     "code_sha256": sha256_json(sorted(code.items()))},
    }
    cpath = ctx.workdir / "context.json"
    write_json_atomic(cpath, payload)
    cmd = command.replace("{python}", sys.executable)
    env = dict(os.environ, VPG_CONTEXT=str(cpath), PYTHONUNBUFFERED="1")
    proc = subprocess.run(cmd, shell=True, cwd=ctx.project_dir, env=env, capture_output=True, text=True, timeout=timeout_s)
    (ctx.workdir / "command.log").write_text(f"$ {cmd}\n--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}\n")
    if proc.returncode != 0:
        raise RuntimeError(f"command for {ctx.node.id} exited {proc.returncode}: {proc.stderr.strip()[-400:]}")
    out = read_json(ctx.workdir / "outputs.json")
    merged = dict(result.outputs)
    merged.update(out.get("outputs", {}))
    data = dict(result.data)
    data.update(out.get("data", {}))
    findings = list(result.findings) + [Finding(**f) for f in out.get("findings", [])]
    return NodeResult(outcome=out.get("outcome", result.outcome), outputs=merged, data=data, findings=findings,
                      review=result.review, executor=f"{result.executor}+command")


def dump_context(ctx: NodeContext) -> str:
    return json.dumps({"node": ctx.node.id, "attempt": ctx.attempt, "token": ctx.dispatch_token}, indent=1)
