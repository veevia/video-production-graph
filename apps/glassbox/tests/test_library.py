"""Iteration 4: the video library (indexer, grouping, API, media and library reactions) on a synthetic tree."""
import http.client
import json
import os
import struct
import threading
import time

import pytest

from glassbox import library as libmod
from glassbox.library import Library, mp4_duration, quick_fingerprint, sidecars
from glassbox.library_group import classify_role, family_of, version_of
from glassbox.reactions import route_of, target_label
from glassbox.web import make_server
from synthetic import write_engine


# ------------------------------------------------------------------------------------------------ synthetic files
def mp4_bytes(duration_s, timescale=1000, version=0, moov_last=False, large_mdat=False, payload=b"\x00" * 64):
    """A tiny MP4: ftyp, mdat and moov/mvhd (version 0 or 1), with moov first or last."""
    if version == 1:
        body = bytes([1, 0, 0, 0]) + struct.pack(">QQ", 0, 0) + struct.pack(">IQ", timescale, int(duration_s * timescale))
    else:
        body = bytes([0, 0, 0, 0]) + struct.pack(">II", 0, 0) + struct.pack(">II", timescale, int(duration_s * timescale))
    body += b"\x00" * 80
    mvhd = struct.pack(">I4s", 8 + len(body), b"mvhd") + body
    moov = struct.pack(">I4s", 8 + 16, b"udta") + b"\x00" * 16
    moov = struct.pack(">I4s", 8 + len(moov) + len(mvhd), b"moov") + moov + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"isom\x00\x00\x02\x00"
    if large_mdat:
        mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)) + payload
    else:
        mdat = struct.pack(">I4s", 8 + len(payload), b"mdat") + payload
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def put(path, data=None, seconds=10.0, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else mp4_bytes(seconds, payload=str(path).encode()))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def make_library_tree(home):
    """Fictional projects, archives and portfolio layouts with tiny files."""
    base = 1_790_000_000
    hubs = home / "GitHub" / "Demo_workspace" / "01_Project-Hubs" / "01_CLIENT" / "03_build"
    demo = hubs / "Demo_hub" / "video" / "vpg"
    (demo / "vpg.project.yaml").parent.mkdir(parents=True, exist_ok=True)
    (demo / "vpg.project.yaml").write_text("name: Demo film\nid: demo-film\n")
    v1 = put(demo / "candidates/v1/n14_video_assembly/master.mp4", mp4_bytes(60.0, payload=b"v1 master"), mtime=base + 100)
    put(demo / "candidates/v2/n14_video_assembly/master.mp4", mp4_bytes(61.5, version=1, payload=b"v2 master"), mtime=base + 200)
    put(demo / "candidates/v2/n14_video_assembly/silent.mp4", mtime=base + 200)
    put(demo / "candidates/v2/n13_motion_scenes/film.mp4", mtime=base + 190)
    (demo / "candidates/v2/n14_video_assembly/master-poster.jpg").write_bytes(b"\xff\xd8\xff\xe0poster")
    (demo / "candidates/v2/n14_video_assembly/captions.srt").write_text("1\n00:00:01,000 --> 00:00:02,500\nHello there\n")
    put(demo / "work/n14_video_assembly/a3/master.mp4", mp4_bytes(62.0, moov_last=True, payload=b"a3"), mtime=base + 300)
    put(demo / "work/n13_motion_scenes/a4/film.mp4", mtime=base + 310)
    put(demo / "sources/capture_TENANT/prototypes-hd/screen-master.mp4", mtime=base + 50)
    (demo / ".vpg").mkdir()
    (demo / ".vpg/state.json").write_text(json.dumps({
        "history": {"versions": [{"version": 3, "node_attempt": "n14_video_assembly#a3", "artifacts": {}}]},
        "artifacts": {"master@n14_video_assembly#a3": {"path": "work/n14_video_assembly/a3/master.mp4", "version": 3,
                                                        "key": "master", "node": "n14_video_assembly"}},
    }))
    water = hubs / "Water_hub" / "video"
    put(water / "revisions/round-07/outputs/r07-v07/demo-orbit-r07-master.mp4", mtime=base + 400)
    put(water / "revisions/round-07/outputs/r07-v15/demo-orbit-r07-master.mp4", mtime=base + 500)
    put(water / "revisions/round-07/outputs/r07-v15/demo-orbit-r07-review.mp4", mtime=base + 501)
    put(water / "revisions/round-07/outputs/r07-v15/demo-orbit-r07-silent.mp4", mtime=base + 502)
    put(water / "revisions/round-07/outputs/r07-v15/clips/01-problem.mp4", mtime=base + 503)
    put(water / "releases/2026-10-04-r08-v16/demo-orbit-r08-v16.mp4", mtime=base + 600)
    put(water / "revisions/round-03/outputs/02-solution.mp4", mtime=base + 150)
    put(hubs / "Orbit_hub" / "video/outputs/v14/orbit-hub-v14-master.mp4", mtime=base + 700)
    archive = home / "Downloads" / "Archive"
    archived = archive / "20261008-example-film" / "vpg"
    (archived / "vpg.project.yaml").parent.mkdir(parents=True, exist_ok=True)
    (archived / "vpg.project.yaml").write_text("name: Example film\n")
    put(archived / "work/n14_video_assembly/a1/master.mp4", mtime=base + 800)
    put(archived / "work/n14_video_assembly/a2/master.mp4", mtime=base + 810)
    intro = archive / "20261002-self-intro"
    for variant in ("vpg/cand-A", "vpg/cand-B", "vpg/_blocked/cand-C"):
        (intro / variant).mkdir(parents=True, exist_ok=True)
        (intro / variant / "vpg.project.yaml").write_text(f"name: Intro {variant}\n")
        put(intro / variant / "work/n14_video_assembly/a1/master.mp4", mtime=base + 900)
    put(intro / "vpg/cand-B/work/n14_video_assembly/a2/master.mp4", mtime=base + 950)
    put(intro / "candidates/v3/V06-questions.mp4", mtime=base + 960)
    put(home / "Downloads/V7_Delivery/01_master/Water_V7_Master.mp4", mtime=base + 1000)
    put(home / "Downloads/V7_Delivery/02_clips/V7_clip_01_signal.mp4", mtime=base + 1001)
    put(home / "Downloads/Team Sync-Meeting Recording.mp4", mtime=base + 1100)
    put(home / "Downloads/Clips/raw-take.mkv", b"\x1aE\xdf\xa3 not really matroska", mtime=base + 1200)
    put(home / "GitHub/Demo_portfolio/src/assets/work/demo/film.mp4", v1.read_bytes(), mtime=base + 1300)
    put(home / "GitHub/Physical-AI/runs/r1/videos/eval_episode_0.mp4", mtime=base + 1400)
    put(home / "GitHub/app/node_modules/pkg/demo-master.mp4")
    put(home / "GitHub/.hidden/secret-master.mp4")
    put(home / "GitHub/repo/.git/objects/x-master.mp4")
    outside = put(home / "elsewhere/outside-master.mp4")
    (home / "GitHub/linkdir").mkdir(parents=True, exist_ok=True)
    os.symlink(outside, home / "GitHub/linkdir/linked-master.mp4")
    return {"demo": demo, "water": water, "v1": v1}


