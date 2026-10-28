"""Iteration 5.3: Needs you means only the owner (a decided gate reads as his while the engine waits, and can take a
new decision where the server allows it), the owner's verdict on each version, notes counted with decisions, and
the inbox summary's per-note lines."""
import pytest

from glassbox.adapters import JobReader
from glassbox.producer import first_sentence, owner_mark
from test_iteration5 import call, get_json, live, post  # noqa: F401  (the live server fixture)

N03 = {"type": "gate", "id": "n03_objective_audience"}


def owner(live, body):
    status, record = post(live, "/api/reactions", dict(body, pid=live["pid"]))
    assert status == 201, record
    return record


def agent(live, body):
    status, record = post(live, "/api/reactions", dict(body, pid=live["pid"]), {"X-Actor": "agent"})
    assert status == 201, record
    return record


def node(live, node_id, version="v10"):
    return next(n for n in get_json(live, f"/api/projects/{live['pid']}/nodes?version={version}") if n["id"] == node_id)


def test_a_decided_gate_reads_as_the_owners_while_the_engine_waits(live, monkeypatch):
    # The synthetic ledger is dated 2030; a decision only counts when it follows the start of the wait.
    monkeypatch.setattr(JobReader, "waiting_since", lambda self, node_id: "2020-01-01T00:00:00+00:00")
    assert node(live, "n03_objective_audience")["status"] == "waiting"
    assert any(d["kind"] == "gate" for d in get_json(live, "/api/films")["decisions"])
    decision = owner(live, {"kind": "request_changes", "target": N03, "text": "Keep the 65 s cap."})
    agent(live, {"kind": "ack", "ref": decision["id"], "text": "A 64 s cut in v11. Then the brief stays.", "producer": "Demo producer"})
    entry = node(live, "n03_objective_audience")
    gate = entry["gate_state"]
    assert entry["status"] == "decided" and entry["status_label"] == "decided by you; engine waits"
    assert gate["state"] == "recorded" and gate["engine_waits"] and gate["redecide"] and "approve" in gate["actions"]
    films = get_json(live, "/api/films")
    assert not any(d["kind"] == "gate" for d in films["decisions"])
    assert not any(item.get("node") == "n03_objective_audience" and item["group"] == "decision"
                   for item in get_json(live, f"/api/needs-you?pid={live['pid']}"))
    decided = films["rows"][0]["decided"]
    assert [(d["node"], d["kind"], d["engine_waits"], d["redecide"]) for d in decided] == [("n03_objective_audience", "gate", True, True)]
    assert decided[0]["record"]["state"] == "picked_up" and decided[0]["record"]["picked_for"] == "v11"
    assert decided[0]["record"]["reply"] == "A 64 s cut in v11."
    status, _ = post(live, "/api/reactions", {"pid": live["pid"], "kind": "approve", "target": N03})
    assert status == 201
    assert get_json(live, "/api/films")["rows"][0]["decided"][0]["record"]["kind"] == "approve"


def test_the_owners_verdict_rides_on_its_version(live):
    release = {"type": "gate", "id": "n23_final_verification", "version": "v10"}
    decision = owner(live, {"kind": "request_changes", "version": "v10", "target": release, "text": "Tighten the ending."})
    owner(live, {"kind": "note", "text": "Hold the logo", "target": {"type": "time", "id": "t", "version": "v10", "t": 3.0}})
    agent(live, {"kind": "ack", "ref": decision["id"], "text": "v10 stays; v11 fixes the ending."})
    row = get_json(live, "/api/films")["rows"][0]
    frame = next(f for f in row["versions"] if f["label"] == "v10")
    assert frame["owner"]["kind"] == "request_changes" and frame["owner"]["state"] == "picked_up"
    assert frame["owner"]["picked_for"] == "v11"
    assert all(f["owner"] is None for f in row["versions"] if f["label"] != "v10")
    detail = get_json(live, f"/api/projects/{live['pid']}")
    v10 = next(c for c in detail["candidates"] if c["version"] == "v10")
    assert v10["owner"]["id"] == decision["id"] and v10["notes_count"] == 2
    gate = get_json(live, f"/api/projects/{live['pid']}/candidates/v10")["release_gate"]
    assert gate["state"] == "recorded" and "picked up by the producer" in gate["label"]
    other = owner(live, {"kind": "note", "text": "Older one", "target": {"type": "version", "id": "v2"}})
    assert other["version"] == "v2"
    assert next(f for f in get_json(live, "/api/films")["rows"][0]["versions"] if f["label"] == "v2")["owner"] is None


def test_the_inbox_summary_lists_each_note(live):
    decision = owner(live, {"kind": "request_changes", "target": N03, "text": "Keep the cap. Cut the film."})
    note = owner(live, {"kind": "note", "text": "Logo late", "target": {"type": "version", "id": "v10"}})
    agent(live, {"kind": "resolve", "ref": note["id"], "version": "v10", "text": "Logo moved earlier. Checked twice."})
    jobs = get_json(live, "/api/inbox/summary?items=1")["jobs"]
    items = {item["id"]: item for item in jobs[0]["items"]}
    assert items[decision["id"]]["state"] == "open" and items[decision["id"]]["text"] == "Keep the cap."
    assert items[note["id"]]["state"] == "resolved" and items[note["id"]]["resolved_in"] == "v10"
    assert items[note["id"]]["reply"] == "Logo moved earlier."
    assert items[decision["id"]]["target_words"].startswith("Gate ")
    assert "items" not in get_json(live, "/api/inbox/summary")["jobs"][0]


@pytest.mark.parametrize("text, expected", [
    ("One. Two.", "One."), ("No stop at all", "No stop at all"), ("", ""),
])
def test_first_sentence(text, expected):
    assert first_sentence(text) == expected


def test_owner_mark_reads_the_thread():
    record = {"id": "x", "kind": "request_changes", "ts": "t", "text": "Fix", "version": "v28",
              "thread": [{"kind": "ack", "text": "Rework v28 into v29.", "producer": "P"},
                         {"kind": "resolve", "version": "v29", "text": "Done in v29.", "producer": "P"},
                         {"kind": "reopen", "text": "Not yet"}]}
    mark = owner_mark(record)
    assert mark["state"] == "open" and mark["reopened"] and mark["picked_for"] == "v29" and mark["resolved_in"] is None
    record["thread"] = record["thread"][:2]
    mark = owner_mark(record)
    assert mark["state"] == "resolved" and mark["resolved_in"] == "v29" and mark["reply"] == "Done in v29."
