"""Read actual engine state and graphs without turning reactions into engine decisions."""
import json
from pathlib import Path
import sys

import pytest

from server import bundled_engine
from glassbox.engine import Engine as ViewerEngine
from glassbox import fsio
from glassbox.web import make_server
from test_integration import call, get_json
from test_iteration5 import start, stop

ENGINE = bundled_engine()
if ENGINE is None:
    pytest.skip("requires the companion app inside a VPG checkout", allow_module_level=True)
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from vpg import cli
from vpg.engine import Engine


@pytest.mark.parametrize("workflow, nodes, edges, gates", [("default", 25, 49, 5), ("quality", 27, 55, 7)])
def test_real_engine_contract_and_read_only_reactions(tmp_path, workflow, nodes, edges, gates):
    project = tmp_path / "fictional-film"
    assert cli.main(["init", str(project), "--name", "Fictional contract film", "--workflow", workflow]) == 0
    engine = Engine(project)
    engine.run(max_steps=1)
    assert cli.main(["telemetry", str(project), "--write"]) == 0
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    selected = ViewerEngine(ENGINE).select(workflow)
    assert (len(selected.graph.nodes), len(selected.graph.edges), len(selected.graph.human_gate_ids())) == (nodes, edges, gates)
    fallback = fsio.parse_yaml(Path(selected.path).read_text(), force_fallback=True)
    assert fallback["nodes"] == selected.graph.nodes and fallback["edges"] == selected.graph.edges

    server = make_server([str(project)], ENGINE, str(tmp_path / "data"))
    thread = start(server)
    ctx = {"port": server.app.port}
    try:
        jobs = get_json(ctx, "/api/projects")
        assert len(jobs) == 1
        pid = jobs[0]["id"]
        detail = get_json(ctx, f"/api/projects/{pid}")
        assert detail["workflow"]["name"] == workflow and len(detail["graph"]) == nodes
        assert detail["run"]["status"] == engine.state.runtime.status.value
        assert detail["telemetry"]["present"] is True
        body = {"pid": pid, "kind": "note", "target": {"type": "node", "id": "n01_context_intake"},
                "text": "Fictional feedback, not an approval."}
        status, _, data = call(ctx, "POST", "/api/reactions", body, {"Content-Type": "application/json"})
        assert status == 201 and json.loads(data)["actor"] == "owner"
        body.update(kind="approve", target={"type": "gate", "id": "n03_objective_audience"})
        assert call(ctx, "POST", "/api/reactions", body, {"Content-Type": "application/json"})[0] == 409
    finally:
        stop(server, thread)
    assert {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()} == before
    log = tmp_path / "data" / "reactions.jsonl"
    assert len(log.read_text().splitlines()) == 1
