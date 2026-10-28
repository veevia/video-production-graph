"""Iteration 5: the Films contact sheet API, reaction batches, retract (undo) and the --static-dir option."""
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from glassbox.web import make_server
from synthetic import make_job, write_engine
from test_library import make_library_tree

APP = Path(__file__).resolve().parents[1]
FINDING = {"type": "finding", "id": "creative-critic:CRE-D1", "version": "v10", "t": 31.71}


def start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.fixture
def live(tmp_path):
    root = tmp_path / "roots"
    job = make_job(root)
    engine = write_engine(tmp_path)
    server = make_server([str(root)], str(engine), str(tmp_path / "data"), port=0, quiet=True)
    thread = start(server)
    yield {"port": server.app.port, "app": server.app, "job": job, "data": tmp_path / "data",
           "pid": hashlib.sha1(os.path.abspath(str(job)).encode()).hexdigest()[:10]}
    stop(server, thread)


def call(ctx, method, path, body=None, headers=None):
    import http.client
    connection = http.client.HTTPConnection("127.0.0.1", ctx["port"], timeout=10)
    sent = {"Host": f"127.0.0.1:{ctx['port']}"}
    if body is not None:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    connection.request(method, path, body=json.dumps(body).encode() if isinstance(body, (dict, list)) else body,
                       headers=sent)
    response = connection.getresponse()
    data = response.read()
    status, received = response.status, response.headers
    connection.close()
    return status, received, data


def get_json(ctx, path):
    status, _, data = call(ctx, "GET", path)
    assert status == 200, data[:300]
    return json.loads(data)


def post(ctx, path, body, headers=None):
    status, _, data = call(ctx, "POST", path, body, headers)
    return status, json.loads(data) if data else None


# ------------------------------------------------------------------------------------------------ films
def test_films_rows_marks_and_decisions(live):
    films = get_json(live, "/api/films")
    assert films["counts"] == {"all": 1, "needs": 1 if films["rows"][0]["needs"] else 0, "jobs": 1, "library": 0,
                               "archive": 0}
    row = films["rows"][0]
    assert row["id"] == f"job-{live['pid']}" and row["kind"] == "job" and row["area"] == "Job"
    assert [f["label"] for f in row["versions"]] == ["v10", "v2", "v1"]
    assert all(f["mark"] in {"needs", "in_progress", "rework", "passed", "not_reviewed", "partial", "not_run"}
               and f["mark_word"] for f in row["versions"])
    assert row["counts_text"] == "3 versions" and films["library"]["status"] == "disabled"
    for decision in films["decisions"]:
        assert decision["film_id"] == row["id"] and decision["words"] and decision["kind"] in {"gate", "release", "findings"}
        assert decision["items"] and all(i["group"] == "decision" for i in decision["items"])
    assert len(films["decisions"]) == row["needs"]
    project = get_json(live, f"/api/projects/{live['pid']}")
    assert project["id"] == live["pid"]


def test_films_merge_library_films_into_their_job(tmp_path):
    home = tmp_path / "home"
    paths = make_library_tree(home)
    projects_root = home / "GitHub" / "Demo_workspace" / "01_Project-Hubs"
    engine = write_engine(tmp_path)
    server = make_server([str(projects_root)], str(engine), str(tmp_path / "data"), port=0, quiet=True,
                         library_roots=[str(home / "GitHub"), str(home / "Downloads")], library_home=str(home))
    server.app.registry.scan()
    server.app.library.scan_now()
    thread = start(server)
    ctx = {"port": server.app.port}
    try:
        films = get_json(ctx, "/api/films")
        names = [r["name"] for r in films["rows"]]
        assert names.count("Demo film") == 1, names
        job = next(r for r in films["rows"] if r["kind"] == "job")
        assert job["film_id"] and job["counts"]["library_versions"] == 4
        assert job["counts_text"].endswith("4 folders with video")
        water = next(r for r in films["rows"] if r["name"] == "Water_hub")
        assert water["kind"] == "library" and water["id"] == f"lib-{water['film_id']}" and water["area"] == "Workspace"
        assert water["versions"][0]["label"] == "2026-10-04-r08-v16" and water["versions"][0]["item"]
        archived = next(r for r in films["rows"] if r["name"] == "Example film")
        assert archived["archived"] and archived["area"] == "Archive"
        assert films["counts"]["library"] == len(films["rows"]) - 1
        assert films["counts"]["archive"] == sum(1 for r in films["rows"] if r["archived"])
        assert any(c["label"] == "Published on the portfolio site" for c in films["other"])
    finally:
        stop(server, thread)
    assert paths["demo"].exists()


