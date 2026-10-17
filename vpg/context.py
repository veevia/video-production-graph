"""Execution context passed to tools, checks, executors and project commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ArtifactRecord, Defect, Finding, NodeDef, ProjectState
from .util import read_json


@dataclass
class CheckResult:
    ok: bool
    messages: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeResult:
    outcome: str = "pass"
    outputs: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    review: dict[str, Any] | None = None
    pending: bool = False
    task_packet: str | None = None
    executor: str = ""


@dataclass
class NodeContext:
    node: NodeDef
    attempt: int
    workdir: Path
    project_dir: Path
    pkg_root: Path
    state: ProjectState
    fingerprint: str
    dispatch_token: str
    config: dict[str, Any]
    rubrics: dict[str, Any]
    controls: Any
    defects: list[Defect] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    pre_findings: list[Finding] = field(default_factory=list)
    pre_result: NodeResult | None = None
    items: list[str] | None = None
    registered: dict[str, list[ArtifactRecord]] = field(default_factory=dict)
    control_hashes_before: dict[str, str | None] = field(default_factory=dict)
    producer_code: dict[str, str] = field(default_factory=dict)

    # ---- read helpers (tools must treat state as read-only) ----
    def current(self, key: str) -> list[ArtifactRecord]:
        if key in self.registered:
            return self.registered[key]
        recs = [a for a in self.state.artifacts.values() if a.key == key and a.status == "current"]
        return sorted(recs, key=lambda a: a.id)

    def artifact_path(self, key: str) -> Path | None:
        recs = self.current(key)
        return (self.project_dir / recs[-1].path) if recs else None

    def artifact_paths(self, key: str) -> list[Path]:
        return [self.project_dir / a.path for a in self.current(key)]

    def load(self, key: str) -> Any:
        p = self.artifact_path(key)
        return read_json(p) if p and p.exists() else None

    def output_path(self, key: str, value: Any) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (self.workdir / p)

    def control_path(self, doc: str) -> Path:
        return self.project_dir / "controls" / f"{doc}.md"

    def fm(self, doc: str) -> dict[str, Any]:
        return self.controls.front_matter(doc)
