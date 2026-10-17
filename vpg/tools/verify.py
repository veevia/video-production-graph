"""Final verification (N23 tool part). It prepares the release decision; the release itself stays a human decision.

Checks, recomputed from state and disk:
  * the latest gate for the current version is `pass`, with a current review from every reviewer node;
  * no open blocking defect remains;
  * the master and every other deliverable on disk still match their registered hashes;
  * every deliverable listed in production.md exists as a current artifact;
  * the provenance ledger verifies;
  * each earlier human gate has a recorded decision (dry-run auto-approvals are flagged, not hidden);
  * when the project declares a confidential name list, no listed name is readable in any frame of the master
    (tools/privacy.py; fails closed without OCR).

It also lays out what the release approver must judge personally, as warnings that never block on their own:
  * the human record: every action recorded under a human name, what prompted it, how fast it came and why. The engine
    cannot see who typed a name, so fast, reasonless, shared-reason or delegated actions are flagged for review;
  * frozen inputs whose files changed on disk after the freeze;
  * redactions that producer commands declared (no engine tool verifies them);
  * reviews of the current version submitted within seconds of each other (possibly one author);
  * (v1.7) reviews not bound to the build they judged, or not independent of its producers; a node that read a source
    whose bytes differ from the registered ones;
  * (v1.7) every action an agent took under a human's delegation grant, named with the grant, never as the human's own.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable

from ..context import CheckResult, NodeResult
from ..provenance import Ledger
from ..util import is_human_name, read_json, sha256_file, write_json_atomic
from .privacy import PrivacyConfigError, load_names, masker, release_problem

FAST_S = 60                  # a human answer faster than this is flagged, never refused
REVIEW_BATCH_S = 10          # three reviews submitted within this window are flagged
_DELEGATION = re.compile(r"delegat|standing instruction|on behalf of", re.I)
_TAKES_REASON = {"decision", "config_accepted", "unblocked", "ledger_break_accepted", "gap_waived", "defect_resolved",
                 "change_request_rejected", "change request deferred", "grant issued", "grant revoked"}
_FLAG_TEXT = {"fast": "answered in {latency} s", "no_reason": "no reason recorded", "shared": "reason shared with other actions",
              "delegation": "cites delegation", "agent_filed": "filed by an agent", "no_actor": "no named actor",
              "released": "released a hold", "held": "held: it led to no open blocking defect",
              "adopted": "kept a pass on the actor's word (--adopt-code)",
              "granted": "an agent acting under {grant}, not the human"}


def _seconds(start: str | None, end: str | None) -> int | None:
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def human_record(events: Iterable[dict], feedback: Iterable[Any] = (), mask: Callable[[str], str] = lambda t: t,
                 dry_run_actor: str = "dry-run-auto") -> list[dict]:
    """Every ledger action recorded under a human name, with the prompt it answered, its latency, reason and flags.

    Latency pairs each answer with its own prompt: a decision with its gate opening, a change-request approval or rejection
    with its filing, an unblock with its node's escalation, an accepted configuration or a restored control document with the
    integrity escalation. Feedback is listed by id only, and listed names in free text are masked."""
    feedback_actor = {getattr(f, "id", None): getattr(f, "actor", "") for f in feedback}
    awaiting: dict[str, str] = {}
    filed: dict[str, tuple[str, str]] = {}
    escalated: dict[str, str] = {}
    held: set[str] = set()
    adopted: list[str] = []   # named by a passes_carried_over event, which comes just before its config_accepted
    reverted: set[str] = set()   # nodes whose legacy blocked plan an unblock reverted (the event comes just before it)
    rows: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        t, ts = ev.get("type"), _text(ev.get("ts"))
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t == "awaiting_human":
            awaiting[_text(d.get("node"))] = ts
            continue
        if t == "feedback_held":   # an engine event: the feedback it names is flagged on its own row below
            held.add(_text(d.get("id")))
            continue
        if t == "escalated":
            escalated[_text(d.get("node"))] = ts
            continue
        if t == "change_request":
            filed[_text(d.get("id"))] = (ts, _text(d.get("by")))
        row = None
        if t == "decision" and (d.get("human", True) or d.get("grant")):
            node = _text(d.get("node"))
            row = {"action": "decision", "subject": f"{node} → {_text(d.get('decision'))}", "actor": _text(d.get("actor")),
                   "since": awaiting.get(node), "prompt": "gate opened", "reason": _text(d.get("reason"))}
        elif t == "change_request":
            row = {"action": "change request filed", "subject": f"{_text(d.get('id'))} on {_text(d.get('doc'))}.md",
                   "actor": _text(d.get("by")), "since": None, "prompt": "", "reason": _text(d.get("reason"))}
        elif t == "change_request_approved":
            since, filer = filed.get(_text(d.get("id")), (None, ""))
            row = {"action": "change_request_approved", "subject": f"{_text(d.get('id'))} on {_text(d.get('doc'))}.md",
                   "actor": _text(d.get("by")), "since": since, "prompt": f"filed by {filer or 'unknown'}", "reason": _text(d.get("reason")),
                   "filer": filer}
        elif t == "change_request_rejected":
            since, filer = filed.get(_text(d.get("id")), (None, ""))
            row = {"action": "change_request_rejected", "subject": f"{_text(d.get('id'))} on {_text(d.get('doc'))}.md",
                   "actor": _text(d.get("by")), "since": since, "prompt": f"filed by {filer or 'unknown'}", "reason": _text(d.get("reason")),
                   "filer": filer}
        elif t == "change_request_deferred":
            since, filer = filed.get(_text(d.get("id")), (None, ""))
            row = {"action": "change request deferred", "subject": f"{_text(d.get('id'))} on {_text(d.get('doc'))}.md, until: "
                   f"{_text(d.get('revisit'))}", "actor": _text(d.get("by")), "since": since, "prompt": f"filed by {filer or 'unknown'}",
                   "reason": _text(d.get("reason"))}
        elif t in ("grant_issued", "grant_revoked"):
            acts = d.get("actions") if isinstance(d.get("actions"), list) else []
            row = {"action": t.replace("_", " "), "subject": (f"{_text(d.get('id'))}: {_text(d.get('grantee'))} may "
                   f"{', '.join(map(_text, acts))} until {_text(d.get('expires'))}" if t == "grant_issued" else _text(d.get("id"))),
                   "actor": _text(d.get("grantor") if t == "grant_issued" else d.get("by")), "since": None, "prompt": "",
                   "reason": _text(d.get("reason"))}
        elif t == "feedback_triaged":
            row = {"action": f"agent's note {_text(d.get('outcome'))}", "subject": f"{_text(d.get('id'))} by {_text(d.get('author'))}",
                   "actor": _text(d.get("by")), "since": None, "prompt": "", "reason": _text(d.get("reason"))}
        elif t == "legacy_plan_reverted":   # part of one unblock: it joins that unblock's row
            reverted.add(_text(d.get("node")))
            continue
        elif t == "unblocked":
            node = _text(d.get("node"))
            holds = d.get("released_holds")
            released = [_text(x) for x in holds if isinstance(x, str)] if isinstance(holds, list) else []
            loops = [_text(x) for x in d.get("reset_loops") or [] if isinstance(x, str)] if isinstance(d.get("reset_loops"), list) else []
            notes = ([f"released {', '.join(released)}"] if released else []) + ([f"reset loops {', '.join(loops)}"] if loops else []) \
                + (["reset the rework budget"] if d.get("reset_budget") is True else []) \
                + (["reverted a plan an older engine applied although blocked"] if node in reverted else [])
            reverted.discard(node)
            row = {"action": "unblocked", "subject": node + (f" ({'; '.join(notes)})" if notes else ""),
                   "actor": _text(d.get("actor")), "since": escalated.pop(node, None), "prompt": "escalation",
                   "reason": _text(d.get("note")), "released": released}
        elif t in ("config_accepted", "control_restored"):
            since = escalated.pop("integrity", None) or (escalated.pop("control-integrity", None) if t == "control_restored" else None)
            row = {"action": t, "subject": "configuration change" if t == "config_accepted" else f"controls/{_text(d.get('doc'))}.md",
                   "actor": _text(d.get("actor")), "since": since, "prompt": "escalation",
                   "reason": _text(d.get("reason")) if t == "config_accepted" else ""}
            if t == "config_accepted" and adopted:
                row["subject"] += f"; kept the last pass of {', '.join(adopted)}"
                row["adopted"], adopted = True, []
        elif t == "passes_carried_over":   # part of one accept-config: the adoption joins its config_accepted row
            names = d.get("adopted") if isinstance(d.get("adopted"), list) else []
            adopted = [_text(x) for x in names if isinstance(x, str)]
        elif t == "feedback":
            fid = _text(d.get("id"))
            row = {"action": "feedback", "subject": fid, "actor": _text(d.get("actor") or feedback_actor.get(fid)),
                   "since": None, "prompt": "", "reason": ""}
        elif t == "defect_resolved":
            row = {"action": "defect resolved", "subject": _text(d.get("id")), "actor": _text(d.get("actor")), "since": None,
                   "prompt": "", "reason": _text(d.get("evidence"))}
        elif t in ("rollback", "ledger_break_accepted", "gap_waived"):
            subject = {"rollback": f"checkpoint {_text(d.get('to'))}", "ledger_break_accepted": "ledger break",
                       "gap_waived": f"intake gap {_text(d.get('gap'))}"}[t]
            row = {"action": t, "subject": subject, "actor": _text(d.get("actor")), "since": None, "prompt": "",
                   "reason": _text(d.get("reason"))}
        elif t == "invalidated":
            reason = _text(d.get("reason"))
            head, sep, tail = reason.rpartition("(requested by ")
            actor = tail[:-1].strip() if sep and tail.endswith(")") else ""
            if actor and (not actor.lower().startswith("agent") or d.get("grant")):
                targets = d.get("targets") if isinstance(d.get("targets"), list) else []
                row = {"action": "invalidate", "subject": ", ".join(map(_text, targets)), "actor": actor, "since": None,
                       "prompt": "", "reason": reason}
        granted = _text(d.get("grant")) if row is not None else ""
        if row is None or (row["actor"] != "cli" and not is_human_name(row["actor"], dry_run_actor) and not granted):
            continue
        if granted:   # stamped at append by the engine (Ledger.stamp): an agent's act on a human's delegation
            row["grant"] = f"grant {granted} from {_text(d.get('grantor')) or 'unknown'}"
        row["at"] = ts
        row["latency_s"] = _seconds(row.pop("since"), ts)
        row["subject"], row["reason"] = mask(row["subject"]), mask(row["reason"])
        rows.append(row)
    keys = [" ".join(r["reason"].lower().split())[:200] for r in rows]
    for row, key in zip(rows, keys):
        codes = []
        if row["actor"] == "cli":
            codes.append("no_actor")
        if row["latency_s"] is not None and row["latency_s"] < FAST_S:
            codes.append("fast")
        if row["action"] in _TAKES_REASON and not row["reason"].strip():
            codes.append("no_reason")
        if key and keys.count(key) > 1:
            codes.append("shared")
        if _DELEGATION.search(row["reason"]):
            codes.append("delegation")
        if row["action"] == "change_request_approved" and row.get("filer", "").lower().startswith("agent"):
            codes.append("agent_filed")
        if row.pop("adopted", None):
            codes.append("adopted")
        if row.pop("released", None):
            codes.append("released")
        if row["action"] == "feedback" and row["subject"] in held:
            codes.append("held")
        if row.get("grant"):
            codes.append("granted")
        row["codes"] = codes
        row["flags"] = [_FLAG_TEXT[c].format(latency=row["latency_s"], grant=row.get("grant", "")) for c in codes]
    return rows


def _cell(text: Any, limit: int = 100) -> str:
    s = " ".join(_text(text).split()).replace("|", "/")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def release_warnings(ctx, wf, events: list[dict]) -> list[str]:
    """Checks that don't block a release but that the approver must look at personally. They print ids and counts
    only, never the content of a declared field."""
    st = ctx.state
    warnings = []
    if st.inputs.frozen:
        drifted, missing = [], []
        for s in st.inputs.sources:
            p = ctx.project_dir / s.path
            if not p.is_file():
                missing.append(s.id)
            elif s.sha256 and sha256_file(p) != s.sha256:
                drifted.append(s.id)
        if drifted or missing:
            warnings.append("frozen inputs changed on disk after the freeze, so the film's sources no longer match its provenance: "
                            + "; ".join(x for x in (f"changed {', '.join(drifted)}" if drifted else "",
                                                    f"missing {', '.join(missing)}" if missing else "") if x))
    for nid, rt in st.runtime.nodes.items():
        for key, value in (rt.result_data or {}).items():
            if ("redact" in key.lower() or "veil" in key.lower()) and value:
                what = (f"{len(value)} entries" if isinstance(value, (list, tuple)) else
                        f"{len(value)} fields" if isinstance(value, dict) else "declared")
                warnings.append(f"{nid} declared redactions ({what}); no engine tool verifies where they sit, so check every "
                                "frame where product UI moves")
    artifacts = getattr(st, "artifacts", None) or {}
    layers_ref = next((a for a in sorted(artifacts.values(), key=lambda a: a.id) if a.key == "text_layers" and a.status == "current"), None)
    try:
        layers = (read_json(ctx.project_dir / layers_ref.path) or {}).get("layers", []) if layers_ref else []
    except Exception:
        layers = []
    bars = [x for x in layers if isinstance(x, dict) and x.get("kind") == "redaction"]
    if bars:   # #22: the approver sees every declared redaction; the privacy scan checks names, not the pixels around them
        scenes = sorted({str(x.get("scene")) for x in bars})
        warnings.append(f"{len(bars)} declared redaction layer(s) in text_layers ({', '.join(scenes[:8])}{', …' if len(scenes) > 8 else ''}); "
                        "N19 checks each is an opaque bar, and the privacy scan checks no listed name is readable, but no engine "
                        "tool compares the UI around them with the capture")
    gate = next((n for n in wf.nodes if n.gate == "review_gate"), None)
    reviewers = {e.source for e in wf.edges if gate is not None and e.target == gate.id and e.kind.value == "flow"}
    versions = getattr(getattr(st, "history", None), "versions", None) or []
    since = versions[-1].created_at if versions else ""
    subs = sorted((ev["ts"], ev["data"]["node"]) for ev in events if isinstance(ev, dict) and isinstance(ev.get("ts"), str)
                  and ev.get("type") == "node_submitted" and isinstance(ev.get("data"), dict)
                  and ev["data"].get("node") in reviewers and ev["ts"] >= since)
    for k, (start, _) in enumerate(subs):
        window = [(ts, node) for ts, node in subs[k:] if (_seconds(start, ts) or 0) <= REVIEW_BATCH_S]
        if len({node for _, node in window}) >= 3:
            spread = _seconds(start, window[-1][0])
            warnings.append(f"{len({node for _, node in window})} reviewers submitted within {spread} s for the current version; "
                            "they may share one author, so they are not independent evidence")
            break
    evaluation = getattr(st, "evaluation", None)
    latest = {r.node: r for r in getattr(evaluation, "reviews", None) or []   # the gate uses each node's latest review
              if r.version == getattr(st.runtime, "current_version", None) and r.node in reviewers}
    current = list(latest.values())
    unbound = sorted(r.node for r in current if getattr(r, "actor", "").startswith("agent:") and not getattr(r, "reviewed", None))
    shared = sorted(r.node for r in current if getattr(r, "independent", None) is False)
    anonymous = sorted(r.node for r in current if getattr(r, "actor", "").startswith("agent:")
                       and getattr(r, "independent", None) is None)
    older = sorted(r.node for r in current if not getattr(r, "actor", ""))
    if unbound:
        warnings.append(f"reviews not bound to the build they judged (no `reviewed` echo): {', '.join(unbound)}")
    if shared:
        warnings.append(f"reviews submitted by an identity that produced what they judged (not independent): {', '.join(shared)}")
    if anonymous:
        warnings.append(f"reviews submitted without a reviewer identity, so their independence is unknown: {', '.join(anonymous)}")
    if older:
        warnings.append(f"reviews recorded before v1.7, so not bound to the bytes they judged: {', '.join(older)}")
    warnings += _source_byte_warnings(st, events)
    return warnings


def _source_byte_warnings(st, events: list[dict]) -> list[str]:
    """#100: a node's passing attempt read a source whose bytes differ from the registered ones (recorded at dispatch)."""
    registered = {s.id: (s.sha256 or "")[:16] for s in st.inputs.sources}
    passed = {}
    for ev in events:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "node_passed":
            passed[_text(d.get("node"))] = d.get("attempt")
    seen = {}
    for ev in events:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "node_dispatched" and isinstance(d.get("sources"), dict):
            seen[(_text(d.get("node")), d.get("attempt"))] = d["sources"]
    out = []
    for node, attempt in sorted(passed.items()):
        read = seen.get((node, attempt))
        if not read:
            continue
        diff = sorted(sid for sid, sha in read.items() if registered.get(sid) and sha != registered[sid])
        if diff:
            out.append(f"{node} attempt {attempt} read {', '.join(diff)} with other bytes than registered (e.g. {diff[0]}: "
                       f"{read[diff[0]]} vs {registered[diff[0]]}); its pass rests on those bytes")
    return out


