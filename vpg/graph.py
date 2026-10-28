"""Load, index and validate the workflow graph (graph/workflow.yaml).

Semantics (see docs/graph-spec.md):
  * flow edges form a DAG. A node is ready when every incoming flow edge is satisfied: its source is
    STEP_PASS and the source's outcome matches the edge's `when`. That is join = all.
  * loop edges are bounded back-edges (`max_traversals`). They invalidate the target and its flow
    descendants, which then re-run or are memo-skipped by fingerprint. They never satisfy a join.
  * escalate edges stop the run (BLOCKED) and report to a human.
  * `when` is "*" (any outcome), a label or "a|b" list matched against the source's outcome, or
    "target:<node>", which matches when the source's result data lists that node in `targets`
    (targeted regeneration).
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import yaml

from .controls import KNOWN_DOCS, doc_label, rel_path
from .models import EdgeDef, EdgeKind, NodeDef, NodeKind, ProjectState, Workflow
from .util import PKG_ROOT, model_has_path, read_text, sha256_file

DEFAULT_WORKFLOW = PKG_ROOT / "graph" / "workflow.yaml"
SPECIAL = {"START", "END", "@escalate"}
# Workflows shipped with the package, by the name a project's `workflow:` may give. Anything else is a path.
QUALITY_WORKFLOW = PKG_ROOT / "graph" / "workflow-quality.yaml"
WORKFLOWS: dict[str, Path] = {"default": DEFAULT_WORKFLOW, "quality": QUALITY_WORKFLOW}


def resolve_workflow(ref: str | None, project_root: Path | None = None) -> Path:
    """`default` (or nothing) or another shipped name, else a path: as given (relative to the working directory, as the
    engine has always read it), or relative to the project when only that exists."""
    if not ref or ref == "default":
        return DEFAULT_WORKFLOW
    if ref in WORKFLOWS:
        return WORKFLOWS[ref]
    p = Path(ref)
    if not p.is_absolute() and not p.exists() and project_root is not None and (Path(project_root) / p).exists():
        return Path(project_root) / p
    return p

# The brief's 25 stages, in order: the shipped graph must implement exactly these.
REQUIRED_STAGES = [
    "Context discovery and intake", "Source validation and context-gap detection", "Objective and audience definition",
    "Product and experience understanding", "Narrative and concept ideation", "Creative-direction selection",
    "Script development", "Storyboard and shot planning", "Visual-system definition", "Asset planning",
    "Asset generation", "Voice and audio production", "Motion and scene production", "Video assembly",
    "Technical QA", "Creative critique", "Design-system compliance", "Accessibility and legibility validation",
    "Product-accuracy validation", "Narrative and pacing evaluation", "Feedback interpretation",
    "Targeted regeneration", "Final verification", "Packaging and delivery", "Post-production learning and memory updates",
]
# Stages a workflow may add after the 25 (numbered 26 on, placed anywhere in the flow). Only the quality workflow does.
OPTIONAL_STAGES = ["Look development", "Animatic"]


def load_workflow(path: str | Path | None = None) -> Workflow:
    path = Path(path or DEFAULT_WORKFLOW)
    data = yaml.safe_load(read_text(path))
    return Workflow.model_validate(data)


def workflow_sha256(path: str | Path | None = None) -> str:
    return sha256_file(Path(path or DEFAULT_WORKFLOW))


def outcome_matches(when: str, outcome: str | None, data: dict[str, Any] | None = None) -> bool:
    if outcome is None:
        return False
    if when == "*":
        return True
    if when.startswith("target:"):
        return when.split(":", 1)[1] in (data or {}).get("targets", [])
    return outcome in {w.strip() for w in when.split("|")}


class GraphIndex:
    def __init__(self, wf: Workflow):
        self.wf = wf
        self.ids = [n.id for n in wf.nodes]
        self._in: dict[str, list[EdgeDef]] = defaultdict(list)
        self._out: dict[str, list[EdgeDef]] = defaultdict(list)
        for e in wf.edges:
            self._out[e.source].append(e)
            self._in[e.target].append(e)
        self.topo = self._toposort()
        self._order = {n: i for i, n in enumerate(self.topo)}

    def incoming(self, node_id: str, kind: EdgeKind | None = None) -> list[EdgeDef]:
        return [e for e in self._in[node_id] if kind is None or e.kind == kind]

    def outgoing(self, node_id: str, kind: EdgeKind | None = None) -> list[EdgeDef]:
        return [e for e in self._out[node_id] if kind is None or e.kind == kind]

    def upstream(self, node_id: str) -> list[str]:
        return [e.source for e in self.incoming(node_id, EdgeKind.FLOW)]

    def downstream(self, node_id: str) -> list[str]:
        return [e.target for e in self.outgoing(node_id)]

    def _toposort(self) -> list[str]:
        indeg = {n: 0 for n in self.ids}
        for e in self.wf.edges:
            if e.kind == EdgeKind.FLOW and e.target in indeg and e.source in indeg:
                indeg[e.target] += 1
        order = [n for n in self.ids if indeg[n] == 0]
        queue = deque(order)
        out: list[str] = []
        while queue:
            n = queue.popleft()
            out.append(n)
            for e in self._out[n]:
                if e.kind == EdgeKind.FLOW and e.target in indeg:
                    indeg[e.target] -= 1
                    if indeg[e.target] == 0:
                        queue.append(e.target)
        if len(out) != len(self.ids):
            cyclic = sorted(set(self.ids) - set(out))
            raise ValueError(f"flow edges contain a cycle through: {cyclic}")
        return out

    def descendants(self, node_id: str) -> set[str]:
        seen, stack = set(), [node_id]
        while stack:
            cur = stack.pop()
            for e in self._out[cur]:
                if e.kind == EdgeKind.FLOW and e.target not in SPECIAL and e.target not in seen:
                    seen.add(e.target)
                    stack.append(e.target)
        return seen

    def ancestors(self, node_id: str) -> set[str]:
        seen, stack = set(), [node_id]
        while stack:
            cur = stack.pop()
            for e in self._in[cur]:
                if e.kind == EdgeKind.FLOW and e.source not in SPECIAL and e.source not in seen:
                    seen.add(e.source)
                    stack.append(e.source)
        return seen

    def order(self, node_id: str) -> int:
        return self._order[node_id]


REQUIRED_NODE_TEXT = ["name", "phase", "subgraph", "purpose", "agent", "instructions"]
REQUIRED_NODE_LISTS = ["inputs", "context", "state_read", "state_write", "outputs", "completion", "rubric",
                       "failure_conditions", "provenance"]


def resolve_tool(ref: str):
    """`module:function`. A bare module name resolves under vpg.tools; a dotted one is imported as-is (tests, extensions)."""
    mod, _, fn = ref.partition(":")
    module = importlib.import_module(mod if "." in mod else f"vpg.tools.{mod}")
    return getattr(module, fn)


_READ_CALLS = {"fm", "control_path", "front_matter"}   # ctx.fm(doc), ctx.control_path(doc), ctx.controls.front_matter(doc)


@functools.lru_cache(maxsize=None)
def _function_control_reads(module_name: str, function: str) -> frozenset[str]:
    try:
        tree = ast.parse(inspect.getsource(sys.modules[module_name]))
    except (KeyError, OSError, TypeError, SyntaxError):
        return frozenset()
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    docs: set[str] = set()
    seen: set[str] = set()
    todo = [function]
    while todo:
        name = todo.pop()
        if name in seen or name not in defs:
            continue
        seen.add(name)
        for call in (n for n in ast.walk(defs[name]) if isinstance(n, ast.Call)):
            f = call.func
            if isinstance(f, ast.Attribute) and f.attr in _READ_CALLS:
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    docs.add(call.args[0].value)
            elif isinstance(f, ast.Attribute) and f.attr == "thresholds":   # _common.thresholds(ctx)
                docs.add("evaluation")
            elif isinstance(f, ast.Name):
                if f.id == "thresholds":   # the evaluation.md front matter overrides the default thresholds
                    docs.add("evaluation")
                elif f.id in defs:
                    todo.append(f.id)
    return frozenset(docs)


def tool_control_reads(fn: Any) -> set[str]:
    """The control documents a tool reads, found in its source: a literal `ctx.fm("<doc>")`, `ctx.control_path("<doc>")`
    or `front_matter("<doc>")`, and `thresholds(ctx)` for evaluation, in the tool function and every function of its
    module it calls. A document named by a variable, or read through another module's helper, is not seen. A node whose
    tool reads a document must declare it, or a change to it would neither change the node's fingerprint nor re-check it."""
    module, name = getattr(fn, "__module__", None), getattr(fn, "__name__", None)
    return set(_function_control_reads(module, name)) if module and name else set()


