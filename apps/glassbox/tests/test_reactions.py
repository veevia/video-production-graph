import json
import os

import pytest

from glassbox.reactions import ReactionError, export_markdown, validate_reaction


class Ctx:
    def __init__(self, project, store):
        self._project = project
        self.store = store

    def project(self, pid):
        return self._project if pid == self._project.id else None

    def versions(self, project):
        return {"v1", "v2", "v10"}

    def node_ids(self):
        return {"n03_objective_audience", "n13_motion_scenes", "n23_final_verification"}

    def human_gate_ids(self):
        return {"n03_objective_audience", "n23_final_verification"}

    def release_node(self):
        return "n23_final_verification"

    def find_reaction(self, reaction_id):
        return self.store.find(reaction_id)


@pytest.fixture
def ctx(project, store):
    return Ctx(project, store)


def check(ctx, body, actor="owner"):
    return validate_reaction(body, actor, ctx)


def test_accepts_valid_reactions(ctx, project):
    pid = project.id
    record = check(ctx, {"pid": pid, "kind": "approve", "version": "v10"})
    assert record["target"] == {"type": "version", "id": "v10", "version": "v10"}
    assert record["actor"] == "owner" and record["project_path"] == project.path
    record = check(ctx, {"pid": pid, "kind": "note", "text": "Hold longer", "tags": ["motion", "story", "motion"],
                         "target": {"type": "time", "id": "00:31.7", "version": "v10", "t": 31.7}})
    assert record["t"] == 31.7 and record["tags"] == ["motion", "story"]
    record = check(ctx, {"pid": pid, "kind": "agree", "target": {"type": "finding", "id": "creative-critic:CRE-D1", "version": "v10", "t": 31.71}})
    assert record["version"] == "v10" and record["text"] == ""
    record = check(ctx, {"pid": pid, "kind": "request_changes", "target": {"type": "gate", "id": "n03_objective_audience"}, "text": "Tighter"})
    assert record["version"] is None
    record = check(ctx, {"pid": pid, "kind": "approve", "target": {"type": "gate", "id": "n23_final_verification", "version": "v10"}})
    assert record["version"] == "v10"
    assert check(ctx, {"pid": pid, "kind": "flag", "target": {"type": "node", "id": "n13_motion_scenes", "version": "v10"}})["kind"] == "flag"
    record = check(ctx, {"pid": pid, "kind": "prefer", "a": "v2", "b": "v10", "choice": "b", "text": "Cleaner"})
    assert record["choice"] == "b" and record["target"] is None and record["a"] == "v2"


@pytest.mark.parametrize("body, message", [
    ({"kind": "approve", "version": "v10", "pid": None}, "pid is required"),
    ({"kind": "approve", "version": "v10", "pid": "0000000000"}, "unknown pid"),
    ({"kind": "cheer", "version": "v10"}, "kind must be one of"),
    ({"kind": "approve", "version": "v99"}, "not a candidate"),
    ({"kind": "approve"}, "version or target is required"),
    ({"kind": "approve", "version": "v10", "extra": 1}, "unknown field"),
    ({"kind": "note", "version": "v10", "text": ""}, "a note needs text"),
    ({"kind": "note", "version": "v10", "text": "x" * 4001}, "longer than 4000"),
    ({"kind": "note", "version": "v10", "text": "ok", "tags": [f"t{i}" for i in range(11)]}, "at most 10 tags"),
    ({"kind": "note", "version": "v10", "text": "ok", "tags": ["x" * 41]}, "longer than 40"),
    ({"kind": "note", "version": "v10", "text": "ok", "tags": "look"}, "list of strings"),
    ({"kind": "note", "version": "v10", "text": "ok", "t": -1}, "between 0"),
    ({"kind": "note", "version": "v10", "text": "ok", "t": True}, "number of seconds"),
    ({"kind": "agree", "target": {"type": "version", "id": "v10"}}, "does not apply"),
    ({"kind": "approve", "target": {"type": "gate", "id": "STILLS", "version": "v10"}}, "human gates only"),
    ({"kind": "approve", "target": {"type": "gate", "id": "n23_final_verification"}}, "needs a version"),
    ({"kind": "flag", "target": {"type": "node", "id": "n99_nope"}}, "not a node"),
    ({"kind": "note", "text": "x", "target": {"type": "time", "id": "t", "version": "v10"}}, "needs t"),
    ({"kind": "note", "text": "x", "target": {"type": "finding", "id": "f1"}}, "needs a version"),
    ({"kind": "note", "text": "x", "target": {"type": "finding", "id": "f" * 121, "version": "v10"}}, "longer than 120"),
    ({"kind": "note", "text": "x", "target": {"type": "galaxy", "id": "g", "version": "v10"}}, "target.type"),
    ({"kind": "note", "text": "x", "target": {"type": "node", "id": "n13_motion_scenes", "colour": "red"}}, "unknown target field"),
    ({"kind": "note", "text": "x", "version": "v2", "target": {"type": "version", "id": "v10"}}, "differ"),
    ({"kind": "prefer", "a": "v2", "b": "v2", "choice": "a"}, "must differ"),
    ({"kind": "prefer", "a": "v2", "b": "v10", "choice": "maybe"}, "choice must be"),
    ({"kind": "prefer", "a": "v2", "b": "v10", "choice": "a", "version": "v2"}, "does not take version"),
    ({"kind": "prefer", "a": "v2", "choice": "a"}, "needs versions a and b"),
    ({"kind": "approve", "version": "v10", "choice": "a"}, "does not take choice"),
    ({"kind": "ack", "ref": "nope"}, "ack requires the header"),
])
def test_rejects_bad_bodies(ctx, project, body, message):
    payload = {"pid": project.id, **body}
    if payload["pid"] is None:
        del payload["pid"]
    with pytest.raises(ReactionError, match=message):
        check(ctx, payload)


