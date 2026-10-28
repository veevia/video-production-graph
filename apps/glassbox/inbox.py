#!/usr/bin/env python3
"""Producer inbox for VPG Glass Box: read the owner's notes on a job and answer them.

    python3 inbox.py --project <vpg project dir>            # open and picked-up notes as Markdown
    python3 inbox.py --project <dir> --json
    python3 inbox.py --project <dir> --ack <id> --reply "<plan>" [--producer "<name>"]
    python3 inbox.py --project <dir> --resolve <id> --version vN --reply "<what changed>" [--producer "<name>"]
    python3 inbox.py --all                                  # every job: who is waiting, and since when

Talks to the Glass Box server (http://127.0.0.1:4420, or --base). When the server is down, listing reads the reaction
store directly and read-only; answers need the server, which validates them and appends them to its private store.
Never writes into a project. Exit codes: 0 ok, 3 nothing open, 2 error. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from glassbox import fsio  # noqa: E402
from glassbox.discovery import PROJECT_FILE, project_id  # noqa: E402
from glassbox.producer import (inbox_markdown, inbox_payload, inbox_summary, parse_states,  # noqa: E402
                               summary_markdown)
from glassbox.reactions import MAX_PRODUCER_CHARS, MAX_TEXT_CHARS, ReactionStore  # noqa: E402

DEFAULT_BASE = "http://127.0.0.1:4420"
DEFAULT_DATA_DIR = "~/.copilot/extension-data/vpg-glassbox"
EXIT_OK, EXIT_ERROR, EXIT_NOTHING = 0, 2, 3
TIMEOUT = 10


class Down(Exception):
    """The server did not answer."""


class Refused(Exception):
    """The server answered with an error."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _request(base, method, path, body=None, actor=None):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if actor:
        request.add_header("X-Actor", actor)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read().decode("utf-8")).get("error") or error.reason
        except (ValueError, AttributeError):
            message = error.reason
        raise Refused(error.code, str(message)) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
        raise Down(str(getattr(error, "reason", error))) from None


def _inbox_get(base, path):
    try:
        return _request(base, "GET", path)
    except Refused as error:
        if error.status == 404 and str(error) == "not found":
            raise Refused(404, "this Glass Box server has no producer inbox yet; restart it on the current code") from None
        raise


def _start_hint(args):
    address = urllib.parse.urlsplit(args.base)
    if address.scheme != "http" or address.hostname not in ("127.0.0.1", "localhost"):
        return "Glass Box is not running: use a loopback HTTP --base, such as http://127.0.0.1:4420."
    folder = _project_root(args.project) if args.project else None
    folder = folder or (os.path.abspath(os.path.expanduser(args.project)) if args.project else "<project directory>")
    command = ["python3", "-B", os.path.join(APP_DIR, "server.py"), "--root", folder,
               "--port", str(address.port or 80),
               "--data-dir", os.path.abspath(os.path.expanduser(args.data_dir))]
    return (f"Glass Box is not running: start it with {shlex.join(command)}; "
            "add --engine DIR outside a VPG checkout, then retry.")


def _project_root(folder):
    """The folder holding vpg.project.yaml at or above folder, or None."""
    current = os.path.abspath(os.path.expanduser(folder))
    while True:
        if os.path.isfile(os.path.join(current, PROJECT_FILE)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _offline_project(folder):
    root = _project_root(folder)
    if root is None:
        raise Refused(404, f"no {PROJECT_FILE} at or above {folder}")
    meta = fsio.read_yaml(os.path.join(root, PROJECT_FILE))
    name = meta.get("name") if isinstance(meta, dict) and isinstance(meta.get("name"), str) else os.path.basename(root)
    return SimpleNamespace(id=project_id(root), name=name, path=root)


def _offline_store(args):
    data_dir = os.path.abspath(os.path.expanduser(args.data_dir))
    print(f"Glass Box is not running at {args.base}; reading {os.path.join(data_dir, 'reactions.jsonl')} directly "
          "(read-only).", file=sys.stderr)
    return ReactionStore(data_dir)


def list_project(args, states):
    folder = os.path.abspath(os.path.expanduser(args.project))
    query = urllib.parse.urlencode({"project": folder, "state": ",".join(states)})
    try:
        payload = _inbox_get(args.base, f"/api/inbox?{query}")
    except Down:
        project = _offline_project(folder)
        store = _offline_store(args)
        payload = inbox_payload(project, store.query(pid=project.id), states, base_url=args.base.rstrip("/") + "/")
        payload["store"] = store.path
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else inbox_markdown(payload), end="" if not args.json else "\n")
    return EXIT_OK if payload["items"] else EXIT_NOTHING


