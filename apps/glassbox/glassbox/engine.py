"""The engine graph: node contracts, human gates and edges from <engine>/graph/workflow.yaml (or the workflow a project
selects with `workflow:` in vpg.project.yaml)."""
from __future__ import annotations

import os
import re
import threading

from . import fsio

PHASE_ORDER = ("Understand", "Design", "Build", "Learn")
CONTRACT_KEYS = (
    "purpose", "inputs", "outputs", "completion", "rubric", "failure_conditions",
    "retry", "escalation", "controls", "human_review",
)
EXTRA_KEYS = ("gate", "instructions", "context", "agent", "skills", "tools", "prompt", "provenance", "state_read", "state_write")
RELEASE_NODE_NUM = 23
_NODE_REF = re.compile(r"(?<![A-Za-z0-9])[nN]0?(\d{1,2})(?![0-9])")
_ID_NUM = re.compile(r"^n0?(\d{1,2})(?:_|$)")


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def short_name(node_id, name):
    parts = node_id.split("_")[1:]
    if not parts:
        return name or node_id
    words = re.sub(r"\bqa\b", "QA", " ".join(parts))
    return words[:1].upper() + words[1:]


class Graph:
    def __init__(self, doc, path):
        self.path = path
        doc = doc if isinstance(doc, dict) else {}
        nodes = doc.get("nodes")
        self.nodes = [n for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)] if isinstance(nodes, list) else []
        self.by_id = {node["id"]: node for node in self.nodes}
        self.num_by_id = {}
        self.id_by_num = {}
        for node in self.nodes:
            num = _as_int(node.get("num"))
            if num is None:
                match = _ID_NUM.match(node["id"])
                num = int(match.group(1)) if match else None
            self.num_by_id[node["id"]] = num
            if num is not None:
                self.id_by_num.setdefault(num, node["id"])
        edges = doc.get("edges")
        self.edges = [e for e in edges if isinstance(e, dict)] if isinstance(edges, list) else []
        phases = []
        for node in self.nodes:
            phase = node.get("phase")
            if isinstance(phase, str) and phase not in phases:
                phases.append(phase)
        known = [p for p in PHASE_ORDER if p in phases]
        self.phases = known + [p for p in phases if p not in known]
        self.workflow = {key: doc.get(key) for key in ("id", "name", "version") if isinstance(doc.get(key), (str, int, float))}

    @property
    def available(self):
        return bool(self.nodes)

    def num(self, node_id):
        return self.num_by_id.get(node_id)

    def resolve_num(self, num):
        """Node id for a number; a synthetic 'nNN' id when the graph lacks it."""
        if num is None:
            return None
        return self.id_by_num.get(num, f"n{num:02d}")

    def node_ref(self, text):
        """First node reference in free text such as 'n13 (pipeline/scenes.py)' or 'N04 (Wei)'."""
        if not isinstance(text, str):
            return None
        for match in _NODE_REF.finditer(text):
            num = int(match.group(1))
            if 1 <= num <= 25 or num in self.id_by_num:
                return self.resolve_num(num)
        return None

    def human_gate(self, node_id):
        node = self.by_id.get(node_id) or {}
        review = node.get("human_review")
        if not isinstance(review, dict) or review.get("required") is not True:
            return None
        outcomes = review.get("outcomes")
        if not isinstance(outcomes, list):
            outcomes = node.get("outcomes") if isinstance(node.get("outcomes"), list) else []
        return {
            "required": True,
            "decision": review.get("decision") if isinstance(review.get("decision"), str) else None,
            "approver": review.get("approver") if isinstance(review.get("approver"), str) else None,
            "outcomes": [str(item) for item in outcomes],
        }

    def human_gate_ids(self):
        return [node["id"] for node in self.nodes if self.human_gate(node["id"])]

    def release_node(self):
        node_id = self.id_by_num.get(RELEASE_NODE_NUM)
        return node_id if node_id and self.human_gate(node_id) else None

    def summary(self, node_id):
        node = self.by_id.get(node_id) or {"id": node_id}
        name = node.get("name") if isinstance(node.get("name"), str) else node_id
        return {
            "id": node_id,
            "num": self.num(node_id),
            "name": name,
            "short": short_name(node_id, name),
            "phase": node.get("phase") if isinstance(node.get("phase"), str) else None,
            "subgraph": node.get("subgraph") if isinstance(node.get("subgraph"), str) else None,
            "kind": node.get("kind") if isinstance(node.get("kind"), str) else None,
            "human_gate": self.human_gate(node_id),
            "contract_gate": node.get("gate") if isinstance(node.get("gate"), str) else None,
        }

    def contract(self, node_id):
        node = self.by_id.get(node_id) or {}
        contract = {key: node.get(key) for key in CONTRACT_KEYS}
        for key in EXTRA_KEYS:
            if key in node:
                contract[key] = node.get(key)
        contract["edges"] = {
            "in": [_edge(e) for e in self.edges if e.get("to") == node_id],
            "out": [_edge(e) for e in self.edges if e.get("from") == node_id],
        }
        return contract


