"""The Films contact sheet: one row per film, jobs (the source of truth) and library films without a job.

A library film linked to a job merges into the job's row. Every version carries a status mark that reads as a glyph and
a word in the UI. Decision groups (one per waiting gate, or per version with findings to answer) feed the Inbox band.
"""
from __future__ import annotations

import hashlib
import os
import re

from . import fsio
from .producer import owner_mark

AREA_WORDS = {"Downloads/Archive": "Archive",
              "Downloads": "Downloads", "Desktop": "Desktop", "Movies": "Movies", "Documents": "Documents"}
MARK_WORDS = {"needs": "Needs you", "in_progress": "In progress", "rework": "Rework", "passed": "Passed",
              "not_reviewed": "Not reviewed", "partial": "Partly reviewed", "not_run": "No master", "file": None}
_GATE_TITLE = re.compile(r"^N(\d{2}):\s*(.+)$")


def area_word(film):
    if film.get("archived"):
        return "Archive"
    area = str(film.get("area") or "").lower()
    if area == "workspace" or area.endswith("_workspace"):
        return "Workspace"
    if area == "portfolio" or area.endswith("_portfolio"):
        return "Portfolio"
    return AREA_WORDS.get(film.get("area"), "Repos")


def version_mark(candidate, decision_versions):
    """(mark, word) for a job version from its summary: needs you, in progress, rework, passed or not reviewed."""
    version = candidate["version"]
    if candidate.get("live"):
        return "in_progress", MARK_WORDS["in_progress"]
    if version in decision_versions:
        return "needs", MARK_WORDS["needs"]
    reviews = candidate.get("reviews") or []
    qa = candidate.get("qa") or {}
    if any(r.get("verdict_class") == "rework" for r in reviews):
        return "rework", MARK_WORDS["rework"]
    if not candidate.get("has_master"):
        return "not_run", MARK_WORDS["not_run"]
    if not reviews:
        return "not_reviewed", MARK_WORDS["not_reviewed"]
    if all(r.get("verdict_class") == "pass" for r in reviews) and not qa.get("blocking"):
        return "passed", MARK_WORDS["passed"]
    return "partial", MARK_WORDS["partial"]


def gate_words(item, release_node):
    """'Release decision for v25' or 'Approve the objective and audience (N03)'."""
    if item.get("node") and item.get("node") == release_node and item.get("version"):
        return f"Release decision for {item['version']}"
    match = _GATE_TITLE.match(item.get("title") or "")
    if match:
        return f"{match.group(2)} (N{match.group(1)})"
    return item.get("title") or "Decision"


def decision_groups(items, release_nodes):
    """One group per waiting gate without a version, and one per (job, version) for release, findings and rework."""
    groups, order = {}, []
    for item in items:
        if item.get("group") != "decision":
            continue
        pid = item["pid"]
        release = release_nodes.get(pid)
        is_release = item.get("kind") == "human_gate" and item.get("node") == release and item.get("version")
        if item.get("kind") == "human_gate" and not is_release:
            key = f"{pid}:gate:{item.get('node')}:{item.get('version') or ''}"
        else:
            key = f"{pid}:version:{item.get('version') or ''}"
        group = groups.get(key)
        if group is None:
            group = groups[key] = {"key": key, "pid": pid, "film_id": f"job-{pid}", "film": item.get("project"),
                                   "version": item.get("version"), "node": None, "node_num": None, "kind": None,
                                   "words": None, "since": None, "reason": None, "detail": None, "release": False,
                                   "findings": 0, "levels": {}, "rework": 0, "items": []}
            order.append(key)
        group["items"].append(item)
        if item.get("kind") == "human_gate":
            group.update(node=item.get("node"), node_num=item.get("node_num"), words=gate_words(item, release),
                         since=item.get("since"), reason=item.get("reason"), detail=item.get("detail"),
                         release=bool(is_release), kind="release" if is_release else "gate")
        elif item.get("kind") == "open_defect":
            count = item.get("more") or 1
            group["findings"] += count
            level = item.get("level") or "P?"
            group["levels"][level] = group["levels"].get(level, 0) + count
        elif item.get("kind") == "rework_verdict":
            group["rework"] += len(item.get("reviewers") or []) or 1
    out = []
    for key in order:
        group = groups[key]
        if group["kind"] is None:
            group["kind"] = "findings"
            group["words"] = f"Findings to answer on {group['version']}" if group["version"] else "Findings to answer"
        out.append(group)
    return out


def _poster_from_library(library, project_path, rel):
    """A sidecar poster the library index found next to a governed master, as /media/lib/<id>/poster."""
    if library is None or not rel:
        return None
    item_id = hashlib.sha1(os.path.join(project_path, rel).encode("utf-8")).hexdigest()[:16]
    item = library.item(item_id)
    return f"/media/lib/{item_id}/poster" if item and item.get("poster") else None


