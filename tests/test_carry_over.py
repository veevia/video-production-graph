"""v1.5: what a producer's fingerprint covers, carrying recorded passes over an engine upgrade, frozen-source provenance
and verdicts judged under the configuration a human accepted.

Found on the SRA run: a `defects:rework` input hashed every defect of the run, so each new defect re-ran every producer
it never reached; passes recorded before code fingerprints could never memo-skip again; hard-linked sources were
rewritten from another project and their registered versions were lost. The rest came from the engineering review."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from datetime import datetime, timezone

import pytest
import yaml

from tests.conftest import FnExecutor, flag, mini_workflow
from tests.test_bookkeeping import COMMAND_SCRIPT, _bind, _defect, events
from tests.test_hardening import BRIEF, SPEC, _init_film, _submit_sources, make_project
from vpg import cli
from vpg.engine import FINGERPRINT_SCHEME, Engine, EngineError
from vpg.executors import TaskPacketExecutor
from vpg.graph import DEFAULT_WORKFLOW, control_reads, load_workflow, node_resolution_errors, resolve_tool, tool_control_reads
from vpg.models import NodeKind, ProjectState
from vpg.models import NodeStatus as S
from vpg.tools import verify
from vpg.util import PKG_ROOT, get_path, glob_files, read_json, rel, sha256_file, sha256_json, write_json_atomic

BRAND = {"id": "S3", "path": "sources/brand.md", "kind": "brand", "evidence": "observed"}


def _rework_inputs(tmp_path, nodes=("c_audio", "d_build")):
    """The mini workflow with `defects:rework` on producers, as the default workflow's N07–N14 declare it."""
    path = mini_workflow(tmp_path)
    wf = yaml.safe_load(path.read_text())
    for n in wf["nodes"]:
        if n["id"] in nodes:
            n["inputs"] = list(n["inputs"]) + ["defects:rework"]
    path.write_text(yaml.safe_dump(wf))
    return path


def _passed(tmp_path, command=None):
    proj = make_project(tmp_path, workflow=_rework_inputs(tmp_path))
    if command:
        (proj / "pipeline").mkdir()
        (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
        _bind(proj, command)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    return proj, eng


def _iso(t: float) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).isoformat()


def _as_recorded_before(eng, proj, nid, *, code=True, defect=None, started_at=None):
    """Rewrite nid's last pass as an engine before v1.5 recorded it: scheme 1 (a `defects:` input hashed every defect of
    the run) or, with code=False, scheme 0 (no command code, before v1.1). `defect` existed when it passed, and
    `started_at` replaces the time its pass began. Returns the pass checkpoint's path."""
    ref = [r for r in eng.state.history.checkpoints if r.event == "node_passed" and r.node == nid][-1]
    path = proj / ref.path
    cp = ProjectState.model_validate(read_json(path))
    cp_rt, live_rt = cp.runtime.nodes[nid], eng.rt(nid)
    if defect is not None:
        cp.evaluation.defects.append(defect)
        eng.state.evaluation.defects.append(defect.model_copy())
    if not code:
        cp_rt.command_code, live_rt.command_code = {}, {}
    if started_at is not None:
        cp_rt.started_at = live_rt.started_at = started_at
    fp = eng.fingerprint(eng.node(nid), state=cp, scheme=1 if code else 0, code=sorted(cp_rt.command_code.items()), rework=[])
    for r in (cp_rt, live_rt):
        r.last_pass_fingerprint, r.last_pass_scheme, r.fingerprint_scheme = fp, None, None
    write_json_atomic(path, cp)
    eng._save("test")
    return path


def _recheck(eng, nid):
    """Send nid back to STALE as an upstream re-check does (not forced), then take one step."""
    eng.invalidate([nid], reason="an upstream re-check")
    eng.step()
    return eng.rt(nid)


def _v141_fingerprint(eng, node, state):
    """The fingerprint v1.4.1 computed: engine.py at 7d8a25d49a, verbatim except that it reads `state`."""
    parts = {"node": sha256_json(node.model_dump(mode="json", by_alias=True)), "wf": eng.wf.version}
    hashes = eng.controls.hashes()
    version = state.runtime.current_version
    for ref in node.inputs:
        kind, _, name = ref.partition(":")
        if kind == "artifact":
            parts[ref] = [a.sha256 for a in sorted((a for a in state.artifacts.values() if a.key == name and a.status == "current"),
                                                   key=lambda a: a.id)]
        elif kind == "control":
            parts[ref] = hashes.get(name)
        elif kind == "state":
            parts[ref] = sha256_json(get_path(state, name))
        elif kind == "rubric":
            parts[ref] = sha256_json(eng.rubrics.get(name))
        elif kind == "reviews":
            parts[ref] = sha256_json([(r.node, r.version, r.verdict, r.defect_ids) for r in state.evaluation.reviews if r.version == version])
        elif kind == "feedback":
            parts[ref] = sha256_json([f.id for f in state.evaluation.feedback if f.status == "open"])
        elif kind == "defects":
            parts[ref] = sha256_json([(d.id, d.status) for d in state.evaluation.defects])
        elif kind == "knowledge":
            parts[ref] = state.knowledge.rules_sha256
        elif kind == "files":
            parts[ref] = [(rel(f, eng.project.root), sha256_file(f)) for f in glob_files(eng.project.root, name)]
    for d in sorted(control_reads(node)):
        parts.setdefault(f"control:{d}", hashes.get(d))
    parts["rework"] = sorted(state.runtime.nodes[node.id].rework_defects)
    parts["prompt"] = sha256_file(PKG_ROOT / node.prompt) if node.prompt and (PKG_ROOT / node.prompt).exists() else None
    parts["command"] = eng.project.command_for(node.id)
    if parts["command"]:
        parts["command_code"] = eng.project.command_code(node.id)
    if node.map_over:
        parts["items"] = sha256_json(get_path(state, node.map_over))
    return sha256_json(parts)


def _after(proj, kind, event_type):
    """Nodes named by `event_type` events after the last `kind` event."""
    evs = [json.loads(x) for x in (proj / ".vpg/ledger.jsonl").read_text().splitlines()]
    last = max(i for i, e in enumerate(evs) if e["type"] == kind)
    return {e["data"]["node"] for e in evs[last:] if e["type"] == event_type}


