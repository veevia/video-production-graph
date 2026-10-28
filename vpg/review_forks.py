from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .util import read_json, sha256_file


def offered_review_forks(root: Path, forks_ref: dict[str, str], artifacts: Iterable[Any], answered: set[str],
                         current_nodes: set[str], node_attempts: dict[str, int],
                         unreadable: list[str] | None = None) -> list[dict]:
    """The forks the end review must ask: in the current graph, unanswered, at their node's highest fork version, and
    (for node-produced forks) from the node's latest attempt. `current_nodes` is the graph's node ids for every caller.
    An unreadable fork file raises, unless `unreadable` collects its id (the packet skips it with a warning)."""
    artifact_by_path = {str(getattr(a, "path", "")): a for a in artifacts if getattr(a, "key", "") == "fork"}
    loaded: list[tuple[str, str, Path, dict, Any | None]] = []
    max_version_by_node: dict[str, int] = {}
    for fid, rel_path in sorted((forks_ref or {}).items()):
        path = root / rel_path
        try:
            fork = read_json(path)
        except (OSError, ValueError):
            if unreadable is None:
                raise
            unreadable.append(str(fid))
            continue
        node = str(fork.get("node") or "")
        fork_id = str(fork.get("fork_id") or fid)
        artifact = artifact_by_path.get(str(rel_path))
        if node not in current_nodes and artifact is not None:
            continue
        version = int(fork.get("version") or 0)
        max_version_by_node[node] = max(version, max_version_by_node.get(node, version))
        loaded.append((fork_id, node, path, fork, artifact))
    out = []
    for fork_id, node, path, fork, artifact in loaded:
        if fork_id in answered:
            continue
        if int(fork.get("version") or 0) != max_version_by_node.get(node):
            continue
        attempt = int(getattr(artifact, "attempt", 0) or 0) if artifact is not None else 0
        if attempt > 0 and attempt != int(node_attempts.get(node, 0) or 0):
            continue
        fork["sha256"] = sha256_file(path)
        out.append(fork)
    return sorted(out, key=lambda f: str(f.get("fork_id") or ""))