# ------------------------------------------------------------------------------------------------ retract
def test_retract_hides_the_original_and_marks_the_export(live):
    pid = live["pid"]
    status, note = post(live, "/api/reactions", {"pid": pid, "kind": "note", "target": FINDING, "text": "Too loud"})
    assert status == 201
    status, agree = post(live, "/api/reactions", {"pid": pid, "kind": "agree", "target": FINDING})
    assert status == 201
    status, retract = post(live, "/api/reactions", {"pid": pid, "kind": "retract", "ref": note["id"]})
    assert status == 201 and retract["target"] == note["target"] and retract["version"] == "v10"
    active = get_json(live, f"/api/reactions?pid={pid}")
    assert [r["id"] for r in active] == [agree["id"]]
    every = get_json(live, f"/api/reactions?pid={pid}&include_retracted=1")
    assert {r["id"] for r in every} == {note["id"], agree["id"]}
    assert next(r for r in every if r["id"] == note["id"])["retracted"]["id"] == retract["id"]
    assert post(live, "/api/reactions", {"pid": pid, "kind": "retract", "ref": note["id"]})[0] == 409
    assert post(live, "/api/reactions", {"pid": pid, "kind": "retract", "ref": agree["id"]}, {"X-Actor": "agent"})[0] == 400
    assert post(live, "/api/reactions", {"pid": pid, "kind": "retract", "ref": "nope"})[0] == 400
    assert post(live, "/api/reactions", {"pid": pid, "kind": "retract", "ref": agree["id"], "text": "x",
                                         "target": FINDING})[0] == 400
    status, text = call(live, "GET", f"/api/reactions/export?format=md&pid={pid}")[0::2]
    text = text.decode()
    assert status == 200 and "1 retracted by the owner" in text and "## Retracted by the owner (ignore these)" in text
    assert "Retracted by the owner," in text


def test_retract_refuses_decisions(live):
    pid = live["pid"]
    status, approval = post(live, "/api/reactions", {"pid": pid, "kind": "approve", "version": "v10",
                                                     "target": {"type": "gate", "id": "n23_final_verification"}})
    if status == 201:
        assert post(live, "/api/reactions", {"pid": pid, "kind": "retract", "ref": approval["id"]})[0] == 400


# ------------------------------------------------------------------------------------------------ batch
def test_batch_is_all_or_nothing(live):
    pid = live["pid"]
    good = [{"pid": pid, "kind": "agree", "target": FINDING, "tags": ["mix"]} for _ in range(3)]
    status, body = post(live, "/api/reactions/batch", {"reactions": good})
    assert status == 201 and len(body["records"]) == 3 and len({r["id"] for r in body["records"]}) == 3
    before = len(get_json(live, f"/api/reactions?pid={pid}"))
    bad = good + [{"pid": pid, "kind": "agree", "target": {"type": "finding", "id": "x"}}]
    status, body = post(live, "/api/reactions/batch", {"reactions": bad})
    assert status == 400 and body["index"] == 3 and "reaction 4" in body["error"]
    assert len(get_json(live, f"/api/reactions?pid={pid}")) == before
    assert post(live, "/api/reactions/batch", {"reactions": good * 34})[0] == 413
    assert post(live, "/api/reactions/batch", {"reactions": []})[0] == 400
    assert post(live, "/api/reactions/batch", {"reactions": good, "extra": 1})[0] == 400
    assert post(live, "/api/reactions/batch", {"reactions": [{"pid": pid, "kind": "retract", "ref": "x"}]})[0] == 400
    assert call(live, "POST", "/api/reactions/batch", {"reactions": good}, {"Origin": "http://evil.example"})[0] == 403


