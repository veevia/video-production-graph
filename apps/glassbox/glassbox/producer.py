"""The producer channel: the owner's reactions that wait for a job's producer, with their threads (ack, resolve,
reopen), as JSON for GET /api/inbox and as Markdown for chat and the inbox.py CLI.

Everything here reads reaction records; nothing writes. The server passes graph-aware target words; the CLI's
offline fallback passes none and gets plain ones."""
from __future__ import annotations

import os
import re
from urllib.parse import quote, urlencode

from .reactions import (KIND_LABELS, LIBRARY_PID, OWNER_ROOT_KINDS, THREAD_STATES, _fmt_t, _fmt_ts, now_iso,
                        producer_name, thread_sentence, thread_state)

DEFAULT_STATES = ("open", "picked_up")
STATE_WORDS = {"open": "Waiting", "picked_up": "Picked up", "resolved": "Addressed"}
_VERSION_WORD = re.compile(r"\bv\d+[a-z]?\d*\b", re.IGNORECASE)
_FIRST_SENTENCE = re.compile(r"^(.+?[.!?])(?=\s|$)")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_PATH = os.path.join(APP_DIR, "inbox.py")
_AND = re.compile(r"(\S+)\s+and\s+(\S+)")


def parse_states(value):
    """open, picked_up, resolved, all, or a comma list of them; the default is open and picked_up."""
    if value is None or value == "":
        return DEFAULT_STATES
    if value == "all":
        return THREAD_STATES
    parts = tuple(dict.fromkeys(part.strip() for part in str(value).split(",") if part.strip()))
    if not parts or any(part not in THREAD_STATES for part in parts):
        raise ValueError("state must be open, picked_up, resolved, all, or a comma list of them")
    return parts


def display_name(name, short):
    """The node name the UI shows: the engine's short name, with the 'and' its full name uses
    ('Objective audience' with 'Objective and audience definition' reads 'Objective and audience')."""
    short = (short or "").strip()
    name = (name or "").strip()
    if not short:
        return name
    words = short.split()
    joined = _AND.search(name.lower())
    if len(words) == 2 and joined:
        first, second = words[0].lower(), words[1].lower()
        g2 = joined.group(2)
        if joined.group(1) == first and (second == g2 or second == g2 + "s" or g2 == second + "s"):
            return f"{words[0]} and {words[1]}"
    return short


def first_sentence(text, limit=160):
    text = " ".join(str(text or "").split())
    match = _FIRST_SENTENCE.match(text)
    sentence = match.group(1) if match else text
    return sentence if len(sentence) <= limit else sentence[:limit - 1].rsplit(" ", 1)[0] + "\u2026"


def owner_mark(record):
    """The owner's approve or request_changes with where the producer is: {id, kind, ts, text, state, reopened,
    producer, picked_for (a version the producer's plan names), resolved_in, reply (the producer's first sentence)}."""
    if not record:
        return None
    events = record.get("thread") or []
    state = record.get("state") or thread_state(events)
    ack = next((e for e in reversed(events) if e.get("kind") == "ack"), None)
    resolve = next((e for e in reversed(events) if e.get("kind") == "resolve"), None)
    own = str(record.get("version") or "").lower()
    picked_for = None
    if ack:
        picked_for = next((m.group(0).lower() for m in _VERSION_WORD.finditer(ack.get("text") or "")
                           if m.group(0).lower() != own), None)
    answer = resolve if state == "resolved" else ack if state == "picked_up" else None
    return {
        "id": record.get("id"), "kind": record.get("kind"), "ts": record.get("ts"), "text": record.get("text") or "",
        "version": record.get("version"), "state": state,
        "reopened": state == "open" and any(e.get("kind") == "reopen" for e in events),
        "producer": (answer or {}).get("producer"), "picked_for": picked_for,
        "resolved_in": resolve.get("version") if resolve and state == "resolved" else None,
        "reply": first_sentence(answer.get("text")) if answer and answer.get("text") else None,
    }


def ui_route(record):
    """The UI address that shows a reaction in context: #/f/job-<pid>?v=...&tab=..."""
    film = f"#/f/job-{quote(str(record.get('pid')), safe='')}"
    target = record.get("target") or {}
    version = record.get("version")
    if record.get("kind") == "prefer":
        params = [("tab", "compare"), ("a", record.get("a")), ("b", record.get("b"))]
    elif target.get("type") in ("node", "gate"):
        params = [("v", version), ("tab", "pipeline"), ("n", target.get("id"))]
    elif target.get("type") == "finding":
        params = [("v", version), ("tab", "watch"), ("f", target.get("id")), ("t", target.get("t"))]
    elif target.get("type") == "time":
        params = [("v", version), ("tab", "watch"), ("t", target.get("t"))]
    else:
        params = [("v", version), ("tab", "watch")] if version else []
    params = [(key, value) for key, value in params if value not in (None, "")]
    return film + ("?" + urlencode(params, quote_via=quote) if params else "")