def run_note(reader, summary, release_node):
    """Why a governed run is stalled while versions continue: {node, num, since, outside} or None."""
    run = summary.get("run")
    if not run or not run.get("waiting_at"):
        return None
    node = run["waiting_at"][0]
    if node == release_node:
        return None
    governed = set(reader.run_model().labels())
    current = run.get("current_version")
    current_label = f"v{current}" if isinstance(current, int) and current else None
    outside = [v for v in summary.get("versions") or []
               if v not in governed and (current_label is None or fsio.version_key(v) > fsio.version_key(current_label))]
    return {"node": node, "num": reader.node_num(node), "since": reader.waiting_since(node), "outside": len(outside),
            "name": reader.graph.summary(node)["name"]}


def job_row(reader, items, library_film, library):
    summary = reader.summary()
    graph = reader.graph
    release_node = graph.release_node()
    decisions = decision_groups(items, {reader.pid: release_node})
    decision_versions = {d["version"] for d in decisions if d["version"]}
    marks = reader.owner_marks()
    frames = []
    for version in reversed(summary["versions"]):
        candidate = reader.candidate_summary(version)
        mark, word = version_mark(candidate, decision_versions)
        media = reader.media(version)
        poster = media.get("poster")
        if not poster and media.get("source") == "governed":
            poster = _poster_from_library(library, reader.project.path, reader.run_artifact_path(version, "master"))
        frames.append({"label": version, "mark": mark, "mark_word": word, "poster": poster,
                       "duration_s": candidate.get("duration_s"), "mtime": candidate.get("mtime"),
                       "has_master": candidate.get("has_master"), "source": candidate.get("source"),
                       "media": media.get("master"), "size": reader.master_size(version) if media.get("master") else None,
                       "owner": marks.get(version)})
    mtimes = [f["mtime"] for f in frames if f["mtime"]]
    library_versions = library_film["versions_count"] if library_film else 0
    counts_text = f"{len(frames)} version{'s' if len(frames) != 1 else ''}"
    if library_film:
        counts_text += f" \u00b7 {library_versions} folder{'s' if library_versions != 1 else ''} with video"
    fyi = [i for i in items if i.get("group") == "fyi"]
    for decision in decisions:
        node = graph.summary(decision["node"]) if decision.get("node") else None
        decision["node_name"] = node["name"] if node else None
        decision["node_short"] = node["short"] if node else None
        if decision.get("version"):
            candidate = reader.candidate_summary(decision["version"])
            reviews = candidate.get("reviews") or []
            decision["facts"] = {"reviews": len(reviews), "passing": sum(1 for r in reviews if r.get("verdict_class") == "pass"),
                                 "open_levels": reader.open_levels(decision["version"]), "duration_s": candidate.get("duration_s")}
    return {
        "id": f"job-{reader.pid}", "kind": "job", "pid": reader.pid, "film_id": library_film["id"] if library_film else None,
        "name": summary["name"], "area": "Job", "archived": False, "rel_path": summary["rel_path"],
        "run": summary["run"], "run_note": run_note(reader, summary, release_node),
        "workflow_nodes": len(graph.nodes), "release_node": release_node,
        "release_version": reader.latest_reviewed_version(),
        "brief_runtime": reader.brief_runtime(),
        "latest": summary["latest_version"], "focus": reader.focus_version(),
        "in_progress": [f["label"] for f in frames if f["mark"] == "in_progress"],
        "decisions": decisions, "needs": len(decisions), "fyi": len(fyi), "decided": decided_gates(reader),
        "versions": frames, "counts": {"versions": len(frames), "library_versions": library_versions,
                                       "videos": library_film["videos"] if library_film else None},
        "counts_text": counts_text, "latest_mtime": max(mtimes) if mtimes else None,
        "total_size": library_film["total_size"] if library_film else 0,
        "search": " ".join([summary["name"], summary["rel_path"], "job"] + summary["versions"]).lower(),
    }


def _facts(reader, version):
    candidate = reader.candidate_summary(version)
    reviews = candidate.get("reviews") or []
    return {"reviews": len(reviews), "passing": sum(1 for r in reviews if r.get("verdict_class") == "pass"),
            "open_levels": reader.open_levels(version), "duration_s": candidate.get("duration_s")}


