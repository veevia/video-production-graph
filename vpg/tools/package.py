"""Packaging and delivery (N24): copy the deliverables into one folder, hash every file, zip it, and verify the zip.

Deliverables come from production.md front-matter `deliverables: [{key, name}]`. Each key names a current
artifact, and `name` is its path inside the package. Evidence travels with the package: reviews, gates,
decisions, the release summary, control-document snapshots and the ledger.
"""

from __future__ import annotations

import shutil
import zipfile

from ..context import CheckResult, NodeResult
from ..util import read_json, sha256_file, write_json_atomic

STORED = {".mp4", ".wav", ".png", ".jpg", ".zip", ".mov", ".m4a"}


def build(ctx) -> NodeResult:
    st = ctx.state
    v = st.runtime.current_version
    pkg = ctx.workdir / "package"
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    missing, tampered = [], []
    for d in ctx.fm("production").get("deliverables", []):
        recs = [a for a in st.artifacts.values() if a.key == d["key"] and a.status == "current"]
        if not recs:
            missing.append(d["key"])
            continue
        for r in recs:
            if sha256_file(ctx.project_dir / r.path) != r.sha256:   # never package a file that differs from what was registered
                tampered.append(f"{d['key']}: {r.path}")
                continue
            name = d["name"] if len(recs) == 1 else f"{d['name'].rstrip('/')}/{r.item or r.id}{(ctx.project_dir / r.path).suffix}"
            dest = pkg / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ctx.project_dir / r.path, dest)
    ev = pkg / "evidence"
    ev.mkdir()
    write_json_atomic(ev / "reviews.json", [r.model_dump() for r in st.evaluation.reviews if r.version == v])
    write_json_atomic(ev / "gates.json", [g.model_dump() for g in st.evaluation.gate_results])
    write_json_atomic(ev / "defects.json", [d.model_dump() for d in st.evaluation.defects])
    write_json_atomic(ev / "decisions.json", [a.model_dump() for a in st.history.approvals])
    write_json_atomic(ev / "comparisons.json", [c.model_dump() for c in st.evaluation.comparisons])
    shutil.copy2(ctx.project_dir / ".vpg" / "ledger.jsonl", ev / "ledger.jsonl")
    for doc in ("product", "creative", "design", "production", "evaluation"):
        if ctx.control_path(doc).exists():
            (pkg / "controls").mkdir(exist_ok=True)
            shutil.copy2(ctx.control_path(doc), pkg / "controls" / f"{doc}.md")
    rs = ctx.artifact_path("release_summary")
    if rs:
        shutil.copy2(rs, pkg / "RELEASE-SUMMARY.md")
    manifest = {"schema": "vpg-package/v1", "project": st.project.id, "version": v, "files": []}
    for p in sorted(pkg.rglob("*")):
        if p.is_file():
            manifest["files"].append({"path": str(p.relative_to(pkg)), "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    write_json_atomic(ctx.workdir / "manifest.json", manifest)
    shutil.copy2(ctx.workdir / "manifest.json", pkg / "MANIFEST.json")
    zpath = ctx.workdir / f"{st.project.id}-v{v}-package.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                z.write(p, f"{st.project.id}/{p.relative_to(pkg)}", compress_type=zipfile.ZIP_STORED if p.suffix.lower() in STORED else zipfile.ZIP_DEFLATED)
    verified, detail = verify_zip(zpath, manifest, st.project.id)
    return NodeResult(outputs={"package": zpath.name, "manifest": "manifest.json"},
                      data={"verified": verified and not tampered, "detail": detail, "files": len(manifest["files"]), "missing": missing,
                            "tampered": tampered})


def verify_zip(zpath, manifest, prefix) -> tuple[bool, str]:
    import hashlib
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        if bad:
            return False, f"corrupt member {bad}"
        for f in manifest["files"]:
            data = z.read(f"{prefix}/{f['path']}")
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                return False, f"hash mismatch {f['path']}"
    return True, f"{len(manifest['files'])} files verified against the manifest"


def check(ctx) -> CheckResult:
    m = ctx.artifact_path("manifest")
    z = ctx.artifact_path("package")
    if not m or not z:
        return CheckResult(False, ["package or manifest missing"])
    ok, detail = verify_zip(z, read_json(m), ctx.state.project.id)
    missing = [d["key"] for d in ctx.fm("production").get("deliverables", [])
               if not any(a.key == d["key"] and a.status == "current" for a in ctx.state.artifacts.values())]
    keys = {d["key"] for d in ctx.fm("production").get("deliverables", [])}
    changed = [a.path for a in ctx.state.artifacts.values() if a.key in keys and a.status == "current"
               and sha256_file(ctx.project_dir / a.path) != a.sha256]
    msgs = ([] if keys else ["production.md lists no deliverables, so the package would ship nothing"]) \
        + ([] if ok else [detail]) + ([f"deliverables missing: {missing}"] if missing else []) \
        + ([f"deliverables changed after registration: {changed}"] if changed else [])
    return CheckResult(not msgs, msgs, metrics={"detail": detail})
