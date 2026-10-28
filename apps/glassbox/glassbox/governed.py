"""The engine's canonical model in .vpg/state.json: versions, artifacts, deliverables, evaluation, approvals.

Read-only. Every path here is project-relative; media access is limited to paths listed in the
state (artifacts and resolved deliverables).
"""
from __future__ import annotations

import os
import posixpath
import re
from datetime import datetime

STEM_NODES = ("n12_voice_audio", "n14_video_assembly")
MEDIA_KEYS = ("master", "captions", "silent", "voice_stem", "music_stem")
_ITEM = re.compile(r"\[(\d+)\]$")


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("v").isdigit():
        return int(value.strip().lstrip("v"))
    return None


def _clean_rel(path):
    """Normalise a project-relative path; None when it is absolute or escapes the project."""
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        return None
    while path.startswith("./"):
        path = path[2:]
    normal = posixpath.normpath(path)
    if normal.startswith("/") or normal in (".", "..") or normal.startswith("../"):
        return None
    return normal


def _epoch(text):
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def label_of(number):
    return f"v{number}"


class GovernedRun:
    def __init__(self, state, project_path):
        state = state if isinstance(state, dict) else {}
        self.project_path = project_path
        history = state.get("history") if isinstance(state.get("history"), dict) else {}
        self.runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        self.versions = {}
        for item in history.get("versions") or []:
            if not isinstance(item, dict):
                continue
            number = _int(item.get("version"))
            if number is None:
                continue
            artifacts = item.get("artifacts") if isinstance(item.get("artifacts"), dict) else {}
            self.versions[number] = {
                "n": number,
                "label": label_of(number),
                "created_at": item.get("created_at") if isinstance(item.get("created_at"), str) else None,
                "node_attempt": item.get("node_attempt") if isinstance(item.get("node_attempt"), str) else None,
                "artifacts": {str(k): v for k, v in artifacts.items() if isinstance(v, str)},
            }
        raw = state.get("artifacts")
        self.artifacts = {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
        self.by_sha = {}
        for artifact_id, artifact in self.artifacts.items():
            sha = artifact.get("sha256")
            if isinstance(sha, str) and (sha not in self.by_sha or artifact.get("status") == "current"):
                self.by_sha[sha] = artifact_id
        evaluation = state.get("evaluation") if isinstance(state.get("evaluation"), dict) else {}
        self.reviews_raw = [r for r in evaluation.get("reviews") or [] if isinstance(r, dict)]
        self.defects_raw = [d for d in evaluation.get("defects") or [] if isinstance(d, dict)]
        self.gates_raw = [g for g in evaluation.get("gate_results") or [] if isinstance(g, dict)]
        self.approvals = [a for a in history.get("approvals") or [] if isinstance(a, dict)]
        self.direct_runs = [d for d in history.get("direct_runs") or [] if isinstance(d, dict)]
        self.deliverables_raw = [d for d in state.get("deliverables") or [] if isinstance(d, dict)]
        current = _int(self.runtime.get("current_version"))
        self.current = current if current else (max(self.versions) if self.versions else None)
        self._allowed = None

    # ------------------------------------------------------------------ versions and artifacts
    def labels(self):
        return [label_of(n) for n in sorted(self.versions)]

    def record(self, label):
        number = _int(label) if isinstance(label, str) and label.startswith("v") else None
        return self.versions.get(number) if number is not None else None

    def is_current(self, label):
        record = self.record(label)
        return bool(record) and record["n"] == self.current

    def artifact_for(self, record, key):
        if not record:
            return None
        if record["node_attempt"]:
            artifact = self.artifacts.get(f"{key}@{record['node_attempt']}")
            if artifact:
                return artifact
        sha = record["artifacts"].get(key)
        artifact_id = self.by_sha.get(sha) if sha else None
        return self.artifacts.get(artifact_id) if artifact_id else None

    def artifact_by_prefix(self, prefix):
        if not isinstance(prefix, str) or len(prefix) < 8:
            return None
        for sha, artifact_id in self.by_sha.items():
            if sha.startswith(prefix):
                return self.artifacts[artifact_id]
        return None

    def version_nodes(self, record):
        nodes = set()
        for sha in record["artifacts"].values():
            artifact_id = self.by_sha.get(sha)
            node = self.artifacts[artifact_id].get("node") if artifact_id else None
            if isinstance(node, str):
                nodes.add(node)
        return nodes

    def keyframes(self, record):
        frames = []
        for artifact_id, artifact in self.artifacts.items():
            if artifact.get("key") == "keyframes" and _int(artifact.get("version")) == record["n"]:
                match = _ITEM.search(artifact_id)
                frames.append((int(match.group(1)) if match else 0, artifact))
        frames.sort(key=lambda pair: pair[0])
        return [artifact for _, artifact in frames]

    def path_of(self, artifact):
        return _clean_rel(artifact.get("path")) if artifact else None

    # ------------------------------------------------------------------ evaluation
    def reviews_for(self, record):
        return [r for r in self.reviews_raw if _int(r.get("version")) == record["n"]]

    def defects_for(self, record):
        number = record["n"]
        out = []
        for defect in self.defects_raw:
            first = _int(defect.get("first_seen_version"))
            last = _int(defect.get("last_seen_version"))
            if first is None:
                first = 0
            if last is None:
                last = self.current if self.current is not None else number
            if first <= number <= last:
                out.append(defect)
        return out

    def gates_for(self, record):
        return [g for g in self.gates_raw if _int(g.get("version")) == record["n"]]

    def approval_version(self, approval):
        """The version an approval applies to: via its output artifacts, else the latest version made before it."""
        outputs = approval.get("outputs") if isinstance(approval.get("outputs"), dict) else {}
        for prefix in outputs.values():
            artifact = self.artifact_by_prefix(prefix)
            number = _int(artifact.get("version")) if artifact else None
            if number:
                return number
        moment = _epoch(approval.get("at"))
        if moment is None:
            return None
        before = [n for n, r in self.versions.items() if (_epoch(r["created_at"]) or 0) <= moment]
        return max(before) if before else None

    def latest_approval(self, node_id, label=None):
        matches = [a for a in self.approvals if a.get("node") == node_id]
        if label is not None:
            record = self.record(label)
            matches = [a for a in matches if record and self.approval_version(a) == record["n"]]
        if not matches:
            return None
        latest = max(matches, key=lambda a: str(a.get("at") or ""))

        def text(key):
            value = latest.get(key)
            return value if isinstance(value, str) and value else None

        evidence = text("evidence")
        provisional = latest.get("provisional") is True or evidence == "assumed"
        return {
            "decision": latest.get("decision") if isinstance(latest.get("decision"), str) else None,
            "actor": latest.get("actor") if isinstance(latest.get("actor"), str) else None,
            "at": latest.get("at") if isinstance(latest.get("at"), str) else None,
            "reason": latest.get("reason") if isinstance(latest.get("reason"), str) else None,
            "human": latest.get("human") if isinstance(latest.get("human"), bool) else None,
            "grant": latest.get("grant") if isinstance(latest.get("grant"), str) and latest.get("grant") else None,
            "count": len(matches),
            "evidence": evidence,
            "provisional": provisional,
            "expires_at": text("expires_at"),
            "expiry_noted_at": text("expiry_noted_at"),
            "confirmed_at": text("confirmed_at"),
            "rejected_at": text("rejected_at"),
            "superseded_at": text("superseded_at"),
            # A provisional approval stands until a named human confirms or rejects it, or a later one supersedes it.
            "standing": provisional and not (text("confirmed_at") or text("rejected_at") or text("superseded_at")),
        }

    def direct_runs_for(self, node_id):
        """history.direct_runs recorded for a node (builds made outside the governed path), oldest first."""
        out = []
        for run in self.direct_runs:
            if run.get("node") != node_id:
                continue
            out.append({
                "id": run.get("id") if isinstance(run.get("id"), str) else None,
                "label": run.get("label") if isinstance(run.get("label"), str) and run.get("label") else None,
                "actor": run.get("actor") if isinstance(run.get("actor"), str) else None,
                "reason": run.get("reason") if isinstance(run.get("reason"), str) else None,
                "at": run.get("at") if isinstance(run.get("at"), str) else None,
                "exit_code": run.get("exit_code") if isinstance(run.get("exit_code"), int) and not isinstance(run.get("exit_code"), bool) else None,
                "outputs": sorted(str(k) for k in run.get("outputs")) if isinstance(run.get("outputs"), dict) else [],
                "source": "state",
            })
        return out

    # ------------------------------------------------------------------ deliverables and the media whitelist
    def deliverables(self):
        bases = []
        for artifact in self.artifacts.values():
            if artifact.get("key") in ("manifest", "package") and isinstance(artifact.get("path"), str):
                base = _clean_rel(posixpath.join(posixpath.dirname(artifact["path"]), "package"))
                if base and (base, artifact.get("status") == "current") not in bases:
                    bases.append((base, artifact.get("status") == "current"))
        ordered = [b for b, current in bases if current] + [b for b, current in bases if not current]
        out = []
        for item in self.deliverables_raw:
            path = _clean_rel(item.get("path"))
            resolved = None
            if path:
                for base in ordered + [""]:
                    candidate = posixpath.join(base, path) if base else path
                    if os.path.isfile(os.path.join(self.project_path, candidate)):
                        resolved = candidate
                        break
            out.append({"key": item.get("key"), "path": item.get("path"), "sha256": item.get("sha256"), "rel": resolved})
        return out

    def allowed_media(self):
        """Project-relative paths listed in the state, mapped to their producing node (None for deliverables)."""
        if self._allowed is None:
            allowed = {}
            for artifact in self.artifacts.values():
                path = self.path_of(artifact)
                if path:
                    allowed.setdefault(path, artifact.get("node") if isinstance(artifact.get("node"), str) else None)
            for item in self.deliverables():
                if item["rel"]:
                    allowed.setdefault(item["rel"], None)
            self._allowed = allowed
        return self._allowed