# ------------------------------------------------------------------------------------------------ fingerprint schemes
def test_scheme_1_is_the_fingerprint_v1_4_1_computed_and_scheme_0_leaves_the_code_out(tmp_path):
    """A golden reference: carrying a pass over is sound only if the scheme it was recorded under is reproduced exactly."""
    _, eng = _passed(tmp_path, command="{python} pipeline/run.py")
    eng.state.evaluation.defects.append(_defect("D1", "visual", "d_build", "x"))
    eng.rt("d_build").rework_defects = ["D1"]
    for node in eng.wf.nodes:
        assert eng.fingerprint(node, scheme=1) == _v141_fingerprint(eng, node, eng.state), node.id
    audio = eng.node("c_audio")
    assert "command_code" not in eng._fingerprint_parts(audio, scheme=0) and eng._fingerprint_parts(audio, scheme=1)["command_code"]
    review = next(n for n in eng.wf.nodes if n.kind == NodeKind.REVIEW)
    assert "judged_under" in eng._fingerprint_parts(review) and "judged_under" not in eng._fingerprint_parts(review, scheme=1)


def test_a_defect_routed_elsewhere_leaves_a_producers_fingerprint_alone(tmp_path):
    """A producer is given only its own rework defects (_context), so only those are in its fingerprint. `defects:open`
    and `defects:all` still hash every defect: engine tools read the whole state."""
    _, eng = _passed(tmp_path)
    audio, gate_node = eng.node("c_audio"), eng.node("g_gate")
    before, v14_before, gate_before = eng.fingerprint(audio), eng.fingerprint(audio, scheme=1), eng.fingerprint(gate_node)
    eng.state.evaluation.defects.append(_defect("D9", "visual", "d_build", "x"))
    assert eng.fingerprint(audio) == before
    assert eng.fingerprint(audio, scheme=1) != v14_before                           # what v1.1–v1.4 did
    assert eng.fingerprint(gate_node) != gate_before
    eng.rt("c_audio").rework_defects = ["D9"]
    assert eng.fingerprint(audio) != before


def test_a_producer_memo_skips_when_only_another_nodes_defects_changed(tmp_path):
    """#60 on the SRA run: D028–D035, routed to N13, changed N11's and N14's fingerprints, so both would re-run."""
    _, eng = _passed(tmp_path)
    eng.state.evaluation.defects.append(_defect("D9", "visual", "d_build", "x"))
    rt = _recheck(eng, "c_audio")
    assert rt.memo_hits == 1 and rt.executions == 1 and rt.status == S.STEP_PASS