@pytest.fixture
def tree(tmp_path):
    home = tmp_path / "home"
    paths = make_library_tree(home)
    roots = [str(home / "GitHub"), str(home / "Downloads")]
    return {"home": home, "roots": roots, "data": tmp_path / "data", **paths}


def scanned(tree, jobs=None):
    lib = Library(tree["roots"], str(tree["data"]), home=str(tree["home"]), jobs=lambda: jobs or {})
    lib.scan_now()
    return lib


def film_named(lib, name):
    matches = [f for f in lib.overview()["films"] if f["name"] == name]
    assert matches, [f["name"] for f in lib.overview()["films"]]
    return lib.film(matches[0]["id"])


# ------------------------------------------------------------------------------------------------ metadata
def test_mp4_duration_parses_mvhd_versions_and_layouts(tmp_path):
    cases = {
        "v0.mp4": (mp4_bytes(12.5), 12.5),
        "v1.mp4": (mp4_bytes(90.0, version=1, timescale=600), 90.0),
        "end.mp4": (mp4_bytes(33.0, moov_last=True), 33.0),
        "large.mov": (mp4_bytes(7.25, moov_last=True, large_mdat=True), 7.25),
    }
    for name, (data, expected) in cases.items():
        assert mp4_duration(str(put(tmp_path / name, data))) == expected, name
    eof = mp4_bytes(5.0)  # moov first, then an mdat whose size 0 means "to the end of the file"
    eof = eof[: eof.index(b"mdat") - 4] + struct.pack(">I4s", 0, b"mdat") + b"\x00" * 32
    assert mp4_duration(str(put(tmp_path / "eof.mp4", eof))) == 5.0
    assert mp4_duration(str(put(tmp_path / "junk.mp4", b"not a movie at all"))) is None
    assert mp4_duration(str(put(tmp_path / "cut.mp4", mp4_bytes(4.0, moov_last=True)[:-40]))) is None
    assert mp4_duration(str(tmp_path / "missing.mp4")) is None


