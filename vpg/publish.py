"""Human-facing version publishing.

Publishing is intentionally outside the graph's pass/fail path: a copy or privacy problem records a hold and a warning,
but never changes node outcomes. The release gate remains the authority for shipping.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .models import ArtifactRecord, VersionRecord
from .tools import privacy
from .util import now, read_json, rel, sha256_file, write_json_atomic

INDEX = "_vpg-index.json"
README = "_vpg-README.md"   # never README.md: a project's own publisher may own that name in the same folder
DEFAULT_KEYS = ["master"]


def configured(config: dict[str, Any]) -> bool:
    return "publish" in (config or {})


def publish_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = (config or {}).get("publish") or {}
    return {"dir": str(raw.get("dir", "outputs")), "keys": list(raw.get("keys", DEFAULT_KEYS)), "auto": bool(raw.get("auto", False))}


def config_problems(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ["publish must be a mapping"]
    out = []
    if "dir" in raw and (not isinstance(raw["dir"], str) or not raw["dir"].strip() or Path(raw["dir"]).is_absolute()
                         or ".." in Path(raw["dir"]).parts):
        out.append("publish.dir must be a non-empty path inside the project (no absolute path, no ..)")
    if "keys" in raw and (not isinstance(raw["keys"], list) or not all(isinstance(k, str) and k for k in raw["keys"])):
        out.append("publish.keys must be a list of artifact key strings")
    if "auto" in raw and not isinstance(raw["auto"], bool):
        out.append("publish.auto must be true or false")
    return out


def output_dir(project) -> Path:
    return (project.root / publish_config(project.config)["dir"]).resolve()


def _load_index(out: Path) -> dict[str, Any]:
    path = out / INDEX
    return read_json(path) if path.exists() else {"generated_at": "", "versions": []}


def _write_index(out: Path, index: dict[str, Any]) -> None:
    index["generated_at"] = now()
    write_json_atomic(out / INDEX, index)
    lines = ["# VPG published versions", "", "| Version | Status | Scan | Files | Time |",
             "|---:|---|---|---:|---|"]
    for row in sorted(index.get("versions", []), key=lambda r: r["version"]):
        scan = row.get("scan", {}).get("result", "not-run")
        lines.append(f"| v{row['version']} | {row['status']} | {scan} | {len(row.get('files', []))} | {row.get('time', '')} |")
        for f in row.get("files", []):
            lines.append(f"|  |  |  | `{f['key']}` `{f['sha256'][:16]}` | `{f['path']}` |")
    (out / README).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _version_row(index: dict[str, Any], version: int) -> dict[str, Any] | None:
    return next((r for r in index.get("versions", []) if r.get("version") == version), None)


def _replace_row(index: dict[str, Any], row: dict[str, Any]) -> None:
    index["versions"] = [r for r in index.get("versions", []) if r.get("version") != row["version"]] + [row]


def _records_for_version(eng, version: VersionRecord) -> list[ArtifactRecord]:
    wanted = set(publish_config(eng.project.config)["keys"])
    by_id = {(a.key + (f"[{a.item}]" if a.item else ""), a.sha256): a for a in eng.state.artifacts.values()}
    out = []
    for key, sha in version.artifacts.items():
        plain = key.split("[", 1)[0]
        if plain in wanted and (key, sha) in by_id:
            out.append(by_id[(key, sha)])
    return sorted(out, key=lambda r: r.key)


def _copy_verified(src: Path, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["cp", "-c", str(src), str(dest)], check=True, capture_output=True)
    except Exception:
        shutil.copy2(src, dest)
    src_sha, dest_sha = sha256_file(src), sha256_file(dest)
    if src_sha != dest_sha:
        raise OSError(f"copied {src} to {dest}, but sha256 verification failed")
    return dest_sha


def _registered(eng, version: VersionRecord, keys: set[str]) -> list[ArtifactRecord]:
    """The version's own artifacts for these keys, found by the hashes the version registered (never publish.keys)."""
    by_id = {(a.key + (f"[{a.item}]" if a.item else ""), a.sha256): a for a in eng.state.artifacts.values()}
    return [by_id[(k, sha)] for k, sha in version.artifacts.items() if k.split("[", 1)[0] in keys and (k, sha) in by_id]


def _unchanged(eng, record: ArtifactRecord) -> bool:
    path = eng.project.root / record.path
    return path.is_file() and sha256_file(path) == record.sha256