# Engine behaviour attached to a node through `gate:` → how many nodes may carry it (min, max).
GATE_HOOKS = {"review_gate": (1, 1), "regeneration_plan": (1, 1), "freeze_inputs": (0, 1), "package": (0, 1)}


SEVERITY_ORDER = ["P1", "P2", "P3"]


def validate(wf: Workflow, pkg_root: Path = PKG_ROOT, check_stages: bool = True) -> list[str]:
    """Return a list of human-readable errors; an empty list means the graph is valid.

    Structural safety (structural_errors) plus content checks: node fields, typed state paths, prompts, tools,
    schemas, rubric placement, the 25 brief stages and subgraph membership."""
    errors = structural_errors(wf)
    if errors:
        return errors
    for node in wf.nodes:
        errors.extend(_node_errors(node, pkg_root))
    errors.extend(_rubric_placement_errors(wf, pkg_root))
    if check_stages:
        names = [n.name for n in sorted(wf.nodes, key=lambda x: x.num)]
        core, extra = names[:len(REQUIRED_STAGES)], names[len(REQUIRED_STAGES):]
        if core != REQUIRED_STAGES:
            missing = [s for s in REQUIRED_STAGES if s not in core]
            errors.append(f"graph must implement the 25 brief stages in order; missing or renamed: {missing or names}")
        unknown = [s for s in extra if s not in OPTIONAL_STAGES]
        if unknown or len(set(extra)) != len(extra):
            errors.append(f"stages after the 25 must be distinct optional stages {OPTIONAL_STAGES}; got {extra}")
    sub_nodes = [n for s in wf.subgraphs for n in s.nodes]
    ids = [n.id for n in wf.nodes]
    for n in ids:
        if n not in sub_nodes:
            errors.append(f"node {n} belongs to no subgraph")
    for node in wf.nodes:
        if node.subgraph not in {s.id for s in wf.subgraphs}:
            errors.append(f"node {node.id}: unknown subgraph {node.subgraph}")
    return errors


