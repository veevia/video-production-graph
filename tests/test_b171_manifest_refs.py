"""#171: a consumer re-runs when a file its input manifest names by path changes, even if the manifest's bytes didn't.

Water r08 (E29): N13 a19 re-rendered `film.mp4`, but `scenes.json` names it only as `"video": "film.mp4"` and no beat
changed, so N14 memo-hit on the unchanged manifest and kept v15's master; N15–N20 memo-hit after it. Nothing warned.
"""
from __future__ import annotations

import hashlib
import json

import yaml

from tests.conftest import FnExecutor, flag
from tests.test_bookkeeping import ROUTES, events
from tests.test_hardening import make_project
from vpg import engine as engine_mod
from vpg.engine import Engine
from vpg.provenance import Ledger
from vpg.util import artifact_identity

sha = lambda b: hashlib.sha256(b).hexdigest()                                  # noqa: E731


def _plan_with_film(take: str | None = None):
    def handler(ctx):
        (ctx.workdir / "film.mp4").write_bytes((take or f"film take {ctx.attempt}").encode())
        (ctx.workdir / "b_plan.json").write_text('{"video": "film.mp4", "note": "unchanged manifest"}')
        return {"b_plan": "b_plan.json"}
    return handler


def _after(proj, seq, node):
    return [e["type"] for e in Ledger(proj / ".vpg/ledger.jsonl").events()
            if e["seq"] > seq and e["type"] in ("memo_hit", "node_dispatched") and e["data"].get("node") == node]


def _rerender(proj, eng):
    seq = max(e["seq"] for e in Ledger(proj / ".vpg/ledger.jsonl").events())
    eng.invalidate_nodes(["b_plan"], reason="re-render the film", actor="Wei")
    eng.run(max_steps=200)
    return _after(proj, seq, "d_build")


def test_a_re_rendered_referenced_file_re_runs_the_consumer(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor({"b_plan": _plan_with_film()}))
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    [plan] = eng.current("b_plan")
    assert plan.named_files == {"film.mp4": sha(b"film take 1")}
    assert _rerender(proj, eng)[:1] == ["node_dispatched"]                 # not a memo hit on the manifest's bytes
    assert eng.current("b_plan")[0].named_files == {"film.mp4": sha(b"film take 2")}


def test_a_byte_identical_re_render_still_memo_passes(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor({"b_plan": _plan_with_film(take="the same film")}))
    eng.run(max_steps=200)
    assert _rerender(proj, eng)[:1] == ["memo_hit"]                        # a new attempt folder alone changes nothing


def _scan(tmp_path, monkeypatch, manifest: dict, **limits):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    work = proj / "work" / "n13" / "a1"
    work.mkdir(parents=True, exist_ok=True)
    for name, value in limits.items():
        monkeypatch.setattr(engine_mod, name, value)
    return proj, eng, work


def test_which_strings_count_as_references(tmp_path, monkeypatch):
    proj, eng, work = _scan(tmp_path, monkeypatch, {}, LARGE_REF_BYTES=5)
    (work / "film.mp4").write_bytes(b"film")
    (work / "big.mov").write_bytes(b"0123456789")
    (work / "sub").mkdir()
    (proj / "sources").mkdir(exist_ok=True)
    (proj / "sources" / "brief.md").write_text("brief")
    (tmp_path / "outside.txt").write_text("outside")
    manifest = work / "scenes.json"
    manifest.write_text(json.dumps({
        "video": "film.mp4", "beats": [{"brief": "sources/brief.md", "caption": "Shared data."}],
        "absolute": str((proj / "sources" / "brief.md").resolve()), "url": "https://example.com/film.mp4", "missing": "gone.mp4",
        "outside": "../../../../outside.txt", "state": ".vpg/state.json", "self": "scenes.json", "folder": "sub/", "raw": "big.mov"}))
    named, stats = eng._manifest_refs(manifest.resolve())
    assert set(named) == {"film.mp4", "sources/brief.md", "big.mov"}                 # the absolute path keyed project-relative
    assert named["sources/brief.md"] == sha(b"brief") and named["film.mp4"] == sha(b"film")
    assert named["big.mov"].startswith("size:10:mtime:")                             # over the size limit
    assert stats == {"hashed": 3, "folded": 0, "truncated": False}
    (work / "broken.json").write_text("{not json")
    assert eng._manifest_refs((work / "broken.json").resolve())[0] == {}


def test_past_the_cap_files_fold_and_top_level_comes_first(tmp_path, monkeypatch):
    proj, eng, work = _scan(tmp_path, monkeypatch, {}, MAX_MANIFEST_REFS=3)
    (work / "film.mp4").write_bytes(b"film")
    for i in range(300):
        (work / f"b{i:03d}.png").write_bytes(f"still {i}".encode())
    manifest = work / "scenes.json"
    manifest.write_text(json.dumps({"scenes": [{"still": f"b{i:03d}.png"} for i in range(300)], "video": "film.mp4"}))
    named, stats = eng._manifest_refs(manifest.resolve())
    assert "film.mp4" in named and len(named) == 4 and "<+298 more>" in named        # breadth-first: the film is never dropped
    assert stats == {"hashed": 3, "folded": 298, "truncated": False}
    (work / "b299.png").write_bytes(b"a re-rendered still, longer")                   # past the cap, still seen
    assert eng._manifest_refs(manifest.resolve())[0]["<+298 more>"] != named["<+298 more>"]
    monkeypatch.setattr(engine_mod, "MAX_MANIFEST_STRINGS", 2)
    first, stats = eng._manifest_refs(manifest.resolve())
    assert stats["truncated"] is True and "<scan stopped>" in first                  # what wasn't read isn't vouched for
    assert eng._manifest_refs(manifest.resolve())[0]["<scan stopped>"] != first["<scan stopped>"]   # so consumers re-run