def plain_target_words(record):
    """Target words without the engine graph (the CLI's offline fallback)."""
    if record.get("kind") == "prefer":
        choice = record.get("choice")
        chose = "a tie" if choice == "tie" else f"chose {record.get(choice)}"
        return f"Preference between {record.get('a')} and {record.get('b')} ({chose})"
    target = record.get("target") or {}
    kind, ident = target.get("type"), target.get("id")
    on = f" on {record.get('version')}" if record.get("version") else ""
    at = f" at {_fmt_t(target.get('t'))}" if target.get("t") is not None else ""
    if kind == "version":
        return f"Version {ident}"
    if kind == "gate":
        return f"Gate {ident}{on}"
    if kind == "node":
        return f"Node {ident}{on}"
    if kind == "finding":
        return f"Finding {ident}{on}{at}"
    if kind == "time":
        return f"Moment{at}{on}"
    return "The job"


def _event(event, *keys):
    return {key: event.get(key) for key in keys} if event else None


def inbox_item(record, describe=None, base_url=None):
    """One owner reaction for the producer: verbatim text, target in words, link, thread and state."""
    events = record.get("thread")
    if events is None:
        events = []
    state = record.get("state") or thread_state(events)
    last = {kind: next((e for e in reversed(events) if e.get("kind") == kind), None) for kind in ("ack", "resolve", "reopen")}
    reopened = last["reopen"] and state == "open"
    words = (describe(record) if describe else None) or plain_target_words(record)
    route = ui_route(record)
    return {
        "id": record.get("id"),
        "kind": record.get("kind"),
        "kind_label": KIND_LABELS.get(record.get("kind"), record.get("kind")),
        "ts": record.get("ts"),
        "text": record.get("text") or "",
        "tags": record.get("tags") or [],
        "version": record.get("version"),
        "target": record.get("target"),
        "t": record.get("t"),
        "a": record.get("a"), "b": record.get("b"), "choice": record.get("choice"),
        "target_words": words,
        "route": route,
        "link": (base_url.rstrip("/") + "/" + route) if base_url else route,
        "state": state,
        "waiting_since": last["reopen"]["ts"] if reopened else record.get("ts"),
        "thread": {
            "ack": _event(last["ack"], "id", "producer", "ts", "text"),
            "resolve": _event(last["resolve"], "id", "producer", "ts", "version", "text"),
            "reopen": _event(last["reopen"], "id", "ts", "text"),
            "events": events,
        },
    }


def owner_records(records, pid):
    """The owner's reactions on one job that a producer answers (no thread records, nothing retracted), oldest first."""
    out = [r for r in records if r.get("pid") == pid and r.get("pid") != LIBRARY_PID and r.get("actor") == "owner"
           and r.get("kind") in OWNER_ROOT_KINDS and not r.get("retracted")]
    return sorted(out, key=lambda r: r.get("ts") or "")


def inbox_payload(project, records, states=DEFAULT_STATES, describe=None, base_url=None, now=None):
    """GET /api/inbox: {pid, name, path, base_url, states, counts, items (oldest first), read_at}."""
    items = [inbox_item(record, describe, base_url) for record in owner_records(records, project.id)]
    counts = {state: sum(1 for item in items if item["state"] == state) for state in THREAD_STATES}
    return {
        "pid": project.id, "name": project.name, "path": project.path, "base_url": base_url,
        "states": list(states), "counts": counts, "read_at": now or now_iso(), "cli": CLI_PATH,
        "items": [item for item in items if item["state"] in states],
    }


def compact_item(item):
    """One line of a job's notes for the Inbox: state, target in words and the producer's first sentence."""
    thread = item["thread"]
    answer = thread["resolve"] if item["state"] == "resolved" else thread["ack"] if item["state"] == "picked_up" else None
    return {"id": item["id"], "kind": item["kind"], "kind_label": item["kind_label"], "state": item["state"],
            "reopened": bool(thread["reopen"]) and item["state"] == "open", "target_words": item["target_words"],
            "version": item["version"], "ts": item["ts"], "waiting_since": item["waiting_since"],
            "resolved_in": thread["resolve"]["version"] if thread["resolve"] and item["state"] == "resolved" else None,
            "reply": first_sentence(answer.get("text")) if answer and answer.get("text") else None,
            "producer": answer.get("producer") if answer else None, "text": first_sentence(item["text"])}