def structural_errors(wf: Workflow) -> list[str]:
    """The checks execution depends on. The engine refuses to run a graph that fails any of them.

    Unique ids, known endpoints, positive loop bounds, sane policies, a flow DAG, reachability, no dead-end outcome,
    and no branch that can leave a join (END included) waiting forever."""
    errors: list[str] = []
    ids = [n.id for n in wf.nodes]
    if len(ids) != len(set(ids)):
        errors.append("duplicate node ids")
    nums = sorted(n.num for n in wf.nodes)
    if nums != list(range(1, len(nums) + 1)):
        errors.append(f"node numbers must be 1..{len(nums)} without gaps: {nums}")
    known = set(ids) | SPECIAL
    edge_ids = [e.id for e in wf.edges]
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("duplicate edge ids")
    for n in wf.nodes:
        if n.gate and n.gate not in GATE_HOOKS:
            errors.append(f"node {n.id}: unknown gate hook '{n.gate}' (known: {', '.join(GATE_HOOKS)})")
    for hook, (lo, hi) in GATE_HOOKS.items():
        count = sum(n.gate == hook for n in wf.nodes)
        if not lo <= count <= hi:
            errors.append(f"gate hook '{hook}' must be on {'exactly one node' if lo == hi else 'at most one node'} (found {count})")

    for e in wf.edges:
        if e.source not in known or e.target not in known:
            errors.append(f"edge {e.id}: unknown endpoint {e.source}->{e.target}")
        if e.kind == EdgeKind.LOOP and not (isinstance(e.max_traversals, int) and e.max_traversals >= 1):
            errors.append(f"edge {e.id}: loop edges need max_traversals ≥ 1 (no unbounded loops)")
        if e.kind == EdgeKind.ESCALATE and e.target != "@escalate":
            errors.append(f"edge {e.id}: escalate edges must target @escalate")
        if e.kind != EdgeKind.ESCALATE and e.target == "@escalate":
            errors.append(f"edge {e.id}: only escalate edges may target @escalate")
        for field in ("when", "threshold", "branching", "on_fail"):
            if not str(getattr(e, field)).strip():
                errors.append(f"edge {e.id}: empty {field}")
        if not e.transfers:
            errors.append(f"edge {e.id}: empty transfers")
        deterministic = e.when in ("*", "pass") and (e.source in SPECIAL or len(_node(wf, e.source).outcomes) == 1)
        if e.transition == "deterministic" and not deterministic:
            errors.append(f"edge {e.id}: marked deterministic but depends on outcome '{e.when}'")
        if e.transition == "conditional" and deterministic:
            errors.append(f"edge {e.id}: marked conditional but always fires")
    pairs = Counter((e.source, e.target) for e in wf.edges if e.kind == EdgeKind.FLOW)
    errors += [f"flow edges {a}->{b} are duplicated; with join = all the target could never become ready" for (a, b), n in pairs.items() if n > 1]
    blocking = list(wf.policies.blocking_severities)
    if sorted(blocking) != SEVERITY_ORDER[:len(blocking)] or not blocking:
        errors.append(f"policies.blocking_severities must start at P1 and include every more severe level (got {blocking})")
    if errors:
        return errors

    try:
        index = GraphIndex(wf)
    except ValueError as exc:
        return [str(exc)]

    for e in wf.edges:
        if e.kind == EdgeKind.LOOP:
            if e.target not in index.ancestors(e.source) and e.target != e.source:
                errors.append(f"edge {e.id}: loop target {e.target} is not upstream of {e.source}")
        if e.source in ids and not e.when.startswith("target:") and e.when != "*":
            for label in e.when.split("|"):
                if label not in _node(wf, e.source).outcomes:
                    errors.append(f"edge {e.id}: '{label}' is not an outcome of {e.source}")

    starts = [e for e in wf.edges if e.source == "START"]
    if not starts:
        errors.append("no edge from START")
    reach = _reach(wf, "START", {EdgeKind.FLOW})
    for n in ids:
        if n not in reach:
            errors.append(f"node {n} is not reachable from START via flow edges")
    for n in ids:
        if "END" not in _reach(wf, n, {EdgeKind.FLOW, EdgeKind.LOOP}):
            errors.append(f"END is not reachable from {n}")

    for node in wf.nodes:
        if node.human_review.required:
            stray = [o for o in node.human_review.outcomes if o not in node.outcomes]
            if stray:
                errors.append(f"node {node.id}: human decision outcome(s) {stray} are not outcomes of the node, so no edge would handle them")
        out = index.outgoing(node.id)
        for outcome in node.outcomes:
            # A `target:` edge fires on the regeneration plan's targets, not on an outcome; only the regeneration
            # node may rely on it (the engine escalates that node when its plan has no target).
            if not any(outcome_matches(e.when, outcome) or (e.when.startswith("target:") and node.gate == "regeneration_plan")
                       for e in out):
                errors.append(f"node {node.id}: outcome '{outcome}' has no outgoing edge (dead end)")
        # Join deadlock safety: a non-matching outcome of a join predecessor must loop or escalate.
        inc = index.incoming(node.id, EdgeKind.FLOW)
        if len(inc) > 1:
            for e in inc:
                if e.source in SPECIAL:
                    continue
                src = _node(wf, e.source)
                for outcome in src.outcomes:
                    if outcome_matches(e.when, outcome):
                        continue
                    alt = [x for x in index.outgoing(src.id) if x.kind in (EdgeKind.LOOP, EdgeKind.ESCALATE)
                           and outcome_matches(x.when, outcome)]
                    if not alt:
                        errors.append(f"join {node.id}: outcome '{outcome}' of {src.id} would leave the join waiting forever")
    errors.extend(_dead_branch_errors(wf, index))
    errors.extend(_recheck_cycle_errors(wf, index))
    return errors