def decided_gates(reader):
    """The gates the owner decided that still stand (the engine still waits, or the release is still recorded):
    [{node, node_num, node_name, node_short, kind (gate or release), version, words, engine_waits, redecide, record,
    facts}], newest decision first. They are never counted as decisions that wait."""
    graph = reader.graph
    release = graph.release_node()
    out, seen = [], set()
    for record in reader.reactions():
        if record.get("actor") != "owner" or record.get("kind") not in ("approve", "request_changes"):
            continue
        target = record.get("target") or {}
        if target.get("type") in ("gate", "node") and graph.human_gate(target.get("id")):
            node = target["id"]
        elif target.get("type") == "version" and release:
            node = release
        else:
            continue
        version = record.get("version") if node == release else None
        if (node, version) in seen:
            continue
        seen.add((node, version))
        state = reader.human_gate_state(node, version)
        if not state or state["state"] != "recorded" or not state.get("decision_record"):
            continue
        summary = graph.summary(node)
        item = {"node": node, "node_num": state["num"], "node_name": summary["name"], "node_short": summary["short"],
                "kind": "release" if node == release else "gate", "version": version,
                "words": f"Release decision for {version}" if node == release else (state.get("decision") or summary["name"]),
                "engine_waits": bool(state.get("engine_waits")), "redecide": bool(state.get("redecide")),
                "record": owner_mark(state["decision_record"])}
        if version:
            item["facts"] = _facts(reader, version)
        out.append(item)
    return out


def library_row(summary, detail):
    frames = []
    for version in detail["versions"]:
        primary = version["primary"]
        frames.append({"label": version["label"], "mark": "file", "mark_word": None, "role": primary["role"],
                       "poster": f"/media/lib/{primary['id']}/poster" if primary.get("has_poster") else None,
                       "duration_s": primary.get("duration_s"), "mtime": version.get("mtime"), "item": primary["id"],
                       "playable": primary.get("playable"), "files": 1 + len(version.get("variants") or []),
                       "media": f"/media/lib/{primary['id']}" if primary.get("playable") else None,
                       "size": primary.get("size")})
    count = len(frames)
    return {
        "id": f"lib-{summary['id']}", "kind": "library", "pid": None, "film_id": summary["id"], "name": summary["name"],
        "area": area_word(summary), "archived": bool(summary.get("archived")), "rel_path": summary.get("root"),
        "run": None, "run_note": None, "latest": frames[0]["label"] if frames else None, "focus": None,
        "in_progress": [], "decisions": [], "needs": 0, "fyi": 0, "versions": frames,
        "counts": {"versions": None, "library_versions": count, "videos": summary.get("videos")},
        "counts_text": f"{count} folder{'s' if count != 1 else ''} with video \u00b7 {summary.get('videos')} file{'s' if summary.get('videos') != 1 else ''}",
        "latest_mtime": summary.get("latest_mtime"), "total_size": summary.get("total_size") or 0,
        "why": detail.get("why"), "search": summary.get("search") or summary["name"].lower(),
    }


def films_payload(app):
    """Rows for the contact sheet, the decision groups for the Inbox, filter counts and the library scan state."""
    items = app.needs()
    by_pid = {}
    for item in items:
        by_pid.setdefault(item["pid"], []).append(item)
    library = app.library
    overview = library.overview() if library is not None else None
    linked = {f["job_pid"]: f for f in (overview["films"] if overview else []) if f.get("job_pid")}
    rows = []
    for project in app.registry.projects():
        rows.append(job_row(app.reader(project), by_pid.get(project.id, []), linked.get(project.id), library))
    job_pids = {row["pid"] for row in rows}
    for summary in overview["films"] if overview else []:
        if summary.get("job_pid") in job_pids:
            continue
        detail = library.film(summary["id"])
        if detail:
            rows.append(library_row(summary, detail))
    rows.sort(key=lambda r: r["latest_mtime"] or "", reverse=True)
    decisions = [d for row in rows for d in row["decisions"]]
    for decision in decisions:
        row = next(r for r in rows if r["pid"] == decision["pid"])
        frame = next((f for f in row["versions"] if f["label"] == decision["version"]), None)
        decision["poster"] = frame["poster"] if frame else None
        decision["duration_s"] = frame["duration_s"] if frame else None
        decision["run_note"] = row["run_note"] if decision["kind"] == "gate" else None
    status = library.status() if library is not None else {"enabled": False, "status": "disabled"}
    status.pop("roots", None)
    return {
        "rows": rows,
        "decisions": decisions,
        "counts": {"all": len(rows), "needs": sum(1 for r in rows if r["needs"]),
                   "jobs": sum(1 for r in rows if r["kind"] == "job"),
                   "library": sum(1 for r in rows if r["kind"] == "library"),
                   "archive": sum(1 for r in rows if r["archived"])},
        "library": status,
        "other": overview["other"] if overview else [],
    }
