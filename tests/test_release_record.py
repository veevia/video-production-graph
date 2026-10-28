"""N23's release summary must show the approver what the engine cannot verify: who acted under a human name, how fast,
and why (first real run, finding #26: it printed "Dry-run auto-approvals: none" for a run whose 20 human decisions were
all recorded by an agent), plus drifted inputs, declared redactions and batch-submitted reviews. It must never print a
listed name. All names below are fictional placeholders."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from types import SimpleNamespace as NS

from tests.conftest import PKG, FnExecutor, mini_workflow
from vpg.engine import Engine
from vpg.tools import verify
from vpg.tools.privacy import masker
from vpg.tools.verify import FAST_S, human_record


def _ev(clock: str, type_: str, **data) -> dict:
    return {"ts": f"2026-09-27T00:{clock}+00:00", "type": type_, "data": data}


def test_human_record_pairs_each_answer_with_its_own_prompt_and_flags_it():
    events = [
        _ev("00:00", "awaiting_human", node="n03", approver="Wei"),
        _ev("29:00", "decision", node="n03", decision="approved", actor="Wei", human=True, reason="no stop until v1"),
        _ev("30:00", "awaiting_human", node="n04", approver="Wei"),
        _ev("30:08", "decision", node="n04", decision="approved", actor="WEI", human=True, reason="No stop until  v1"),
        _ev("31:00", "escalated", node="integrity", reason="integrity"),
        _ev("31:01", "config_accepted", actor="Wei", reason=""),
        _ev("32:00", "change_request", id="CR-001", doc="product", by="agent:feedback-interpreter", reason="closes D018"),
        _ev("32:07", "change_request_approved", id="CR-001", doc="product", by="Wei"),
        _ev("33:00", "escalated", node="n21", reason="E37"),
        _ev("38:00", "escalated", node="n10", reason="rights"),                  # a later escalation elsewhere
        _ev("38:10", "unblocked", node="n21", actor="Wei", note="Delegated under Wei's standing instruction"),
        _ev("39:00", "control_restored", doc="creative", actor="Wei", kept="x"),  # 31:00 was already answered at 31:01
        _ev("40:00", "feedback", id="F001", actor="Wei", at="", version=1),
        _ev("40:01", "feedback", id="F002", actor="agent:critic", at="", version=1),
        _ev("41:00", "defect_resolved", id="D018", actor="Wei", evidence="CR-001 added the verified claim", was="open", version=8),
        _ev("42:00", "invalidated", targets=["n07"], reason="more words (requested by agent:script-writer)"),
        _ev("42:10", "invalidated", targets=["n01"], reason="late source (requested by cli)"),
        _ev("43:00", "decision", node="n09", decision="approved", actor="dry-run-auto", human=False, reason="dry run"),
        {"ts": "2026-09-27T00:44:00+00:00", "type": "decision", "data": ["not", "a", "dict"]},
        _ev("45:00", "decision", node="n06", decision="approved", actor="Wei", human=True, reason=None),
    ]
    rows = {(r["action"], r["subject"]): r for r in human_record(events)}
    assert set(rows) == {("decision", "n03 → approved"), ("decision", "n04 → approved"), ("config_accepted", "configuration change"),
                         ("change_request_approved", "CR-001 on product.md"), ("unblocked", "n21"),
                         ("control_restored", "controls/creative.md"), ("feedback", "F001"), ("defect resolved", "D018"),
                         ("invalidate", "n01"), ("decision", "n06 → approved")}   # agent filings, agent feedback, dry runs: left out
    n03, n04 = rows[("decision", "n03 → approved")], rows[("decision", "n04 → approved")]
    assert n03["latency_s"] == 1740 and "fast" not in n03["codes"] and n04["latency_s"] == 8 and "fast" in n04["codes"]
    assert "shared" in n03["codes"] and "shared" in n04["codes"]
    assert rows[("config_accepted", "configuration change")]["codes"] == ["fast", "no_reason"]
    cr = rows[("change_request_approved", "CR-001 on product.md")]
    assert cr["latency_s"] == 7 and cr["codes"] == ["fast", "agent_filed"]            # a CR approval takes no reason: not flagged
    unblock = rows[("unblocked", "n21")]
    assert unblock["latency_s"] == 310 and "delegation" in unblock["codes"]              # its own escalation, not the later one
    assert rows[("control_restored", "controls/creative.md")]["latency_s"] is None       # an answered escalation is not reused
    assert rows[("feedback", "F001")]["reason"] == ""                                     # feedback is listed by id only
    resolved = rows[("defect resolved", "D018")]
    assert resolved["reason"].startswith("CR-001") and "no_reason" not in resolved["codes"]
    assert rows[("invalidate", "n01")]["codes"] == ["no_actor"]
    assert rows[("decision", "n06 → approved")]["codes"] == ["no_reason"]


def test_human_record_includes_structured_relays_as_agent_acts():
    rows = human_record([
        _ev("00:00", "awaiting_human", node="n04", approver="Wei"),
        _ev("00:05", "decision", node="n04", decision="approved", actor="agent:runner", human=True, reason="proceed",
            relayed=True, principal="Wei", instruction="approve this and keep going", instruction_at="2026-09-30T12:00:00Z"),
    ])
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"] == "agent:runner" and row["principal"] == "Wei" and row["instruction"].startswith("approve")
    assert "relayed" in row["codes"] and "delegation" not in row["codes"]
    assert FAST_S == 60


def test_an_answered_escalation_is_not_reused_and_the_workflow_dry_run_actor_is_left_out():
    events = [_ev("00:00", "escalated", node="n21", reason="E37"),
              _ev("05:00", "unblocked", node="n21", actor="Wei", note="checked the take"),
              _ev("09:00", "unblocked", node="n21", actor="Wei", note="checked it again"),     # no new escalation
              _ev("10:00", "feedback", id="F009", actor="sim-bot", at="", version=1)]        # this workflow's dry-run actor
    rows = human_record(events, dry_run_actor="sim-bot")
    assert [(r["action"], r["latency_s"]) for r in rows] == [("unblocked", 300), ("unblocked", None)]


def test_a_malformed_released_holds_value_never_crashes_or_splits_into_letters():
    events = [_ev("00:00", "unblocked", node="n21", actor="Wei", note="a", released_holds=7),
              _ev("01:00", "unblocked", node="n22", actor="Wei", note="b", released_holds="F001")]
    assert [r["subject"] for r in human_record(events)] == ["n21", "n22"]


def test_listed_names_in_free_text_are_masked():
    events = [_ev("00:00", "awaiting_human", node="n04"),
              _ev("09:00", "decision", node="n04", decision="approved", actor="Wei", human=True,
                  reason="blur jane placeholder in the header; Placeholder asked for it")]
    row = human_record(events, mask=masker(["Jane Placeholder"]))[0]
    assert "jane" not in row["reason"].lower() and row["reason"].count("[list entry #1]") == 2


def test_every_human_ledger_event_is_in_the_record_or_deliberately_left_out():
    """A new human action must not be silently missing from the release summary."""
    tree = ast.parse((PKG / "vpg/engine.py").read_text())
    human_methods = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                     and any(isinstance(d, ast.Call) and getattr(d.func, "id", "") == "_human_action" for d in f.decorator_list)]
    appended = {n.args[0].value for f in human_methods for n in ast.walk(f)
                if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "append" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}
    source = (PKG / "vpg/tools/verify.py").read_text()
    mapped = {e for e in appended if f'"{e}"' in source}
    left_out = {"control_locked", "invalidated", "inputs_unfrozen", "note"}   # side effects of a mapped action
    assert appended and appended - mapped - left_out == set(), appended - mapped - left_out


def _human_gated_project(tmp_path):
    proj = tmp_path / "p"
    (proj / "controls").mkdir(parents=True)
    for doc in ("product", "creative", "design", "production", "evaluation", "memory"):
        shutil.copy2(PKG / "examples/sample-demo/controls" / f"{doc}.md", proj / "controls" / f"{doc}.md")
    wf = mini_workflow(tmp_path, human_b=True)
    (proj / "vpg.project.yaml").write_text(json.dumps({"id": "h", "workflow": str(wf)}))
    return proj


def test_release_summary_lays_out_the_human_record_and_warnings(tmp_path):
    proj = _human_gated_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="Delegated under a standing instruction")
    text = "make the opening match the approved film"
    eng.add_feedback(text, actor="Wei")
    eng.run(max_steps=200)                             # the test interpreter closes it with no action: held for Wei (v1.2)
    assert eng.state.runtime.status.value == "BLOCKED" and eng.state.evaluation.feedback[0].hold
    eng.unblock("g_gate", actor="Wei", note="the opening stays as it is")
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    (proj / "names.json").write_text(json.dumps({"names": ["Jane Placeholder", "Al"]}))
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    ctx.config = {**ctx.config, "privacy": {"names": "names.json"}}
    res = verify.final(ctx)
    summary = (ctx.workdir / "release-summary.md").read_text()
    report = json.loads((ctx.workdir / "verification.json").read_text())
    assert "## Human record" in summary and "## Warnings" in summary
    decision = next(r for r in report["human_record"] if r["action"] == "decision")
    assert decision["actor"] == "Wei" and {"delegation", "fast"} <= set(decision["codes"])
    feedback = next(r for r in report["human_record"] if r["action"] == "feedback")
    assert feedback["subject"] == "F001" and "make the opening" not in summary                # listed by id, never its text
    assert "held" in feedback["codes"]                                                          # the interpreter dismissed it
    unblock = next(r for r in report["human_record"] if r["action"] == "unblocked")
    assert unblock["subject"] == "g_gate (released F001)" and "released" in unblock["codes"]
    assert "1 feedback item(s) were held, and 1 unblock(s) released a hold" in summary
    assert "flagged human action(s) below" in summary.split("\n")[2]
    assert text not in summary and text not in json.dumps(report)                            # feedback by id, never its text
    assert any("privacy list entries #2 are too short" in w for w in report["warnings"])
    known = ("no current master", "deliverables not produced")                                 # the mini fixture renders no film
    assert all(p.startswith(known) for p in res.data["problems"])                              # warnings never block


def test_release_writes_blind_review_packet_template_and_html(tmp_path):
    proj = _human_gated_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="looks right")
    eng.run(max_steps=200)
    fork_dir = proj / "work/forks"
    fork_dir.mkdir(parents=True)
    fork = {
        "fork_id": "F-001",
        "fork_type": "concept",
        "node": "n06_creative_direction",
        "version": eng.state.runtime.current_version,
        "dims": [{"id": "CON-FIT", "measured": False}],
        "candidates": [
            {"id": "A", "label": "Alpha", "scores": {"CON-FIT": 5}, "passes_rubric": True, "rationale": "strong"},
            {"id": "B", "label": "Beta", "scores": {"CON-FIT": 3}, "passes_rubric": True, "rationale": "useful"},
        ],
        "scored": {"model_version": 0, "predicted": {"A": 0.7, "B": 0.3}, "confidence": 0.8, "band": "flag", "build": ["A", "B"]},
    }
    (fork_dir / "F-001.json").write_text(json.dumps(fork))
    eng.state.production.forks_ref["F-001"] = "work/forks/F-001.json"
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")

    verify.final(ctx)

    packet = (ctx.workdir / "review-packet.md").read_text()
    template = json.loads((ctx.workdir / "decision.template.json").read_text())
    html = (ctx.workdir / "review.html").read_text()
    blind = packet.split("## 9. Reveal", 1)[0]
    assert "## 2. Blind fork candidates" in packet and "## 9. Reveal" in packet
    assert "scores" not in blind and "0.7" not in blind
    assert "Current model: v" in packet and "Predicted probabilities" in packet and "0.7" in packet
    assert template["forks"]["F-001"]["ranking"] == []
    assert template["forks"]["F-001"]["ratings"] == {}
    assert template["forks"]["F-001"]["why"] == ""
    assert template["forks"]["F-001"]["sha256"]
    assert "Export decision.json" in html and "Reveal scores" in html


def test_release_warnings_report_drift_redaction_counts_and_a_batch_of_three_reviews(tmp_path):
    from vpg.models import SourceRef, VersionRecord
    proj = _human_gated_project(tmp_path)
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    eng.decide("b_plan", "approved", actor="Wei", reason="looks right")
    eng.run(max_steps=200)
    src = proj / "sources/spec.md"
    src.parent.mkdir(exist_ok=True)
    src.write_text("text at freeze time")
    eng.state.inputs.sources.append(SourceRef(id="S99", path="sources/spec.md", kind="spec", evidence="observed",
                                              sha256=hashlib.sha256(b"text at freeze time").hexdigest()))
    eng.state.inputs.frozen = True
    eng.rt("d_build").result_data["redactions"] = {"Jane Placeholder": [0, 0, 10, 10]}
    ctx = eng._context(eng.node("z_done"), 1, "fp", "tok")
    assert not any("frozen inputs" in w for w in verify.release_warnings(ctx, eng.wf, []))
    src.write_text("edited after the freeze")                                             # a hard-linked hub file edited elsewhere
    warnings = verify.release_warnings(ctx, eng.wf, [])
    assert any("frozen inputs changed on disk" in w and "S99" in w for w in warnings)
    assert any("d_build declared redactions (1 fields)" in w for w in warnings) and not any("Jane" in w for w in warnings)
    wf = NS(nodes=[NS(id="g", gate="review_gate")], edges=[NS(source=r, target="g", kind=NS(value="flow")) for r in "abcde"])
    ctx2 = NS(state=NS(inputs=NS(frozen=False, sources=[]), runtime=NS(nodes={}),
                       history=NS(versions=[VersionRecord(version=2, created_at="2026-09-27T00:40:00+00:00", node_attempt="x",
                                                          artifacts={})])))
    sub = lambda clock, n: _ev(clock, "node_submitted", node=n, actor=f"agent:{n}")
    batch = [sub("41:00", "a"), sub("41:03", "b"), sub("41:06", "c"), sub("47:00", "d"), sub("52:00", "e")]
    assert any("3 reviewers submitted within 6 s" in w for w in verify.release_warnings(ctx2, wf, batch))
    same = [sub("41:00", "a"), sub("41:04", "a"), sub("41:08", "a"), sub("47:00", "b")]      # one reviewer, three submissions
    assert verify.release_warnings(ctx2, wf, same) == []
    odd = batch + [{"ts": 12345, "type": "node_submitted", "data": {"node": "a"}}, {"type": "node_submitted", "data": "x"}]
    assert verify.release_warnings(ctx2, wf, odd)                                              # malformed events never crash
    older = [sub("39:00", "a"), sub("39:01", "b"), sub("39:02", "c"), sub("45:00", "d")]    # before the current version
    assert verify.release_warnings(ctx2, wf, older) == []
