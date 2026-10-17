"""#98: a density exception for one scene. R-DEN-001's film-wide limit (8) is a first estimate awaiting Wei's calibration;
SRA's B02 arrival beat shows 18 labelled tiles at once, as the approved reference does. The only way to accept that was
CR-007's film-wide limit of 18, which would stop the check for every other scene. `thresholds.density.scene_limits`
in evaluation.md sets one scene's limit; every other scene keeps the film-wide one."""

from __future__ import annotations

import json

import pytest

from tests.test_tools import Ctx, layer
from vpg.tools import legibility


def crowd(scene: str, n: int) -> list[dict]:
    return [layer(id=f"{scene}-{i}", scene=scene, text=f"label {i}", bbox=[200, 100 + (i % 12) * 60, 300, 40]) for i in range(n)]


def density(tmp_path, layers, scene_limits=None) -> list:
    fm = {"evaluation": {"thresholds": {"density": {"scene_limits": scene_limits}}}} if scene_limits is not None else {}
    ctx = Ctx(tmp_path, loads={"text_layers": {"frame": {"w": 1920, "h": 1080}, "layers": layers}, "timings": {"units": []}}, fm=fm)
    return [f for f in legibility.check(ctx).findings if f.dimension == "LEG-DENSITY"]


def test_a_scene_limit_applies_to_its_own_scene_only(tmp_path):
    found = density(tmp_path, crowd("B02", 18) + crowd("B03", 9), scene_limits={"B02": 18})
    assert [(f.element, f.severity) for f in found] == [("B03", "P2")]                 # the rest of the film keeps 8
    assert "(> 8)" in found[0].message
    over = density(tmp_path, crowd("B02", 19), scene_limits={"B02": 18})
    assert [f.element for f in over] == ["B02"]
    assert "19 text elements at once" in over[0].message and "(> 18, this scene's own limit in evaluation.md)" in over[0].message


def test_without_scene_limits_the_finding_is_unchanged(tmp_path):
    for limits in (None, {}):
        found = density(tmp_path, crowd("B02", 18), scene_limits=limits)
        assert [f.message for f in found] == ["scene B02 shows 18 text elements at once at 0.00s (> 8); one message per frame"]


@pytest.mark.parametrize("limits", [{"B02": "18"}, {"B02": 0}, {"B02": True}, {"B02": 17.5}, ["B02"]])
def test_a_malformed_scene_limit_is_refused(tmp_path, limits):
    with pytest.raises(ValueError, match="scene_limits"):
        density(tmp_path, crowd("B02", 18), scene_limits=limits)


def test_a_scene_limit_naming_no_scene_is_reported_and_exempts_nothing(tmp_path):
    report = tmp_path / "work" / "accessibility-report.json"
    found = density(tmp_path, crowd("B02", 18), scene_limits={"B2": 18})             # a typo for B02
    assert [f.element for f in found] == ["B02"] and "(> 8)" in found[0].message
    assert json.loads(report.read_text())["unused_scene_limits"] == ["B2"]
    density(tmp_path, crowd("B02", 18), scene_limits={"B02": 18})
    assert "unused_scene_limits" not in json.loads(report.read_text())             # nothing to report: the key is absent
