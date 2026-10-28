"""Iteration 5.2: library items that belong to a job, decision facts and node names, the brief's runtime with its
node, each version's runtime window, and the producer channel (ack, resolve, reopen, GET /api/inbox, inbox.py)."""
import json
import socket

import pytest

import inbox
from glassbox.producer import display_name, parse_states, ui_route
from glassbox.reactions import ReactionError, export_markdown, thread_state, validate_reaction
from glassbox.web import make_server
from synthetic import update_state, write_engine, write_json
from test_iteration5 import call, get_json, live, post, start, stop  # noqa: F401  (the live server fixture)
from test_library import make_library_tree
from test_reactions import Ctx

NOTE = {"type": "version", "id": "v10"}


# ------------------------------------------------------------------------------------------------ 5.2 server fields
def test_library_items_inside_a_job_folder_carry_its_pid(tmp_path):
    home = tmp_path / "home"
    make_library_tree(home)
    projects_root = home / "GitHub" / "Demo_workspace" / "01_Project-Hubs"
    server = make_server([str(projects_root)], str(write_engine(tmp_path)), str(tmp_path / "data"), port=0, quiet=True,
                         library_roots=[str(home / "GitHub"), str(home / "Downloads")], library_home=str(home))
    server.app.registry.scan()
    server.app.library.scan_now()
    thread = start(server)
    ctx = {"port": server.app.port}
    try:
        job_pid = server.app.registry.projects()[0].id
        items = get_json(ctx, "/api/library/items?limit=200")["items"]
        inside = [i for i in items if "/Demo_hub/video/vpg/" in i["path"]]
        outside = [i for i in items if "/Water_hub/" in i["path"] or "/Archive/" in i["path"]]
        assert inside and all(i["job_pid"] == job_pid for i in inside)
        assert outside and all(i.get("job_pid") is None for i in outside)
        films = get_json(ctx, "/api/films")
        water = next(r for r in films["rows"] if r["name"] == "Water_hub")
        assert water["counts_text"].endswith("files") or " 1 file" in water["counts_text"]
        assert "file(s)" not in json.dumps(films)
    finally:
        stop(server, thread)


def test_films_decisions_carry_node_names_and_facts(live):
    row = get_json(live, "/api/films")["rows"][0]
    gate = next(d for d in row["decisions"] if d["kind"] == "gate")
    assert gate["node_name"] == "Synthetic node 3" and gate["node_short"] == "Objective audience"
    findings = next(d for d in row["decisions"] if d["kind"] == "findings")
    assert findings["facts"] == {"reviews": 5, "passing": 4, "open_levels": {"P4": 3, "P3": 1}, "duration_s": 80.5}
    assert row["brief_runtime"] == {}


def test_brief_runtime_names_its_node_and_versions_carry_their_runtime_window(live):
    job = live["job"]
    write_json(job / "work" / "n03_objective_audience" / "a1" / "brief.json",
               {"objective": "One thing well.", "runtime_target_s": 60.0, "runtime_max_s": 65.0})

    def add(state):
        state["artifacts"] = {"brief@n03_objective_audience#a1": {
            "key": "brief", "path": "work/n03_objective_audience/a1/brief.json", "node": "n03_objective_audience",
            "status": "current", "sha256": "b1"}}
    update_state(job, add)
    detail = get_json(live, f"/api/projects/{live['pid']}")
    assert detail["brief_runtime"] == {"runtime_target_s": 60.0, "runtime_max_s": 65.0, "node": "n03_objective_audience"}
    assert get_json(live, "/api/films")["rows"][0]["brief_runtime"]["runtime_max_s"] == 65.0
    window = get_json(live, f"/api/projects/{live['pid']}/candidates/v10")["runtime_window"]
    assert window["label"] and window["source"]


@pytest.mark.parametrize("name, short, shown", [
    ("Objective and audience definition", "Objective audience", "Objective and audience"),
    ("Motion and scene production", "Motion scenes", "Motion and scenes"),
    ("Post-production learning and memory updates", "Learning memory", "Learning and memory"),
    ("Context discovery and intake", "Context intake", "Context intake"),
    ("Product and experience understanding", "Product understanding", "Product understanding"),
    ("Final verification", "Final verification", "Final verification"),
])
def test_display_name_keeps_the_and_of_the_full_name(name, short, shown):
    assert display_name(name, short) == shown


# ------------------------------------------------------------------------------------------------ C8.1 threads
class ThreadCtx(Ctx):
    def is_retracted(self, reaction_id):
        return reaction_id in self.store.retractions()

    def thread_events(self, reaction_id):
        return self.store.thread_of(reaction_id)


