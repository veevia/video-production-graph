"""'Needs you': what the owner should look at next, across jobs, split into decisions and FYI.

Decisions for you (counted in the badge): human gates the governed run waits at; gates whose provisional approval
expired; the release decision (N23) for the latest fully reviewed version when its gates passed; and, when nothing
newer is being built, that version's unanswered P1 and P2 defects and its rework verdicts.

Producer is working on (FYI, not counted): the version being built while it is in flight, unreviewed or partly
reviewed, with its open P1 to P3 defects; the latest fully reviewed version's P3 defects; and, once a newer version
exists, the latest fully reviewed version's open defects and rework verdicts (superseded).
"""
from __future__ import annotations

from urllib.parse import quote

from . import fsio
from .reviews import BLOCKING_LEVELS, LEVEL_RANK

RANK = {"human_gate": 0, "open_defect": 1, "rework_verdict": 2, "unreviewed": 3, "in_flight": 4}
ACTIONS = {
    "human_gate": "Open gate",
    "open_defect": "Seek to finding",
    "rework_verdict": "Review version",
    "unreviewed": "Review version",
    "in_flight": "Watch version",
}
GROUPS = ("decision", "fyi")
DECISION_LEVELS = ("P1", "P2")
MAX_DEFECTS_PER_GROUP = 12