def control_reads(node: NodeDef) -> set[str]:
    """The control documents a node reads: declared reads plus `control:` inputs."""
    return set(node.controls.read) | {ref.partition(":")[2] for ref in node.inputs if ref.startswith("control:")}


def recheck_targets(wf: Workflow, index: "GraphIndex", writer: NodeDef) -> set[str]:
    """The nodes a writer's control-document change can send back to re-check: every reader that is not upstream of
    the writer (for an upstream reader the edit is input to later work), plus each reader's flow descendants, which
    re-check with it. The engine applies exactly this rule at run time."""
    docs, upstream, out = set(writer.controls.write), index.ancestors(writer.id), set()
    for n in wf.nodes:
        if n.id != writer.id and n.id not in upstream and control_reads(n) & docs:
            out |= {n.id} | index.descendants(n.id)
    return out


def _recheck_cycle_errors(wf: Workflow, index: "GraphIndex") -> list[str]:
    """Reject control documents that can re-check their way back to their writer.

    A re-checked node re-runs; if it (or a descendant) also writes a document that the first writer reads, the two
    keep re-running each other and no loop edge bounds it."""
    writers = {n.id: n for n in wf.nodes if n.controls.write}
    succ = {w: sorted(recheck_targets(wf, index, n) & set(writers)) for w, n in writers.items()}
    colour: dict[str, int] = {}
    path: list[str] = []
    cycles: list[list[str]] = []

    def visit(u: str) -> None:
        colour[u] = 1
        path.append(u)
        for v in succ[u]:
            if colour.get(v) == 1:
                cycles.append(path[path.index(v):] + [v])
            elif not colour.get(v):
                visit(v)
        path.pop()
        colour[u] = 2

    for w in sorted(writers):
        if not colour.get(w):
            visit(w)
    def hop(x: str) -> str:
        return f"{x} (writes {', '.join(doc_label(d) for d in writers[x].controls.write)})"
    return [f"control-document re-check cycle {' → '.join(hop(x) for x in c[:-1])} → {c[-1]}: each change re-checks the next "
            "node and comes back round with no loop bound; order these nodes in the flow so one runs after the other, or stop "
            "one reading the other's document" for c in cycles]