def test_rejects_non_objects(ctx):
    with pytest.raises(ReactionError, match="JSON object"):
        check(ctx, ["not", "an", "object"])


def test_agent_actor_may_only_ack(ctx, project, store):
    with pytest.raises(ReactionError, match="only post ack"):
        check(ctx, {"pid": project.id, "kind": "note", "version": "v10", "text": "hi"}, actor="agent")
    with pytest.raises(ReactionError, match="X-Actor"):
        check(ctx, {"pid": project.id, "kind": "approve", "version": "v10"}, actor="robot")
    original = store.append(check(ctx, {"pid": project.id, "kind": "note", "version": "v10", "text": "Please fix"}))
    ack = check(ctx, {"pid": project.id, "kind": "ack", "ref": original["id"], "text": "picked up"}, actor="agent")
    assert ack["actor"] == "agent" and ack["ref"] == original["id"] and ack["version"] == "v10"
    with pytest.raises(ReactionError, match="ref does not name"):
        check(ctx, {"pid": project.id, "kind": "ack", "ref": "missing"}, actor="agent")
    stored = store.append(ack)
    with pytest.raises(ReactionError, match="cannot acknowledge another ack"):
        check(ctx, {"pid": project.id, "kind": "ack", "ref": stored["id"]}, actor="agent")
    with pytest.raises(ReactionError, match="ack does not take"):
        check(ctx, {"pid": project.id, "kind": "ack", "ref": original["id"], "version": "v10"}, actor="agent")


def test_store_is_append_only_jsonl_with_acks(ctx, project, store, tmp_path):
    assert not (tmp_path / "data").exists()
    first = store.append(check(ctx, {"pid": project.id, "kind": "approve", "version": "v10"}))
    second = store.append(check(ctx, {"pid": project.id, "kind": "note", "version": "v2", "text": "Older note"}))
    store.append(check(ctx, {"pid": project.id, "kind": "ack", "ref": first["id"]}, actor="agent"))
    lines = (tmp_path / "data" / "reactions.jsonl").read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(line)["id"] for line in lines)
    assert oct(os.stat(store.path).st_mode & 0o777) == "0o600"
    records = store.query(pid=project.id)
    assert [r["id"] for r in records] == [second["id"], first["id"]]
    assert records[1]["acks"][0]["ts"] and records[0]["acks"] == []
    assert [r["id"] for r in store.query(pid=project.id, version="v2")] == [second["id"]]
    assert first["ts"].endswith("Z") and len(first["id"]) == 36
    with open(store.path, "a", encoding="utf-8") as handle:
        handle.write("not json\n")
    assert len(store.query(pid=project.id)) == 2


def test_export_markdown_groups_unacknowledged_first(ctx, project, store):
    approved = store.append(check(ctx, {"pid": project.id, "kind": "approve", "version": "v10"}))
    finding = store.append(check(ctx, {"pid": project.id, "kind": "disagree", "text": "It reads fine", "tags": ["story"],
                                       "target": {"type": "finding", "id": "creative-critic:CRE-D1", "version": "v10", "t": 31.71}}))
    older = store.append(check(ctx, {"pid": project.id, "kind": "note", "version": "v2", "text": "Older note"}))
    preference = store.append(check(ctx, {"pid": project.id, "kind": "prefer", "a": "v2", "b": "v10", "choice": "tie"}))
    store.append(check(ctx, {"pid": project.id, "kind": "ack", "ref": approved["id"], "text": "queued"}, actor="agent"))
    text = export_markdown(store.query(pid=project.id), [project])
    assert text.startswith("# VPG Glass Box reactions\n")
    pending, done = text.split("## Acknowledged")
    assert "## Not yet acknowledged" in pending
    for record in (finding, older, preference):
        assert record["id"] in pending
    assert approved["id"] not in pending and approved["id"] in done and "Picked up by the producer" in done
    assert pending.index("### v10") < pending.index("### v2") < pending.index("### No version")
    assert "#### Finding creative-critic:CRE-D1" in pending and "at 00:31.7" in pending and "Tags: story." in pending
    assert ": It reads fine. Tags: story. Route: " in pending and '("queued").' in done and "Picked up by the producer, " in done
    assert "#### Preference between v2 and v10" in pending and "* Tie, " in pending
    assert "\u2014" not in text
    assert not any(line.startswith("* **") for line in text.splitlines())
    items = [line for line in text.splitlines() if line.startswith("* ")]
    assert items and all(line.endswith(")") and "(Id: " in line and " Route: #/p/" in line for line in items)
    assert any(line.startswith("* Disagree, ") and f"Route: #/p/{project.id}/v/v10?f=creative-critic%3ACRE-D1&t=31.71" in line
               for line in items)
    assert any(line.startswith("* Note, ") and f"Route: #/p/{project.id}/v/v2 " in line for line in items)
    assert any(f"Route: #/p/{project.id}/compare?a=v2&b=v10 " in line for line in items)