def test_fingerprint_and_sidecars(tmp_path):
    a = put(tmp_path / "a/master.mp4", b"x" * 200_000)
    b = put(tmp_path / "b/master.mp4", b"x" * 200_000)
    c = put(tmp_path / "c/master.mp4", b"x" * 199_999 + b"y")
    assert quick_fingerprint(str(a), 200_000) == quick_fingerprint(str(b), 200_000) != quick_fingerprint(str(c), 200_000)
    names = {n.lower(): n for n in ("master.mp4", "master-poster.jpg", "poster.png", "captions.en.srt", "master.vtt")}
    poster, captions = sidecars("/x", "master", names)
    assert poster == "/x/master-poster.jpg" and captions == "/x/master.vtt"
    poster, captions = sidecars("/x", "other", names)
    assert poster == "/x/poster.png" and captions == "/x/captions.en.srt"


@pytest.mark.parametrize("path, role", [
    ("/h/GitHub/x/candidates/v2/n14_video_assembly/master.mp4", "master"),
    ("/h/GitHub/x/outputs/v14/orbit-hub-v14-master.mp4", "master"),
    ("/h/GitHub/x/demo-orbit-r07-review.mp4", "review"),
    ("/h/GitHub/x/demo-orbit-r07-silent.mp4", "silent"),
    ("/h/GitHub/x/n13_motion_scenes/film.mp4", "picture"),
    ("/h/GitHub/x/cuts/V01/rough-cut.mp4", "rough"),
    ("/h/Downloads/Water Video/Water_Intelligence_preview_reel.mp4", "rough"),
    ("/h/GitHub/x/clips/03-people.mp4", "clip"),
    ("/h/GitHub/x/seg2.mp4", "clip"),
    ("/h/GitHub/x/B12.mp4", "clip"),
    ("/h/Downloads/Full CC 60mins [02_12_26].mp4", "recording"),
    ("/h/GitHub/Physical-AI/eval_episode_3.mp4", "eval"),
    ("/h/GitHub/x/video/vpg/sources/capture_TENANT/prototypes-hd/a-master.mp4", "source"),
    ("/h/GitHub/Demo_portfolio/src/assets/work/example/film.mp4", "published"),
    ("/h/Downloads/Archive/b/candidates/v3/V06-questions.mp4", "master"),
    ("/h/GitHub/x/random-name.mp4", "other"),
])
def test_roles(path, role):
    assert classify_role(path)[0] == role