def _edge(edge):
    return {key: edge.get(key) for key in ("id", "from", "to", "kind", "when") if key in edge}


_WORKFLOW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_YAML_EXT = (".yaml", ".yml")


class Workflow:
    """Which graph a project uses: its `workflow:` value, the file actually loaded and a note when it fell back."""

    def __init__(self, graph, requested, name, path, found, note):
        self.graph = graph
        self.requested = requested
        self.name = name
        self.path = path
        self.found = found
        self.note = note

    def info(self, engine_dir=None):
        path = self.path
        if engine_dir and path and fsio.is_within(path, engine_dir):
            path = os.path.relpath(path, engine_dir)
        return {
            "requested": self.requested,
            "name": self.name,
            "found": self.found,
            "note": self.note,
            "file": path,
            "id": self.graph.workflow.get("id"),
            "title": self.graph.workflow.get("name"),
            "version": self.graph.workflow.get("version"),
            "nodes": len(self.graph.nodes),
            "human_gates": len(self.graph.human_gate_ids()),
        }


class Engine:
    def __init__(self, engine_dir):
        self.dir = os.path.abspath(os.path.expanduser(engine_dir))
        self.path = os.path.join(self.dir, "graph", "workflow.yaml")
        self._graphs = {}
        self._lock = threading.Lock()

    def _load(self, path):
        doc = fsio.read_yaml(path)
        with self._lock:
            cached = self._graphs.get(path)
            if cached is not None and cached[0] is doc:
                return cached[1]
        graph = Graph(doc, path)
        with self._lock:
            self._graphs[path] = (doc, graph)
        return graph

    def graph(self, workflow=None, project_path=None):
        """The default graph, or the graph a project's `workflow:` value selects (falling back to the default)."""
        if workflow is None:
            return self._load(self.path)
        return self.select(workflow, project_path).graph

    def workflows(self):
        """Workflow names shipped with the engine: 'default' plus every graph/workflow-<name>.yaml."""
        names = ["default"]
        try:
            entries = sorted(os.listdir(os.path.join(self.dir, "graph")))
        except OSError:
            return names
        for entry in entries:
            if entry.startswith("workflow-") and entry.endswith(".yaml") and _WORKFLOW_NAME.match(entry[9:-5]):
                names.append(entry[9:-5])
        return names

    def select(self, workflow, project_path=None):
        """Resolve a `workflow:` value: 'default' or nothing, a name (graph/workflow-<name>.yaml in the engine), or a
        .yaml path inside the project or the engine. Anything else falls back to the default graph with a note."""
        default = self._load(self.path)
        if workflow is None or (isinstance(workflow, str) and workflow.strip() in ("", "default")):
            return Workflow(default, workflow.strip() if isinstance(workflow, str) else None, "default", self.path, True, None)
        if not isinstance(workflow, str):
            return Workflow(default, str(workflow)[:80], "default", self.path, False,
                            "workflow value in vpg.project.yaml is not a name or a path; showing default")
        value = workflow.strip()
        shown = value if len(value) <= 80 else value[:79] + "\u2026"
        if _WORKFLOW_NAME.match(value):
            candidate = os.path.join(self.dir, "graph", f"workflow-{value}.yaml")
            label = value
        else:
            candidate = self._workflow_path(value, project_path)
            label = value
        if candidate and fsio.is_file(candidate):
            graph = self._load(candidate)
            if graph.available:
                return Workflow(graph, value, label, candidate, True, None)
            return Workflow(default, value, "default", self.path, False,
                            f"workflow '{shown}' could not be read; showing default")
        return Workflow(default, value, "default", self.path, False, f"workflow '{shown}' not found in the engine; showing default")

    def _workflow_path(self, value, project_path):
        """A workflow given as a path: a .yaml file inside the project or the engine folder, else None."""
        if "\x00" in value or not value.lower().endswith(_YAML_EXT):
            return None
        bases = [project_path, self.dir] if project_path else [self.dir]
        for base in bases:
            candidate = value if os.path.isabs(value) else os.path.join(base, value)
            if fsio.is_within(candidate, base) and fsio.is_file(candidate):
                return os.path.realpath(candidate)
        return None