@pytest.fixture
def tctx(project, store):
    return ThreadCtx(project, store)


def add(tctx, body, actor="owner"):
    return tctx.store.append(validate_reaction(dict(body, pid=tctx._project.id), actor, tctx))


def test_ack_resolve_reopen_rules(tctx):
    note = add(tctx, {"kind": "note", "target": NOTE, "text": "Too loud at the end"})
    with pytest.raises(ReactionError, match="only post ack and resolve"):
        add(tctx, {"kind": "note", "target": NOTE, "text": "x"}, actor="agent")
    with pytest.raises(ReactionError, match="resolve requires the header X-Actor: agent"):
        add(tctx, {"kind": "resolve", "ref": note["id"], "version": "v10", "text": "done"})
    with pytest.raises(ReactionError, match="producer applies to ack and resolve only"):
        add(tctx, {"kind": "note", "target": NOTE, "text": "x", "producer": "me"})
    with pytest.raises(ReactionError, match="longer than 80"):
        add(tctx, {"kind": "ack", "ref": note["id"], "producer": "p" * 81}, actor="agent")
    ack = add(tctx, {"kind": "ack", "ref": note["id"], "text": "Lower the music in v11", "producer": "Demo producer"},
              actor="agent")
    assert ack["producer"] == "Demo producer" and ack["version"] == "v10"
    with pytest.raises(ReactionError, match="owner reaction"):
        add(tctx, {"kind": "resolve", "ref": ack["id"], "version": "v10", "text": "x"}, actor="agent")
    with pytest.raises(ReactionError, match="needs version"):
        add(tctx, {"kind": "resolve", "ref": note["id"], "text": "x"}, actor="agent")
    with pytest.raises(ReactionError, match="not a candidate"):
        add(tctx, {"kind": "resolve", "ref": note["id"], "version": "v99", "text": "x"}, actor="agent")
    with pytest.raises(ReactionError, match="needs text"):
        add(tctx, {"kind": "resolve", "ref": note["id"], "version": "v10"}, actor="agent")
    resolve = add(tctx, {"kind": "resolve", "ref": note["id"], "version": "v10", "text": "Music down 3 dB"}, actor="agent")
    assert resolve["version"] == "v10" and resolve["ref"] == note["id"] and resolve["producer"] is None
    with pytest.raises(ReactionError, match="already addressed") as error:
        add(tctx, {"kind": "resolve", "ref": note["id"], "version": "v10", "text": "again"}, actor="agent")
    assert error.value.status == 409
    with pytest.raises(ReactionError, match="only post ack and resolve"):
        add(tctx, {"kind": "reopen", "ref": resolve["id"], "text": "no"}, actor="agent")
    with pytest.raises(ReactionError, match="the id of a resolve"):
        add(tctx, {"kind": "reopen", "ref": note["id"], "text": "no"})
    with pytest.raises(ReactionError, match="what did not land"):
        add(tctx, {"kind": "reopen", "ref": resolve["id"]})
    reopen = add(tctx, {"kind": "reopen", "ref": resolve["id"], "text": "Still loud at 01:02"})
    assert reopen["root"] == note["id"] and reopen["version"] == "v10"
    with pytest.raises(ReactionError, match="no longer the latest") as error:
        add(tctx, {"kind": "reopen", "ref": resolve["id"], "text": "again"})
    assert error.value.status == 409
    assert add(tctx, {"kind": "resolve", "ref": note["id"], "version": "v10", "text": "Now -6 dB"}, actor="agent")


def test_thread_states_follow_the_last_word(tctx):
    store = tctx.store
    note = add(tctx, {"kind": "note", "target": NOTE, "text": "Tighter cut"})
    state = lambda: next(r for r in store.query(pid=note["pid"]) if r["id"] == note["id"])  # noqa: E731
    assert state()["state"] == "open" and state()["thread"] == []
    add(tctx, {"kind": "ack", "ref": note["id"], "text": "On it"}, actor="agent")
    assert state()["state"] == "picked_up" and state()["acks"][0]["text"] == "On it"
    resolve = add(tctx, {"kind": "resolve", "ref": note["id"], "version": "v10", "text": "Cut 4 s",
                         "producer": "Demo producer"}, actor="agent")
    assert state()["state"] == "resolved"
    add(tctx, {"kind": "reopen", "ref": resolve["id"], "text": "Not enough"})
    record = state()
    assert record["state"] == "open" and [e["kind"] for e in record["thread"]] == ["ack", "resolve", "reopen"]
    assert thread_state([{"kind": "resolve"}, {"kind": "ack"}]) == "picked_up"
    assert not any(r["kind"] in ("ack", "resolve", "reopen") for r in store.query(pid=note["pid"]))
    assert {r["kind"] for r in store.query(pid=note["pid"], include_acks=True)} == {"note", "ack", "resolve", "reopen"}