def _one_line(text, limit=140):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def needs_for_job(reader):
    """Needs-you items for one job (unsorted); each carries group 'decision' or 'fyi'."""
    pid = reader.pid
    graph = reader.graph
    items = []

    def add(kind, title, link, group, **extra):
        item = {"pid": pid, "project": reader.project.name, "kind": kind, "title": title, "link": link,
                "action": ACTIONS[kind], "rank": RANK[kind], "group": group}
        item.update({k: v for k, v in extra.items() if v is not None})
        items.append(item)

    run = reader.run()
    waiting = run["waiting_at"] if run else []
    for node_id in waiting:
        state = reader.human_gate_state(node_id, None) if graph.human_gate(node_id) else None
        if state and state["state"] == "recorded":
            continue
        summary = graph.summary(node_id)
        num = reader.node_num(node_id)
        decision = state["decision"] if state and state.get("decision") else summary["name"]
        add("human_gate", f"N{num:02d}: {decision}" if num else decision, f"#/p/{pid}/n/{quote(node_id)}", "decision",
            node=node_id, node_num=num, since=reader.waiting_since(node_id),
            detail="The governed run is paused at this human gate.")
    for state in reader.expired_provisionals():
        if state["node"] in waiting or state["state"] != "waiting":
            continue
        engine = state["engine_decision"] or {}
        num = state["num"]
        decision = state.get("decision") or graph.summary(state["node"])["name"]
        version = state.get("version")
        link = f"#/p/{pid}/n/{quote(state['node'])}" + (f"?v={quote(version)}" if version else "")
        when = engine.get("expires_label") or engine.get("expires_at")
        add("human_gate", f"N{num:02d}: {decision}" if num else decision, link, "decision",
            node=state["node"], node_num=num, version=version, since=engine.get("expires_at"),
            reason="provisional approval expired",
            detail=f"The provisional approval by {engine.get('actor') or 'unknown actor'} expired"
                   f"{f' {when}' if when else ''}; confirm or reject it.")

    live = set(reader.live_versions())
    owner_reactions = [r for r in reader.reactions() if r.get("actor") == "owner"]

    def reacted_to_version(version):
        return any((r.get("target") or {}).get("type") == "version" and r.get("version") == version for r in owner_reactions)

    def answered_findings(version):
        return {(r.get("target") or {}).get("id") for r in owner_reactions
                if r.get("version") == version and (r.get("target") or {}).get("type") == "finding"}

    def defect_cards(version, defects, group, reason=None, more_word="open defect"):
        defects = sorted(defects, key=lambda d: (LEVEL_RANK[d["level"]], d["t0"] if d["t0"] is not None else 1e9))
        for defect in defects[:MAX_DEFECTS_PER_GROUP]:
            label = f"{defect['level']} {defect['id']}" if defect["id"] else defect["level"]
            query = f"?f={quote(defect['uid'], safe='')}"
            if defect["t0"] is not None:
                query += f"&t={defect['t0']}"
            add("open_defect", f"{label}: {_one_line(defect['title'])}", f"#/p/{pid}/v/{quote(version)}{query}", group,
                version=version, level=defect["level"], node=defect["node"], node_num=defect.get("node_num"),
                t=defect["t0"], finding=defect["uid"], reviewer=defect.get("reviewer"), reason=reason)
        if len(defects) > MAX_DEFECTS_PER_GROUP:
            rest = len(defects) - MAX_DEFECTS_PER_GROUP
            add("open_defect", f"{rest} more {more_word}{'s' if rest != 1 else ''} on {version}",
                f"#/p/{pid}/v/{quote(version)}", group, version=version, level=defects[MAX_DEFECTS_PER_GROUP]["level"],
                reason=reason, more=rest)

    def rework_card(version, reviews, group, reason=None):
        rework = [r for r in reviews if r["verdict_class"] == "rework"]
        if not rework or reacted_to_version(version):
            return
        worst = min((r["worst_level"] for r in rework if r["worst_level"]), key=lambda level: LEVEL_RANK.get(level, 9),
                    default=None)
        if len(rework) == 1:
            title = f"{rework[0]['label']}: {rework[0]['verdict']} on {version}"
        else:
            title = f"{len(rework)} of {len(reviews)} reviewers ask for rework on {version}"
        names = ", ".join(r["label"] for r in rework)
        single = len(rework) == 1
        add("rework_verdict", title, f"#/p/{pid}/v/{quote(version)}", group, version=version,
            node=rework[0]["node"] if single else None, reviewer=rework[0]["key"] if single else None,
            reviewers=[r["key"] for r in rework], level=worst, reason=reason,
            detail=f"Rework verdicts from {names}" + (f"; worst {worst}." if worst else "."))

    reviewed = reader.latest_reviewed_version()
    current = reader.current_version()
    superseded = current if reviewed and current and fsio.version_key(current) > fsio.version_key(reviewed) else None

    if reviewed:
        release = graph.release_node()
        if release:
            state = reader.human_gate_state(release, reviewed)
            if state and state["state"] == "open" and state["gates_passed"]:
                other = state.get("compare_with")
                add("human_gate", f"Decide release for {reviewed}", f"#/p/{pid}/n/{quote(release)}?v={quote(reviewed)}",
                    "decision", node=release, node_num=reader.node_num(release), version=reviewed,
                    detail="Gates passed, your decision.", caveat=state.get("caveat"),
                    secondary={"label": f"Compare with {other}", "link": f"#/p/{pid}/compare?a={quote(other)}&b={quote(reviewed)}"}
                    if other else None)
        answered = answered_findings(reviewed)
        blocking = [d for d in reader.open_defects(reviewed) if d["level"] in BLOCKING_LEVELS and d["uid"] not in answered]
        reviews = reader.reviews(reviewed)
        if superseded:
            reason = f"superseded by {superseded}"
            defect_cards(reviewed, blocking, "fyi", reason)
            rework_card(reviewed, reviews, "fyi", reason)
        else:
            defect_cards(reviewed, [d for d in blocking if d["level"] in DECISION_LEVELS], "decision", None, "P1-P2 defect")
            defect_cards(reviewed, [d for d in blocking if d["level"] not in DECISION_LEVELS], "fyi",
                         "P3: the producer fixes it in the next round")
            rework_card(reviewed, reviews, "decision")

    if current and current != reviewed and reader.has_version(current):
        reviews = reader.reviews(current)
        in_flight = current in live
        if not in_flight and reader.has_master(current) and not reacted_to_version(current):
            expected = len(reader.expected_reviewer_nodes(current))
            title = (f"{current} has no reviews yet" if not reviews
                     else f"{current}: {len(reviews)} of {max(expected, len(reviews))} reviews in")
            add("unreviewed", title, f"#/p/{pid}/v/{quote(current)}", "fyi", version=current,
                reason="not reviewed yet" if not reviews else "review set incomplete")
        answered = answered_findings(current)
        blocking = [d for d in reader.open_defects(current) if d["level"] in BLOCKING_LEVELS and d["uid"] not in answered]
        defect_cards(current, blocking, "fyi", "in flight" if in_flight else "review set incomplete")

    for version in sorted(live, key=fsio.version_key):
        updated = reader.updated_at(version)
        link = f"#/p/{pid}/v/{quote(version)}" if reader.has_version(version) else f"#/p/{pid}"
        add("in_flight", f"{version} in flight", link, "fyi", version=version, updated=fsio.iso_utc(updated),
            reason="in flight", detail="Files changed in the last 30 minutes and the review set is not complete.")
    return items


def sort_needs(items):
    """Human gates, then P1-P3 defects, rework verdicts, unreviewed versions and in-flight work; decisions first on ties."""
    ordered = sorted(items, key=lambda item: fsio.version_key(item.get("version") or ""), reverse=True)
    return sorted(ordered, key=lambda item: (
        RANK[item["kind"]],
        LEVEL_RANK.get(item.get("level") or "", 9) if item["kind"] == "open_defect" else 0,
        0 if item.get("group", "decision") == "decision" else 1,
        str(item.get("project") or "").lower(),
    ))


def needs_you(readers):
    items = []
    for reader in readers:
        items.extend(needs_for_job(reader))
    return sort_needs(items)


def split(items):
    """(decisions, fyi), each in the given order; an item without a group counts as a decision."""
    decisions = [item for item in items if item.get("group", "decision") == "decision"]
    return decisions, [item for item in items if item.get("group", "decision") != "decision"]
