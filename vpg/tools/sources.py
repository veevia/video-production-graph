"""N01/N02 intake tools: source registry checks, source validation and context-gap detection."""

from __future__ import annotations

from ..context import CheckResult, NodeResult
from ..controls import CONTROL_DOCS
from ..util import NotRegularFile, sha256_file, write_json_atomic

REMOTE_KINDS = {"url", "transcript", "conversation", "meeting"}
EVIDENCE = {"observed", "reported", "assumed"}
PRODUCT_KINDS = {"product", "prototype", "demo", "recording", "spec"}
BRAND_KINDS = {"brand", "design"}


def kind_group(kind: str) -> str:
    return "product" if kind in PRODUCT_KINDS else "brand" if kind in BRAND_KINDS else kind


def _not_counted(s: dict, ctx, relabeled: set[str], workflow_copies: set[str]) -> str | None:
    """Why a registered source does not count as coverage evidence (None when it counts)."""
    if not s.get("sha256"):
        return "not hashed"
    if (ctx.project_dir / "sources").is_symlink():
        return "sources/ is itself a symlink; only files stored in the project's own sources/ directory count"
    if not (ctx.project_dir / s["path"]).resolve().is_relative_to(ctx.project_dir.resolve() / "sources"):
        return "outside sources/"
    if s["sha256"] in workflow_copies:
        return "a copy of a control document or template, which the workflow writes and is not evidence"
    if s["sha256"] in relabeled:
        return "the same file is registered under another kind too"
    if s["evidence"] == "assumed":
        return "assumed evidence"
    return None


def _link_below_sources(project_dir, path):
    """The first symbolic link below the project's sources/ directory on the way to `path`, the file included. A symlinked
    sources/ itself is handled by coverage (its files count as nothing) rather than refused here."""
    base = project_dir / "sources"
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return path if path.is_symlink() else None
    here = base
    for part in parts:
        here = here / part
        if here.is_symlink():
            return here
    return None


def check_registry(ctx) -> CheckResult:
    data = ctx.load("sources") or {}
    msgs = []
    ids = [s.get("id") for s in data.get("sources", [])]
    if not ids:
        msgs.append("no sources registered")
    if len(ids) != len(set(ids)):
        msgs.append("duplicate source ids")
    for s in data.get("sources", []):
        if s.get("evidence") not in EVIDENCE:
            msgs.append(f"{s.get('id')}: evidence must be observed/reported/assumed (evidence-tagging)")
        p = ctx.project_dir / s.get("path", "")
        if s.get("kind") not in REMOTE_KINDS and not p.exists():
            msgs.append(f"{s.get('id')}: local source {s.get('path')} does not exist")
        elif s.get("kind") not in REMOTE_KINDS and not p.is_file():
            msgs.append(f"{s.get('id')}: {s.get('path')} is not a regular file (a directory, pipe or device); register the files "
                        "themselves, one source per file")
        elif s.get("kind") not in REMOTE_KINDS and (link := _link_below_sources(ctx.project_dir, p)) is not None:
            what = ("is a symbolic link" if link == p else
                    f"is inside a symbolically linked directory ({link.relative_to(ctx.project_dir)})")
            msgs.append(f"{s.get('id')}: {s.get('path')} {what}, so an edit to what it points to would change this source "
                        "after it is registered; store a copy (cp, not ln -s)")
        elif s.get("kind") not in REMOTE_KINDS and p.stat().st_nlink > 1:
            msgs.append(f"{s.get('id')}: {s.get('path')} is a hard link ({p.stat().st_nlink} paths share it), so an edit made "
                        "through another path would rewrite this source after it is registered; store a copy (cp, not ln)")
    return CheckResult(not msgs, msgs)


def waivers_in_effect(waivers, gap_report: dict | None) -> list:
    """Waivers whose gap the latest gap report still lists. A gap later closed by a registered source drops out; the
    waiver stays in the frozen inputs and the ledger as history."""
    listed = {g.get("id") for g in (gap_report or {}).get("gaps", [])}
    return [w for w in waivers if w.gap in listed]


