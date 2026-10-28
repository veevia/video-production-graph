import hashlib
import os

from glassbox import fsio, miniyaml
from glassbox.discovery import Registry, find_project_dirs, load_project, project_id
from synthetic import engine_yaml, make_job


def test_discovery_finds_projects_and_skips_noise(tmp_path):
    root = tmp_path / "roots"
    make_job(root)
    make_job(root, rel="other/deep/a/b/c/vpg", name="Second", slug="second")
    for skipped in ("node_modules/x", ".git/y", "candidates/z", "archive/old", "_archive-2025/old", "render/r", "outputs/o"):
        target = root / "hub" / skipped
        target.mkdir(parents=True)
        (target / "vpg.project.yaml").write_text("id: should-not-appear\n")
    too_deep = root / "/".join(f"d{i}" for i in range(10))
    too_deep.mkdir(parents=True)
    (too_deep / "vpg.project.yaml").write_text("id: too-deep\n")
    found = find_project_dirs(str(root))
    names = sorted(os.path.relpath(p, root) for p in found)
    assert names == ["hub/film/video/vpg", "other/deep/a/b/c/vpg"]


def test_project_identity_and_names(tmp_path):
    root = tmp_path / "roots"
    job = make_job(root)
    project = load_project(str(job), str(root))
    assert project.id == hashlib.sha1(os.path.abspath(str(job)).encode()).hexdigest()[:10] == project_id(str(job))
    assert project.name == "Demo film \u2014 synthetic"
    assert project.slug == "demo-film"
    bare = root / "bare" / "vpg"
    bare.mkdir(parents=True)
    (bare / "vpg.project.yaml").write_text("workflow: default\n")
    assert load_project(str(bare), str(root)).name == "bare/vpg"
    only_id = root / "only-id" / "vpg"
    only_id.mkdir(parents=True)
    (only_id / "vpg.project.yaml").write_text("id: just-an-id\n")
    assert load_project(str(only_id), str(root)).name == "just-an-id"


def test_registry_lookup(tmp_path):
    root = tmp_path / "roots"
    job = make_job(root)
    registry = Registry([str(root), str(tmp_path / "missing-root")])
    projects = registry.scan()
    assert [p.path for p in projects] == [str(job)]
    assert registry.get(projects[0].id).path == str(job)
    assert registry.get("not-a-pid") is None
    assert registry.get("0123456789") is None


def test_miniyaml_matches_pyyaml_on_engine_style():
    text = engine_yaml()
    parsed = miniyaml.loads(text)
    assert len(parsed["nodes"]) == 25
    node = parsed["nodes"][2]
    assert node["human_review"] == {"required": True, "decision": "Decide on node 3", "approver": "Owner", "outcomes": ["approved", "rejected"]}
    assert node["outputs"][0]["key"] == "out3" and node["escalation"]["after"] == "3 failures"
    try:
        import yaml
    except ImportError:  # pragma: no cover - environment without PyYAML
        return
    assert parsed == yaml.safe_load(text)


def test_miniyaml_project_file_and_block_scalars():
    text = (
        'id: demo\nname: "Demo \\u2014 film"\nconstraints:\n- \'Runtime: 60 s, read\n  across lines\'\n'
        "- plain item # with a comment\ndescription: >-\n  Folded text\n  continues here.\n"
        "literal: |\n  line one\n  line two\nempty:\nnested:\n  a: 1\n  b: [x, 'y z', \"w\"]\n"
    )
    parsed = miniyaml.loads(text)
    assert parsed["name"] == "Demo \u2014 film"
    assert parsed["constraints"] == ["Runtime: 60 s, read across lines", "plain item"]
    assert parsed["description"] == "Folded text continues here."
    assert parsed["literal"] == "line one\nline two\n"
    assert parsed["empty"] is None
    assert parsed["nested"] == {"a": 1, "b": ["x", "y z", "w"]}
    assert fsio.parse_yaml(text, force_fallback=True) == parsed