def test_versions_from_paths_and_names():
    assert version_of("/r/revisions/round-07/outputs/r07-v15/a.mp4", "/r")[0] == "r07-v15"
    assert version_of("/r/outputs/v14/orbit-v14-master.mp4", "/r")[0] == "v14"
    assert version_of("/r/demo-orbit-r07-master.mp4", "/r")[0] == "r07"
    assert version_of("/r/work/n14_video_assembly/a7/master.mp4", "/r")[0] == "a7"
    assert version_of("/r/work/n13_motion_scenes/a7/film.mp4", "/r")[0] == "n13 a7"
    assert version_of("/r/work/n13_motion_scenes/a7/film.mp4", "/r", attempts_bucket=True)[0] == "N13 attempts"
    assert version_of("/r/plain/folder/x.mp4", "/r")[0] == "plain/folder"
    assert family_of("v14a") == family_of("v3") == "v#"


# ------------------------------------------------------------------------------------------------ grouping
def test_grouping_on_a_synthetic_mac(tree):
    demo_pid = "0123456789"
    lib = scanned(tree, jobs={str(tree["demo"]): demo_pid})
    overview = lib.overview()
    assert overview["status"] == "ready"
    paths = {item["path"] for item in lib._snapshot()["items"].values()}
    assert not any(part in p for p in paths for part in ("node_modules", ".hidden", ".git", "linkdir"))
    assert overview["counts"]["videos"] == 28

    demo = film_named(lib, "Demo film")
    assert demo["job_pid"] == demo_pid and demo["kind"] == "vpg" and not demo["archived"]
    labels = [v["label"] for v in demo["versions"]]
    assert labels == ["v3", "v2", "v1", "N13 attempts"]
    v3 = demo["versions"][0]
    assert v3["primary"]["version_why"].startswith("governed version v3") and v3["primary"]["duration_s"] == 62.0
    v2 = demo["versions"][1]
    assert v2["primary"]["name"] == "master.mp4" and v2["primary"]["duration_s"] == 61.5
    assert sorted(m["role"] for m in v2["variants"]) == ["picture", "silent"]
    assert v2["primary"]["has_poster"] and v2["primary"]["has_captions"]

    water = film_named(lib, "Water_hub")
    labels = [v["label"] for v in water["versions"]]
    assert labels[0] == "2026-10-04-r08-v16" and "r07-v15" in labels and "r07-v07" in labels
    r15 = next(v for v in water["versions"] if v["label"] == "r07-v15")
    assert r15["primary"]["name"] == "demo-orbit-r07-master.mp4"
    assert sorted(m["role"] for m in r15["variants"]) == ["clip", "review", "silent"]
    assert [v["label"] for v in film_named(lib, "Orbit_hub")["versions"]] == ["v14"]

    archived = film_named(lib, "Example film")
    assert archived["archived"] and archived["job_pid"] is None and [v["label"] for v in archived["versions"]] == ["a2", "a1"]
    variants = film_named(lib, "20261002-self-intro / vpg")
    assert variants["archived"] and sorted(v["label"] for v in variants["versions"]) == ["_blocked/cand-C", "cand-A", "cand-B"]
    cand_b = next(v for v in variants["versions"] if v["label"] == "cand-B")
    assert len(cand_b["variants"]) == 1 and "variant project cand-B" in cand_b["primary"]["version_why"]
    assert [v["label"] for v in film_named(lib, "20261002-self-intro")["versions"]] == ["v3 / v06"]
    v7 = film_named(lib, "V7_Delivery")
    assert [v["label"] for v in v7["versions"]] == ["v7"] and v7["versions"][0]["primary"]["role"] == "master"

    other = {c["label"]: c for c in overview["other"]}
    published = lib.collection(other["Published on the portfolio site"]["id"])
    copy = published["items"][0]
    assert copy["role"] == "published" and copy["duplicate_of"]["place"] == "Demo film / v1"
    assert any("Meeting Recording" in c["label"] or c["roles"] == ["recording"] for c in overview["other"])
    assert any(c["roles"] == ["eval"] for c in overview["other"])
    assert any(c["roles"] == ["source"] for c in overview["other"])
    assert any("round-03" in c["label"] and c["roles"] == ["clip"] for c in overview["other"])
    mkv = next(i for i in lib._snapshot()["items"].values() if i["name"] == "raw-take.mkv")
    assert mkv["playable"] is False and mkv["duration_s"] is None