def _dead_branch_errors(wf: Workflow, index: "GraphIndex") -> list[str]:
    """Find branches that can strand a join, however deep (END included).

    With join = all, a flow successor whose edge an outcome does not satisfy can never run, and neither can anything
    downstream of it. For every forward outcome (one that fires no loop or escalate edge), walk the activated part
    of the graph. If it reaches one of those dead nodes before the work loops back, the run would stop with nothing
    ready. A router, a node with no flow successor, always loops back or escalates.
    """
    flow_out = {n: [e for e in index.outgoing(n) if e.kind == EdgeKind.FLOW] for n in index.ids}
    routers = {n for n, es in flow_out.items() if not es}
    errors = []
    for node in wf.nodes:
        exits = [e for e in index.outgoing(node.id) if e.kind in (EdgeKind.LOOP, EdgeKind.ESCALATE)]
        for outcome in node.outcomes:
            if any(outcome_matches(e.when, outcome) for e in exits):
                continue
            active = {e.target for e in flow_out[node.id] if outcome_matches(e.when, outcome)}
            dead, stack = set(), [e.target for e in flow_out[node.id] if not outcome_matches(e.when, outcome) and e.target not in active]
            while stack:
                t = stack.pop()
                if t not in dead:
                    dead.add(t)
                    stack.extend(e.target for e in flow_out.get(t, []))
            if not dead:
                continue
            seen, stack = set(), list(active)
            while stack:
                t = stack.pop()
                if t in seen:
                    continue
                seen.add(t)
                if t in dead:
                    waits = sorted(e.source for e in index.incoming(t, EdgeKind.FLOW) if e.source in dead)
                    errors.append(f"outcome '{outcome}' of {node.id} leaves {t} waiting forever: {t} also needs {waits}, "
                                  "which cannot run on that branch")
                    break
                if t == "END" or t in routers:
                    continue
                stack.extend(e.target for e in flow_out.get(t, []))
    return errors


def _rubric_placement_errors(wf: Workflow, pkg_root: Path) -> list[str]:
    """A MUST dimension must be scored by someone independent of the node's author: review-type dimensions only on
    review nodes, human-type dimensions only where a named human decides (a human gate) or a review records them."""
    from .evaluation import load_rubrics
    rubrics = load_rubrics(pkg_root)
    errors = []
    for node in wf.nodes:
        for rid in node.rubric:
            for d in rubrics.get(rid, {}).get("dimensions", []):
                if d.get("level", "MUST") != "MUST":
                    continue
                kind = d["check"]["type"]
                if kind == "review" and node.kind != NodeKind.REVIEW:
                    errors.append(f"node {node.id}: MUST review dimension {d['id']} would never be scored on a {node.kind.value} node; "
                                  "score it at a review node or make it a human decision at a human gate")
                if kind == "human" and node.kind != NodeKind.REVIEW and not node.human_review.required:
                    errors.append(f"node {node.id}: MUST human dimension {d['id']} needs a human gate or a review node to record it")
    return errors