def test_the_digest_cache_sees_a_same_size_rewrite_with_its_mtime_restored(tmp_path, monkeypatch):
    import os
    from vpg.source_store import SourceStore
    proj, eng, work = _scan(tmp_path, monkeypatch, {})
    monkeypatch.setattr(SourceStore, "RACY_S", 0.0)                                   # cache at once, for the test
    film = work / "film.mp4"
    film.write_bytes(b"take A")
    before = film.stat()
    assert eng._file_digest(film, before) == sha(b"take A")
    film.write_bytes(b"take B")                                                       # same size, in place
    os.utime(film, ns=(before.st_atime_ns, before.st_mtime_ns))                       # mtime set back; ctime moves anyway
    assert eng._file_digest(film, film.stat()) == sha(b"take B")


def test_a_folded_registration_is_ledgered(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "MAX_MANIFEST_REFS", 2)

    def plan(ctx):
        for i in range(5):
            (ctx.workdir / f"f{i}.png").write_bytes(f"still {i}".encode())
        (ctx.workdir / "b_plan.json").write_text(json.dumps({"stills": [f"f{i}.png" for i in range(5)]}))
        return {"b_plan": "b_plan.json"}
    proj = make_project(tmp_path)
    Engine(proj, executor=FnExecutor({"b_plan": plan})).run(max_steps=200)
    [event] = events(proj, "manifest_refs_folded")
    assert {k: event[k] for k in ("node", "artifact", "hashed", "folded", "truncated")} == \
        {"node": "b_plan", "artifact": "b_plan", "hashed": 2, "folded": 3, "truncated": False}


def test_commands_get_each_inputs_identity(tmp_path):
    proj = make_project(tmp_path)
    (proj / "tools").mkdir()
    (proj / "tools/cmd.py").write_text(
        "import json, os, pathlib\n"
        "ctx = json.load(open(os.environ['VPG_CONTEXT']))\n"
        "w = pathlib.Path(ctx['workdir'])\n"
        "(w / 'd_build.json').write_text('{}')\n"
        "(w / 'outputs.json').write_text(json.dumps({'outputs': {'d_build': 'd_build.json'}, 'data': {'seen': ctx['inputs_identity']}}))\n")
    cfg = yaml.safe_load((proj / "vpg.project.yaml").read_text())
    cfg["commands"] = {"d_build": "{python} tools/cmd.py"}
    (proj / "vpg.project.yaml").write_text(yaml.safe_dump(cfg))
    eng = Engine(proj, executor=FnExecutor({"b_plan": _plan_with_film()}))
    eng.run(max_steps=200)
    seen = eng.rt("d_build").result_data["seen"]
    [plan] = eng.current("b_plan")
    assert seen["b_plan"] == [artifact_identity(plan)] != [plan.sha256]                # covers the film, not only the json


def test_a_film_only_rework_shows_as_changed(tmp_path):
    proj = make_project(tmp_path, review_tool=ROUTES)
    flag(proj, "defect_plan")                                                          # a defect traced to b_plan, which reworks
    eng = Engine(proj, executor=FnExecutor({"b_plan": _plan_with_film()}))
    for _ in range(300):
        eng.step()
        if any(c.after_version >= 2 for c in eng.state.evaluation.comparisons):
            break
    changed = next(c for c in eng.state.evaluation.comparisons if c.after_version >= 2).artifacts_changed
    assert "b_plan" in changed                                                         # only film.mp4 changed in the rework


def test_an_artifact_without_named_files_keeps_its_identity(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    for name in ("b_plan", "d_build"):
        for a in eng.current(name):
            assert a.named_files == {} and artifact_identity(a) == a.sha256    # fingerprints of earlier runs don't move


def test_a_fresh_file_is_hashed_once_per_registration_but_not_cached(tmp_path, monkeypatch):
    from vpg.source_store import SourceStore
    from vpg.util import sha256_file as real_sha
    proj, eng, work = _scan(tmp_path, monkeypatch, {})
    monkeypatch.setattr(SourceStore, "RACY_S", 3600.0)                                 # "fresh" however slow the runner
    film = work / "film.mp4"
    film.write_bytes(b"just rendered")
    calls = []
    monkeypatch.setattr(engine_mod, "sha256_file", lambda p: calls.append(p) or real_sha(p))
    memo: dict = {}
    for name in ("scenes.json", "takes.json", "cast.json"):                              # three manifests name the film
        (work / name).write_text(json.dumps({"video": "film.mp4"}))
        assert eng._manifest_refs((work / name).resolve(), memo)[0] == {"film.mp4": sha(b"just rendered")}
    assert len(calls) == 1                                                                # hashed once for the registration
    assert not getattr(eng, "_digests", {})                                               # changed too recently: never cached
