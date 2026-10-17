"""`vpg dry-run`: run the sample production end to end and prove the workflow operates as designed.

Agents are scripted (fixtures). Media is real: PIL plates, macOS `say` narration and ffmpeg assembly. Human gates are
auto-approved and labelled `dry-run-auto` (NOT a human). The run exercises:
  * a node-level retry (N07 script attempt 1 uses a prohibited term and fails its accuracy check);
  * a checkpoint interruption after N11, then a resume from disk with a fresh engine;
  * human feedback on version 1 (simulated and labelled), interpreted at N21;
  * a gate-driven rework: N18's caption-sync audit finds a lag, N22 routes it to N14 only, reviewers with unchanged
    inputs are memo-skipped, and the gate compares before and after;
  * final verification, a verified package, and a learning proposal that is never applied automatically.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .engine import Engine, EngineError
from .executors import ScriptedExecutor
from .models import NodeStatus
from .provenance import Ledger
from .util import PKG_ROOT, write_json_atomic

EXAMPLE = PKG_ROOT / "examples" / "sample-demo"
MARKER = ".vpg-dry-run"   # marks a directory this command created, so `--out` never deletes anything else
REVIEWERS = ["n15_technical_qa", "n16_creative_critique", "n17_design_compliance", "n18_accessibility_legibility",
             "n19_product_accuracy", "n20_narrative_pacing"]


def dry_run(out: str | None = None, keep: bool = False) -> dict:
    tmp = None
    if out:
        proj = Path(out).resolve()
        if proj.exists():
            previous = (proj / MARKER).exists() or (proj / "dry-run-report.json").exists()
            if any(proj.iterdir()) and not previous:
                raise EngineError(f"refusing to replace {proj}: it exists and is not a previous dry-run output; "
                                  "pass a new or empty directory")
            shutil.rmtree(proj)
    else:
        tmp = tempfile.mkdtemp(prefix="vpg-dry-run-")
        proj = Path(tmp) / "sample-demo"
    shutil.copytree(EXAMPLE, proj, ignore=shutil.ignore_patterns("work", ".vpg", "__pycache__"))
    (proj / MARKER).write_text("created by `vpg dry-run`; safe to delete or replace\n")
    ex = ScriptedExecutor(proj / "fixtures")

    # Phase A: run until asset generation passes, then stop (a simulated interruption).
    eng = Engine(proj, executor=ex, auto_approve=True)
    for _ in range(200):
        eng.step()
        if eng.rt("n11_asset_generation").status == NodeStatus.STEP_PASS:
            break
    before = {nid: rt.executions for nid, rt in eng.state.runtime.nodes.items() if rt.status == NodeStatus.STEP_PASS}
    del eng

    # Phase B: resume from disk with a fresh engine; inject simulated feedback once version 1 has been reviewed.
    eng = Engine(proj, executor=ex, auto_approve=True)
    injected = {"done": False}

    def hook(e: Engine) -> None:
        if not injected["done"] and e.state.runtime.current_version == 1 and all(
                e.rt(n).status == NodeStatus.STEP_PASS for n in REVIEWERS):
            e.add_feedback("The captions appear after the words are spoken, right from the first line.",
                           actor="simulated reviewer (dry run, not Wei)", at="00:00–00:05", severity="P2")
            injected["done"] = True

    eng.on_step = hook
    status = eng.run(max_steps=400)
    st = eng.state
    rts = st.runtime.nodes
    if status["status"] != "RUN_PASS":
        details = "; ".join(f"{nid}: {', '.join(rt.failures)}" for nid, rt in rts.items()
                            if rt.status == NodeStatus.BLOCKED)
        raise EngineError(f"dry run stopped with {status['status']}: {details or 'workflow did not finish'}; "
                          f"inspect {proj / '.vpg' / 'escalations'}")
    ledger = Ledger(proj / ".vpg" / "ledger.jsonl")
    passed = {ev["data"]["node"] for ev in ledger.events("node_passed")} | {ev["data"]["node"] for ev in ledger.events("memo_hit")}
    memo = [ev["data"]["node"] for ev in ledger.events("memo_hit")]
    plans = [ev["data"] for ev in ledger.events("regeneration_plan")]
    tqa = [r for r in st.evaluation.reviews if r.node == "n15_technical_qa"]
    tqa_report = json.loads((proj / next(a.path for a in st.artifacts.values() if a.key == "technical_qa" and a.status == "current")).read_text())
    pkg = rts["n24_packaging_delivery"].result_data
    ok_ledger, n_events, ledger_msg = ledger.verify()
    evs = list(ledger.events())
    resumed_at = next(ev["seq"] for ev in evs if ev["type"] == "resumed")
    first_plan = next((ev["seq"] for ev in evs if ev["type"] == "regeneration_plan"), float("inf"))
    reexecuted = sorted({ev["data"]["node"] for ev in evs if ev["type"] == "node_dispatched" and resumed_at < ev["seq"] < first_plan
                         and ev["data"]["node"] in before})
    comparisons = [c.model_dump() for c in st.evaluation.comparisons]
    checks = {
        "run finished (RUN_PASS)": status["status"] == "RUN_PASS",
        "all 25 nodes passed at least once": len(passed & set(rts)) == 25,
        "node-level retry happened (N07 attempt 1 failed its accuracy check)": any(r.node == "n07_script_development" for r in st.history.retries),
        "checkpoint resume re-executed no approved node": st.runtime.resumes >= 1 and bool(before) and not reexecuted,
        "gate-driven rework cycle happened": st.runtime.rework_cycles >= 1 and bool(plans),
        "rework targeted only the traced node": bool(plans) and all(p["targets"] == ["n14_video_assembly"] for p in plans),
        "upstream nodes preserved (N01–N13 executed once, N07 twice)": all(rts[n].executions == (2 if n == "n07_script_development" else 1)
                                                                          for n in list(rts)[:13]),
        "unchanged reviewers memo-skipped": {"n17_design_compliance", "n19_product_accuracy", "n20_narrative_pacing"} <= set(memo),
        "defect resolved after rework (before/after comparison recorded)": any(c["resolved"] for c in comparisons),
        "feedback interpreted": all(f.status == "interpreted" for f in st.evaluation.feedback) and bool(st.evaluation.feedback),
        "final gate passed": bool(st.evaluation.gate_results) and st.evaluation.gate_results[-1].outcome == "pass",
        "technical QA passed on the real master": bool(tqa) and tqa[-1].verdict == "pass",
        "master is H.264/AAC at 1920x1080/30": (tqa_report["video_codec"], tqa_report["audio_codec"], tqa_report["width"], tqa_report["height"], tqa_report["fps"]) == ("h264", "aac", 1920, 1080, 30.0),
        "loudness within -16±1 LUFS, true peak ≤ -1 dBTP": abs(tqa_report["integrated_lufs"] + 16) <= 1 and tqa_report["true_peak_dbtp"] <= -1,
        "package verified against its manifest": bool(pkg.get("verified")),
        "ledger hash chain verifies": ok_ledger,
        "dry-run approvals labelled as not human": bool(st.history.approvals) and all(not a.human for a in st.history.approvals),
        "parallel fan-out batches ran": any(len(b) > 1 for b in st.runtime.batches),
    }
    report = {
        "ok": all(checks.values()), "project_dir": str(proj), "run": st.runtime.run_id, "status": status["status"],
        "checks": checks, "versions": len(st.history.versions), "rework_cycles": st.runtime.rework_cycles, "resumes": st.runtime.resumes,
        "retries": [r.model_dump() for r in st.history.retries], "memo_hits": memo, "regeneration_plans": plans,
        "comparisons": comparisons, "gates": [g.model_dump() for g in st.evaluation.gate_results],
        "parallel_batches": [b for b in st.runtime.batches if len(b) > 1],
        "executions": {n: rt.executions for n, rt in rts.items()},
        "technical_qa": {k: tqa_report[k] for k in ("duration_s", "width", "height", "fps", "video_codec", "audio_codec",
                                                     "integrated_lufs", "true_peak_dbtp", "lra", "av_diff_ms")},
        "package": pkg, "ledger_events": n_events, "ledger": ledger_msg,
        "defects": [d.model_dump() for d in st.evaluation.defects],
        "voice_engine": rts["n12_voice_audio"].result_data.get("engine"),
    }
    report["summary"] = "\n".join([f"dry run {'PASS' if report['ok'] else 'FAIL'} — {st.runtime.run_id} in {proj}"]
                                  + [f"  [{'x' if v else ' '}] {k}" for k, v in checks.items()]
                                  + [f"  versions {report['versions']}, rework cycles {report['rework_cycles']}, resumes {report['resumes']}, "
                                     f"memo-skipped {', '.join(memo)}", f"  master {report['technical_qa']}", f"  ledger: {n_events} events, {ledger_msg}"])
    write_json_atomic(proj / "dry-run-report.json", report)
    if tmp and not keep:
        report["project_dir"] = f"{proj} (removed; pass --keep to inspect)"
        shutil.rmtree(tmp, ignore_errors=True)
    return report