def _privacy_scan(eng, version: VersionRecord, records: list[ArtifactRecord]) -> dict[str, Any]:
    """The release's own scan of this version's master, with its declared live windows, whatever publish.keys lists."""
    cfg = eng.project.config
    names, skipped = privacy.load_names(eng.project.root, cfg)
    if not names:
        return {"result": "not-declared"}
    keys = {o.key for n in eng.wf.nodes for o in n.outputs if o.state_path == "production.master_ref"} or {"master"}
    master = next(iter(_registered(eng, version, keys)), None)   # the version's master, whatever its node calls it
    if master is None:
        return {"result": "held", "reason": "the version's master is not registered, so it cannot be scanned"}
    if not _unchanged(eng, master):
        return {"result": "held", "reason": "the master changed on disk since the version registered it"}
    try:
        text_layers = next(iter(_registered(eng, version, {"text_layers"})), None)
        windows = privacy.manifest_live_windows(read_json(eng.project.root / text_layers.path) if text_layers else None)
        pcfg = (cfg or {}).get("privacy") or {}
        report = privacy.scan_video(eng.project.root / master.path, names, pcfg.get("scan_fps", "native"),
                                    float(pcfg.get("time_budget_s", 1800)), live_windows=windows)
    except (privacy.OcrUnavailable, privacy.PrivacyConfigError, privacy.PrivacyScanError) as exc:
        return {"result": "held", "reason": f"privacy scan could not complete ({type(exc).__name__})"}
    if report.get("windows"):
        return {"result": "held", "reason": "readable listed name", "scanner": privacy.SCANNER}
    if privacy.live_window_problem(report):
        return {"result": "held", "reason": "a declared live window was not fully scanned", "scanner": privacy.SCANNER}
    return {"result": "pass", "scanner": privacy.SCANNER, "skipped_entries": skipped}


def unpublished_versions(eng) -> list[int]:
    if not configured(eng.project.config):
        return []
    idx = _load_index(output_dir(eng.project))
    done = {r.get("version") for r in idx.get("versions", []) if r.get("status") == "published"}
    return [v.version for v in eng.state.history.versions if v.version not in done]


def status_warnings(eng) -> list[str]:
    if not configured(eng.project.config):
        return []
    idx = _load_index(output_dir(eng.project))
    held = [f"v{r['version']} ({r.get('reason', 'held')})" for r in idx.get("versions", []) if r.get("status") == "held"]
    unpublished = unpublished_versions(eng)
    out = []
    if unpublished:
        out.append("publish: unpublished versions " + ", ".join(f"v{v}" for v in unpublished))
    if held:
        out.append("publish: held versions " + ", ".join(held))
    return out


def publish(eng, version: int | None = None, check: bool = False) -> dict[str, Any]:
    cfg = publish_config(eng.project.config)
    out = output_dir(eng.project)
    versions = [v for v in eng.state.history.versions if version is None or v.version == version]   # published ones are re-checked
    idx = _load_index(out)
    result = {"published": [], "held": [], "in_sync": [], "out_of_sync": [], "check": check}
    for v in versions:
        row = _version_row(idx, v.version)
        if row and row.get("status") == "published":
            intact = all((out / f["path"]).is_file() and sha256_file(out / f["path"]) == f["sha256"] for f in row.get("files", []))
            result["in_sync" if intact else "out_of_sync"].append(v.version)   # a copy edited or removed since
            continue
        if row and row.get("status") == "held" and version is None:
            result["held"].append(v.version)   # a held master never changes; re-scan it only when asked by version
            continue
        records = _records_for_version(eng, v)
        if check:   # a check writes nothing and scans nothing: it says what a publish would do
            result["published"].append(v.version)
            continue
        scan = _privacy_scan(eng, v, records) if (eng.project.config.get("privacy") or {}).get("names") else {"result": "not-declared"}
        if scan["result"] == "held":
            result["held"].append(v.version)
            if not check:
                held = {"version": v.version, "status": "held", "reason": scan["reason"], "scan": scan, "files": [], "time": now()}
                _replace_row(idx, held)
                eng.ledger.append("version_held", version=v.version, reason=scan["reason"], scanner=privacy.SCANNER)
            continue
        plan, used, clash = [], set(), None
        for r in records:
            name = Path(r.path).name
            if name in used:   # two artifacts with one file name: keep both, prefixed by key (and item)
                name = f"{r.key}{'-' + r.item if r.item else ''}-{name}"
            used.add(name)
            dest = out / f"v{v.version}" / name
            if not _unchanged(eng, r):
                clash = f"{r.path} changed on disk since the version registered it"
            elif dest.exists() and (not dest.is_file() or sha256_file(dest) != r.sha256):
                clash = f"{rel(dest, out)} already exists with other content; publish never overwrites"
            plan.append((r, dest))
        if clash:
            held = {"version": v.version, "status": "held", "reason": clash, "scan": scan, "files": [], "time": now()}
            _replace_row(idx, held)
            eng.ledger.append("version_held", version=v.version, reason=clash, scanner=scan.get("scanner"))
            result["held"].append(v.version)
            continue
        copied = []
        for r, dest in plan:
            sha = r.sha256 if dest.is_file() else _copy_verified(eng.project.root / r.path, dest)
            if sha != r.sha256:
                raise OSError(f"the copy of {r.path} does not match the version's registered hash")
            copied.append({"key": r.key, "path": rel(dest, out), "sha256": sha})
        row = {"version": v.version, "status": "published", "files": copied, "scan": scan, "time": now()}
        _replace_row(idx, row)
        eng.ledger.append("version_published", version=v.version, files=copied, scanner=scan.get("scanner"))
        result["published"].append(v.version)
    if not check:
        out.mkdir(parents=True, exist_ok=True)
        _write_index(out, idx)
    return result


def auto_publish(eng) -> None:
    if configured(eng.project.config) and publish_config(eng.project.config)["auto"]:
        publish(eng)