def list_all(args):
    try:
        payload = _inbox_get(args.base, "/api/inbox/summary")
        rows = payload["jobs"]
    except Down:
        store = _offline_store(args)
        records = store.query()
        folders = sorted({r.get("project_path") for r in records if isinstance(r.get("project_path"), str)})
        projects = []
        for folder in folders:
            try:
                projects.append(_offline_project(folder))
            except Refused:
                continue
        rows = inbox_summary(projects, records)
    if args.json:
        print(json.dumps({"jobs": rows}, indent=2, ensure_ascii=False))
    else:
        print(summary_markdown(rows, base_url=args.base.rstrip("/") + "/"), end="")
    return EXIT_OK if any(row["open"] or row["picked_up"] for row in rows) else EXIT_NOTHING


def answer(args):
    """--ack or --resolve through the server (it validates, and appends to its private store)."""
    kind = "ack" if args.ack else "resolve"
    ref = args.ack or args.resolve
    reply = (args.reply or "").strip()
    if not reply:
        raise Refused(400, f"--{kind} needs --reply: {'your plan' if kind == 'ack' else 'what changed'}")
    if len(reply) > MAX_TEXT_CHARS:
        raise Refused(400, f"--reply is longer than {MAX_TEXT_CHARS} characters")
    if args.producer and len(args.producer) > MAX_PRODUCER_CHARS:
        raise Refused(400, f"--producer is longer than {MAX_PRODUCER_CHARS} characters")
    if kind == "resolve" and not args.version:
        raise Refused(400, "--resolve needs --version, the version that addresses the note")
    folder = os.path.abspath(os.path.expanduser(args.project))
    try:
        inbox = _inbox_get(args.base, "/api/inbox?" + urllib.parse.urlencode({"project": folder, "state": "all"}))
    except Down:
        raise Down(_start_hint(args)) from None
    item = next((i for i in inbox["items"] if i["id"] == ref), None)
    if item is None:
        raise Refused(404, f"no owner note {ref} on {inbox['name']}; list the inbox to see the ids")
    body = {"pid": inbox["pid"], "kind": kind, "ref": ref, "text": reply}
    if args.producer:
        body["producer"] = args.producer.strip()
    if kind == "resolve":
        body["version"] = args.version
    try:
        record = _request(args.base, "POST", "/api/reactions", body, actor="agent")
    except Down:
        raise Down(_start_hint(args)) from None
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    elif kind == "ack":
        print(f"Picked up {ref} ({item['kind_label']}: {item['target_words']}) on {inbox['name']}. Recorded {record['ts']}.")
    else:
        print(f"Marked {ref} ({item['kind_label']}: {item['target_words']}) as addressed in {args.version} on "
              f"{inbox['name']}. Recorded {record['ts']}. The owner sees it, and can reopen it.")
    return EXIT_OK


def parse(argv):
    parser = argparse.ArgumentParser(prog="inbox.py", description="Read and answer the owner's Glass Box notes on a job.")
    parser.add_argument("--project", metavar="DIR", help="the job folder (holds vpg.project.yaml), or a folder inside it")
    parser.add_argument("--all", action="store_true", help="every job's waiting notes: counts and the oldest")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--state", default=None, help="open, picked_up, resolved, all, or a comma list (default open,picked_up)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--ack", metavar="ID", help="pick a note up; --reply carries your plan")
    action.add_argument("--resolve", metavar="ID", help="say a version addresses a note; needs --version and --reply")
    parser.add_argument("--version", metavar="VN", help="with --resolve: the version that addresses the note")
    parser.add_argument("--reply", metavar="TEXT", help="your plan (--ack) or what changed (--resolve)")
    parser.add_argument("--producer", metavar="NAME", help=f"who answers, at most {MAX_PRODUCER_CHARS} characters "
                                                          "(for example 'Film producer (Copilot chat)')")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"the Glass Box server (default {DEFAULT_BASE})")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="where the server keeps reactions.jsonl, read only "
                                                                     "when the server is down (default %(default)s)")
    args = parser.parse_args(argv)
    if not args.all and not args.project:
        parser.error("give --project <job folder>, or --all")
    if args.all and (args.ack or args.resolve or args.project):
        parser.error("--all lists every job; it does not take --project, --ack or --resolve")
    if args.version and not args.resolve:
        parser.error("--version goes with --resolve")
    if (args.reply or args.producer) and not (args.ack or args.resolve):
        parser.error("--reply and --producer go with --ack or --resolve")
    return args


def main(argv=None):
    try:
        args = parse(argv)
    except SystemExit as exit_:
        return EXIT_ERROR if exit_.code else EXIT_OK
    try:
        if args.ack or args.resolve:
            return answer(args)
        if args.all:
            return list_all(args)
        try:
            states = parse_states(args.state)
        except ValueError as error:
            raise Refused(400, str(error)) from None
        return list_project(args, states)
    except Down as error:
        print(str(error) if str(error).startswith("Glass Box is not running") else _start_hint(args), file=sys.stderr)
        return EXIT_ERROR
    except Refused as error:
        print(f"inbox.py: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