def test_cache_reuse_and_writes_only_the_data_dir(tree, monkeypatch):
    def snapshot():
        out = {}
        for root, _, files in os.walk(tree["home"]):
            for name in files:
                st = os.lstat(os.path.join(root, name))
                out[os.path.join(root, name)] = (st.st_size, st.st_mtime_ns)
        return out
    before = snapshot()
    lib = scanned(tree)
    assert snapshot() == before
    assert sorted(os.listdir(tree["data"])) == ["library-index.json"]
    calls = []
    real = libmod.quick_fingerprint
    monkeypatch.setattr(libmod, "quick_fingerprint", lambda path, size: calls.append(path) or real(path, size))
    again = scanned(tree)
    assert calls == [] and again.overview()["counts"]["videos"] == 28
    changed = tree["water"] / "revisions/round-07/outputs/r07-v07/demo-orbit-r07-master.mp4"
    put(changed, mp4_bytes(44.0, payload=b"changed"))
    third = scanned(tree)
    assert calls == [str(changed)]
    item = next(i for i in third._snapshot()["items"].values() if i["path"] == str(changed))
    assert item["duration_s"] == 44.0
    assert lib.status()["status"] == "ready"


def test_owner_map_overrides(tree):
    tree["data"].mkdir(parents=True, exist_ok=True)
    (tree["data"] / "library-map.json").write_text(json.dumps([
        {"match": "V7_Delivery", "film": "Water V7 delivery", "version_regex": "(V7)_"},
        {"match": "Team Sync", "film": "Team syncs", "role": "master"},
        {"match": "(", "film": "broken rule is skipped"},
    ]))
    lib = scanned(tree)
    v7 = film_named(lib, "Water V7 delivery")
    assert [v["label"] for v in v7["versions"]] == ["V7"] and "owner map rule 1" in v7["versions"][0]["primary"]["version_why"]
    sync = film_named(lib, "Team syncs")
    assert sync["versions"][0]["primary"]["role_why"] == "owner map rule 2"


# ------------------------------------------------------------------------------------------------ HTTP
@pytest.fixture
def served(tree, tmp_path):
    engine = write_engine(tmp_path)
    projects_root = tree["home"] / "GitHub" / "Demo_workspace" / "01_Project-Hubs"
    server = make_server([str(projects_root)], str(engine), str(tree["data"]), port=0, quiet=True,
                         library_roots=tree["roots"], library_home=str(tree["home"]))
    server.app.registry.scan()
    server.app.library.scan_now()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"port": server.app.port, "app": server.app, **tree}
    server.shutdown()
    server.server_close()
    thread.join(5)


