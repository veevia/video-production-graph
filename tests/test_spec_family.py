"""P1: the spec family (rule ids, DTCG tokens, lint, diff, hash, gates).

Acceptance (handoff brief, P1): `vpg validate` passes; a spec diff names the changed rules; old projects still
validate (the default workflow reads no spec document).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest
import yaml

from tests.conftest import PKG
from vpg import spec as sp
from vpg.graph import load_workflow, validate
from vpg.metrics import measured
from vpg.tools.specchecks import term_report

EXAMPLE = PKG / "examples/sample-demo/spec"


@pytest.fixture
def fam_dir(tmp_path):
    d = tmp_path / "spec"
    shutil.copytree(EXAMPLE, d)
    return d


def _edit_fm(path, fn):
    text = path.read_text()
    if path.suffix == ".yaml":
        data = yaml.safe_load(text)
        fn(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        return
    fm, body = sp._split(text)
    fn(fm)
    path.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n" + body)


def errors(fam, **kw):
    return [i for i in sp.lint(fam, **kw) if i.level == "error"]


def test_the_example_family_is_clean_and_complete():
    fam = sp.load_family(EXAMPLE)
    assert set(fam.docs) == set(sp.FILES)
    assert errors(fam, require=tuple(sp.FILES)) == []
    assert len(fam.rules()) >= 12 and all(r["id"].startswith(sp.PREFIX[r["doc"]]) for r in fam.rules().values())


def test_templates_refuse_approval_until_filled(tmp_path):
    fam = sp.load_family(PKG / "templates/spec")
    msgs = [i.message for i in errors(fam, require=tuple(sp.FILES))]
    assert any("<fill: ...> marker" in m for m in msgs)
    out = tmp_path / "spec"
    sp.init_family(out, from_controls=PKG / "examples/sample-demo/controls")
    tokens = json.loads((out / "DESIGN.tokens.json").read_text())
    assert tokens["palette"]["brand"]["$value"]["hex"] == "#0b5cad"
    assert tokens["font"]["primary"]["$value"][0] == "Mona Sans"
    assert "pace_wpm: {min: 151, max: 185}" in (out / "VOICE.md").read_text()
    with pytest.raises(sp.SpecError, match="exists"):
        sp.init_family(out)


@pytest.mark.parametrize("mutate, expect", [
    (lambda fm: fm["rules"].append(dict(fm["rules"][0])), "duplicate rule id"),
    (lambda fm: fm["rules"][0].update(id="MO-GROUND-001"), "starts with DS-"),
    (lambda fm: fm["rules"][0].update(examples={}), "Do or Don't"),
    (lambda fm: fm["rules"][0].update(source=""), "source is required"),
    (lambda fm: fm["rules"][0].update(level="MAY"), "MUST or SHOULD"),
    (lambda fm: fm["rules"][0]["enforcement"].update(metric="video.vibes"), "not a known metric"),
    (lambda fm: fm["rules"][0].update(enforcement={"type": "tool", "check": "nowhere:nothing"}), "does not resolve"),
    (lambda fm: fm["rules"][0]["enforcement"].update(gate="GT-NOPE-001"), "not a gate"),
    (lambda fm: fm["ground"]["roles"].update(ground="{color.nothing}"), "unknown token"),
    (lambda fm: fm.update(spec={"family": "vpg-spec/v1", "doc": "design"}), "front matter needs spec"),
])
def test_lint_catches_rule_and_reference_problems(fam_dir, mutate, expect):
    _edit_fm(fam_dir / "DESIGN.md", mutate)
    assert any(expect in i.message for i in errors(sp.load_family(fam_dir))), expect


@pytest.mark.parametrize("mutate, expect", [
    (lambda t: t["color"]["ground"]["$value"].update(components=[1.2, 0, 0]), "srgb components"),
    (lambda t: t.update(stray={"x": {"$value": 3}}), "no $type"),
    (lambda t: t["type"]["body"]["$value"].update(fontFamily="{font.nope}"), "unknown token"),
    (lambda t: t["space"].update(a={"$value": "{space.b}"}, b={"$value": "{space.a}"}), "circular"),
    (lambda t: t["duration"]["fast"].update({"$value": {"value": 200, "unit": "frames"}}), "duration"),
    (lambda t: t["easing"]["standard"].update({"$value": [1.5, 0, 0, 1]}), "cubicBezier"),
])
def test_lint_validates_dtcg_tokens(fam_dir, mutate, expect):
    path = fam_dir / "DESIGN.tokens.json"
    tokens = json.loads(path.read_text())
    mutate(tokens)
    path.write_text(json.dumps(tokens))
    assert any(expect in i.message for i in errors(sp.load_family(fam_dir))), expect


@pytest.mark.parametrize("mutate, expect", [
    (lambda g: g["gates"][0].update(version=2), "re-measure the benchmarks and baseline"),
    (lambda g: g["gates"][0].update(rules=["DS-NOPE-001"]), "no spec document defines"),
    (lambda g: g["gates"][0].update(envelope="vibes"), "envelope is"),
    (lambda g: g["gates"][0].update(stage="someday"), "stage is one of"),
    (lambda g: g["gates"][1].update(id=g["gates"][0]["id"]), "duplicate gate id"),
    (lambda g: g["gates"][0].update(metric="video.motion_share"), "measures video.motion_share"),
    (lambda g: g["gates"][2].update(roles=["product_ui", "product_ui"]), "distinct style-frame roles"),
    (lambda g: g["gates"][2].update(roles="product_ui"), "distinct style-frame roles"),
    (lambda g: g["gates"][2].update(roles=["poster"]), "which DESIGN.md does not ask for"),
    (lambda g: g["gates"][3].update(roles=["title"]), "only a still.* gate"),
])
def test_lint_checks_gates(fam_dir, mutate, expect):
    _edit_fm(fam_dir / "GATES.yaml", mutate)
    assert any(expect in i.message for i in errors(sp.load_family(fam_dir))), expect


def _bump(fm):
    fm["spec"]["version"] += 1


def test_acceptance_a_spec_diff_names_the_changed_rules(fam_dir, tmp_path):
    old = sp.load_family(EXAMPLE)

    def change_design(fm):
        _bump(fm)
        fm["rules"][0]["statement"] = "The ground is white; the brand blue appears as one accent per frame."
        fm["rules"].append({"id": "DS-CHIP-001", "level": "SHOULD", "statement": "Chips are pills.", "rationale": "Consistency.",
                            "source": "F004", "enforcement": {"type": "judge"}, "examples": {"do": ["a pill"]}})

    def retire_seam(fm):
        _bump(fm)
        fm["rules"][1].update(status="retired", retired_reason="merged into the line-carry rule")
    _edit_fm(fam_dir / "DESIGN.md", change_design)
    _edit_fm(fam_dir / "MOTION.md", retire_seam)
    tokens = json.loads((fam_dir / "DESIGN.tokens.json").read_text())
    tokens["color"]["ground"]["$value"] = {"colorSpace": "srgb", "components": [1, 1, 1], "hex": "#ffffff"}
    (fam_dir / "DESIGN.tokens.json").write_text(json.dumps(tokens))
    new = sp.load_family(fam_dir)
    assert errors(new, previous=old) == []
    d = sp.diff(old, new)
    assert d["rules"]["added"] == ["DS-CHIP-001"] and d["rules"]["retired"] == ["MO-SEAM-001"]
    assert d["rules"]["modified"] == [{"id": "DS-GROUND-001", "fields": ["statement"], "doc": "design"}]
    assert [c["path"] for c in d["tokens"]["changed"]] == ["color.ground"]
    assert d["earliest_stage"] == "look" and d["old_hash"] != d["new_hash"]
    lines = sp.diff_lines(d)
    assert "rule DS-GROUND-001: modified (statement)" in lines and "rule DS-CHIP-001: added" in lines
    assert "rule MO-SEAM-001: retired" in lines


def test_a_story_change_reopens_from_the_animatic(fam_dir):
    old = sp.load_family(EXAMPLE)

    def more_terms(fm):
        _bump(fm)
        fm["must_say"].append("plant team")
    _edit_fm(fam_dir / "STORY.md", more_terms)
    d = sp.diff(old, sp.load_family(fam_dir))
    assert d["earliest_stage"] == "animatic" and d["rules"] == {"added": [], "modified": [], "retired": [], "removed": []}


def test_versions_and_deletions_are_governed(fam_dir):
    old = sp.load_family(EXAMPLE)
    _edit_fm(fam_dir / "MOTION.md", lambda fm: fm["rules"].pop())   # deleted, not retired, and no version bump
    msgs = [i.message for i in errors(sp.load_family(fam_dir), previous=old)]
    assert any("retire it instead" in m for m in msgs) and any("bump it" in m for m in msgs)


def test_rules_that_only_accumulate_are_flagged(fam_dir):
    old = sp.load_family(EXAMPLE)

    def pile_on(fm):
        _bump(fm)
        for i in range(6):
            fm["rules"].append({"id": f"DS-NOTE{i}-001", "level": "SHOULD", "statement": f"note {i}", "rationale": "feedback",
                                "source": f"F0{i}", "enforcement": {"type": "judge"}, "examples": {"do": ["x"]}})
    _edit_fm(fam_dir / "DESIGN.md", pile_on)
    warns = [i.message for i in sp.lint(sp.load_family(fam_dir), previous=old) if i.level == "warning"]
    assert any("rules only accumulate" in w for w in warns)


def test_family_hash_tracks_content_only(fam_dir):
    a = sp.family_hash(sp.load_family(fam_dir))
    assert a == sp.family_hash(sp.load_family(EXAMPLE))
    (fam_dir / "MUSIC.md").write_text((fam_dir / "MUSIC.md").read_text() + "\n")
    assert sp.family_hash(sp.load_family(fam_dir)) != a


def test_gates_evaluate_measurements_against_their_envelopes(fam_dir):
    fam = sp.load_family(fam_dir)
    good = {"text.overlap_all.count": measured("text.overlap_all.count", 0), "audio.pace_wpm": measured("audio.pace_wpm", 170)}
    bad = {"text.overlap_all.count": measured("text.overlap_all.count", 2), "audio.pace_wpm": measured("audio.pace_wpm", 210)}
    rows = {r["gate"]: r for r in sp.evaluate_gates(fam, good)}
    assert rows["GT-OVERLAP-001"]["status"] == "pass"
    assert rows["GT-GROUND-001"]["status"] == rows["GT-PACE-001"]["status"] == "no_envelope"   # no registry: never a silent pass
    rows = {r["gate"]: r for r in sp.evaluate_gates(fam, bad)}
    assert rows["GT-OVERLAP-001"]["status"] == "fail" and rows["GT-OVERLAP-001"]["direction"] == "above"
    assert {r["gate"] for r in sp.blocking(sp.evaluate_gates(fam, bad, stage="animatic"))} >= {"GT-PACE-001"}
    stale = dict(bad["text.overlap_all.count"], basis="0" * 16)
    row = next(r for r in sp.evaluate_gates(fam, {"text.overlap_all.count": stale}) if r["gate"] == "GT-OVERLAP-001")
    assert row["status"] == "not_comparable"


def test_a_still_gate_with_its_own_roles_judges_exactly_those(fam_dir):
    """GT-UI-001 says roles: [product_ui]: a title card's large type is not judged as UI, and the UI frame is judged
    whatever its envelope; a UI frame that gives no value cannot pass."""
    roles = ["title", "product_ui", "data_callout", "closing"]
    ui = "still.ocr_word_px.median"

    def rows_for(per_role, envelope=None):
        if envelope is not None:
            _edit_fm(fam_dir / "GATES.yaml", lambda g: g["gates"][2].update(envelope=envelope))
        fam = sp.load_family(fam_dir)
        rec = measured(ui, 9.0, {"per_role": per_role})
        return [r for r in sp.evaluate_gates(fam, {ui: rec}, stage="look", roles=roles) if r["gate"] == "GT-UI-001"]
    rows = rows_for({"title": 30.0, "product_ui": 5.0}, envelope={"lo": 6})
    assert [(r["role"], r["status"], r["direction"]) for r in rows] == [("product_ui", "fail", "below")]
    assert [r["status"] for r in rows_for({"title": 30.0, "product_ui": 7.5})] == ["pass"]
    missing = rows_for({"title": 30.0})
    assert [r["status"] for r in missing] == ["not_measured"] and "too little confidently read text" in missing[0]["why"]
    bench = rows_for({"title": 30.0, "product_ui": 7.5}, envelope="benchmark")   # no registry: never a silent pass
    assert [(r["role"], r["status"], r["why"]) for r in bench] == [("product_ui", "no_envelope",
                                                                   "no reference shows a product_ui style frame")]


def test_story_terms_cover_spoken_and_shown_text():
    rep = term_report(["expected range", "seven-day outlook", "audit trail"], ["game-changing"],
                      "It leaves its own expected range. A seven-day outlook helps.", "A game-changing monitor")
    assert rep["covered"] == ["expected range", "seven-day outlook"] and rep["missing"] == ["audit trail"]
    assert rep["banned_hits"] == ["game-changing"] and rep["share"] == pytest.approx(0.6667, abs=1e-3)


def test_the_default_workflow_reads_no_spec_document_and_still_validates():
    wf = load_workflow()
    assert validate(wf) == []
    docs = {d for n in wf.nodes for d in n.controls.read + n.controls.write + n.controls.lock_on_approval}
    assert not docs & set(sp.CONTROL_IDS.values()), "the spec family is opt-in; the default workflow must not read it"
    assert not set(sp.CONTROL_IDS.values()) & {"product", "creative", "design", "production", "evaluation", "memory"}
    lower = [f.lower() for f in sp.FILES.values()]
    assert len(set(lower)) == len(lower) and sp.SPEC_DIR != "controls"   # no two files collide on a case-free disk


def test_cli_spec_lint_and_diff(fam_dir):
    run = lambda *a: subprocess.run([sys.executable, "-m", "vpg", "spec", *a], cwd=PKG, capture_output=True, text=True)  # noqa: E731
    assert run("lint", str(EXAMPLE), "--require-all").returncode == 0
    assert run("lint", str(PKG / "templates/spec")).returncode == 1

    def tighten(fm):
        _bump(fm)
        fm["rules"][0]["level"] = "SHOULD"
    _edit_fm(fam_dir / "VOICE.md", tighten)
    r = run("diff", str(EXAMPLE), str(fam_dir))
    assert r.returncode == 0 and "rule VO-PACE-001: modified (level)" in r.stdout and "animatic" in r.stdout


def test_must_say_terms_count_plurals_and_spellings_but_not_other_words():
    from vpg.tools.specchecks import term_report
    spoken = "Sub-agents draft the plan; the glass boxes show the AI judge's decision factors."
    r = term_report(["sub-agent", "glass box", "AI Judge", "decision factor", "human in the loop"], ["game-changer"], spoken)
    assert r["covered"] == ["sub-agent", "glass box", "AI Judge", "decision factor"]
    assert r["missing"] == ["human in the loop"]
    assert term_report(["agent"], [], "Agentic review follows.")["missing"] == ["agent"]   # another word, not a form of it
    assert term_report([], ["game-changer"], "two game changers")["banned_hits"] == ["game-changer"]
