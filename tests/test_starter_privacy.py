"""Portable initialization and private runtime data boundaries."""
import json
import importlib.util
from pathlib import Path

import pytest
import yaml
from PIL import Image

from vpg import cli, scoring
from vpg.engine import Engine, EngineError
from vpg.memory import DEFAULT_RULES
from vpg.util import PKG_ROOT


def test_init_copies_policy_without_history_into_the_private_project(tmp_path):
    project = tmp_path / "film"
    assert cli.main(["init", str(project), "--human", "Example Reviewer"]) == 0
    config = yaml.safe_load((project / "vpg.project.yaml").read_text())
    assert config["humans"] == ["Example Reviewer"]
    assert config["rules"] == ".vpg/rules.json"
    private = project / config["rules"]
    assert private.read_bytes() == DEFAULT_RULES.read_bytes()
    eng = Engine(project)
    assert eng.rules_path == private
    assert eng._principal() == "Example Reviewer"
    assert eng._preference_store() == project / ".vpg/preferences/example-reviewer"
    assert not eng._preference_store().exists()


def test_a_project_without_a_named_principal_cannot_record_preferences(tmp_path):
    project = tmp_path / "film"
    assert cli.main(["init", str(project)]) == 0
    with pytest.raises(EngineError, match="declare a named reviewer"):
        Engine(project)._principal()


def test_init_never_writes_project_data_into_the_checkout(capsys):
    target = PKG_ROOT / "private-film"
    assert cli.main(["init", str(target)]) == 2
    assert "outside the toolkit" in capsys.readouterr().err
    assert not target.exists()
    with pytest.raises(EngineError, match="outside the toolkit"):
        Engine(target)
    assert not target.exists()
    assert cli.main(["run", str(target)]) == 4
    assert not target.exists()


def test_cli_scorer_requires_an_external_store():
    with pytest.raises(SystemExit) as error:
        scoring.main(["--data", str(PKG_ROOT / "preferences"), "report"])
    assert error.value.code == 2


def test_policy_seed_contains_only_reusable_defaults():
    from collections import Counter
    data = json.loads(DEFAULT_RULES.read_text())
    assert len(data["rules"]) == 51
    assert Counter(r["default_enforcement"] for r in data["rules"]) == {"enforced": 38, "advisory": 12, "inactive": 1}
    for rule in data["rules"]:
        assert rule["scope"] != "project_specific"
        assert rule["lifecycle"] == {"state": "builtin", "history": []}
        assert rule["evidence"] == []
        assert not {"origin", "calibration", "observations", "previous_versions"} & rule.keys()


@pytest.mark.parametrize("placeholder", ["<name>", "<reviewer>", "agent:worker", "dry-run-owner"])
def test_template_placeholders_are_not_human_approvals(placeholder):
    from vpg.util import is_human_name
    assert not is_human_name(placeholder)


def test_init_refuses_unfilled_reviewer_before_creating_files(tmp_path, capsys):
    project = tmp_path / "film"
    assert cli.main(["init", str(project), "--human", "<reviewer>"]) == 2
    assert "--human must name" in capsys.readouterr().err
    assert not project.exists()


def test_memory_cli_refuses_shipped_policy_before_creating_a_lock(capsys):
    before = {p.name: p.read_bytes() for p in (PKG_ROOT / "knowledge").iterdir() if p.is_file()}
    assert cli.main(["memory", "promote", "--rule", "R-LEG-001", "--to", "candidate",
                     "--by", "Example Reviewer", "--human", "--evidence", "example"]) == 4
    assert "shipped policy is read-only" in capsys.readouterr().err
    after = {p.name: p.read_bytes() for p in (PKG_ROOT / "knowledge").iterdir() if p.is_file()}
    assert after == before


def test_release_packet_uses_the_same_private_preference_store_as_the_engine(tmp_path):
    from vpg.tools import verify
    project = tmp_path / "film"
    assert cli.main(["init", str(project), "--human", "Example Reviewer"]) == 0
    eng = Engine(project)
    store = eng._preference_store()
    (store / "models").mkdir(parents=True)
    model = scoring.new_model("Example Reviewer")
    model["version"] = 1
    (store / "models/model-v1.json").write_text(json.dumps(model))
    (store / "current.json").write_text('{"version": 1}')
    assert verify._current_model(project, "Example Reviewer") == model


def _raster(path: Path) -> tuple[str, tuple[int, int], bytes]:
    with Image.open(path) as image:
        return image.mode, image.size, image.tobytes()


def test_sample_images_are_reproducible_from_geometric_fixture_code(tmp_path):
    sample = PKG_ROOT / "examples/sample-demo"
    spec = importlib.util.spec_from_file_location("sample_fixture_images", sample / "pipeline/fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.generate(tmp_path)
    # PNG bytes are not portable: Pillow wheels bundle different zlib builds.
    for name in ("sources/ui/board.png", "assets/brand/shape-mark.png"):
        assert _raster(sample / name) == _raster(tmp_path / name), name
    name = "sources/ui/board-regions.json"
    assert (sample / name).read_bytes() == (tmp_path / name).read_bytes(), name