def test_export_shows_each_thread(tctx, project):
    open_note = add(tctx, {"kind": "note", "target": NOTE, "text": "Open one"})
    picked = add(tctx, {"kind": "note", "target": NOTE, "text": "Picked one"})
    done = add(tctx, {"kind": "note", "target": NOTE, "text": "Done one"})
    add(tctx, {"kind": "ack", "ref": picked["id"], "text": "plan A", "producer": "Demo producer"}, actor="agent")
    add(tctx, {"kind": "resolve", "ref": done["id"], "version": "v10", "text": "fixed it"}, actor="agent")
    text = export_markdown(tctx.store.query(pid=project.id, include_retracted=True), [project])
    pending, rest = text.split("## Acknowledged")
    acknowledged, addressed = rest.split("## Addressed by the producer")
    assert open_note["id"] in pending and picked["id"] in acknowledged and done["id"] in addressed
    assert 'Picked up by Demo producer, ' in acknowledged and '("plan A").' in acknowledged
    assert "Addressed in v10 by the producer, " in addressed and ": fixed it." in addressed
    assert "1 not yet acknowledged, 1 acknowledged, 1 addressed" in text and "inbox.py" in text
    assert "\u2014" not in text


# ------------------------------------------------------------------------------------------------ C8.2 inbox API
def owner_post(live, body):
    status, record = post(live, "/api/reactions", dict(body, pid=live["pid"]))
    assert status == 201, record
    return record


def agent_post(live, body):
    return post(live, "/api/reactions", dict(body, pid=live["pid"]), {"X-Actor": "agent"})


def test_inbox_lists_owner_reactions_with_threads(live):
    pid = live["pid"]
    gate = owner_post(live, {"kind": "request_changes", "target": {"type": "gate", "id": "n03_objective_audience"},
                             "text": "Raise the cap.\nOr cut the film."})
    finding = owner_post(live, {"kind": "disagree", "target": {"type": "finding", "id": "creative-critic:CRE-D1",
                                                               "version": "v10", "t": 31.71}, "text": "Reads fine"})
    note = owner_post(live, {"kind": "note", "target": {"type": "time", "id": "t", "version": "v10", "t": 12.3},
                             "text": "Hold longer"})
    retracted = owner_post(live, {"kind": "flag", "target": NOTE})
    assert post(live, "/api/reactions", {"pid": pid, "kind": "retract", "ref": retracted["id"]})[0] == 201
    status, ack = agent_post(live, {"kind": "ack", "ref": finding["id"], "text": "Will check", "producer": "Demo producer"})
    assert status == 201
    status, _ = agent_post(live, {"kind": "resolve", "ref": note["id"], "version": "v10", "text": "Held 1 s longer"})
    assert status == 201
    inbox_ = get_json(live, f"/api/inbox?pid={pid}")
    assert inbox_["counts"] == {"open": 1, "picked_up": 1, "resolved": 1}
    assert [i["id"] for i in inbox_["items"]] == [gate["id"], finding["id"]]
    first, second = inbox_["items"]
    assert first["text"] == "Raise the cap.\nOr cut the film." and first["state"] == "open"
    assert first["target_words"] == "Gate Objective audience (N03)"
    assert first["link"] == f"http://127.0.0.1:{live['port']}/#/f/job-{pid}?tab=pipeline&n=n03_objective_audience"
    assert second["thread"]["ack"]["producer"] == "Demo producer" and second["state"] == "picked_up"
    assert second["target_words"].startswith("Finding creative-critic:CRE-D1 on v10 at 00:31.7")
    assert second["link"].endswith(f"#/f/job-{pid}?v=v10&tab=watch&f=creative-critic%3ACRE-D1&t=31.71")
    resolved = get_json(live, f"/api/inbox?pid={pid}&state=resolved")["items"]
    assert [i["id"] for i in resolved] == [note["id"]] and resolved[0]["thread"]["resolve"]["version"] == "v10"
    assert len(get_json(live, f"/api/inbox?pid={pid}&state=all")["items"]) == 3
    by_folder = get_json(live, "/api/inbox?" + f"project={live['job'] / 'candidates'}")
    assert by_folder["pid"] == pid and by_folder["counts"]["open"] == 1
    status, _, markdown = call(live, "GET", f"/api/inbox?pid={pid}&format=md")
    text = markdown.decode()
    assert status == 200 and "> Raise the cap.\n> Or cut the film." in text and f"* Id: {gate['id']}" in text
    assert "## Waiting (1)" in text and "## Picked up (1)" in text and "--ack <id>" in text
    summary = get_json(live, "/api/inbox/summary")["jobs"]
    assert summary[0]["pid"] == pid and summary[0]["open"] == 1 and summary[0]["oldest_open_ts"] == gate["ts"]
    assert call(live, "GET", f"/api/inbox?pid={pid}&state=late")[0] == 400
    assert call(live, "GET", "/api/inbox")[0] == 400
    assert call(live, "GET", "/api/inbox?project=relative/path")[0] == 400
    assert call(live, "GET", "/api/inbox?project=/nowhere/at/all")[0] == 404
    status, body = post(live, "/api/reactions/batch", {"reactions": [{"pid": pid, "kind": "resolve", "ref": note["id"]}]})
    assert status == 400 and "resolve" in body["error"]