# ------------------------------------------------------------------------------------------------ static dir
def test_static_dir_serves_another_ui(tmp_path):
    ui = tmp_path / "ui_next"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>next</title>")
    (ui / "app.js").write_text("export const next = true;\n")
    (ui / ".hidden.js").write_text("secret")
    engine = write_engine(tmp_path)
    (tmp_path / "roots").mkdir()
    server = make_server([str(tmp_path / "roots")], str(engine), str(tmp_path / "data"), port=0, quiet=True,
                         static_dir=str(ui))
    thread = start(server)
    ctx = {"port": server.app.port}
    try:
        status, headers, body = call(ctx, "GET", "/")
        assert status == 200 and b"<title>next</title>" in body
        assert call(ctx, "GET", "/static/app.js")[2] == b"export const next = true;\n"
        assert call(ctx, "GET", "/static/.hidden.js")[0] == 404
        assert call(ctx, "GET", "/static/../server.py")[0] == 404
    finally:
        stop(server, thread)


# ------------------------------------------------------------------------------------------------ redirects
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_old_deep_links_redirect():
    """The UI router (static or static_next) maps every old route to the new information architecture."""
    for folder in ("static_next", "static"):
        routes = APP / folder / "js" / "routes.js"
        if routes.exists():
            break
    else:
        pytest.skip("no routes module yet")
    cases = {
        "": "#/",
        "#/needs-you": "#/",
        "#/activity": "#/activity",
        "#/library": "#/films",
        "#/p/abcdef0123": "#/f/job-abcdef0123",
        "#/p/abcdef0123?v=v25": "#/f/job-abcdef0123?v=v25",
        "#/p/abcdef0123/v/v25?t=12.5&f=x%3Ay": "#/f/job-abcdef0123?v=v25&tab=watch&t=12.5&f=x%3Ay",
        "#/p/abcdef0123/n/n13_motion_scenes?v=v25": "#/f/job-abcdef0123?v=v25&tab=pipeline&n=n13_motion_scenes",
        "#/p/abcdef0123/v/v25?n=n15_technical_qa": "#/f/job-abcdef0123?v=v25&tab=pipeline&n=n15_technical_qa",
        "#/p/abcdef0123/gates?v=v25": "#/f/job-abcdef0123?v=v25&tab=pipeline",
        "#/p/abcdef0123/compare?a=v15&b=v25": "#/f/job-abcdef0123?tab=compare&a=v15&b=v25",
        "#/p/abcdef0123/compare?b=v25": "#/f/job-abcdef0123?tab=compare&b=v25",
        "#/library/f/0123456789abcdef": "#/f/lib-0123456789abcdef",
        "#/library/f/0123456789abcdef?v=r07-v15&i=fedcba9876543210&t=3":
            "#/f/lib-0123456789abcdef?v=r07-v15&i=fedcba9876543210&t=3",
        "#/library/f/0123456789abcdef/compare?a=1111111111111111&b=2222222222222222":
            "#/f/lib-0123456789abcdef?tab=compare&a=1111111111111111&b=2222222222222222",
        "#/library/i/fedcba9876543210?t=4": "#/v/fedcba9876543210?t=4",
    }
    script = (f"import {{ redirectOf }} from {json.dumps(routes.as_uri())};\n"
              f"const cases = {json.dumps(cases)};\n"
              "const out = {};\n"
              "for (const [from, to] of Object.entries(cases)) out[from] = redirectOf(from);\n"
              "console.log(JSON.stringify(out));\n")
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == cases