def engine_decisions(events: Iterable[dict], mask: Callable[[str], str] = lambda t: t) -> list[str]:
    """What the engine decided in a human's place, and who checked attempts before the graph reviewed them (v1.6): each
    budget extension with its reason, and each independent pre-flight verdict. Nothing when neither happened, so an
    older run's summary is unchanged."""
    ext, pre = [], []
    for ev in events:
        if not isinstance(ev, dict) or not isinstance(ev.get("data"), dict):
            continue
        t, d, ts = ev.get("type"), ev["data"], _text(ev.get("ts"))[:19].replace("T", " ")
        if t == "loop_budget_extended":
            loops = ", ".join(f"{e} {a} → {b}" for e, (a, b) in sorted((d.get("loops") or {}).items()))
            ext.append(f"- {ts}: loop {loops} ({_text(d.get('profile'))}): {_cell(mask(_text(d.get('reason'))), 200)}")
        elif t == "rework_budget_extended":
            ext.append(f"- {ts}: rework cycles {d.get('before')} → {d.get('after')} ({_text(d.get('profile'))}): "
                       f"{_cell(mask(_text(d.get('reason'))), 200)}")
        elif t in ("preflight_passed", "preflight_revise"):
            verdict = "passed" if t == "preflight_passed" else "returned for revision"
            pre.append(f"- {ts}: {_text(d.get('node'))} attempt {d.get('attempt')} {verdict} by {_text(d.get('actor'))}"
                       + (f": {_cell(mask(_text(d.get('notes'))), 200)}" if d.get("notes") else ""))
    out = []
    if ext:
        out += ["", "## Budget extensions (the run was converging, so a spent budget did not stop it)", *ext]
    if pre:
        out += ["", "## Independent pre-flight verdicts", *pre]
    return out