def test_routes_and_states_parse():
    assert parse_states(None) == ("open", "picked_up") and parse_states("all") == ("open", "picked_up", "resolved")
    assert parse_states("resolved,open") == ("resolved", "open")
    with pytest.raises(ValueError):
        parse_states("closed")
    assert ui_route({"pid": "abc", "kind": "prefer", "a": "v1", "b": "v2"}) == "#/f/job-abc?tab=compare&a=v1&b=v2"
    assert ui_route({"pid": "abc", "kind": "approve", "version": "v2", "target": {"type": "version", "id": "v2"}}) \
        == "#/f/job-abc?v=v2&tab=watch"


# ------------------------------------------------------------------------------------------------ C8.3 inbox.py
def closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_cli_lists_acks_and_resolves_through_the_server(live, capsys):
    base = f"http://127.0.0.1:{live['port']}"
    folder = str(live["job"])
    assert inbox.main(["--base", base, "--project", folder]) == 3
    note = owner_post(live, {"kind": "note", "target": NOTE, "text": "Fade the logo out"})
    capsys.readouterr()
    assert inbox.main(["--base", base, "--project", folder]) == 0
    listed = capsys.readouterr().out
    assert "> Fade the logo out" in listed and note["id"] in listed and "## Waiting (1)" in listed
    assert inbox.main(["--base", base, "--project", folder, "--ack", note["id"], "--reply", "Fading in v11",
                       "--producer", "Demo producer (Copilot chat)"]) == 0
    assert "Picked up" in capsys.readouterr().out
    assert inbox.main(["--base", base, "--project", folder, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["state"] == "picked_up"
    assert payload["items"][0]["thread"]["ack"]["producer"] == "Demo producer (Copilot chat)"
    assert inbox.main(["--base", base, "--project", folder, "--resolve", note["id"], "--reply", "Faded"]) == 2
    assert "--version" in capsys.readouterr().err
    assert inbox.main(["--base", base, "--project", folder, "--resolve", note["id"], "--version", "v10",
                       "--reply", "Logo fades over 12 frames"]) == 0
    capsys.readouterr()
    assert inbox.main(["--base", base, "--project", folder]) == 3
    assert inbox.main(["--base", base, "--project", folder, "--state", "resolved"]) == 0
    assert "Addressed in v10 by the producer" in capsys.readouterr().out
    assert inbox.main(["--base", base, "--project", folder, "--ack", "no-such-id", "--reply", "x"]) == 2
    assert "no owner note" in capsys.readouterr().err
    assert inbox.main(["--base", base, "--all"]) == 3
    assert "Nothing waits for a producer" in capsys.readouterr().out


def test_cli_reads_the_store_when_the_server_is_down(live, capsys):
    owner_post(live, {"kind": "note", "target": NOTE, "text": "Offline note"})
    down = f"http://127.0.0.1:{closed_port()}"
    capsys.readouterr()
    code = inbox.main(["--base", down, "--data-dir", str(live["data"]), "--project", str(live["job"])])
    out = capsys.readouterr()
    assert code == 0 and "> Offline note" in out.out and "read-only" in out.err
    assert "because Glass Box is not running" in out.out
    assert inbox.main(["--base", down, "--data-dir", str(live["data"]), "--all"]) == 0
    assert "1 waiting" in capsys.readouterr().out
    code = inbox.main(["--base", down, "--project", str(live["job"]), "--ack", "x", "--reply", "plan"])
    assert code == 2 and "Glass Box is not running: start it with" in capsys.readouterr().err
    assert inbox.main(["--project", str(live["job"]), "--version", "v1"]) == 2
