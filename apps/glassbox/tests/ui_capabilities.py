#!/usr/bin/env python3
"""Check that every capability listed in PRODUCT.md is reachable in the Glass Box UI: navigate, open, see.

Run from the app directory with the test dependencies and ffmpeg installed:

    python -B tests/ui_capabilities.py

Creates fictional films, a temporary store and an isolated server. Records reactions to prove the write paths,
checks that project bytes remain unchanged, and stops the server. Never connects to a production instance.
Prints a pass/fail table and exits 1 when a capability or browser error check fails. Not collected by pytest.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

from playwright.sync_api import sync_playwright
from browser_fixtures import isolated_app

BASE = ""
HOST = ""
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.expanduser("~/.copilot/extension-data/vpg-glassbox")
results = []


def api(path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    sent = {"Host": HOST, **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})}
    with urllib.request.urlopen(urllib.request.Request(BASE + path.lstrip("/"), data=data, headers=sent)) as response:
        return json.loads(response.read() or b"null")


def check(capability, what):
    def wrap(fn):
        try:
            ok, detail = fn()
        except Exception as error:  # a failed step is a failed check, with its reason
            ok, detail = False, f"{type(error).__name__}: {str(error).splitlines()[0][:120]}"
        results.append((capability, what, bool(ok), str(detail)))
        return fn
    return wrap


def run_checks(base):
    global BASE, HOST
    BASE = base.rstrip("/") + "/"
    HOST = BASE.split("//", 1)[1].rstrip("/")
    results.clear()
    meta = api("api/meta")
    if os.path.realpath(meta["data_dir"]) == os.path.realpath(DEFAULT_DATA_DIR):
        print(f"Refusing: {BASE} writes to the default data dir {meta['data_dir']}; start a server with a temporary --data-dir.")
        return 2
    films = api("api/films")
    jobs = [r for r in films["rows"] if r["kind"] == "job"]
    job = next(r for r in jobs if r["name"] == "Demo film")
    pid = job["pid"]
    project = api(f"api/projects/{pid}")
    versions = project["versions"]
    v = project["focus_version"]
    old = versions[versions.index(v) - 1]
    release = next(d for d in films["decisions"] if d["kind"] == "release")
    lib = next(r for r in films["rows"] if r["kind"] == "library" and r["name"] == "Water_hub")
    assert job.get("film_id"), "synthetic job must have a library twin"
    room = f"#/f/job-{pid}"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        def go(hash_, wait="main h1"):
            page.goto(BASE + hash_)
            page.wait_for_selector(wait, timeout=25000)
            page.wait_for_timeout(600)

        def text(selector="main"):
            return page.locator(selector).first.inner_text()

        def hidden_shown(scope="body"):
            """Elements marked [hidden] that still render: there must be none."""
            return page.evaluate("""(scope) => [...document.querySelectorAll(`${scope} [hidden]`)]
                .filter((el) => getComputedStyle(el).display !== 'none').length""", scope)

        # ------------------------------------------------------------------ Needs you
        @check("Needs you", "Decisions across jobs, one card each with Decide")
        def _():
            go("#/", ".page-title")
            n = len(films["decisions"])
            title = text("main h1")
            cards = page.locator(".decision").count()
            decide = page.locator(".decision a.btn.decide").count()
            return (cards == n and decide == n and (str(n) in title or (n == 0 and "No decisions" in title)),
                    f"title '{title}', {cards} cards, {decide} Decide")

        @check("Needs you", "FYI groups with reasons (producer, in progress, superseded)")
        def _():
            go("#/", ".groups")
            titles = page.locator(".group > summary .g-title").all_inner_texts()
            group = page.locator("details.group").filter(has=page.locator(".group-item")).first
            detail = "no FYI items now"
            if group.count():
                group.locator("summary").click()
                item = group.locator(".group-item").first
                item.locator(".gi-select").click()
                item.locator(".gi-actions .btn").first.wait_for(timeout=10000)
                detail = item.inner_text().replace("\n", " ")[:80]
                ok_actions = item.locator(".gi-actions .btn").count() >= 1
            else:
                ok_actions = True
            known = ["waiting", "progress", "unreviewed", "superseded"]
            groups = page.locator("details.group").evaluate_all("(els) => els.map(el => el.dataset.group)")
            reasons = page.locator(".group > summary .g-why").count()
            counts = [int(c) for c in page.locator(".group > summary .count").all_inner_texts()]
            ordered = groups == [key for key in known if key in groups]
            return (bool(titles) and all(titles) and ordered and reasons == len(titles) == len(counts)
                    and all(c > 0 for c in counts) and ok_actions,
                    f"{len(titles)} groups shown (empty ones hidden); {detail}")

        # ------------------------------------------------------------------ Jobs
        @check("Jobs", "Pipeline map: every node of the workflow with status")
        def _():
            go(f"{room}?v={v}&tab=pipeline", ".pnode")
            nodes = page.locator(".pnode").count()
            marks = page.locator(".pnode .mark").count()
            paths = page.locator(".pipe-svg path").count()
            return (nodes == len(project["graph"]) and marks == nodes and paths > 0, f"{nodes} nodes ({len(project['graph'])} in workflow), {paths} connectors")

        @check("Jobs", "Provenance per node (engine run, outside the engine)")
        def _():
            page.locator(".pnode").first.click()
            page.wait_for_selector(".drawer .provenance", timeout=15000)
            prov = text(".drawer .provenance")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            return (bool(prov.strip()) and page.locator(".drawer").count() == 0, prov[:80])

        @check("Jobs", "Versions with their marks (version rail and Films row)")
        def _():
            go(f"{room}?v={v}", ".rail .frame-cell")
            frames = page.locator(".rail .frame-cell").count()
            marks = page.locator(".rail .frame-cell .mark").count()
            return (frames == len(versions) and marks == frames, f"{frames} frames, {len(versions)} versions")

        @check("Jobs", "Versions table: date, duration, status, open findings by level, checks, notes, source; A and B to compare")
        def _():
            go(f"{room}?v={v}", ".rail .frame-cell")
            page.get_by_role("button", name="Show the versions as a table").click()
            page.wait_for_selector(".vtable table tbody tr")
            heads = [t.strip().upper() for t in page.locator(".vtable thead th").all_inner_texts()]
            rows = page.locator(".vtable tbody tr").count()
            statuses = page.locator(".vtable tbody tr .mark").count()
            page.get_by_role("checkbox", name=f"Compare {old} as A").check()
            page.get_by_role("checkbox", name=f"Compare {v} as B").check()
            href = page.locator(".vtable-box a", has_text="Compare A and B").get_attribute("href") or ""
            page.locator(".vtable-box a", has_text="Compare A and B").click()
            page.wait_for_selector(".cmp-players", timeout=20000)
            compared = "tab=compare" in page.url and f"a={old}" in page.url and f"b={v}" in page.url
            page.get_by_role("button", name="Show the versions as a table").click()
            wanted = {"VERSION", "DATE", "DURATION", "STATUS", "OPEN FINDINGS", "AUTOMATIC CHECKS", "NOTES", "SOURCE", "A", "B"}
            return (wanted <= set(heads) and rows == len(versions) and statuses >= rows - 1 and compared and "tab=compare" in href,
                    f"{rows} rows ({len(versions)} versions), columns {len(heads)}, compare {old} and {v}: {compared}")

        @check("Jobs", "Engine run card: ledger and telemetry coverage")
        def _():
            go(f"{room}?v={v}&tab=files", "#run-h")
            card = page.locator("details", has_text="Engine run").first
            card.locator(":scope > summary").click()
            ledger = card.locator("table.ledger").count()
            telemetry = card.locator(".telemetry-line").count()
            return (ledger >= 1 and telemetry >= 1, f"ledger tables {ledger}, telemetry lines {telemetry}")

        @check("Jobs", "README head")
        def _():
            card = page.locator("details", has_text="README").first
            card.locator("summary").click()
            return (card.locator("pre").count() == 1, card.locator("pre").inner_text()[:60].replace("\n", " "))

        # ------------------------------------------------------------------ Node detail
        @check("Node detail", "Status with reason, evidence, checks and telemetry, reviews, contract, notes")
        def _():
            go(f"{room}?v={v}&tab=pipeline&n=n13_motion_scenes", ".drawer [role=tab]")
            labels = [t.split("\n")[0].strip() for t in page.locator(".drawer [role=tab]").all_inner_texts()]
            filled = 0
            for index in range(len(labels)):
                page.locator(".drawer [role=tab]").nth(index).click()
                page.wait_for_timeout(250)
                filled += len(page.locator(".drawer [role=tabpanel]").inner_text().strip()) > 0
            status = text(".drawer .status-line")
            reason = page.locator(".drawer .status-line span:not(.mark)").last.inner_text().strip()
            repeats = page.locator(".drawer [role=tabpanel]").get_by_text(reason, exact=True).count() if reason else 0
            shown = hidden_shown(".drawer")
            page.keyboard.press("Escape")
            return ([l.upper() for l in labels] == ["EVIDENCE", "CHECKS", "REVIEWS", "CONTRACT", "NOTES"] and filled == len(labels) and shown == 0
                    and bool(reason) and repeats == 0,
                    f"tabs {labels}; status with reason in the head: '{status[:60]}'; repeated in a tab {repeats}; hidden rendered {shown}")

        # ------------------------------------------------------------------ Version
        @check("Version", "Player with captions")
        def _():
            go(f"{room}?v={v}&tab=watch", ".player video")
            src = page.locator(".player video").get_attribute("src")
            tracks = page.locator(".player video track").count()
            return (bool(src), f"master {src.split('/')[-1] if src else None}, caption tracks {tracks}")

        @check("Version", "Timeline: findings and notes; key frames and captions under More lanes; filmstrip")
        def _():
            lanes = page.locator(".tl-lane").count()
            page.get_by_role("button", name=re.compile("More lanes")).click()
            page.wait_for_timeout(300)
            more = page.locator(".tl-lane").count()
            strip = page.locator(".filmstrip button").count()
            slider = page.locator("[role=slider]").count()
            page.get_by_role("button", name=re.compile("Fewer lanes")).click()
            return (lanes == 2 and more == 4 and slider == 1, f"{lanes} lanes, {more} with more, filmstrip {strip}")

        @check("Version", "Verdicts: one line that opens to each reviewer and the automatic checks")
        def _():
            line = page.locator(".verdict-summary > summary").inner_text()
            page.locator(".verdict-summary > summary").click()
            lines = page.locator(".verdict-summary .verdicts li").evaluate_all("(els) => els.filter((e) => e.offsetParent).length")
            checks = page.locator(".verdicts li", has_text="Automatic checks").count()
            page.locator(".verdict-summary > summary").click()
            return (" pass" in line and "open finding" in line and lines >= 2 and checks == 1, f"'{line.strip()}'; {lines} lines when open")

        @check("Version", "Findings: the first three, Show all with the filter, Agree, Disagree, Note, Details")
        def _():
            section = page.locator("section[aria-labelledby=findings-h]")
            first = section.locator(".finding").count()
            more = section.get_by_role("button", name=re.compile("^Show all"))
            if more.count():
                more.click()
                page.wait_for_timeout(300)
            total = section.locator(".finding").count()
            options = page.locator("#findings-filter option").count()
            shown_before = hidden_shown("main")
            collapsed_actions = section.locator(".finding:not(.open) .f-actions button").evaluate_all("(els) => els.filter((e) => e.offsetParent).length")
            section.locator(".finding .f-select").first.click()
            buttons = section.locator(".finding.open .f-actions button").all_inner_texts()
            return (first <= 3 and total >= first and options >= 2 and {"Agree", "Disagree", "Note", "Details"} <= {b.strip() for b in buttons} and shown_before == 0 and collapsed_actions == 0,
                    f"{first} rows first, {total} after Show all, {options} filter options, actions {buttons}; hidden rendered {shown_before}, actions on collapsed rows {collapsed_actions}")

        @check("Version", "Reviews (scores) and work notes")
        def _():
            reviews = page.locator("details > summary", has_text="Scores and full reviews").count()
            notes = page.locator("details.work-notes").count()
            return (reviews == 1, f"reviews disclosure {reviews}, work notes {notes}")

        @check("Version", "Release decision (N23) with its caveat, Approve confirms")
        def _():
            if not release:
                return True, "no release waits now"
            go(f"#/f/{release['film_id']}?v={release['version']}&tab=decide&g={release['node']}", ".decide-band")
            caveat = page.locator("#room-panel .caveat").inner_text()
            shown = hidden_shown("main")
            reason_visible = page.locator(".band-reason textarea").is_visible()
            approve_box = page.get_by_role("button", name="Approve").bounding_box()
            page.get_by_role("button", name="Approve").click()
            page.wait_for_selector("dialog[open]")
            summary = page.locator("dialog[open]").inner_text()
            page.locator("dialog[open]").get_by_role("button", name="Cancel").click()
            return ("don't judge taste" in caveat and release["version"] in summary and "open finding" in summary and shown == 0
                    and not reason_visible and approve_box and approve_box["y"] + approve_box["height"] <= 900,
                    "confirm: " + next((l for l in summary.splitlines() if release["version"] in l), summary)[:60]
                    + f"; Approve at {round(approve_box['y'])} px; reason box hidden {not reason_visible}; hidden rendered {shown}")

        # ------------------------------------------------------------------ Gates
        @check("Gates", "Human gates (state, recorded decision), automatic and reviewer gates")
        def _():
            go(f"{room}?v={v}&tab=pipeline", "#hg-h")
            human = page.locator(".gate-list").first.locator("li").count()
            automatic = page.locator("details summary", has_text=re.compile("automatic checks")).count()
            reviewers = page.locator("details summary", has_text="Reviewer gates").count()
            return (human >= 1 and automatic == 1 and reviewers == 1, f"{human} human gates")

        # ------------------------------------------------------------------ Compare
        @check("Compare", "Two players in sync; what changed with readings, runtime targets, basis warnings")
        def _():
            go(f"{room}?tab=compare&a={old}&b={v}", ".cmp-players video")
            videos = page.locator(".cmp-players video").count()
            cells = page.locator(".wc-cell").count()
            body = text()
            return (videos == 2 and page.get_by_role("button", name=re.compile("Play both")).count() == 1 and cells >= 3
                    and "target" in body and page.locator(".pol-basis").count() >= 1,
                    f"{cells} cells; summary '{page.locator('.wc-summary').first.inner_text()[:70]}'")

        @check("Compare", "Score deltas, verdicts, metric deltas")
        def _():
            names = [s for s in page.locator("details.card > summary").all_inner_texts()]
            return (any("Score changes" in s for s in names) and any("Verdicts" in s for s in names) and any("Metric changes" in s for s in names), " | ".join(n.split("\n")[0] for n in names))

        @check("Compare", "Preference in two steps (A, B or tie), with Undo")
        def _():
            page.locator(".prefer-choice", has_text="B,").click()
            page.get_by_role("button", name="Record preference").click()
            page.wait_for_selector(".prefer-box .undo-line", timeout=10000)
            listed = page.locator(".prefer-box .reaction").count()
            page.locator(".prefer-box .undo-line button").click()
            page.wait_for_timeout(1200)
            return (listed >= 1 and "Undone" in page.locator(".prefer-box").inner_text(), f"{listed} listed before undo")

        # ------------------------------------------------------------------ Reactions on a version
        @check("Reactions", "Note at a moment (N), listed, then undone")
        def _():
            go(f"{room}?v={v}&tab=watch", ".player video")
            page.locator("main h1").click()
            page.keyboard.press("n")
            page.wait_for_selector(".note-slot textarea")
            marker = f"Capability check: a note at this moment {os.getpid()}"
            page.locator(".note-slot textarea").fill(marker)
            page.locator(".note-slot button[type=submit]").click()
            page.wait_for_selector(".note-slot .undo-line button", timeout=10000)
            listed = page.locator(".reactions .reaction", has_text=marker).count()
            page.locator(".note-slot .undo-line button").click()
            page.wait_for_timeout(1200)
            gone = page.locator(".reactions .reaction", has_text=marker).count()
            return (listed == 1 and gone == 0, f"listed {listed}, after undo {gone}")

        @check("Reactions", "Agree and disagree on a finding, with Undo")
        def _():
            row = page.locator("section[aria-labelledby=findings-h] .finding").first
            row.locator(".f-select").click()
            row.get_by_role("button", name=re.compile("^Agree with")).click()
            row.locator("button[type=submit]").click()
            row.locator(".undo-line button").wait_for(timeout=10000)
            row.locator(".undo-line button").click()
            page.wait_for_timeout(800)
            row.get_by_role("button", name=re.compile("^Disagree with")).click()
            disagree = row.locator(".composer .kind[aria-pressed=true]").inner_text()
            records = api(f"api/reactions?pid={pid}&include_retracted=1")
            agreed = [r for r in records if r["kind"] == "agree"]
            undone = any(r.get("retracted") for r in agreed)
            return undone and "Disagree" in disagree, f"agree saved and undone: {undone}; disagree ready: {disagree.strip()}"

        @check("Reactions", "Agree with all P4 in one batch (with one confirmation)")
        def _():
            more = page.locator("section[aria-labelledby=findings-h]").get_by_role("button", name=re.compile("^Show all"))
            if more.count():
                more.click()
                page.wait_for_timeout(300)
            bulk = page.get_by_role("button", name=re.compile(r"Agree with all P4"))
            if not bulk.count():
                return False, "synthetic P4 findings must expose the batch action"
            bulk.click()
            page.wait_for_selector("dialog[open]")
            dialog = page.locator("dialog[open]").inner_text()
            page.locator("dialog[open]").get_by_role("button", name="Cancel").click()
            return ("P4" in dialog, dialog.splitlines()[0][:80])

        @check("Reactions", "Flag a node and note on a gate")
        def _():
            go(f"{room}?v={v}&tab=pipeline&n=n13_motion_scenes", ".drawer [role=tab]")
            page.locator(".drawer").get_by_role("tab", name=re.compile("^Notes")).click()
            page.locator(".drawer .composer .kind", has_text="Flag").click()
            page.locator(".drawer .composer textarea").fill("Capability check: flag")
            page.locator(".drawer .composer button[type=submit]").click()
            page.locator(".drawer .undo-line button").wait_for(timeout=10000)
            page.locator(".drawer .undo-line button").click()
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            note = page.get_by_role("button", name=re.compile("^Note on ")).first
            note.click()
            slot = page.locator("#" + note.get_attribute("aria-controls"))
            slot.locator("textarea").wait_for(timeout=5000)
            inline = page.locator("dialog[open]").count() == 0 and note.get_attribute("aria-expanded") == "true"
            slot.locator("textarea").fill("Capability check: a note on a gate")
            slot.locator("button[type=submit]").click()
            slot.locator(".undo-line button").wait_for(timeout=10000)
            slot.locator(".undo-line button").click()
            page.wait_for_timeout(800)
            note.click()
            closed = note.get_attribute("aria-expanded") == "false" and slot.locator(".composer").count() == 0
            return (inline and closed, "flag saved and undone; gate note composed inline, saved and undone")

        @check("Reactions", "Request changes on a release; the producer's pickup shows in Activity")
        def _():
            if not release:
                return True, "no release waits now"
            go(f"#/f/{release['film_id']}?v={release['version']}&tab=decide&g={release['node']}", ".decide-band")
            page.get_by_role("button", name=re.compile("Request changes")).click()
            page.locator(".band-reason textarea").fill("Capability check: please tighten the ending")
            page.get_by_role("button", name=re.compile("Send the request")).click()
            page.wait_for_selector("dialog[open]")
            before = page.locator(".decide-band .band-effect").inner_text()
            page.locator("dialog[open]").get_by_role("button", name="Request changes").click()
            page.wait_for_function("() => /was recorded/.test(document.querySelector('.decide-band .band-effect').textContent)", timeout=10000)
            recorded = page.locator(".decide-band").inner_text()
            records = api(f"api/reactions?pid={release['pid']}&kind=request_changes")
            api("api/reactions", {"pid": release["pid"], "kind": "ack", "ref": records[0]["id"], "text": "Capability check pickup"}, {"X-Actor": "agent"})
            go("#/activity", ".reactions")
            pickup = page.locator(".reaction", has_text="please tighten").locator(".pickup").first.inner_text()
            return ("recorded" in recorded.lower() and before.startswith("Requesting changes records") and "picked up" in pickup.lower(),
                    f"before: '{before[:40]}'; {pickup.strip()[:60]}")

        # ------------------------------------------------------------------ Activity
        @check("Activity", "Pickup summary first, Waiting / Picked up / Addressed, filters, Copy for chat and Download .md")
        def _():
            go("#/activity", ".filters")
            summary = page.locator(".activity-summary").inner_text()
            seg = [t.split()[0] if t.split() else t for t in page.locator(".seg[aria-label='Where the producer is'] button").all_inner_texts()]
            labels = page.locator(".filters label").all_inner_texts()
            items = page.locator(".reactions .reaction").count()
            copy_enabled = page.get_by_role("button", name="Copy for chat").is_enabled()
            href = page.locator("a", has_text="Download .md").get_attribute("href")
            return (re.match(r"^\d+ notes?: ", summary) is not None and len(seg) == 4
                    and {"FILM", "VERSION", "ABOUT", "KIND", "RETRACTED"} <= {l.strip().upper() for l in labels} and items >= 1 and copy_enabled and bool(href),
                    f"'{summary}'; {items} items; pickup filter {seg}; filters {labels}")

        # ------------------------------------------------------------------ Library
        @check("Library", "Films and versions with search, filters, sort, Rescan")
        def _():
            go("#/films", ".film-row")
            visible = {re.match(r"[A-Za-z]+", t.strip()).group(0).title() for t in page.locator(".sheet-tools > .seg button").all_inner_texts()}
            page.locator(".view-menu > summary").click()
            rescan = page.get_by_role("button", name="Rescan").count()
            sort = page.locator("#films-sort").is_visible()
            page.get_by_role("button", name=re.compile("^Library")).click()
            page.wait_for_timeout(500)
            url_filter = "filter=library" in page.url
            heads = page.locator(".film-row .row-meta .label").all_inner_texts()
            page.locator(".view-menu > summary").click()
            page.locator("#films-q").fill("demo-orbit")
            page.wait_for_timeout(600)
            fuse = page.locator(".film-row").count()
            page.locator("#films-q").fill("")
            page.get_by_role("button", name=re.compile("^All")).click()
            page.wait_for_timeout(300)
            return (visible == {"All", "Needs"} and rescan == 1 and sort and url_filter and all(h != "Job" for h in heads) and fuse >= 1,
                    f"visible {sorted(visible)}; View menu has sort and Rescan; URL mirrors filter {url_filter}; {len(heads)} library rows; 'demo-orbit' finds {fuse}")

        @check("Library", "Every file in a list, and Other videos collections")
        def _():
            page.locator(".view-menu > summary").click()
            page.get_by_role("button", name="List").click()
            page.wait_for_selector(".list-table tbody tr", timeout=15000)
            rows = page.locator(".list-table tbody tr").count()
            page.get_by_role("button", name="Sheet").click()
            page.locator(".view-menu > summary").click()
            page.wait_for_timeout(300)
            page.locator("details.other-box > summary").click()
            page.locator("details.coll > summary").first.click()
            page.wait_for_selector("details.coll[open] .lib-row", timeout=15000)
            items = page.locator("details.coll[open] .lib-row").count()
            return (rows >= 1 and items >= 1, f"{rows} files listed, {items} in the first collection")

        @check("Library", "Film page: player, variants, files and copies, compare, notes")
        def _():
            if not lib:
                return True, "library off"
            go(f"#/f/{lib['id']}", ".player")
            player = page.locator(".player video").count()
            variants = page.locator("[aria-label^='Files of'] button").count()
            notes = page.locator(".composer .kind", has_text=re.compile("^Note at ")).count()
            page.locator("[role=tab]", has_text="Files").click()
            page.wait_for_selector(".files-table tbody tr")
            files = page.locator(".files-table tbody tr").count()
            page.locator("[role=tab]", has_text="Compare").click()
            page.wait_for_selector(".cmp-players")
            players = page.locator(".cmp-players .player").count()
            return (player == 1 and notes == 1 and files >= 1 and players == 2, f"variants {variants}, files {files}, compare players {players}")

        @check("Library", "Note on a library video at a moment")
        def _():
            if not lib:
                return True, "library off"
            go(f"#/f/{lib['id']}", ".player")
            page.locator(".composer .kind", has_text=re.compile("^Note at ")).click()
            page.locator(".composer textarea").fill("Capability check: library note")
            page.locator(".composer button[type=submit]").click()
            page.wait_for_selector(".composer .undo-line button", timeout=10000)
            listed = page.locator(".reaction", has_text="Capability check: library note").count()
            page.locator(".composer .undo-line button").click()
            page.wait_for_timeout(800)
            return (listed == 1, "saved and undone")

        # ------------------------------------------------------------------ Keyboard and navigation
        @check("Keyboard", "Space, Left and Right, N, G, C, D, /, ?, Esc")
        def _():
            go(f"{room}?v={v}&tab=watch", ".player video")
            page.locator("main h1").click()
            video = page.locator(".player video")
            page.keyboard.press("Space")
            page.wait_for_timeout(1200)
            playing = not video.evaluate("v => v.paused")
            page.keyboard.press("Space")
            before = video.evaluate("v => v.currentTime")
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(400)
            after = video.evaluate("v => v.currentTime")
            page.keyboard.press("Shift+ArrowRight")
            page.wait_for_timeout(400)
            after5 = video.evaluate("v => v.currentTime")
            page.keyboard.press("?")
            help_open = page.locator("dialog[open]").count() == 1
            page.keyboard.press("Escape")
            page.keyboard.press("/")
            search = page.evaluate("document.activeElement && document.activeElement.id")
            page.locator("main h1").click()
            page.keyboard.press("g")
            page.wait_for_timeout(1200)
            on_pipe = "tab=pipeline" in page.url
            page.keyboard.press("c")
            page.wait_for_timeout(1200)
            on_cmp = "tab=compare" in page.url
            page.keyboard.press("d")
            page.wait_for_timeout(1200)
            on_dec = "tab=decide" in page.url or not job["needs"]
            ok = playing and after - before > 0.5 and after5 - after > 4 and help_open and search == "search-input" and on_pipe and on_cmp and on_dec
            return ok, f"play {playing}, seek +{after - before:.1f} then +{after5 - after:.1f}, help {help_open}, / -> {search}, G {on_pipe}, C {on_cmp}, D {on_dec}"

        @check("Navigation", "Search across films, versions and files")
        def _():
            go("#/", ".page-title")
            page.keyboard.press("/")
            page.keyboard.type(f"{v} {job['name'].split()[0]}")
            page.wait_for_selector("#search-list [role=option]")
            options = page.locator("#search-list [role=option]").count()
            files = page.locator("#search-list .files-option").count()
            page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            film_url = page.url
            page.keyboard.press("/")
            page.keyboard.type("demo-orbit")
            page.wait_for_selector("#search-list .files-option")
            page.locator("#search-list .files-option").click()
            page.wait_for_selector(".list-table tbody tr", timeout=20000)
            listed = page.locator(".list-table tbody tr").count()
            page.locator(".view-menu > summary").click()
            page.get_by_role("button", name="Sheet").click()
            return ("#/f/" in film_url and options >= 2 and files == 1 and listed >= 1,
                    f"{options - 1} matches -> {film_url.split('#')[1][:40]}; 'demo-orbit' files in List: {listed}")

        @check("Navigation", "A library film or file that belongs to a job opens in the job's Files, highlighted")
        def _():
            twin = job.get("film_id")
            if not twin:
                return True, "this job has no library twin"
            go(f"#/f/lib-{twin}", ".selected-file")
            room_url = page.url.split("#")[1]
            heading = page.locator(".selected-file h2").inner_text()
            film = api(f"api/library/films/{twin}")
            item = film["versions"][0]["primary"]
            go(f"#/v/{item['id']}", ".selected-file")
            video_url = page.url.split("#")[1]
            go("#/films?mode=list&q=" + item["name"].split(".")[0], ".list-table tbody tr")
            hrefs = page.locator(".list-table tbody tr a").evaluate_all("(els) => els.map((a) => a.getAttribute('href'))")
            in_job = [h for h in hrefs if h.startswith(f"#/f/job-{pid}") and "tab=files" in h and "file=" in h]
            twin_rooms = [h for h in hrefs if h.startswith(f"#/f/lib-{twin}")]
            ok = (room_url.startswith(f"/f/job-{pid}?") and "tab=files" in room_url and "file=" in room_url and heading.startswith("Selected file")
                  and video_url.startswith(f"/f/job-{pid}?") and in_job and not twin_rooms)
            return ok, f"lib room -> {room_url[:60]}; #/v -> {video_url[:50]}; List links into the job {len(in_job)}, to the twin {len(twin_rooms)}"

        # ------------------------------------------------------------------ Producer channel
        @check("Producer channel", "A note waits, the producer picks it up and addresses it, the owner reopens it; the thread shows in Activity and on the Inbox")
        def _():
            note = api("api/reactions", {"pid": pid, "kind": "note", "text": "Capability check: producer channel",
                                         "target": {"type": "version", "id": v}})
            go("#/", ".page-title")
            producers = page.locator(".producers").inner_text() if page.locator(".producers").count() else ""
            go("#/activity", ".reactions")
            row = page.locator(".reaction", has_text="Capability check: producer channel").first
            waiting = row.locator(".pickup").inner_text()
            api("api/reactions", {"pid": pid, "kind": "ack", "ref": note["id"], "text": "Capability plan", "producer": "Capability producer"}, {"X-Actor": "agent"})
            api("api/reactions", {"pid": pid, "kind": "resolve", "ref": note["id"], "version": v, "text": "Capability fix", "producer": "Capability producer"}, {"X-Actor": "agent"})
            go("#/activity", ".reactions")
            row = page.locator(".reaction", has_text="Capability check: producer channel").first
            thread = row.locator(".thread").inner_text()
            link = row.locator(".th-resolve a").get_attribute("href")
            row.get_by_role("button", name="Reopen").click()
            row.locator(".reopen-form textarea").fill("Capability check: still wrong")
            row.get_by_role("button", name=re.compile("Reopen for the producer")).click()
            page.wait_for_timeout(1500)
            go("#/activity", ".reactions")
            after = page.locator(".reaction", has_text="Capability check: producer channel").first.locator(".pickup").inner_text()
            state = next(i for i in api(f"api/inbox?pid={pid}")["items"] if i["id"] == note["id"])["state"]
            ok = ("waiting" in producers.lower() and "waiting" in waiting.lower() and "Picked up by Capability producer" in thread
                  and f"Addressed in {v}" in thread and link and f"v={v}" in link and "reopened" in after.lower() and state == "open")
            return ok, f"Inbox producers line {'yes' if producers else 'no'}; '{waiting.strip()}' -> thread with pickup and fix -> '{after.strip()}' (inbox state {state})"

        @check("Producer channel", "inbox.py round trip on this server's store: list, ack, resolve, then the owner reopens")
        def _():
            folder = project["path"]
            note = api("api/reactions", {"pid": pid, "kind": "note", "text": "Capability check: CLI round trip",
                                         "target": {"type": "version", "id": v}})
            run = lambda *args: subprocess.run([sys.executable, "-B", os.path.join(APP, "inbox.py"), "--base", BASE.rstrip("/"), "--project", folder, *args],
                                               capture_output=True, text=True, timeout=60)  # noqa: E731
            listed = run()
            ack = run("--ack", note["id"], "--reply", "Capability plan", "--producer", "Capability CLI")
            resolve = run("--resolve", note["id"], "--version", v, "--reply", "Capability fix", "--producer", "Capability CLI")
            state = json.loads(run("--json", "--state", "all").stdout)
            item = next(i for i in state["items"] if i["id"] == note["id"])
            resolve_id = item["thread"]["resolve"]["id"]
            api("api/reactions", {"pid": pid, "kind": "reopen", "ref": resolve_id, "text": "Capability check: reopened"})
            after = next(i for i in api(f"api/inbox?pid={pid}&state=all")["items"] if i["id"] == note["id"])
            ok = (listed.returncode == 0 and note["id"] in listed.stdout and "> Capability check: CLI round trip" in listed.stdout
                  and ack.returncode == 0 and resolve.returncode == 0 and item["state"] == "resolved"
                  and item["thread"]["ack"]["producer"] == "Capability CLI" and after["state"] == "open")
            return ok, f"list {listed.returncode}, ack {ack.returncode}, resolve {resolve.returncode} -> {item['state']}; reopened -> {after['state']}"

        @check("Navigation", "Old deep links redirect; unknown versions say so")
        def _():
            go(f"#/p/{pid}/v/{v}?t=12", ".room-head")
            redirected = page.url.split("#")[1]
            go(f"{room}?v=v999", ".room-error")
            alert = text(".room-error")
            return (redirected == f"/f/job-{pid}?v={v}&tab=watch&t=12" and "not part of this film" in alert, f"{redirected}; '{alert.splitlines()[0]}'")

        browser.close()
    width = max(len(c) for c, _, _, _ in results)
    print(f"{'Capability'.ljust(width)}  Result  Check (detail)")
    for capability, what, ok, detail in results:
        print(f"{capability.ljust(width)}  {'PASS  ' if ok else 'FAIL  '}  {what} ({detail})")
    failed = sum(1 for r in results if not r[2])
    print(f"\n{len(results) - failed} of {len(results)} checks pass; browser errors: {len(errors)}")
    for error in errors:
        print(f"BROWSER ERROR: {error}")
    return 1 if failed or errors else 0


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    with isolated_app() as base:
        return run_checks(base)


if __name__ == "__main__":
    raise SystemExit(main())