def validate(ctx) -> NodeResult:
    """Hash every local source and detect context gaps.

    Outcomes: pass (no blocking or resolvable gaps), gaps_resolvable (loop back to intake), gaps_blocking (escalate).
    """
    sources = [s.model_dump() for s in ctx.state.inputs.sources]
    gaps = []
    for s in sources:
        if s["kind"] in REMOTE_KINDS:
            gaps.append({"id": f"G-{s['id']}", "severity": "note", "where": s["id"],
                         "text": f"{s['kind']} source is referenced, not hashed; cite it by id and never quote confidential content"})
            continue
        p = ctx.project_dir / s["path"]
        if not p.exists():
            gaps.append({"id": f"G-{s['id']}", "severity": "blocking", "where": s["id"], "text": f"registered source {s['path']} is missing"})
            continue
        try:
            if not p.is_file():
                raise NotRegularFile(str(p))
            s["sha256"], s["bytes"] = sha256_file(p), p.stat().st_size
        except NotRegularFile:
            gaps.append({"id": f"G-{s['id']}", "severity": "blocking", "where": s["id"],
                         "text": f"registered source {s['path']} is no longer a regular file; restore it"})
            continue
        if s["evidence"] == "assumed":
            gaps.append({"id": f"G-{s['id']}-assumed", "severity": "note", "where": s["id"],
                         "text": "assumed evidence: it may inform questions, never product claims"})
    kinds = {s["kind"] for s in sources}
    if not ctx.state.inputs.brief.strip() and "brief" not in kinds:
        gaps.append({"id": "G-BRIEF", "severity": "blocking", "where": "inputs.brief", "text": "no production brief: the objective cannot be defined without inventing it"})
    # Coverage is judged on content, not on the agent's labels. Only files under sources/ count, and not copies of the
    # workflow's own control documents or templates. One file (by hash, the configured brief included) is evidence of one
    # kind only, and assumed sources never ground product or brand truth.
    workflow_copies = {sha256_file(p) for p in [ctx.control_path(d) for d in CONTROL_DOCS]
                       + sorted((ctx.pkg_root / "templates" / "control").glob("*.md")) if p.is_file()}
    brief_file = ctx.project_dir / str((ctx.config or {}).get("brief") or "")
    brief_sha = sha256_file(brief_file) if (ctx.config or {}).get("brief") and brief_file.is_file() else None
    groups: dict[str, set[str]] = {}
    for s in sources:
        if s.get("sha256"):
            groups.setdefault(s["sha256"], set()).add(kind_group(s["kind"]))
    if brief_sha:
        groups.setdefault(brief_sha, set()).add("brief")
    relabeled = {sha for sha, g in groups.items() if len(g) > 1}
    for sha in sorted(relabeled):
        ids = ", ".join(f"{s['id']} {s['path']} ({s['kind']})" for s in sources if s.get("sha256") == sha)
        gaps.append({"id": f"G-RELABEL-{sha[:8]}", "severity": "note", "where": ids,
                     "text": f"one file is registered as {' and '.join(sorted(groups[sha]))}: {ids}"
                             f"{' (it is the brief)' if sha == brief_sha else ''}. A file is evidence of one kind only, so it closes "
                             "no product or brand gap; register each file once, with its one kind"})
    for gid, wanted, text in (("G-PRODUCT", PRODUCT_KINDS, "no product evidence (prototype, demo recording or spec): product truth would have to be invented"),
                              ("G-BRAND", BRAND_KINDS, "no brand or design authority: the visual system would be ungrounded")):
        labelled = [s for s in sources if s["kind"] in wanted]
        if any(_not_counted(s, ctx, relabeled, workflow_copies) is None for s in labelled):
            continue
        why = "; ".join(f"{s['id']} not counted: {_not_counted(s, ctx, relabeled, workflow_copies)}" for s in labelled)
        gaps.append({"id": gid, "severity": "resolvable", "where": "sources", "text": text + (f" ({why})" if why else "")})
    for q in ctx.state.production.open_questions:
        if q.blocking and q.status == "open":
            gaps.append({"id": f"G-{q.id}", "severity": "blocking", "where": "open_questions", "text": q.text})
    waived = {w.gap: w for w in ctx.state.inputs.waivers}
    for g in gaps:   # only a named human's waiver (`vpg unblock --waive`) lets the film proceed past a resolvable gap
        if g["severity"] == "resolvable" and g["id"] in waived:
            w = waived[g["id"]]
            g["severity"], g["text"] = "note", f"{g['text']} — waived by {w.by} ({w.at[:10]}): {w.reason}"
    for doc in CONTROL_DOCS:
        if not ctx.control_path(doc).exists():
            gaps.append({"id": f"G-CTRL-{doc}", "severity": "blocking", "where": f"controls/{doc}.md",
                         "text": f"control document {doc}.md is missing; run `vpg init` to create it from the template"})
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / "gaps.json", {"gaps": gaps})
    write_json_atomic(ctx.workdir / "sources-validated.json", {"sources": sources})
    sev = {g["severity"] for g in gaps}
    outcome = "gaps_blocking" if "blocking" in sev else "gaps_resolvable" if "resolvable" in sev else "pass"
    return NodeResult(outcome=outcome, outputs={"gaps": "gaps.json", "sources_validated": "sources-validated.json"},
                      data={"gaps": len(gaps), "blocking": sum(g["severity"] == "blocking" for g in gaps),
                            "resolvable": sum(g["severity"] == "resolvable" for g in gaps),
                            "reasons": [g["text"] for g in gaps if g["severity"] != "note"]})