def call(ctx, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", ctx["port"], timeout=10)
    sent = {"Host": f"127.0.0.1:{ctx['port']}"}
    if body is not None:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    payload = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    data = response.read()
    status, received = response.status, response.headers
    connection.close()
    return status, received, data


def get_json(ctx, path, status=200):
    code, _, data = call(ctx, "GET", path)
    assert code == status, (code, data[:300])
    return json.loads(data)


def demo_items(ctx):
    overview = get_json(ctx, "/api/library")
    film = next(f for f in overview["films"] if f["name"] == "Demo film")
    detail = get_json(ctx, f"/api/library/films/{film['id']}")
    return film, detail


def test_library_api_and_media(served):
    meta = get_json(served, "/api/meta")
    assert meta["library"]["enabled"] is True and meta["library"]["status"] == "ready"
    film, detail = demo_items(served)
    assert film["job_pid"] == served["app"].registry.projects()[0].id
    v2 = detail["versions"][1]["primary"]
    status = get_json(served, "/api/library/status")
    assert status["counts"]["films"] == len(get_json(served, "/api/library")["films"])
    page = get_json(served, "/api/library/items?q=demo-orbit&sort=name&limit=2")
    assert page["total"] == 5 and len(page["items"]) == 2
    assert get_json(served, "/api/library/items?role=nope", status=400)
    item = get_json(served, f"/api/library/items/{v2['id']}")
    assert item["film_summary"]["name"] == "Demo film" and item["version"] == "v2"
    copy = get_json(served, f"/api/library/items/{detail['versions'][2]['primary']['id']}")
    assert [m["role"] for m in copy["same_file"]] == ["published"]

    code, headers, body = call(served, "GET", f"/media/lib/{v2['id']}", headers={"Range": "bytes=0-15"})
    assert code == 206 and headers["Content-Type"] == "video/mp4" and headers["Cache-Control"] == "no-store"
    assert headers["Content-Range"].startswith("bytes 0-15/") and body == open(v2["path"], "rb").read()[:16]
    code, headers, body = call(served, "GET", f"/media/lib/{v2['id']}/poster")
    assert code == 200 and headers["Content-Type"] == "image/jpeg"
    code, headers, body = call(served, "GET", f"/media/lib/{v2['id']}/captions.vtt")
    assert code == 200 and body.startswith(b"WEBVTT") and b"00:00:01.000 --> 00:00:02.500" in body
    for bad in ("/media/lib/0000000000000000", "/media/lib/zz", "/media/lib/..%2F..%2Fetc%2Fpasswd",
                f"/media/lib/{v2['id']}/other", f"/media/lib/{detail['versions'][0]['primary']['id']}/poster"):
        assert call(served, "GET", bad)[0] == 404, bad
    os.remove(v2["path"])
    assert call(served, "GET", f"/media/lib/{v2['id']}")[0] == 404
    v1 = detail["versions"][2]["primary"]
    os.remove(v1["path"])
    os.symlink(str(served["home"] / "elsewhere/outside-master.mp4"), v1["path"])
    assert call(served, "GET", f"/media/lib/{v1['id']}")[0] == 404


def test_rescan_checks_and_conflict(served, monkeypatch):
    library = served["app"].library
    assert call(served, "POST", "/api/library/rescan", {}, {"Origin": "http://evil.example"})[0] == 403
    assert call(served, "POST", "/api/library/rescan", {}, {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(served, "POST", "/api/library/rescan", b"{}", {"Content-Type": "text/plain"})[0] == 415
    assert call(served, "POST", "/api/library/rescan", {"roots": ["/"]})[0] == 400
    gate = threading.Event()
    real_walk = library._walk
    monkeypatch.setattr(library, "_walk", lambda: (gate.wait(5), real_walk())[1])
    code, _, body = call(served, "POST", "/api/library/rescan", {}, {"Origin": f"http://127.0.0.1:{served['port']}"})
    assert code == 202 and json.loads(body)["status"]["status"] == "scanning"
    assert get_json(served, "/api/library")["counts"]["videos"] == 28
    assert call(served, "POST", "/api/library/rescan", {})[0] == 409
    gate.set()
    library.wait(10)
    assert get_json(served, "/api/library/status")["status"] == "ready"


def test_library_reactions(served):
    film, detail = demo_items(served)
    v2 = detail["versions"][1]["primary"]
    v1 = detail["versions"][2]["primary"]
    note = {"pid": "library", "kind": "note", "target": {"type": "library", "id": v2["id"], "t": 1.5}, "text": "Too fast here"}
    code, _, body = call(served, "POST", "/api/reactions", note)
    assert code == 201, body
    record = json.loads(body)
    assert record["project_path"] is None and record["library"]["film"] == "Demo film" and record["library"]["version"] == "v2"
    assert route_of(record) == f"#/library/f/{film['id']}?i={v2['id']}&t=1.5"
    assert target_label(record) == "Library video Demo film / v2: master.mp4"
    bad = [
        {**note, "text": ""},
        {**note, "target": {"type": "library", "id": "0" * 16}},
        {**note, "target": {"type": "library", "id": "../../etc/passwd"}},
        {**note, "target": {"type": "version", "id": "v2"}},
        {**note, "target": {"type": "library", "id": v2["id"], "path": "/etc"}},
        {**note, "kind": "approve"},
        {**note, "version": "v2"},
        {"pid": "library", "kind": "prefer", "a": v1["id"], "b": v1["id"], "choice": "a"},
        {"pid": "library", "kind": "prefer", "a": v1["id"], "b": v2["id"], "choice": "maybe"},
    ]
    for body_ in bad:
        assert call(served, "POST", "/api/reactions", body_)[0] == 400, body_
    pid = served["app"].registry.projects()[0].id
    assert call(served, "POST", "/api/reactions", {**note, "pid": pid})[0] == 400
    prefer = {"pid": "library", "kind": "prefer", "a": v1["id"], "b": v2["id"], "choice": "b", "text": "B reads better"}
    code, _, body = call(served, "POST", "/api/reactions", prefer)
    assert code == 201
    record = json.loads(body)
    assert record["target"] == {"type": "library", "id": v2["id"]}
    assert route_of(record) == f"#/library/f/{film['id']}/compare?a={v1['id']}&b={v2['id']}"
    ack = {"pid": "library", "kind": "ack", "ref": record["id"], "text": "noted"}
    assert call(served, "POST", "/api/reactions", ack, {"X-Actor": "agent"})[0] == 201
    listed = get_json(served, "/api/reactions?pid=library")
    assert [r["kind"] for r in listed] == ["prefer", "note"] and listed[0]["acks"] and listed[0]["project"] == "Library"
    code, _, text = call(served, "GET", "/api/reactions/export?format=md&pid=library")
    assert code == 200 and b"Library videos" in text and b"Demo film / v2" in text and b"Prefer B (master.mp4)" in text


def test_library_disabled_by_default(tmp_path):
    engine = write_engine(tmp_path)
    (tmp_path / "roots").mkdir()
    server = make_server([str(tmp_path / "roots")], str(engine), str(tmp_path / "data"), port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ctx = {"port": server.app.port}
    try:
        assert get_json(ctx, "/api/meta")["library"]["enabled"] is False
        assert get_json(ctx, "/api/library")["enabled"] is False
        assert call(ctx, "GET", "/api/library/films/0123456789abcdef")[0] == 404
        assert call(ctx, "GET", "/media/lib/0123456789abcdef")[0] == 404
        assert call(ctx, "POST", "/api/library/rescan", {})[0] == 409
        note = {"pid": "library", "kind": "note", "target": {"type": "library", "id": "0123456789abcdef"}, "text": "x"}
        code, _, body = call(ctx, "POST", "/api/reactions", note)
        assert code == 400 and b"disabled" in body
        assert not os.path.exists(tmp_path / "data" / "library-index.json")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_background_start_serves_cache_then_rescans(tree):
    first = scanned(tree)
    assert first.status()["status"] == "ready"
    lib = Library(tree["roots"], str(tree["data"]), home=str(tree["home"]))
    gate = threading.Event()
    real_walk = lib._walk
    lib._walk = lambda: (gate.wait(5), real_walk())[1]
    assert lib.start() is True
    status = lib.status()
    assert status["status"] == "scanning" and status["from_cache"] and status["counts"]["videos"] == 28
    assert lib.rescan() is False
    gate.set()
    lib.wait(10)
    deadline = time.time() + 5
    while lib.status()["status"] != "ready" and time.time() < deadline:
        time.sleep(0.05)
    assert lib.status()["status"] == "ready" and not lib.status()["from_cache"]


def test_videos_outside_films_have_no_version(tree):
    lib = scanned(tree)
    other = {c["label"]: c for c in lib.overview()["other"]}
    published = lib.collection(other["Published on the portfolio site"]["id"])
    assert all(item["version"] is None and item["version_why"] is None for item in published["items"])
    copy = published["items"][0]
    assert lib.context_of(copy["id"])["version"] is None and lib.item_detail(copy["id"])["collection"]["label"] == "Published on the portfolio site"