def final(ctx) -> NodeResult:
    st = ctx.state
    v = st.runtime.current_version
    problems = []
    gates = [g for g in st.evaluation.gate_results if g.version == v]
    if not gates or gates[-1].outcome != "pass":
        problems.append(f"no passing gate for version {v}")
    from ..graph import load_workflow
    wf_ref = ctx.config.get("workflow")
    wf = load_workflow(None if not wf_ref or wf_ref == "default" else ctx.project_dir / wf_ref)
    policies_blocking = set(wf.policies.blocking_severities)
    open_blocking = [d.id for d in st.evaluation.defects if d.status in ("open", "persisting") and d.severity in policies_blocking]
    if open_blocking:
        problems.append(f"open blocking defects: {open_blocking}")
    masters = [a for a in st.artifacts.values() if a.key == "master" and a.status == "current"]
    if not masters:
        problems.append("no current master")
    else:
        m = masters[-1]
        if sha256_file(ctx.project_dir / m.path) != m.sha256:
            problems.append("the master on disk no longer matches the reviewed hash")
        if m.version != v:
            problems.append(f"current master is version {m.version}, reviews are for version {v}")
        leak = release_problem(ctx, ctx.project_dir / m.path, m.sha256)
        if leak:
            problems.append(leak)
    wanted = ctx.fm("production").get("deliverables", [])
    if not wanted:
        problems.append("production.md lists no deliverables, so the release would ship nothing; restore the document")
    for d in wanted:   # every deliverable must still be the file that was registered (and reviewed)
        for a in (a for a in st.artifacts.values() if a.key == d.get("key") and a.status == "current"):
            path = ctx.project_dir / a.path
            if not path.exists():
                problems.append(f"deliverable {a.key} ({a.path}) is missing on disk")
            elif sha256_file(path) != a.sha256:
                problems.append(f"deliverable {a.key} ({a.path}) changed on disk after it was registered")
    have = {a.key for a in st.artifacts.values() if a.status == "current"}
    own = {o.key for o in ctx.node.outputs}   # produced by this verification itself
    missing = [d["key"] for d in wanted if d["key"] not in have and d["key"] not in own]
    if missing:
        problems.append(f"deliverables not produced: {missing}")
    ok, n, msg = Ledger(ctx.project_dir / ".vpg" / "ledger.jsonl").verify()
    if not ok:
        problems.append(f"ledger does not verify: {msg}")
    tail_problem = Ledger(ctx.project_dir / ".vpg" / "ledger.jsonl", read_only=True).reaches(st.runtime.ledger_tail)
    if tail_problem:
        problems.append(tail_problem)
    approvals = [a.model_dump() for a in st.history.approvals]
    auto = [a["node"] for a in approvals if not a["human"] and not a.get("grant")]
    delegated = [f"{a['node']} → {a['decision']} by {a['actor']} under {a['grant']}" for a in approvals if a.get("grant")]
    triage = [f.id for f in st.evaluation.feedback if getattr(f, "triage", "") == "pending"]
    if triage:
        problems.append(f"agents' notes await a human's triage: {', '.join(triage)} (`vpg feedback --triage F --accept|--dismiss`)")
    open_nits = [f"{d.id} {d.dimension}: {d.description}" for d in st.evaluation.defects if d.status in ("open", "persisting") and d.severity == "nit"]
    names = {d["id"]: d["name"] for r in ctx.rubrics.values() for d in r["dimensions"]}
    pending_human = sorted({dim for r in st.evaluation.reviews if r.version == v for dim in r.pending_human})
    auto_human = sorted({d["id"] for nid in auto for rid in wf.node(nid).rubric for d in ctx.rubrics.get(rid, {}).get("dimensions", [])
                         if d["check"]["type"] == "human"})
    try:
        events = list(Ledger(ctx.project_dir / ".vpg" / "ledger.jsonl", read_only=True).events())
    except Exception:   # a damaged ledger is already a release problem above; the record is then built from nothing
        events = []
    try:
        listed, skipped = load_names(ctx.project_dir, ctx.config)
    except PrivacyConfigError:   # already a release problem; free text is then shown as recorded
        listed, skipped = [], []
    mask = masker(listed) if listed else (lambda text: text)
    record = human_record(events, st.evaluation.feedback, mask, wf.policies.dry_run_actor)
    warnings = release_warnings(ctx, wf, events)
    if skipped:
        warnings.append(f"privacy list entries #{', #'.join(map(str, skipped))} are too short to check (a single word needs 4+ letters)")
    flagged = [r for r in record if r["codes"]]
    actors = sorted({str(r["actor"]) for r in record})
    count = lambda code: sum(code in r["codes"] for r in record)
    review_note = (f" Before deciding, review the {len(warnings)} warning(s) and the {len(flagged)} flagged human action(s) below."
                   if warnings or flagged else "")
    summary = [
        f"# Release decision — {st.project.name}, version {v}", "",
        f"**TL;DR:** version {v} {'passes every recomputed release check' if not problems else 'is NOT releasable: ' + '; '.join(problems)}. "
        f"Releasing it is a human decision.{review_note}", "",
        f"- Gate: {gates[-1].outcome if gates else 'none'} ({'; '.join(gates[-1].reasons) if gates else ''})",
        f"- Rework cycles: {st.runtime.rework_cycles}; defects resolved: {sum(d.status == 'resolved' for d in st.evaluation.defects)}",
        f"- Ledger: {n} events, {'verified' if ok else msg}",
        f"- Master: {masters[-1].path if masters else '-'} (sha256 {masters[-1].sha256[:16] if masters else '-'})",
        f"- Open nits (non-blocking): {', '.join(open_nits) or 'none'}",
        f"- Dry-run auto-approvals, NOT human decisions: {', '.join(auto) or 'none'}",
        f"- Decisions an agent made under a human's delegation grant, NOT the human's own: {'; '.join(delegated) or 'none'}",
        "- Ledger breaks accepted by a named human: "
        + ("; ".join(f"{b['actor']} ({b['at'][:10]}): events after {b['lost_after_seq']} lost — {b['reason']}"
                     for b in st.runtime.ledger_breaks) or "none"),
        "- Awaiting a named human (never claimed by an agent): "
        + (", ".join(f"{d} ({names.get(d, d)})" for d in pending_human) or "none recorded by the reviews"),
        "- Human decisions covered only by a dry-run auto-approval (NOT human): "
        + (", ".join(f"{d} ({names.get(d, d)})" for d in auto_human) or "none"),
        "- Not claimed by any agent: human listening to voice and music, taste approval, release approval.",
        "", "## Warnings (non-blocking; judge them yourself)",
        *([f"- {w}" for w in warnings] or ["- none"]),
        "", "## Human record",
        "The engine cannot tell who typed a human name. Review the flagged rows (fast, reasonless, shared-reason, delegated or "
        "agent-filed) yourself before releasing.",
        f"- {len(record)} action(s) under {', '.join(actors) or 'nobody'}; {count('fast')} answered in under {FAST_S} s; "
        f"{count('shared')} share a reason; {count('delegation')} cite delegation; {count('no_reason')} record no reason; "
        f"{count('agent_filed')} approve an agent-filed change request; {count('held')} feedback item(s) were held, and "
        f"{count('released')} unblock(s) released a hold.",
    ]
    if record:
        summary += ["", "| When (UTC) | Action | Subject | Actor | Prompt | Latency | Flags | Reason |", "|---|---|---|---|---|---|---|---|"]
        summary += [f"| {r['at'][:19].replace('T', ' ')} | {r['action']} | {_cell(r['subject'], 60)} | {_cell(r['actor'], 30)} | "
                    f"{_cell(r['prompt'], 40)} | {'-' if r['latency_s'] is None else str(r['latency_s']) + ' s'} | "
                    f"{_cell('; '.join(r['flags']), 80) or '-'} | {_cell(r['reason'])} |" for r in record]
    summary += engine_decisions(events, mask)
    grants = getattr(st.history, "grants", None) or []
    if grants:
        summary += ["", "## Delegation grants (a human let an agent act for them; each act is in the human record)"]
        summary += [f"- {g.id}: {g.grantee} may {', '.join(g.actions)} for {g.grantor}, {g.at[:10]} to {g.expires}"
                    + (f"; revoked {g.revoked_at[:10]} by {g.revoked_by}" if g.revoked_at else "")
                    + f". Reason: {_cell(mask(g.reason), 200)}" for g in grants]
    if st.history.change_requests:
        summary += ["", "## Change requests to approved documents"]
        summary += [f"- {c.id} on {c.doc}.md: rejected; filed by {c.requested_by}; the human record gives who declined it and why. "
                    f"Request: {_cell(mask(c.reason), 200)}" if c.status == "rejected" else
                    f"- {c.id} on {c.doc}.md: deferred until: {_cell(mask(c.revisit), 120)}; filed by {c.requested_by}. "
                    f"Request: {_cell(mask(c.reason), 200)}" if c.status == "deferred" else
                    f"- {c.id} on {c.doc}.md: {c.status}; filed by {c.requested_by}, approved by {c.approved_by or '-'}: {_cell(mask(c.reason), 200)}"
                    for c in st.history.change_requests]
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    (ctx.workdir / "release-summary.md").write_text("\n".join(summary) + "\n")
    write_json_atomic(ctx.workdir / "verification.json", {"version": v, "ok": not problems, "problems": problems,
                                                          "ledger_events": n, "auto_approvals": auto, "delegated_decisions": delegated,
                                                          "pending_human": pending_human, "auto_approved_human_dimensions": auto_human,
                                                          "warnings": warnings, "human_record": record})
    return NodeResult(outputs={"verification": "verification.json", "release_summary": "release-summary.md"},
                      data={"ok": not problems, "problems": problems})


def check_final(ctx) -> CheckResult:
    p = ctx.artifact_path("verification")
    data = read_json(p) if p else {"ok": False, "problems": ["no verification report"]}
    return CheckResult(bool(data["ok"]), data.get("problems", []))