# ------------------------------------------------------------------------------------------------------ carry-over
def test_accept_config_carries_a_pass_recorded_before_v15_over_so_it_still_memo_skips(tmp_path):
    proj, eng = _passed(tmp_path)
    _as_recorded_before(eng, proj, "c_audio", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    eng.state.evaluation.defects.append(_defect("D2", "visual", "d_build", "y"))     # a new defect, routed elsewhere
    assert eng.rt("c_audio").last_pass_fingerprint != eng.fingerprint(eng.node("c_audio"))
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    [ev] = events(proj, "passes_carried_over")
    assert ev["carried"] == ["c_audio"] and ev["adopted"] == [] and ev["kept"] == {} and ev["actor"] == "Wei"
    assert eng.rt("c_audio").last_pass_scheme == FINGERPRINT_SCHEME
    rt = _recheck(eng, "c_audio")
    assert rt.memo_hits == 1 and rt.executions == 1


def test_a_carried_pass_is_derived_from_its_checkpoint_so_a_later_input_change_still_re_runs_it(tmp_path):
    """Critic I1: deriving the new fingerprint from the live state would memo-skip d_build onto a build made from the
    old c_audio output. I4: a later accept does not re-derive it again, or report it."""
    proj, eng = _passed(tmp_path)
    _as_recorded_before(eng, proj, "d_build", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    (proj / "flags").mkdir(exist_ok=True)
    (proj / "flags/c_audio.variant").write_text("a new take")
    eng.invalidate(["c_audio"], reason="a human asked for a new take")
    eng.rt("c_audio").force_rerun = True
    eng.step()                                                                        # c_audio re-runs; d_build waits
    assert eng.rt("c_audio").executions == 2 and eng.rt("d_build").status == S.STALE
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert events(proj, "passes_carried_over")[0]["carried"] == ["d_build"]
    eng.accept_config(actor="Wei", reason="a rubric change")
    assert len(events(proj, "passes_carried_over")) == 1
    eng.step()
    assert eng.rt("d_build").executions == 2 and eng.rt("d_build").memo_hits == 0


def test_a_pass_that_recorded_its_code_is_carried_with_that_code_so_a_later_code_edit_still_re_runs_it(tmp_path):
    """Critic I1: carrying it with the current code would memo-skip c_audio onto output made by the old code."""
    proj, eng = _passed(tmp_path, command="{python} pipeline/run.py")
    assert eng.rt("c_audio").command_code
    _as_recorded_before(eng, proj, "c_audio", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    (proj / "pipeline/helper.py").write_text("STEP = 2\n")                          # its code changed after the pass
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert events(proj, "passes_carried_over")[0]["carried"] == ["c_audio"]
    rt = _recheck(eng, "c_audio")
    assert rt.executions == 2 and rt.memo_hits == 0


def test_a_pass_that_cannot_be_re_derived_re_runs_once_and_says_why(tmp_path):
    proj, eng = _passed(tmp_path)
    path = _as_recorded_before(eng, proj, "c_audio", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    wf = yaml.safe_load(eng.workflow_path.read_text())
    for n in wf["nodes"]:
        if n["id"] == "c_audio":
            n["purpose"] = "its definition changed since the pass"
    eng.workflow_path.write_text(yaml.safe_dump(wf))
    eng = Engine(proj, executor=FnExecutor())
    eng.accept_config(actor="Wei", reason="v1.5 engine and a workflow edit")
    [ev] = events(proj, "passes_carried_over")
    assert ev["carried"] == [] and ev["kept"]["c_audio"].startswith(
        f"checkpoint {int(path.name[:5])} does not reproduce its last pass: something it reads changed between its dispatch")
    rt = _recheck(eng, "c_audio")
    assert rt.memo_hits == 0 and rt.executions == 2


def test_a_missing_checkpoint_keeps_the_old_fingerprint(tmp_path):
    proj, eng = _passed(tmp_path)
    path = _as_recorded_before(eng, proj, "c_audio", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    path.unlink()
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert events(proj, "passes_carried_over")[0]["kept"]["c_audio"].startswith("no checkpoint records its last pass")


def test_a_rework_pass_is_re_derived_with_the_defects_it_was_sent_to_fix(tmp_path):
    """The ledger's node_dispatched event names the rework defects, which were in the fingerprint the pass recorded."""
    proj, eng = _passed(tmp_path)
    ref = [r for r in eng.state.history.checkpoints if r.event == "node_passed" and r.node == "d_build"][-1]
    cp = ProjectState.model_validate(read_json(proj / ref.path))
    for d in (_defect("D1", "visual", "d_build", "x", status="resolved"), _defect("D2", "audio", "c_audio", "y", status="resolved")):
        cp.evaluation.defects.append(d)
        eng.state.evaluation.defects.append(d.model_copy())
    fp = eng.fingerprint(eng.node("d_build"), state=cp, scheme=1, rework=["D1"])      # dispatched to fix D1, before v1.5
    for r in (cp.runtime.nodes["d_build"], eng.rt("d_build")):
        r.last_pass_fingerprint, r.last_pass_scheme = fp, None
    write_json_atomic(proj / ref.path, cp)
    attempt = cp.runtime.nodes["d_build"].attempts
    eng.ledger.append("node_dispatched", node="d_build", attempt=attempt, fingerprint=fp[:16], executor="t", rework=["D1"])
    eng._save("test")
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert events(proj, "passes_carried_over")[0]["carried"] == ["d_build"]
    rt = _recheck(eng, "d_build")                                                     # carried as settled: no re-run
    assert rt.memo_hits == 1 and rt.executions == 1


def test_a_rework_pass_is_kept_when_a_later_re_check_finds_nothing_changed(tmp_path):
    """Triage #61: a pass recorded the rework defects it was dispatched to fix, so after the fix the node re-ran once at
    its next re-check although nothing had changed (on SRA, N12 would have re-synthesized the voice)."""
    proj = make_project(tmp_path, workflow=_rework_inputs(tmp_path))
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.runtime.rework_cycles >= 1
    reworked = [e for e in events(proj, "node_dispatched") if e["rework"]]
    assert reworked, "the flag should have routed a rework"
    nid = reworked[-1]["node"]
    before = eng.rt(nid).executions
    rt = _recheck(eng, nid)
    assert rt.memo_hits >= 1 and rt.executions == before, nid


def test_a_later_accept_leaves_passes_recorded_under_the_current_scheme_alone(tmp_path):
    """Critic I4: a rework pass (its fingerprint names the defects it fixed) was reported as "does not reproduce", which
    was false. Every pass records its scheme, and only older ones are re-derived."""
    proj = make_project(tmp_path, workflow=_rework_inputs(tmp_path))
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS" and eng.state.runtime.rework_cycles >= 1
    passed = [rt for rt in eng.state.runtime.nodes.values() if rt.last_pass_fingerprint]
    assert passed and all(rt.last_pass_scheme == FINGERPRINT_SCHEME for rt in passed)
    eng.accept_config(actor="Wei", reason="a rubric change")
    assert events(proj, "passes_carried_over") == []


# ------------------------------------------------------------------------------------------------------------ #57
def test_adopt_code_keeps_a_pass_from_before_code_fingerprints_only_while_its_code_is_older(tmp_path):
    proj, eng = _passed(tmp_path, command="{python} pipeline/run.py")
    now = time.time()
    _as_recorded_before(eng, proj, "c_audio", code=False, started_at=_iso(now - 3600))    # its code is newer than the pass
    assert any("c_audio: its last pass predates command-code fingerprints" in w and "--adopt-code c_audio" in w
               for w in eng.status()["warnings"])
    with pytest.raises(EngineError, match=r"pipeline/run.py changed after its last pass began"):
        eng.accept_config(actor="Wei", reason="v1.5 engine", adopt_code=["c_audio"])
    assert not events(proj, "passes_carried_over") and not events(proj, "config_accepted")
    for adopt, message in [(["c_audoi"], "unknown node"), (["d_build"], "d_build: it runs no bound command"),
                           (["r1_review"], "r1_review: it is a review, which re-runs after every accepted change")]:
        with pytest.raises(EngineError, match=message):
            eng.accept_config(actor="Wei", reason="v1.5 engine", adopt_code=adopt)
    with pytest.raises(EngineError, match="named human"):
        eng.accept_config(actor="agent:x", reason="v1.5 engine", adopt_code=["c_audio"])
    _as_recorded_before(eng, proj, "c_audio", code=False, started_at=_iso(now + 5))       # its code predates the pass
    assert cli.main(["accept-config", str(proj), "--actor", "Wei", "--reason", "v1.5 engine; run.py unchanged since v3",
                     "--adopt-code", "c_audio"]) == 0
    eng = Engine(proj, executor=FnExecutor())
    [ev] = events(proj, "passes_carried_over")
    assert ev["adopted"] == ["c_audio"] and ev["carried"] == ["c_audio"] and ev["reason"].startswith("v1.5 engine")
    rt = eng.rt("c_audio")
    assert rt.command_code == dict(eng.project.command_code("c_audio")) and rt.last_pass_scheme == FINGERPRINT_SCHEME
    assert not any("c_audio: its last pass predates" in w for w in eng.status()["warnings"])
    rt = _recheck(eng, "c_audio")
    assert rt.memo_hits == 1 and rt.executions == 1
    with pytest.raises(EngineError, match="c_audio: its last pass already records its code"):
        eng.accept_config(actor="Wei", reason="again", adopt_code=["c_audio"])
    rows = [r for r in verify.human_record(eng.ledger.events()) if r["action"] == "config_accepted"]   # critic I3: one row
    assert [r["subject"] for r in rows] == ["configuration change; kept the last pass of c_audio"]
    assert "adopted" in rows[0]["codes"] and "shared" not in rows[0]["codes"]


def test_adopt_code_records_the_code_hashed_before_the_file_times_were_checked(tmp_path, monkeypatch):
    """Critic r2 N2: an edit landing after the file-time check was hashed as the code Wei vouched for. The code is hashed
    first now, so that edit is not vouched for, and the node re-runs at its next re-check."""
    proj, eng = _passed(tmp_path, command="{python} pipeline/run.py")
    _as_recorded_before(eng, proj, "c_audio", code=False, started_at=_iso(time.time() + 5))   # its code predates the pass
    vouched = dict(eng.project.command_code("c_audio"))
    real = eng._changed_since

    def check_then_edit(nid, when):
        out = real(nid, when)
        (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT + "# edited during the acceptance\n")
        return out
    monkeypatch.setattr(eng, "_changed_since", check_then_edit)
    eng.accept_config(actor="Wei", reason="v1.5 engine", adopt_code=["c_audio"])
    assert eng.rt("c_audio").command_code == vouched


def test_restoring_an_older_modification_time_does_not_hide_a_code_edit(tmp_path):
    """Critic S1: cp -p, rsync -a or touch -r put an old modification time back; the change time still shows the edit."""
    proj, eng = _passed(tmp_path, command="{python} pipeline/run.py")
    now = time.time()
    _as_recorded_before(eng, proj, "c_audio", code=False, started_at=_iso(now - 60))
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT + "# edited after the pass\n")
    os.utime(proj / "pipeline/run.py", (now - 3600, now - 3600))
    with pytest.raises(EngineError, match=r"pipeline/run.py changed after its last pass began"):
        eng.accept_config(actor="Wei", reason="v1.5 engine", adopt_code=["c_audio"])
    changed = os.stat(proj / "pipeline/run.py").st_ctime                               # an edit in the second the pass began
    _as_recorded_before(eng, proj, "c_audio", code=False, started_at=_iso(changed))
    with pytest.raises(EngineError, match=r"pipeline/run.py changed after its last pass began"):
        eng.accept_config(actor="Wei", reason="v1.5 engine", adopt_code=["c_audio"])
    ref = [r for r in eng.state.history.checkpoints if r.event == "node_passed" and r.node == "c_audio"][-1]
    cp = read_json(proj / ref.path)
    cp["runtime"]["nodes"]["c_audio"]["started_at"] = None                           # no start time: nothing is ruled out
    write_json_atomic(proj / ref.path, cp)
    with pytest.raises(EngineError, match="c_audio: its last pass records no start time"):
        eng.accept_config(actor="Wei", reason="v1.5 engine", adopt_code=["c_audio"])


def test_without_adopt_code_a_pass_from_before_code_fingerprints_re_runs_once(tmp_path):
    proj, eng = _passed(tmp_path, command="{python} pipeline/run.py")
    _as_recorded_before(eng, proj, "c_audio", code=False)
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert "--adopt-code c_audio" in events(proj, "passes_carried_over")[0]["kept"]["c_audio"]
    rt = _recheck(eng, "c_audio")
    assert rt.memo_hits == 0 and rt.executions == 2 and rt.command_code and rt.last_pass_scheme == FINGERPRINT_SCHEME


# ------------------------------------------------------------------------------------------- judged under the config
def test_a_rollback_past_an_accepted_change_judges_the_restored_version_again(tmp_path):
    """Critic I5: a rollback kept the accepted configuration but restored verdicts reached under the old one, and
    re-checked nothing. A rollback under the same configuration re-checks nothing."""
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    seq = eng.state.history.checkpoints[-1].seq
    cfg = proj / "vpg.project.yaml"
    cfg.write_text(cfg.read_text() + "note: a stricter rubric was adopted mid-run\n")
    eng = Engine(proj, executor=FnExecutor())
    eng.accept_config("Wei", "stricter rubric")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    eng.rollback(seq, actor="Wei")
    judged = {n.id for n in eng.wf.nodes if n.kind == NodeKind.REVIEW} | {"g_gate"}
    assert set(events(proj, "rollback")[-1]["rechecked"]) >= judged
    assert eng.state.runtime.config_parts == eng._config_parts()
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    assert _after(proj, "rollback", "node_dispatched") >= judged - {"g_gate"} and not (_after(proj, "rollback", "memo_hit") & judged)
    eng.rollback(eng.state.history.checkpoints[-1].seq, actor="Wei")
    assert events(proj, "rollback")[-1]["rechecked"] == []


def test_a_tool_that_reads_an_undeclared_control_document_is_refused():
    """Critic S7: pacing:analyze reads its thresholds from evaluation.md, which N20 did not declare, and legibility:check
    reads production.md, which N18 did not, so a change to either document neither changed the node's fingerprint nor
    re-checked its verdict. The engine now refuses such a graph. The scan follows the tool into its module's helpers."""
    wf = load_workflow(DEFAULT_WORKFLOW)
    assert [e for n in wf.nodes for e in node_resolution_errors(n, PKG_ROOT)] == []
    for nid, read, missing in [("n20_narrative_pacing", ["creative"], "controls/evaluation.md"),
                               ("n18_accessibility_legibility", ["evaluation", "design"], "controls/production.md")]:
        node = wf.node(nid)
        stripped = node.model_copy(update={"controls": node.controls.model_copy(update={"read": read})})
        assert node_resolution_errors(stripped, PKG_ROOT) == [
            f"node {nid}: its tool {node.run.tool} reads {missing}, which the node does not declare; list it in "
            "controls.read, so a change to it re-checks the node"]
    assert tool_control_reads(resolve_tool("design:compliance")) == {"design"}          # N17's tool: design only
    assert tool_control_reads(resolve_tool("design:check_palette")) == {"design", "evaluation"}   # through _fm and thresholds
    assert tool_control_reads(resolve_tool("pacing:estimate_script")) == {"production"}


def test_prompts_and_agent_role_files_are_part_of_the_accepted_configuration(tmp_path):
    proj = make_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    parts = eng._config_parts()
    assert "n20_narrative_pacing.md" in parts["prompts"] and len(parts["agents"]) >= 10
    rt = eng.state.runtime
    for key in ("prompts", "agents"):                                               # as a run accepted before v1.5 has them
        rt.config_parts.pop(key)
    rt.config_sha256 = sha256_json(rt.config_parts)
    eng._save("test")
    [problem] = Engine(proj, executor=FnExecutor())._integrity_problems()
    assert "agents (newly part of the configuration), prompts (newly part of the configuration)" in problem
    assert ".md" not in problem and ".yaml" not in problem


# ------------------------------------------------------------------------------------------------------------ #56
def _frozen(tmp_path):
    proj = _init_film(tmp_path)
    (proj / "sources/spec.md").write_text("# Product spec\n")
    (proj / "sources/brand.md").write_text("# Brand guide\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, BRAND])
    eng.run(max_steps=20)
    assert eng.state.inputs.frozen
    return proj, eng


def test_intake_refuses_a_hard_linked_or_symbolically_linked_source(tmp_path):
    proj = _init_film(tmp_path)
    (tmp_path / "hub-spec.md").write_text("# Product spec, owned by another project\n")
    os.link(tmp_path / "hub-spec.md", proj / "sources/spec.md")
    (tmp_path / "hub-brand.md").write_text("# Brand guide, owned by another project\n")
    os.symlink(tmp_path / "hub-brand.md", proj / "sources/brand.md")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, SPEC, BRAND])
    failures = " ".join(eng.rt("n01_context_intake").failures)
    assert "S2: sources/spec.md is a hard link (2 paths share it)" in failures and "cp, not ln" in failures
    assert "S3: sources/brand.md is a symbolic link" in failures
    assert eng.rt("n01_context_intake").status != S.STEP_PASS


def test_frozen_sources_are_kept_by_content_and_drift_is_reported_by_id(tmp_path):
    """Critic I2: a drifted source that is still a hard link reported only the drift, and the documented `cp` restore
    wrote into the shared file. Both are reported now, and the restore deletes first. S6: ids only, never paths."""
    proj, eng = _frozen(tmp_path)
    types = [e["type"] for e in eng.ledger.events()]
    assert types[types.index("inputs_frozen") + 1] == "sources_kept"                  # kept at the freeze, not a step later
    store = proj / ".vpg/sources"
    for src in eng.state.inputs.sources:
        copy = store / src.sha256
        assert copy.read_bytes() == (proj / src.path).read_bytes() and not copy.stat().st_mode & stat.S_IWUSR
    assert (store / ".gitignore").read_text() == "*\n"                                # #79: copies never enter a commit by accident
    assert not [w for w in eng.status()["warnings"] if w.startswith("source ")]
    by_id = {s.id: s for s in eng.state.inputs.sources}
    elsewhere = tmp_path / "other-project-spec.md"
    os.link(proj / "sources/spec.md", elsewhere)                                     # another project shares the file,
    elsewhere.write_text("# Product spec, as the other project edited it\n")          # and edits it
    (proj / "sources/brief.md").unlink()
    warnings = [w for w in Engine(proj, read_only=True).status()["warnings"] if w.startswith("source ")]
    assert any(w.startswith(f"source S2 changed after the inputs were frozen; its registered version is kept at "
                            f".vpg/sources/{by_id['S2'].sha256}. Restore it: delete its file first") for w in warnings)
    assert any(w.startswith("source S2 is a hard link (2 paths share it)") for w in warnings)
    assert any(w.startswith("source S1 is missing; its registered version is kept") for w in warnings)
    assert not any(name in w for w in warnings for name in ("spec.md", "brief.md", "brand.md"))
    (tmp_path / "brand-elsewhere.md").write_bytes((proj / "sources/brand.md").read_bytes())
    (proj / "sources/brand.md").unlink()
    os.symlink(tmp_path / "brand-elsewhere.md", proj / "sources/brand.md")           # same content, now a link
    warnings = Engine(proj, read_only=True).status()["warnings"]
    assert [w for w in warnings if w.startswith("source S3")] == [
        "source S3 is a symbolic link, so an edit to what it points to changes it; replace it with a copy"]
    (proj / "sources/spec.md").unlink()                                               # the documented restore
    shutil.copyfile(store / by_id["S2"].sha256, proj / "sources/spec.md")
    assert elsewhere.read_text() == "# Product spec, as the other project edited it\n"   # the other file is untouched
    assert not any("S2" in w for w in Engine(proj, read_only=True).status()["warnings"])


def test_a_run_frozen_before_v15_gets_copies_of_the_sources_that_still_match(tmp_path):
    proj, eng = _frozen(tmp_path)
    store = proj / ".vpg/sources"
    for f in store.iterdir():
        os.chmod(f, 0o644)
        f.unlink()
    (proj / "sources/spec.md").write_text("drifted before the copies existed\n")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.step()
    by_id = {s.id: s for s in eng.state.inputs.sources}
    assert sorted(p.name for p in store.glob("[0-9a-f]" * 64)) == sorted([by_id["S1"].sha256, by_id["S3"].sha256])
    assert events(proj, "sources_kept")[-1]["sources"] == ["S1", "S3"]
    assert any(w.startswith("source S2 changed after the inputs were frozen; no intact copy of its registered version exists")
               for w in eng.status()["warnings"])
    eng.step()
    assert len(events(proj, "sources_kept")) == 2                                      # once at the freeze, once for the backfill
    assert sha256_file(store / by_id["S1"].sha256) == by_id["S1"].sha256


def test_a_store_made_before_v151_gets_its_gitignore_at_the_next_step(tmp_path):
    """#79: v1.5 wrote full copies of the sources into a folder git did not ignore. A store it made keeps its copies and
    gains the .gitignore at the next step, though nothing needs copying."""
    proj, eng = _frozen(tmp_path)
    ignore = proj / ".vpg/sources/.gitignore"
    ignore.unlink()
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.step()
    assert ignore.read_text() == "*\n" and len(events(proj, "sources_kept")) == 1          # nothing was copied again


def test_a_damaged_copy_is_not_trusted_and_a_crashed_copy_is_removed(tmp_path):
    """Critic S5: a copy was trusted because it existed, and a crash left source content in a .part file."""
    proj, eng = _frozen(tmp_path)
    by_id = {s.id: s for s in eng.state.inputs.sources}
    copy = proj / ".vpg/sources" / by_id["S3"].sha256
    os.chmod(copy, 0o644)
    copy.write_text("torn")
    part = proj / ".vpg/sources/.0123456789abcdef.x1.part"
    part.write_text("# Brand guide, half copied\n")
    (proj / "sources/brand.md").write_text("# Brand guide, edited after the freeze\n")
    assert any(w.startswith("source S3 changed after the inputs were frozen; no intact copy") for w in eng.status()["warnings"])
    (proj / "sources/brand.md").write_text("# Brand guide\n")                          # put back as registered
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.step()
    assert events(proj, "sources_kept")[-1]["sources"] == ["S3"] and sha256_file(copy) == by_id["S3"].sha256
    assert not part.exists()


def test_a_blocked_run_still_gets_copies_of_its_sources(tmp_path):
    """Critic S5: step() returned for a blocked run before making copies."""
    proj, eng = _frozen(tmp_path)
    for f in (proj / ".vpg/sources").iterdir():
        os.chmod(f, 0o644)
        f.unlink()
    eng._escalate_run("no-progress", "a test block")
    eng._save("test")
    eng = Engine(proj, executor=TaskPacketExecutor())
    assert eng.step() == [] and eng.state.runtime.status.value == "BLOCKED"
    assert len(list((proj / ".vpg/sources").glob("[0-9a-f]" * 64))) == 3
    assert (proj / ".vpg/sources/.gitignore").read_text() == "*\n"                   # put back once the store is written
    assert Engine(proj, read_only=True).state.runtime.status.value == "BLOCKED"


def test_status_hashes_a_frozen_source_again_only_when_its_file_changed(tmp_path, monkeypatch):
    """Critic S2: every status re-hashed every frozen source. A verified stat identity is kept instead."""
    import vpg.source_store as ss
    monkeypatch.setattr(ss.SourceStore, "RACY_S", 0)                                  # these files were written just now
    proj, _ = _frozen(tmp_path)
    Engine(proj, executor=TaskPacketExecutor()).step()                                  # a writable engine keeps the records
    opened, real = [], ss.open_regular
    monkeypatch.setattr(ss, "open_regular", lambda p, *a: (opened.append(str(p)), real(p, *a))[1])
    Engine(proj, read_only=True).status()
    assert opened == []
    spec = proj / "sources/spec.md"
    before = os.stat(spec)
    spec.write_text("# Product spex\n")                                               # same size, same inode ...
    os.utime(spec, ns=(before.st_atime_ns, before.st_mtime_ns))                         # ... and its old modification time
    warnings = Engine(proj, read_only=True).status()["warnings"]
    assert [p for p in opened if p.endswith("spec.md")] and any(w.startswith("source S2 changed") for w in warnings)


def test_a_copy_is_verified_on_the_bytes_it_copied(tmp_path, monkeypatch):
    """A source that changes between its check and its copy must not be kept as the registered version."""
    from vpg.source_store import SourceStore
    proj, eng = _frozen(tmp_path)
    by_id = {s.id: s for s in eng.state.inputs.sources}
    for f in (proj / ".vpg/sources").iterdir():
        os.chmod(f, 0o644)
        f.unlink()
    (proj / "sources/spec.md").write_text("# Product spec, edited\n")
    real = SourceStore.digest
    monkeypatch.setattr(SourceStore, "digest", lambda self, path: by_id["S2"].sha256 if path.name == "spec.md" else real(self, path))
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.step()
    assert not (proj / ".vpg/sources" / by_id["S2"].sha256).exists() and events(proj, "sources_kept")[-1]["sources"] == ["S1", "S3"]
    assert not list((proj / ".vpg/sources").glob(".*.part"))


def test_a_file_changed_in_the_last_seconds_is_hashed_but_not_recorded(tmp_path, monkeypatch):
    """Critic (racy clean): on a file system with coarse timestamps, a second edit within the same tick keeps the
    identity, so a file changed within RACY_S of its hash is not recorded. A copy the engine wrote itself is."""
    from vpg.source_store import SourceStore
    (tmp_path / ".vpg").mkdir()
    f = tmp_path / "spec.md"
    f.write_text("# Product spec\n")
    store = SourceStore(tmp_path, tmp_path / ".vpg")
    monkeypatch.setattr(SourceStore, "RACY_S", 1e9)                                   # always "just changed", even on a slow run
    assert store.digest(f) == sha256_file(f) and "spec.md" not in store._verified
    monkeypatch.setattr(SourceStore, "RACY_S", 0)
    store.digest(f)
    assert store._verified["spec.md"][4] == sha256_file(f)


def test_a_command_node_without_a_defects_input_is_still_checked_for_its_code(tmp_path):
    """Critic nit: a pass from before code fingerprints, on a command node without `defects:rework`, must not be marked
    current: it keeps its warning and its --adopt-code path."""
    proj = make_project(tmp_path, workflow=_rework_inputs(tmp_path, nodes=("d_build",)))
    (proj / "pipeline").mkdir()
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    _bind(proj, "{python} pipeline/run.py")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    _as_recorded_before(eng, proj, "c_audio", code=False)
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert "--adopt-code c_audio" in events(proj, "passes_carried_over")[0]["kept"]["c_audio"]
    assert eng.rt("c_audio").last_pass_scheme is None
    assert any("c_audio: its last pass predates command-code fingerprints" in w for w in eng.status()["warnings"])


def test_the_next_scheme_bump_re_derives_passes_recorded_under_this_one(tmp_path, monkeypatch):
    """Critic nit: the candidates were fixed at schemes 0 and 1, so after the next bump a pass recorded under scheme 2
    would not reproduce, and the false reason would be back. A pass whose scheme is known is re-derived under it."""
    import vpg.engine as ve
    proj, eng = _passed(tmp_path)
    ref = [r for r in eng.state.history.checkpoints if r.event == "node_passed" and r.node == "d_build"][-1]
    cp = ProjectState.model_validate(read_json(proj / ref.path))
    d = _defect("D1", "audio", "c_audio", "x", status="resolved")                     # routed elsewhere, present at the pass
    cp.evaluation.defects.append(d)
    eng.state.evaluation.defects.append(d.model_copy())
    write_json_atomic(proj / ref.path, cp)
    lp = eng.rt("d_build").last_pass_fingerprint
    assert eng.fingerprint(eng.node("d_build"), state=cp, rework=[]) == lp != eng.fingerprint(eng.node("d_build"), state=cp, scheme=1, rework=[])
    (proj / "flags").mkdir(exist_ok=True)
    (proj / "flags/c_audio.variant").write_text("a new take")                        # so d_build's inputs changed since
    eng.invalidate(["c_audio"], reason="a human asked for a new take")
    eng.rt("c_audio").force_rerun = True
    eng.step()
    monkeypatch.setattr(ve, "FINGERPRINT_SCHEME", 3)
    eng.accept_config(actor="Wei", reason="the next engine")
    [ev] = events(proj, "passes_carried_over")
    assert "d_build" in ev["carried"] and ev["kept"] == {}


def test_the_next_scheme_bump_re_derives_a_settled_rework_pass(tmp_path, monkeypatch):
    """Critic must-fix: a pass recorded since v1.5 is settled (no rework ids), so it must be re-derived without them;
    re-deriving it with its dispatch's ids reported it as not reproducible and re-ran it (N12's voice, at a bump)."""
    import vpg.engine as ve
    proj = make_project(tmp_path, workflow=_rework_inputs(tmp_path))
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    reworked = [e["node"] for e in events(proj, "node_dispatched") if e["rework"]]
    assert "d_build" in reworked and eng.rt("d_build").last_pass_scheme == FINGERPRINT_SCHEME
    (proj / "flags/c_audio.variant").write_text("a new take")                        # so d_build's inputs changed since
    eng.invalidate(["c_audio"], reason="a human asked for a new take")
    eng.rt("c_audio").force_rerun = True
    eng.step()
    monkeypatch.setattr(ve, "FINGERPRINT_SCHEME", 3)
    eng.accept_config(actor="Wei", reason="the next engine")
    [ev] = events(proj, "passes_carried_over")
    assert "d_build" in ev["carried"] and "d_build" not in ev["kept"]


def test_a_normal_dispatch_replaces_the_settled_fingerprint_of_an_earlier_rework_pass(tmp_path):
    """Critic must-fix: keeping the rework pass's settled fingerprint through a later normal dispatch would let the
    node memo-skip onto an output made from other inputs once its inputs returned to the earlier ones."""
    proj = make_project(tmp_path, workflow=_rework_inputs(tmp_path))
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert any(e["node"] == "d_build" and e["rework"] for e in events(proj, "node_dispatched"))
    eng.on_step = None
    for variant in ("B", None):                                                       # to input B, then back to A
        if variant:
            (proj / "flags/c_audio.variant").write_text(variant)
        else:
            (proj / "flags/c_audio.variant").unlink()
        eng.invalidate(["c_audio"], reason="another take")
        eng.rt("c_audio").force_rerun = True
        before = eng.rt("d_build").executions
        eng.run(max_steps=200)
        assert eng.rt("d_build").executions == before + 1, variant                   # B is new; A again is not B's output


def test_a_pass_whose_script_is_gone_is_not_carried_onto_empty_code(tmp_path):
    """Critic suggestion: with no current code at all (the script was deleted), `_narrowed_code` keeps the recorded
    code, so the node re-runs (and fails loudly) instead of memo-skipping."""
    proj, eng = _passed(tmp_path, command="{python} pipeline/run.py")
    _as_recorded_before(eng, proj, "c_audio", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    (proj / "pipeline/run.py").unlink()
    assert eng.project.command_code("c_audio") == []
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert eng.rt("c_audio").last_pass_fingerprint != eng.fingerprint(eng.node("c_audio"))


def test_a_rollback_across_the_upgrade_carries_the_restored_passes_over(tmp_path):
    """Critic suggestion: a rollback to a checkpoint from before the upgrade left its passes on the old scheme, so they
    re-ran once. It carries over what is provable, as accept-config does (without adoption)."""
    proj, eng = _passed(tmp_path)
    path = _as_recorded_before(eng, proj, "d_build", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    cfg = proj / "vpg.project.yaml"
    cfg.write_text(cfg.read_text() + "note: the v1.5 engine\n")
    eng = Engine(proj, executor=FnExecutor())
    eng.accept_config("Wei", "v1.5 engine")
    eng.run(max_steps=200)
    eng.rollback(int(path.name[:5]), actor="Wei")
    ev = events(proj, "passes_carried_over")[-1]
    assert ev["carried"] == ["d_build"] and ev["reason"].startswith("rollback to checkpoint")
    assert eng.rt("d_build").last_pass_scheme == FINGERPRINT_SCHEME
    rt = _recheck(eng, "d_build")
    assert rt.status == S.STEP_PASS and rt.executions == 1 and "d_build" in _after(proj, "rollback", "memo_hit")


def test_a_source_inside_a_symbolically_linked_directory_is_refused_and_reported(tmp_path):
    """Critic nit: only the last path component was checked, so a linked directory under sources/ got past both."""
    proj = _init_film(tmp_path)
    (tmp_path / "hub").mkdir()
    (tmp_path / "hub/spec.md").write_text("# Product spec, owned by another project\n")
    os.symlink(tmp_path / "hub", proj / "sources/hub")
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=20)
    _submit_sources(eng, proj, [BRIEF, {"id": "S2", "path": "sources/hub/spec.md", "kind": "spec", "evidence": "observed"}])
    assert "S2: sources/hub/spec.md is inside a symbolically linked directory (sources/hub)" in " ".join(eng.rt("n01_context_intake").failures)
    proj2, eng2 = _frozen(tmp_path / "second")
    (tmp_path / "moved").mkdir()
    shutil.move(str(proj2 / "sources"), tmp_path / "moved/sources")
    os.symlink(tmp_path / "moved/sources", proj2 / "sources")
    warnings = [w for w in Engine(proj2, read_only=True).status()["warnings"] if w.startswith("source ")]
    assert sorted(warnings) == [f"source {i} is inside a symbolically linked directory, so an edit to what it points to changes it; "
                                "replace it with a copy" for i in ("S1", "S2", "S3")]


def test_a_copy_the_engine_wrote_is_recorded_at_once(tmp_path, monkeypatch):
    """Critic nit: copies had no records, so every read-only status re-hashed all of them until the next writable step."""
    import vpg.source_store as ss
    monkeypatch.setattr(ss.SourceStore, "RACY_S", 1e9)                                # sources stay racy however slow the run is
    proj, _ = _frozen(tmp_path)
    opened, real = [], ss.open_regular
    monkeypatch.setattr(ss, "open_regular", lambda p, *a: (opened.append(str(p)), real(p, *a))[1])
    Engine(proj, read_only=True).status()
    assert opened and not [p for p in opened if "/.vpg/sources/" in p]


def test_the_scan_sees_literal_control_path_and_front_matter_reads_but_not_a_computed_name(tmp_path, monkeypatch):
    import importlib
    (tmp_path / "scan_probe.py").write_text(
        "from vpg.tools import _common\n\n"
        "def _helper(ctx):\n    return ctx.controls.front_matter('product')\n\n"
        "def tool(ctx):\n    ctx.control_path('design').read_text()\n    for doc in ('memory',):\n        ctx.fm(doc)\n"
        "    return _helper(ctx)\n\n"
        "def qualified(ctx):\n    return _common.thresholds(ctx)\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    probe = importlib.import_module("scan_probe")
    assert tool_control_reads(probe.tool) == {"design", "product"}
    assert tool_control_reads(probe.qualified) == {"evaluation"}


def test_a_pass_whose_code_was_only_narrowed_is_carried_with_the_narrower_code(tmp_path):
    """Excluding a generated file (or narrowing command_deps) in the accepted change must not re-run the node when every
    file it still counts is the one its pass recorded; a real edit to one of them still re-runs it."""
    for edit_real_code in (False, True):
        root = tmp_path / ("edited" if edit_real_code else "narrowed")
        root.mkdir()
        proj = make_project(root, workflow=_rework_inputs(root))
        (proj / "pipeline/film").mkdir(parents=True)
        (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
        (proj / "pipeline/film/data.js").write_text("// generated by the scene engine: v1\n")
        _bind(proj, "{python} pipeline/run.py")
        eng = Engine(proj, executor=FnExecutor())
        eng.run(max_steps=200)
        assert sorted(eng.rt("c_audio").command_code) == ["pipeline/film/data.js", "pipeline/run.py"]
        _as_recorded_before(eng, proj, "c_audio", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
        (proj / "pipeline/film/data.js").write_text("// generated by the scene engine: v2\n")
        if edit_real_code:
            (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT + "# a real edit\n")
        _bind(proj, "{python} pipeline/run.py", command_code_exclude=["pipeline/film/data.js"])
        eng = Engine(proj, executor=FnExecutor())
        [before] = [w for w in eng.status()["warnings"] if w.startswith("c_audio:")]     # #97: what Wei reads before accepting
        assert ("its code changed" in before and "1 file(s) it recorded are no longer counted" in before) if edit_real_code \
            else (before.startswith("c_audio: command_deps or command_code_exclude now leave out 1 file(s)")
                  and "accepting the configuration keeps its pass" in before and "re-runs" not in before), before
        eng.accept_config(actor="Wei", reason="v1.5 engine; exclude the generated scene data")
        assert events(proj, "passes_carried_over")[-1]["carried"] == ["c_audio"]
        assert events(proj, "config_accepted")[-1]["rerun"] == (["c_audio"] if edit_real_code else []), edit_real_code   # #99
        # #73: a narrowed carry records the narrower code, so status reports no change that re-runs nothing
        warned = [w for w in eng.status()["warnings"] if w.startswith("c_audio: its code changed")]
        assert bool(warned) == edit_real_code and sorted(eng.rt("c_audio").command_code) == (
            ["pipeline/film/data.js", "pipeline/run.py"] if edit_real_code else ["pipeline/run.py"]), edit_real_code
        rt = _recheck(eng, "c_audio")
        assert (rt.executions, rt.memo_hits) == ((2, 0) if edit_real_code else (1, 1)), edit_real_code


def test_a_narrowed_carry_records_the_code_its_proof_hashed(tmp_path, monkeypatch):
    """Critic r2 S2 (S3): the proof, the carried fingerprint and the recorded code map must be the same bytes. Here the
    tree reads differently after the proof; the pass must still be carried with the bytes the proof saw."""
    proj = make_project(tmp_path, workflow=_rework_inputs(tmp_path))
    (proj / "pipeline/film").mkdir(parents=True)
    (proj / "pipeline/run.py").write_text(COMMAND_SCRIPT)
    (proj / "pipeline/film/data.js").write_text("// generated by the scene engine: v1\n")
    _bind(proj, "{python} pipeline/run.py")
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    _as_recorded_before(eng, proj, "c_audio", defect=_defect("D1", "visual", "d_build", "x", status="resolved"))
    _bind(proj, "{python} pipeline/run.py", command_code_exclude=["pipeline/film/data.js"])
    eng = Engine(proj, executor=FnExecutor())
    real_code, real_narrow, seen = eng.project.command_code, eng._narrowed_code, {}

    def narrow(nid, recorded):
        seen[nid] = real_narrow(nid, recorded)
        return seen[nid]
    monkeypatch.setattr(eng, "_narrowed_code", narrow)
    monkeypatch.setattr(eng.project, "command_code",
                        lambda nid: [(p, "0" * 64) for p, _ in real_code(nid)] if nid in seen else real_code(nid))
    eng.accept_config(actor="Wei", reason="exclude the generated scene data")
    monkeypatch.undo()
    code, narrowed = seen["c_audio"]
    assert narrowed and eng.rt("c_audio").command_code == dict(code)
    rt = _recheck(eng, "c_audio")
    assert (rt.executions, rt.memo_hits) == (1, 1)


def test_a_rework_pass_of_a_node_with_no_command_or_defects_input_is_carried_too(tmp_path):
    """The rework defects were in every node's fingerprint, not only in a `defects:rework` input, so a node that reads
    neither defects nor code (d_build in the plain mini workflow) still needs its rework pass re-derived and settled."""
    proj = make_project(tmp_path)
    flag(proj, "defect")
    eng = Engine(proj, executor=FnExecutor())
    eng.on_step = lambda e: flag(proj, "defect", on=False) if e.state.runtime.rework_cycles else None
    eng.run(max_steps=200)
    assert "defects:rework" not in eng.node("d_build").inputs and not eng.project.command_for("d_build")
    ref = [r for r in eng.state.history.checkpoints if r.event == "node_passed" and r.node == "d_build"][-1]
    cp = ProjectState.model_validate(read_json(proj / ref.path))
    attempt = cp.runtime.nodes["d_build"].attempts
    rework = next(e["rework"] for e in reversed(events(proj, "node_dispatched")) if e["node"] == "d_build" and e["attempt"] == attempt)
    assert rework, "d_build's last pass should be a rework pass"
    fp = eng.fingerprint(eng.node("d_build"), state=cp, scheme=1, rework=rework)      # as v1.1–v1.4 recorded it
    for r in (cp.runtime.nodes["d_build"], eng.rt("d_build")):
        r.last_pass_fingerprint, r.last_pass_scheme = fp, None
    write_json_atomic(proj / ref.path, cp)
    eng._save("test")
    eng.accept_config(actor="Wei", reason="v1.5 engine")
    assert "d_build" in events(proj, "passes_carried_over")[0]["carried"]
    before = eng.rt("d_build").executions
    rt = _recheck(eng, "d_build")
    assert rt.memo_hits >= 1 and rt.executions == before