def inbox_summary(projects, records, with_items=False, describe=None):
    """GET /api/inbox/summary: per job with any owner reaction {pid, name, path, open, picked_up, resolved,
    oldest_open_ts}, the longest-waiting first; with_items adds `items`, each note as one compact line (newest first)."""
    out = []
    for project in projects:
        items = [inbox_item(record, describe) for record in owner_records(records, project.id)]
        if not items:
            continue
        waiting = sorted(item["waiting_since"] or "" for item in items if item["state"] == "open")
        row = {"pid": project.id, "name": project.name, "path": project.path}
        row.update({state: sum(1 for item in items if item["state"] == state) for state in THREAD_STATES})
        row["oldest_open_ts"] = waiting[0] if waiting else None
        if with_items:
            row["items"] = [compact_item(item) for item in reversed(items)]
        out.append(row)
    out.sort(key=lambda row: (row["oldest_open_ts"] is None, row["oldest_open_ts"] or "", row["name"].lower()))
    return out


def _quote_block(text):
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _item_markdown(item):
    lines = [f"### {item['kind_label']}: {item['target_words']}", ""]
    if item["text"]:
        lines += [f"The owner wrote, {_fmt_ts(item['ts'])}:", "", _quote_block(item["text"]), ""]
    else:
        lines += [f"The owner recorded this {_fmt_ts(item['ts'])}, without text.", ""]
    if item["tags"]:
        lines.append(f"* Tags: {', '.join(item['tags'])}")
    lines.append(f"* Link: {item['link']}")
    lines.append(f"* Id: {item['id']}")
    for event in item["thread"]["events"]:
        lines.append("* " + thread_sentence(event))
    return lines


def inbox_markdown(payload):
    """The producer's inbox for one job: verbatim owner text, target in words, link and id, grouped by state."""
    counts = payload["counts"]
    lines = [f"# Glass Box notes for the producer: {payload['name']}", ""]
    source = (f" from {payload['store']}, read-only, because Glass Box is not running" if payload.get("store")
              else f" from {payload['base_url']}" if payload.get("base_url") else "")
    lines.append(f"{counts['open']} waiting, {counts['picked_up']} picked up, {counts['resolved']} addressed. "
                 f"Read {_fmt_ts(payload.get('read_at'))}{source}.")
    lines.append(f"Job folder: {payload['path']}")
    if not payload["items"]:
        lines += ["", "Nothing waits for the producer."]
    for state in THREAD_STATES:
        if state not in payload["states"]:
            continue
        items = [item for item in payload["items"] if item["state"] == state]
        if not items:
            continue
        lines += ["", f"## {STATE_WORDS[state]} ({len(items)})"]
        for item in items:
            lines += [""] + _item_markdown(item)
    cli = payload.get("cli") or CLI_PATH
    lines += ["", "## How to answer", "",
              f'* Pick a note up, with your plan: python3 "{cli}" --project "{payload["path"]}" --ack <id> '
              '--reply "<plan>" --producer "<your name>"',
              f'* Say which version addresses it: python3 "{cli}" --project "{payload["path"]}" --resolve <id> '
              '--version vN --reply "<what changed>" --producer "<your name>"']
    return "\n".join(lines).rstrip() + "\n"


def summary_markdown(rows, base_url=None, now=None):
    lines = ["# Glass Box notes waiting for producers", ""]
    waiting = [row for row in rows if row["open"] or row["picked_up"]]
    if not waiting:
        lines.append("Nothing waits for a producer.")
    for row in waiting:
        oldest = f", the oldest since {_fmt_ts(row['oldest_open_ts'])}" if row.get("oldest_open_ts") else ""
        lines.append(f"* {row['name']}: {row['open']} waiting{oldest}; {row['picked_up']} picked up; "
                     f"{row['resolved']} addressed. Folder: {row['path']}")
    done = [row for row in rows if not (row["open"] or row["picked_up"])]
    if done:
        lines += ["", f"All addressed: {', '.join(row['name'] for row in done)}."]
    lines += ["", f"Read {_fmt_ts(now or now_iso())}" + (f" from {base_url}." if base_url else ".")]
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["DEFAULT_STATES", "STATE_WORDS", "CLI_PATH", "parse_states", "display_name", "ui_route", "plain_target_words",
           "inbox_item", "owner_records", "inbox_payload", "inbox_summary", "inbox_markdown", "summary_markdown",
           "producer_name"]