def _node(wf: Workflow, node_id: str) -> NodeDef:
    return wf.node(node_id)


def _reach(wf: Workflow, start: str, kinds: set[EdgeKind]) -> set[str]:
    out = defaultdict(list)
    for e in wf.edges:
        if e.kind in kinds:
            out[e.source].append(e.target)
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        for t in out[cur]:
            if t not in seen:
                seen.add(t)
                stack.append(t)
    return seen


def _node_errors(node: NodeDef, pkg_root: Path) -> list[str]:
    return _node_documentation_errors(node) + node_resolution_errors(node, pkg_root)


def _node_documentation_errors(node: NodeDef) -> list[str]:
    """The brief's documentation bar: every node field filled in. `vpg validate` enforces it; a run does not need it."""
    errs = []
    for f in REQUIRED_NODE_TEXT:
        if not str(getattr(node, f)).strip():
            errs.append(f"node {node.id}: empty {f}")
    for f in REQUIRED_NODE_LISTS:
        if not getattr(node, f):
            errs.append(f"node {node.id}: empty {f}")
    if not node.retry.behavior.strip() or not node.escalation.after.strip() or not node.human_review.decision.strip():
        errs.append(f"node {node.id}: retry, escalation and human-review text must be explicit")
    return errs


def node_resolution_errors(node: NodeDef, pkg_root: Path) -> list[str]:
    """Everything a node refers to must exist: state paths, prompts, tools, schemas, rubrics and control documents. The
    engine refuses to run a graph that fails any of these, because a missing rubric or tool silently drops its checks."""
    errs = []
    for path in node.state_read + node.state_write:
        if not model_has_path(ProjectState, path):
            errs.append(f"node {node.id}: state path '{path}' is not in the typed state model")
    for ref in node.inputs:
        kind = ref.split(":", 1)[0]
        if kind not in {"artifact", "control", "state", "rubric", "reviews", "feedback", "defects", "knowledge", "files"}:
            errs.append(f"node {node.id}: input '{ref}' has an unknown kind")
    if node.kind in (NodeKind.AGENT, NodeKind.REVIEW):
        if not node.prompt or not (pkg_root / node.prompt).exists():
            errs.append(f"node {node.id}: agent/review nodes need an existing prompt template ({node.prompt})")
    if node.kind in (NodeKind.TOOL, NodeKind.REVIEW) and not (node.run and node.run.tool):
        errs.append(f"node {node.id}: tool/review nodes need run.tool")
    if node.run and node.run.tool:
        try:
            fn = resolve_tool(node.run.tool)
        except (ImportError, AttributeError) as exc:
            errs.append(f"node {node.id}: run.tool {node.run.tool} does not resolve ({exc})")
        else:
            undeclared = sorted(tool_control_reads(fn) - control_reads(node))
            if undeclared:
                errs.append(f"node {node.id}: its tool {node.run.tool} reads {', '.join(rel_path(d) for d in undeclared)}, "
                            "which the node does not declare; list it in controls.read, so a change to it re-checks the node")
    for chk in node.completion:
        if chk.type == "tool":
            try:
                resolve_tool(chk.fn or "")
            except (ImportError, AttributeError, ValueError) as exc:
                errs.append(f"node {node.id}: completion check {chk.fn} does not resolve ({exc})")
        if chk.schema_name and not (pkg_root / "schemas" / "artifacts" / f"{chk.schema_name}.schema.json").exists():
            errs.append(f"node {node.id}: schema {chk.schema_name} missing")
    for rid in node.rubric:
        if not (pkg_root / "rubrics" / f"{rid}.yaml").exists():
            errs.append(f"node {node.id}: rubric {rid} missing")
    for doc in node.controls.read + node.controls.write + node.controls.lock_on_approval:
        if doc not in KNOWN_DOCS:
            errs.append(f"node {node.id}: unknown control document {doc}")
    if node.human_review.required and not node.human_review.outcomes:
        errs.append(f"node {node.id}: human review needs outcomes")
    return errs
