"""`python -m vpg <command>` — the interface agents and humans use to drive a production.

Run from 02_Workflows/video-production (or add it to PYTHONPATH).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from .controls import CONTROL_DOCS, KNOWN_DOCS, doc_label, rel_path
from .util import PKG_ROOT, ConcurrentChange, NotRegularFile, read_json

INHERITED_SAFETY_KEYS = ("privacy", "reviews", "preflight", "loop_budget", "command_deps", "humans", "autonomy", "regression")


def _engine(args, **kw):
    from .engine import Engine
    return Engine(args.project, auto_approve=getattr(args, "auto_approve_dry_run", False), **kw)


def _read_only(args):
    """Inspect the last saved state: never resumes, saves, ledgers or waits for the project lock."""
    from .engine import Engine
    return Engine(args.project, read_only=True)


def _known_node(eng, node_id: str) -> str:
    from .engine import EngineError
    if node_id not in eng.index.ids:
        raise EngineError(f"unknown node '{node_id}'; `vpg status` lists the node ids")
    return node_id


# Commands that change a production: each runs under the project's exclusive lock (vpg/lock.py).
MUTATING = {"run", "submit", "approve", "feedback", "change-request", "unblock", "invalidate", "rollback", "accept-config",
            "restore-control", "accept-ledger-break", "resolve", "preflight", "publish", "grant", "fork", "review", "calibrate",
            "direct-run"}


def _human_gate_lines(eng, nid: str) -> list[str]:
    """What a human gate needs: the decision, the outputs under review, and the exact commands to record it."""
    node, proj = eng.node(nid), Path(eng.project.root)
    here = proj.relative_to(Path.cwd()) if proj.is_relative_to(Path.cwd()) else proj
    outs = sorted(a.path for a in eng.state.artifacts.values() if a.node == nid and a.status == "current")
    ok, no = node.human_review.outcomes[0], node.human_review.outcomes[1] if len(node.human_review.outcomes) > 1 else None
    locks = [rel_path(d) for d in node.controls.lock_on_approval]
    lines = [f"{nid} — decision: {node.human_review.decision} (approver: {node.human_review.approver})",
             f"    review: {', '.join(outs + [rel_path(d) for d in node.controls.write if rel_path(d) not in outs]) or 'see the node catalog for what this gate decides'}",
             *_since_last_approval(eng, nid),
             f"    approving locks: {', '.join(locks) or 'nothing'} (later edits then need `vpg change-request`)",
             f"    approve: python -m vpg approve {here} {nid} --actor <human> --reason \"<their words>\"   (records '{ok}')"]
    if no:
        lines.append(f"    reject:  python -m vpg approve {here} {nid} --actor <human> --reject --reason \"<their words>\" "
                     f"--feedback \"<what to change>\"   (records '{no}')")
    from .tools.sources import BRAND_KINDS, PRODUCT_KINDS, waivers_in_effect
    for doc, kinds, label in (("product", PRODUCT_KINDS, "product evidence"), ("design", BRAND_KINDS, "brand and design authority")):
        if doc in node.controls.write:   # the approver sees what grounds the truth they are approving
            srcs = [s for s in eng.state.inputs.sources if s.kind in kinds]
            lines.append(f"    {label}: " + ("; ".join(f"{s.id} {s.path} ({s.kind}, {s.evidence})" for s in srcs) or "none registered"))
    latest = next(iter(reversed(eng.current("gaps"))), None)
    report = read_json(eng.project.root / latest.path) if latest and (eng.project.root / latest.path).exists() else None
    for w in waivers_in_effect(eng.state.inputs.waivers, report):   # the film proceeds without this source; every approver sees it
        lines.append(f"    note: intake gap {w.gap} was waived by {w.by} ({w.at[:10]}): {w.reason}")
    return lines


def _since_last_approval(eng, nid: str) -> list[str]:
    """#43: the outputs a gate's human last decided on, against the ones waiting now, so a re-opened gate says whether
    anything they would judge actually changed."""
    last = next((a for a in reversed(eng.state.history.approvals) if a.node == nid), None)
    if last is None:
        return []
    if not last.outputs:
        return [f"    last decided {last.at[:16]} by {last.actor} ({last.decision}); that record predates output hashes (v1.7)"]
    rt = eng.rt(nid)
    now_out = {k: sha[:16] for k, (_, sha) in eng._attempt_outputs(nid, rt.attempts).items()}
    changed = sorted(k for k in set(now_out) | set(last.outputs) if now_out.get(k) != last.outputs.get(k))
    return [f"    since {last.actor}'s {last.decision} ({last.at[:16]}): "
            + (f"changed: {', '.join(changed)}" if changed else "every output is byte-identical to what they decided on")]


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def _relay(args) -> dict[str, str]:
    """The relay flags. A flag given but blank (e.g. an unset shell variable) is an error, never a silent "no relay"."""
    from .engine import EngineError
    out = {}
    for key, flag in (("on_behalf_of", "--on-behalf-of"), ("instruction", "--instruction"), ("instruction_at", "--instruction-at")):
        value = getattr(args, key, None)
        if value is not None and not value.strip():
            raise EngineError(f"{flag} was given but is blank: leave it out, or pass the value")
        out[key] = value or ""
    return out


def cmd_init(args) -> int:
    root = Path(args.project)
    if root.exists() and any(root.iterdir()) and not args.force:
        print(f"{root} is not empty; use --force to add missing files only")
        return 1
    (root / "controls").mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    for doc in CONTROL_DOCS:
        dest = root / "controls" / f"{doc}.md"
        if not dest.exists():
            shutil.copy2(PKG_ROOT / "templates" / "control" / f"{doc}.md", dest)
    cfg = root / "vpg.project.yaml"
    quality = getattr(args, "workflow", "default") == "quality"
    if not cfg.exists():
        cfg.write_text(yaml.safe_dump({
            "id": args.id or root.name, "name": args.name or root.name, "workflow": "quality" if quality else "default",
            "executor": "task", "brief": "sources/brief.md", "constraints": [], "commands": {}, "command_timeout_s": 900,
            **({"observability": {"evaluation_events": True}} if quality else {})}, sort_keys=False))
    elif quality and (yaml.safe_load(cfg.read_text()) or {}).get("workflow") != "quality":
        print(f"note: {cfg} exists and does not say `workflow: quality`; the spec family is added, the config is unchanged")
    spec_files = []
    if quality:
        from .spec import FILES, SPEC_DIR, TEMPLATES
        (root / SPEC_DIR).mkdir(exist_ok=True)
        for name in FILES.values():
            if not (root / SPEC_DIR / name).exists():
                shutil.copy2(TEMPLATES / name, root / SPEC_DIR / name)
                spec_files.append(name)
    copied, not_copied = [], []
    if args.from_project:
        src_root = Path(args.from_project)
        src_cfg = src_root / "vpg.project.yaml"
        try:
            source = yaml.safe_load(src_cfg.read_text()) or {}
        except yaml.YAMLError as exc:
            raise SystemExit(f"vpg init --from: cannot parse {src_cfg} ({exc.__class__.__name__})") from None
        except OSError as exc:
            raise SystemExit(f"vpg init --from: cannot read {src_cfg} ({exc.__class__.__name__})") from None
        if not isinstance(source, dict):
            raise SystemExit(f"vpg init --from: {src_cfg} must be a mapping")
        target = yaml.safe_load(cfg.read_text()) or {}
        if not isinstance(target, dict):
            raise SystemExit(f"vpg init: {cfg} must be a mapping")
        for key in INHERITED_SAFETY_KEYS:
            if key not in source:
                not_copied.append(f"{key} (not in source)")
            elif key in target:
                not_copied.append(f"{key} (already set)")
            else:
                value = source[key]
                if key == "privacy" and isinstance(value, dict) and isinstance(value.get("names"), str) and value["names"] != "none":
                    name_path = Path(value["names"])
                    if not name_path.is_absolute():
                        value = {**value, "names": str((src_root / name_path).resolve())}
                if key == "regression" and isinstance(value, dict):
                    value = json.loads(json.dumps(value))
                    value.pop("departures", None)
                    baseline = value.get("baseline")
                    if isinstance(baseline, dict):
                        for pkey in ("inventory", "master", "layout_audit"):
                            raw = baseline.get(pkey)
                            if isinstance(raw, str) and raw.strip() and not Path(raw).is_absolute():
                                baseline[pkey] = str((src_root / raw).resolve())
                target[key] = value
                copied.append(key)
        cfg.write_text(yaml.safe_dump(target, sort_keys=False))
    if not (root / "sources" / "brief.md").exists():
        (root / "sources" / "brief.md").write_text("# Brief\n\n<fill: the requester's brief, verbatim>\n")
    print(f"initialised {root}: controls/ (6 templates), sources/brief.md, vpg.project.yaml"
          + (f", spec/ ({len(spec_files)} templates)" if quality else ""))
    if quality:
        print("quality workflow: N03 completes spec/GATES.yaml and pins a benchmark registry (`vpg benchmark init|add|measure`, "
              "with style frames per role); see docs/quality-workflow.md")
    if args.from_project:
        print("safety keys copied:", ", ".join(copied) or "none")
        print("safety keys not copied:", ", ".join(not_copied) or "none")
    print("next: fill sources/brief.md; add product evidence (a spec, prototype capture or recording) and brand authority to "
          f"sources/; then `python -m vpg run {root}`")
    return 0


def cmd_validate(args) -> int:
    from .graph import load_workflow, resolve_workflow, validate
    wf = load_workflow(resolve_workflow(args.workflow))
    errs = validate(wf)
    print(f"{wf.id} v{wf.version}: {len(wf.nodes)} nodes, {len(wf.edges)} edges, {len(wf.subgraphs)} subgraphs — "
          + ("valid" if not errs else f"{len(errs)} errors"))
    for e in errs:
        print("  -", e)
    return 1 if errs else 0


def cmd_run(args) -> int:
    from .executors import ScriptedExecutor
    ex = ScriptedExecutor(Path(args.project) / args.fixtures) if args.fixtures else None
    eng = _engine(args, executor=ex)
    st = eng.run(max_steps=args.max_steps)
    _summary(st)
    return 0 if st["status"] in ("RUN_PASS", "WAITING_RUNTIME", "WAITING_HUMAN", "RUNNING") else 2


def _escalation_text(e: dict, nodes: dict | None) -> str:
    """A run-level escalation (integrity, configuration) keeps its report whole: its reason is that report cut at 240
    characters, which can drop the advice (#147). A node's escalation prints its reason; its details are in its file."""
    run_level = nodes is not None and e["node"] not in nodes
    return e["report"] if run_level and e.get("report") else e["reason"]


def _summary(st) -> None:
    cycles = f"{st['rework_cycles']} of {st['max_rework_cycles']}" if st.get("max_rework_cycles") else st["rework_cycles"]
    print(f"run {st['run']}: {st['status']} · version {st['version']} · rework cycles {cycles}")
    for nid, n in st["nodes"].items():
        print(f"  N{n['num']:02d} {nid:32s} {n['status']:16s} attempts={n['attempts']} memo={n['memo_hits']} outcome={n['outcome']}")
    if st["waiting_runtime"]:
        print("open task packets:", *(f"\n  {k}: {v}" for k, v in st["waiting_runtime"].items()))
    if st["waiting_human"]:
        print("waiting for a human decision:", ", ".join(st["waiting_human"]), "(`vpg tasks` shows how to record it)")
    if st.get("loops"):
        print("loop budgets:", ", ".join(f"{l['edge']} {l['used']}/{l['max']}" + (" (at its bound)" if l["used"] >= l["max"] else "")
                                          for l in st["loops"]))
    budget = st.get("budget")
    if budget:
        granted = budget["granted"]
        extra = [f"{e} +{n}" for e, n in sorted(granted["loops"].items())] + ([f"{granted['rework_cycles']} rework cycle(s)"]
                                                                            if granted["rework_cycles"] else [])
        loops = f"rework loops {budget['rework_loop_traversals']} traversals" if budget["rework_loop_traversals"] else "each loop's own bound"
        print(f"loop budget: {budget['profile']} ({loops}, {budget['rework_cycles']} rework cycles; when spent: "
              + ("extend while converging" if budget["on_exhaust"] == "extend_if_converging" else "block") + ")"
              + (f"; granted so far: {', '.join(extra)}" if extra else ""))
    if st.get("trend"):
        from .budget import trend_lines
        print("quality trend (blocking P1–P3 per review round):", *(f"\n  {line}" for line in trend_lines(st["trend"])))
        if st.get("convergence"):
            c = st["convergence"]
            print(f"  latest round: {'converging' if c['converging'] else 'not converging'}: {c['why']}")
    for r in st.get("pending_reverts") or []:
        if r.get("unavailable"):
            print(f"unblocking {r['node']} may revert a plan an older engine applied; no preview: {r['unavailable']}")
            continue
        undo = ([f"rework cycles {r['cycle']} → {r['cycle'] - 1}"] if r.get("cycle") is not None else []) \
            + [f"{edge} {n + 1} → {n}" for edge, n in r["loops"].items()] + [f"{d} → {s}" for d, s in r["defects"].items()]
        print(f"unblocking {r['node']} reverts the plan an older engine applied although blocked: {'; '.join(undo) or 'nothing left'}")
    if st["escalations"]:
        print("BLOCKED:", *(f"\n  {e['node']}: {_escalation_text(e, st.get('nodes'))}" for e in st["escalations"]))
    if st["open_defects"]:
        print("open defects:", *(f"\n  {d}" for d in st["open_defects"]))
    if st.get("holds"):
        print("on hold for a named human:", *(f"\n  {h}" for h in st["holds"]))
    if st.get("triage"):
        print("agents' notes awaiting a human's triage (`vpg feedback --triage F --accept|--dismiss`):",
              *(f"\n  {t}" for t in st["triage"]))
    if st.get("deferred_change_requests"):
        print("deferred change requests:", *(f"\n  {c}" for c in st["deferred_change_requests"]))
    if st.get("grants"):
        print("live delegation grants (`vpg grant --list`):", *(f"\n  {g}" for g in st["grants"]))
    if st.get("warnings"):
        print("warnings:", *(f"\n  {w}" for w in st["warnings"]))
    for note in st.get("notes") or []:
        print("note:", note)
    if st.get("lock"):
        print("in progress:", st["lock"])


def cmd_status(args) -> int:
    from .lock import describe, holder, project_lock_path
    st = _read_only(args).status()
    info = holder(project_lock_path(args.project))
    st["lock"] = f"{describe(info)} is changing this run; this is the last saved state" if info else None
    _print(st) if args.json else _summary(st)
    return 0


def cmd_tasks(args) -> int:
    eng = _read_only(args)
    for nid, rt in eng.state.runtime.nodes.items():
        if rt.task_packet and rt.status.value == "WAITING_RUNTIME":
            token = rt.preflight["token"] if rt.preflight else rt.dispatch_token   # a held attempt waits on its pre-flight token
            print(f"{nid}: {rt.task_packet}  (token {token}{', pre-flight: `vpg preflight`' if rt.preflight else ''})")
        elif rt.status.value == "WAITING_HUMAN":
            print(*_human_gate_lines(eng, nid), sep="\n")
    return 0


def cmd_submit(args) -> int:
    outputs: dict = {}
    if args.outputs:   # #27: every output in one file, including per-item ones ({"key": {"item": "path"}}) and lists
        data = read_json(args.outputs)
        if not isinstance(data, dict):
            raise SystemExit(f"vpg submit: --outputs {args.outputs} must hold a JSON object mapping output keys to paths")
        outputs.update(data)
    for kv in args.output or []:
        k, _, v = kv.partition("=")
        if k in outputs:
            raise SystemExit(f"vpg submit: output '{k}' is given twice (--output and --outputs); give each key once")
        outputs[k] = v
    eng = _engine(args)
    review = None
    if args.review:   # like --output: a relative path is found in the packet's work directory first
        path = Path(args.review)
        rt = eng.state.runtime.nodes.get(args.node)
        in_workdir = eng.project.work / args.node / f"a{rt.attempts}" / path if rt and not path.is_absolute() else None
        review = read_json(in_workdir if in_workdir is not None and in_workdir.is_file() else path)
    st = eng.submit(_known_node(eng, args.node), outputs, token=args.token, actor=args.actor, review=review, outcome=args.outcome)
    _summary(st)
    rt, node = eng.rt(args.node), eng.node(args.node)
    if rt.status.value in ("ITERATE", "BLOCKED") and rt.failures:
        left = node.retry.max_attempts - rt.fail_streak
        print(f"attempt {rt.attempts} of {args.node} failed its checks:", *(f"\n  - {f}" for f in rt.failures))
        print(f"next: `python -m vpg run {args.project}` opens attempt {rt.attempts + 1} ({left} left before escalation)"
              if rt.status.value == "ITERATE" else "the node escalated; see .vpg/escalations/")
    return 0


def cmd_fork(args) -> int:
    if args.action != "record":
        raise SystemExit("vpg fork: action must be record")
    st = _engine(args).record_fork(args.from_file, actor=args.actor)
    _summary(st)
    return 0


def cmd_review(args) -> int:
    if args.action != "record":
        raise SystemExit("vpg review: action must be record")
    st = _engine(args).record_review(args.from_file, actor=args.actor, **_relay(args))
    _summary(st)
    return 0


def cmd_calibrate(args) -> int:
    from . import scoring
    if getattr(args, "reviewers", False):
        data = _read_only(args)._preference_store(args.principal)
        print(json.dumps({"reviewer_calibration": scoring.reviewer_calibration(data),
                          "skipped_lines": scoring.skipped_lines(data)}, indent=1))
        return 0
    data = _engine(args)._preference_store(args.principal)
    model = scoring.fit_store(data, principal=args.principal)
    metrics = model.get("metrics", {})
    print(json.dumps({
        "principal": model.get("principal"),
        "version": model.get("version"),
        "n": metrics.get("n"),
        "top1": metrics.get("top1"),
        "pairwise": metrics.get("pairwise"),
        "brier": metrics.get("brier"),
        "ece_raw": metrics.get("ece"),
        "ece_calibrated": metrics.get("ece_calibrated"),
        "calibrated": scoring.is_calibrated(model),
        "reviewer_calibration": scoring.reviewer_calibration(data),
        "skipped_lines": scoring.skipped_lines(data),
    }, indent=1))
    return 0


def cmd_approve(args) -> int:
    eng = _engine(args)
    node = eng.node(_known_node(eng, args.node))
    decision = args.decision or (node.human_review.outcomes[1] if args.reject else node.human_review.outcomes[0])
    rejection_feedback = "\n".join(args.feedback or [])
    st = eng.decide(args.node, decision, actor=args.actor, reason=args.reason or "", feedback=args.feedback or [],
                    provisional=args.provisional, confirm_provisional=args.confirm_provisional or "",
                    reject_provisional=args.reject_provisional or "", rejection_feedback=rejection_feedback,
                    ack_warnings=args.ack_warning or [], signoffs=args.signoff or [], **_relay(args))
    _summary(st)
    return 0


def cmd_feedback(args) -> int:
    from .engine import EngineError
    if args.triage:
        if args.accept == args.dismiss:
            raise EngineError("--triage needs exactly one of --accept or --dismiss")
        eng = _engine(args)
        fb = eng.triage_feedback(args.triage, accept=args.accept, actor=args.actor, reason=args.reason or "", **_relay(args))
        gate = eng._gate_node()
        escalated = any(e.node == gate and not e.resolved for e in eng.state.runtime.escalations)
        print(f"{fb.id} {fb.triage} by {args.actor}"
              + ("; it counts as feedback now, and N21 interprets it once the gate re-runs" if args.accept else
                 f"; closed: {fb.no_action_reason}")
              + (f". The gate has an open escalation: `python -m vpg unblock {args.project} {gate} --actor <you>` re-runs it"
                 if escalated else ""))
        return 0
    if not args.text:
        raise EngineError("--text is required (or --triage F --accept|--dismiss for an agent's note)")
    eng = _engine(args)
    fb = eng.add_feedback(args.text, actor=args.actor, at=args.at or "", severity=args.severity or "", **_relay(args))
    if fb.triage == "pending":
        print(f"recorded {fb.id} on version {fb.version} as an agent's note: it re-opens nothing and holds the version until a "
              f"human (or an agent granted feedback-triage) runs `python -m vpg feedback {args.project} --triage {fb.id} "
              "--accept|--dismiss --reason … --actor …`")
    else:
        print(f"recorded {fb.id} on version {fb.version}; N21 will interpret it on the next run")
    return 0


def _impact_lines(impact: dict) -> list[str]:
    what = impact["cr"] or f"a change to {doc_label(impact['doc'])}"
    return [f"impact of {what} ({doc_label(impact['doc'])}), if approved:",
            f"  writer re-runs: {impact['writer'] or 'none (no node writes it)'}",
            f"  readers re-check when it changes: {', '.join(impact['readers']) or 'none'}",
            f"  human decisions that would re-open: {', '.join(impact['human_gates']) or 'none'}",
            f"  commands that would re-run (if their inputs change): {', '.join(impact['commands']) or 'none'}",
            f"  {len(impact['recheck'])} node(s) re-check in all"]


def cmd_grant(args) -> int:
    from .engine import EngineError
    if args.list:
        from .util import now
        eng = _read_only(args)
        today = now()[:10]
        for g in eng.state.history.grants:
            state = (f"revoked {g.revoked_at[:10]} by {g.revoked_by}" if g.revoked_at else
                     "expired" if g.expires < today else f"live until {g.expires}")
            print(f"{g.id} {g.grantee} ← {g.grantor} ({g.at[:10]}): {', '.join(g.actions)}; {state}. Reason: {g.reason}")
        if not eng.state.history.grants:
            print("no grants")
        return 0
    if not (args.actor or "").strip():
        raise EngineError("--actor is required: the named human granting or revoking")
    eng = _engine(args)
    if args.revoke:
        g = eng.revoke_grant(args.revoke, actor=args.actor, reason=args.reason or "", **_relay(args))
        print(f"{g.id} revoked by {g.revoked_by}; {g.grantee} can no longer act for {g.grantor}")
        return 0
    g = eng.grant(args.to or "", args.allow or [], args.expires or "", args.reason or "", actor=args.actor, **_relay(args))
    print(f"{g.id}: {g.grantee} may {', '.join(g.actions)} for {g.grantor} until {g.expires} (inclusive). Each act is ledgered "
          f"with the grant; revoke it with `python -m vpg grant {args.project} --revoke {g.id} --actor {g.grantor} --reason …`")
    return 0


def cmd_change_request(args) -> int:
    from .engine import EngineError
    if sum(bool(x) for x in (args.approve, args.reject, args.defer, args.impact)) > 1:
        raise EngineError("--approve, --reject, --defer and --impact are exclusive: one change request at a time")
    if args.impact:   # read-only (#80)
        print(*_impact_lines(_read_only(args).change_request_impact(args.impact)), sep="\n")
        return 0
    if not (args.actor or "").strip():
        raise EngineError("--actor is required: who files or decides the change request")
    eng = _engine(args)
    if args.defer:
        cr = eng.defer_change_request(args.defer, by=args.actor, until=args.until or "", reason=args.reason, **_relay(args))
        print(f"{cr.id} deferred by {args.actor} until: {cr.revisit}; {doc_label(cr.doc)} stays as approved and nothing re-runs")
        return 0
    if args.approve:
        cr = eng.approve_change_request(args.approve, by=args.actor, reason=args.reason, **_relay(args))
        writer = eng.control_writer(cr.doc)
        if writer:
            print(f"{cr.id} approved by {cr.approved_by}; {writer} (the writer of {doc_label(cr.doc)}) is invalidated and will re-run. "
                  f"{eng.edit_window_advice(cr.doc)}. An edit made before the task is dispatched is outside the workflow and "
                  "blocks the run")
        else:
            print(f"{cr.id} approved by {cr.approved_by}; no node writes {doc_label(cr.doc)}, so nothing re-runs")
    elif args.reject:
        cr = eng.reject_change_request(args.reject, by=args.actor, reason=args.reason, **_relay(args))
        print(f"{cr.id} rejected by {args.actor}; {doc_label(cr.doc)} stays as approved and nothing re-runs")
    else:
        cr = eng.change_request(args.doc, args.reason, by=args.actor)
        print(f"opened {cr.id} on {doc_label(args.doc)}; approve it with --approve {cr.id} --actor <human>")
        print(*_impact_lines(eng.change_request_impact(cr.id)), sep="\n")
    return 0


def cmd_preflight(args) -> int:
    from .engine import EngineError
    notes = ""
    if args.notes:
        path = Path(args.notes)
        if not path.is_file():   # #169: the notes are a file, not inline text
            pathlike = "/" in args.notes or args.notes.endswith((".md", ".txt"))
            shown = args.notes if len(args.notes) <= 60 else ("…" + args.notes[-57:] if pathlike else args.notes[:57] + "…")
            where = "" if path.is_absolute() else " (relative to the current directory)"
            raise EngineError(f"--notes expects the path of a file with the reviewer's notes, and there is no file at "
                              f"{shown!r}{where}; if that is the notes themselves, write them to a file (for example "
                              "notes.md) and pass its path")
        notes = path.read_text()
    st = _engine(args).preflight(args.node, args.token, args.verdict, actor=args.actor, notes=notes)
    _summary(st)
    return 0


def cmd_unblock(args) -> int:
    from .engine import EngineError
    eng = _engine(args)
    run_level = {e.node for e in eng.state.runtime.escalations if not e.resolved and e.node not in eng.index.ids}
    if args.node not in eng.index.ids and args.node not in run_level:
        raise EngineError(f"nothing to unblock called '{args.node}'; open escalations: "
                          f"{sorted({e.node for e in eng.state.runtime.escalations if not e.resolved}) or 'none'}")
    _summary(eng.unblock(args.node, actor=args.actor, note=args.note or "", reset_budget=args.reset_budget, waive=args.waive or [],
                         all_loops=args.reset_loops, **_relay(args)))
    return 0


def cmd_resolve(args) -> int:
    import shlex
    eng = _engine(args)
    d = eng.resolve_defect(args.defect, actor=args.actor, evidence=args.evidence, **_relay(args))
    print(f"{d.id} resolved by {args.actor}: {d.dimension} → {d.traced_node} ({d.description[:80]})")
    gate_node = eng._gate_node()
    if any(e.node == gate_node and not e.resolved for e in eng.state.runtime.escalations):
        print(f"next: python -m vpg unblock {shlex.quote(str(args.project))} {gate_node} --actor {shlex.quote(args.actor)} "
              "--note '…' (the gate re-evaluates without it)")
    return 0


def cmd_invalidate(args) -> int:
    eng = _engine(args)
    changed = eng.invalidate_nodes([_known_node(eng, args.node)], reason=args.reason, actor=args.actor, **_relay(args))
    print("invalidated:", ", ".join(changed))
    return 0


def cmd_restore_control(args) -> int:
    eng = _engine(args)
    eng.restore_control(args.doc, actor=args.actor, **_relay(args))
    import shlex
    kept = next(e["data"]["kept"] for e in reversed(list(eng.ledger.events("control_restored"))) if e["data"]["doc"] == args.doc)
    print(f"{rel_path(args.doc)} restored to the accepted version; what was there is kept at: {kept}")
    if any(e.node == "integrity" and not e.resolved for e in eng.state.runtime.escalations):
        print(f"next: python -m vpg unblock {shlex.quote(str(args.project))} integrity --actor {shlex.quote(args.actor)}")
    return 0


def cmd_accept_ledger_break(args) -> int:
    from .engine import Engine
    eng = Engine(args.project, accept_ledger_break={"actor": args.actor, "reason": args.reason, **_relay(args)})
    brk = eng.state.runtime.ledger_breaks[-1]
    print(f"ledger break accepted by {brk['actor']}: events after {brk['lost_after_seq']} were lost; the ledger continues "
          f"at event {brk['resumes_at_seq']}, and the release summary lists the break")
    return 0


def cmd_accept_config(args) -> int:
    eng = _engine(args)
    _summary(eng.accept_config(actor=args.actor, reason=args.reason, adopt_code=args.adopt_code, **_relay(args)))
    return 0


def cmd_rollback(args) -> int:
    _summary(_engine(args).rollback(args.checkpoint, actor=args.actor, **_relay(args)))
    return 0


def cmd_lineage(args) -> int:
    _print(_read_only(args).lineage(args.artifact))
    return 0


def cmd_ledger_verify(args) -> int:
    from .provenance import Ledger
    ledger = Ledger(Path(args.project) / ".vpg" / "ledger.jsonl", read_only=True)
    ok, n, msg = ledger.verify()
    state = Path(args.project) / ".vpg" / "state.json"
    if ok and state.exists():   # the chain is whole; does it still reach the last event the state recorded?
        rt = read_json(state).get("runtime", {})
        lost = ledger.reaches(rt.get("ledger_tail"))
        if lost and ledger.pending_fill(rt.get("ledger_tail"), rt.get("pending_ledger") or []):
            n_pending = len(rt["pending_ledger"])
            msg = f"ok; {n_pending} event(s) are pending in the state and will be written by the next command that changes the run"
        elif lost:
            ok, msg = False, lost
        for brk in rt.get("ledger_breaks", []):
            msg += f"; break accepted by {brk['actor']} ({brk['at'][:10]}): events after {brk['lost_after_seq']} were lost"
    print(f"{n} events: {msg}")
    return 0 if ok else 1


def cmd_docs(args) -> int:
    from .docs import generate
    drift = generate(check=args.check)
    if args.check:
        print("docs up to date" if not drift else "docs drift: " + ", ".join(drift))
        return 1 if drift else 0
    print("regenerated:", ", ".join(drift) or "nothing changed")
    return 0


def cmd_memory(args) -> int:
    from .memory import DEFAULT_RULES, GovernanceError, RuleBase, apply_proposal, summarize
    path = Path(args.rules) if args.rules else DEFAULT_RULES
    try:
        if args.action == "status":
            _print(summarize(path))
        elif args.action == "promote":
            rule = RuleBase(path).transition(args.rule, args.to, by=args.by, evidence=args.evidence or [], human=args.human,
                                             reason=args.reason or "", adopted_in=args.adopted_in or "")
            print(f"{rule['id']} → {rule['lifecycle']['state']}")
        elif args.action == "apply":
            _print(apply_proposal(read_json(args.proposal), approved_by=args.approved_by, rules_path=path))
    except GovernanceError as exc:
        print(f"refused: {exc}")
        return 3
    return 0


def cmd_dry_run(args) -> int:
    from .dryrun import dry_run
    report = dry_run(out=args.out, keep=args.keep)
    print(report["summary"])
    return 0 if report["ok"] else 1


def cmd_publish(args) -> int:
    from .publish import publish
    eng = _read_only(args) if args.check else _engine(args)
    result = publish(eng, version=args.version, check=args.check)
    if args.check:
        print("publish check:", json.dumps(result, sort_keys=True))
    else:
        print("publish:", json.dumps(result, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vpg", description="Graph-based video production workflow")
    sub = p.add_subparsers(dest="cmd", required=True)

    def proj(sp):
        sp.add_argument("project", help="project directory")
        sp.add_argument("--lock-timeout", type=float, default=900.0,
                        help="seconds to wait while another vpg process is changing the project (default 900)")

    def relay(sp):
        sp.add_argument("--on-behalf-of", dest="on_behalf_of", help="declared human principal for a relayed agent act")
        sp.add_argument("--instruction", help="verbatim human instruction for a relayed agent act")
        sp.add_argument("--instruction-at", dest="instruction_at",
                        help="when the human gave the instruction: ISO 8601 with timezone, e.g. 2026-10-15T17:00:00-05:00")

    sp = sub.add_parser("init", help="create a project from the control-document templates"); proj(sp)
    sp.add_argument("--name"); sp.add_argument("--id"); sp.add_argument("--force", action="store_true")
    sp.add_argument("--from", dest="from_project", help="copy safety-critical top-level config keys from an existing project")
    sp.add_argument("--workflow", choices=["default", "quality"], default="default",
                    help="quality: the look and animatic gates, with the spec family templates in spec/ (docs/quality-workflow.md)")
    sp.set_defaults(fn=cmd_init)
    sp = sub.add_parser("validate", help="validate the workflow graph")
    sp.add_argument("--workflow", help="default (the shipped graph), quality (with look and animatic gates), or a path")
    sp.set_defaults(fn=cmd_validate)
    sp = sub.add_parser("run", help="run until the graph waits for an agent or a human, blocks, or finishes"); proj(sp)
    sp.add_argument("--max-steps", type=int, default=1000); sp.add_argument("--fixtures", help="replay agent outputs from fixtures (dry runs)")
    sp.add_argument("--auto-approve-dry-run", action="store_true",
                    help="label human gates as dry-run approvals; refused unless the project sets dry_run: true")
    sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("status", help="show node states, open tasks, waits and escalations"); proj(sp); sp.add_argument("--json", action="store_true"); sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("tasks", help="list open task packets"); proj(sp); sp.set_defaults(fn=cmd_tasks)
    sp = sub.add_parser("submit", help="submit an agent's outputs for an open task"); proj(sp); sp.add_argument("node")
    sp.add_argument("--token", required=True, help="the dispatch token printed in the task packet")
    sp.add_argument("--actor", required=True, help="who did the work, e.g. agent:narrative-script")
    sp.add_argument("--output", action="append", help="key=path; relative paths are resolved against the packet's work directory")
    sp.add_argument("--outputs", help="a JSON file mapping output keys to paths (a path, a list, or {item: path} for per-item outputs)")
    sp.add_argument("--review", help="review.json for review nodes")
    sp.add_argument("--outcome", default="pass", help="the node's outcome when it has several (default: pass)"); sp.set_defaults(fn=cmd_submit)
    sp = sub.add_parser("fork", help="record a scored fork artifact"); proj(sp); sp.add_argument("action", choices=["record"])
    sp.add_argument("--from", dest="from_file", required=True, help="fork.json to validate and record")
    sp.add_argument("--actor", default="agent:production-orchestrator", help="agent recording the fork")
    sp.set_defaults(fn=cmd_fork)
    sp = sub.add_parser("review", help="record the principal's single end-review answer"); proj(sp)
    sp.add_argument("action", choices=["record"]); sp.add_argument("--from", dest="from_file", required=True, help="decision.json")
    sp.add_argument("--actor", required=True, help="declared human, or agent:<id> with relay fields while release waits")
    relay(sp); sp.set_defaults(fn=cmd_review)
    sp = sub.add_parser("calibrate", help="refit the principal's scored-fork preference model"); proj(sp)
    sp.add_argument("--principal", default=None, help="principal preference store to refit (default: project/autonomy principal)")
    sp.add_argument("--reviewers", action="store_true", help="report reviewer calibration from verdicts.jsonl without fitting")
    sp.set_defaults(fn=cmd_calibrate)
    sp = sub.add_parser("approve", help="record a human decision at a human gate"); proj(sp); sp.add_argument("node")
    sp.add_argument("--actor", required=True, help="the named human deciding (agents and dry-run actors are refused)")
    sp.add_argument("--reject", action="store_true", help="record the gate's second outcome (e.g. rejected)")
    sp.add_argument("--decision", help="an explicit outcome from the gate's list, instead of approve or --reject")
    sp.add_argument("--reason", required=True, help="the human's own words for the decision (required)")
    sp.add_argument("--feedback", action="append", help="feedback to act on after a rejection, or after --reject-provisional (repeatable)")
    sp.add_argument("--provisional", action="store_true", help="record an autonomy-mode provisional approval at a pre-production gate")
    sp.add_argument("--confirm-provisional", metavar="all|NODE,...", help="at release, confirm standing provisional approvals")
    sp.add_argument("--reject-provisional", metavar="NODE", help="at release, reject one standing provisional approval and reopen it")
    sp.add_argument("--ack-warning", action="append", metavar="N|all", help="acknowledge release-summary warning number(s)")
    sp.add_argument("--signoff", action="append", metavar="DIM,...", help="sign off pending human-judged dimension(s)")
    relay(sp); sp.set_defaults(fn=cmd_approve)
    sp = sub.add_parser("feedback", help="record feedback on the current version, or triage an agent's note"); proj(sp)
    sp.add_argument("--text", help="the feedback (a named human's re-opens the gate; an agent's note waits for triage)")
    sp.add_argument("--actor", required=True); sp.add_argument("--at"); sp.add_argument("--severity")
    sp.add_argument("--triage", metavar="F", help="rule on an agent's note: --accept or --dismiss, with --reason")
    sp.add_argument("--accept", action="store_true"); sp.add_argument("--dismiss", action="store_true")
    sp.add_argument("--reason", help="why the note is accepted or dismissed (required with --triage)"); relay(sp); sp.set_defaults(fn=cmd_feedback)
    sp = sub.add_parser("grant", help="a named human delegates human-only actions to one agent until a date, or revokes it"); proj(sp)
    sp.add_argument("--actor", help="the named human granting or revoking"); sp.add_argument("--to", metavar="agent:ID")
    sp.add_argument("--allow", action="append", metavar="ACTION", help="repeatable: accept-config, unblock, invalidate, "
                    "change-request, resolve, restore-control, feedback-triage, approve:<node> (not at a node with human-judged dimensions)")
    sp.add_argument("--expires", metavar="YYYY-MM-DD", help="the last day the grant holds (at most 90 days out)")
    sp.add_argument("--reason", help="the human's reason, e.g. their instruction quoted")
    sp.add_argument("--revoke", metavar="G-NNN"); sp.add_argument("--list", action="store_true", help="read-only: every grant and its state")
    relay(sp); sp.set_defaults(fn=cmd_grant)
    sp = sub.add_parser("change-request", help="open, approve, reject, defer or preview a change request on a locked control document"); proj(sp)
    sp.add_argument("--doc"); sp.add_argument("--reason", default="", help="why it is filed, approved, rejected or deferred (required to reject or defer)")
    sp.add_argument("--actor", help="who files or decides it (required except with --impact)"); sp.add_argument("--approve", metavar="CR")
    sp.add_argument("--reject", metavar="CR", help="decline an open change request; nothing is invalidated (needs --reason)")
    sp.add_argument("--defer", metavar="CR", help="park an open change request until a trigger (needs --until and --reason)")
    sp.add_argument("--until", help="what brings a deferred change request back, e.g. \"after the v1 release\"")
    sp.add_argument("--impact", metavar="CR", help="read-only: what approving it would re-run and re-open")
    relay(sp); sp.set_defaults(fn=cmd_change_request)
    sp = sub.add_parser("preflight", help="an independent reviewer passes a held attempt, or returns it with notes"); proj(sp)
    sp.add_argument("node"); sp.add_argument("--token", required=True, help="the pre-flight token from the packet")
    sp.add_argument("--verdict", required=True, choices=["pass", "revise"])
    sp.add_argument("--actor", required=True, help="the reviewer: agent:<id> or a named human, never the attempt's producer")
    sp.add_argument("--notes", help="a file with the reviewer's notes (required to revise)")
    sp.set_defaults(fn=cmd_preflight)
    sp = sub.add_parser("unblock", help="a human clears an escalation (a node id, or a run-level name such as no-progress)"); proj(sp)
    sp.add_argument("node"); sp.add_argument("--actor", required=True, help="the named human"); sp.add_argument("--note")
    sp.add_argument("--reset-budget", action="store_true", help="also restart the rework-cycle and repeated-defect counters (ledgered)")
    sp.add_argument("--reset-loops", action="store_true",
                    help="also give every loop of this node a fresh traversal budget, not only the loops that escalated (ledgered)")
    sp.add_argument("--waive", action="append", metavar="GAP",
                    help="source validation only: proceed without resolving this resolvable intake gap (repeatable; --note gives "
                         "the reason). Works whether or not N02 is blocked, until the inputs freeze")
    relay(sp); sp.set_defaults(fn=cmd_unblock)
    sp = sub.add_parser("resolve", help="a human closes a defect no review can clear, with the evidence it is fixed or does not apply")
    proj(sp); sp.add_argument("defect", help="defect id, e.g. D018"); sp.add_argument("--actor", required=True, help="the named human")
    sp.add_argument("--evidence", required=True, help="what shows the defect is fixed or does not apply (recorded in the ledger)")
    relay(sp); sp.set_defaults(fn=cmd_resolve)
    sp = sub.add_parser("invalidate", help="force a node and its dependents to re-check"); proj(sp); sp.add_argument("node")
    sp.add_argument("--reason", required=True)
    sp.add_argument("--actor", required=True, help="who asks: an agent id, or a named human (or a granted agent) when a human's "
                    "decision or the frozen inputs would re-open (recorded in the ledger)"); relay(sp); sp.set_defaults(fn=cmd_invalidate)
    sp = sub.add_parser("accept-ledger-break", help="a human accepts that ledger events were lost (no backup exists)")
    proj(sp); sp.add_argument("--actor", required=True, help="the named human"); sp.add_argument("--reason", required=True)
    relay(sp); sp.set_defaults(fn=cmd_accept_ledger_break)
    sp = sub.add_parser("restore-control", help="a human puts a control document back to the version the workflow accepted")
    proj(sp); sp.add_argument("--doc", required=True, choices=list(KNOWN_DOCS)); sp.add_argument("--actor", required=True, help="the named human")
    relay(sp); sp.set_defaults(fn=cmd_restore_control)
    sp = sub.add_parser("accept-config", help="a human accepts a mid-run change to the workflow, rules, rubrics, thresholds or project config")
    proj(sp); sp.add_argument("--actor", required=True, help="the named human"); sp.add_argument("--reason", required=True)
    sp.add_argument("--adopt-code", action="append", default=[], metavar="NODE",
                    help="keep NODE's last pass, which predates code fingerprints: you vouch its current code made it (repeatable)")
    relay(sp); sp.set_defaults(fn=cmd_accept_config)
    sp = sub.add_parser("rollback", help="restore state from a checkpoint"); proj(sp); sp.add_argument("--checkpoint", type=int, required=True); sp.add_argument("--actor", required=True); relay(sp); sp.set_defaults(fn=cmd_rollback)
    sp = sub.add_parser("lineage", help="walk an artifact's inputs"); proj(sp); sp.add_argument("artifact"); sp.set_defaults(fn=cmd_lineage)
    sp = sub.add_parser("ledger-verify", help="verify the hash-chained ledger"); proj(sp); sp.set_defaults(fn=cmd_ledger_verify)
    sp = sub.add_parser("docs", help="regenerate the catalogs, schemas and graph visualisation"); sp.add_argument("--check", action="store_true"); sp.set_defaults(fn=cmd_docs)
    sp = sub.add_parser("memory", help="inspect or govern the cross-project rule base"); sp.add_argument("action", choices=["status", "promote", "apply"])
    sp.add_argument("--rules"); sp.add_argument("--rule"); sp.add_argument("--to"); sp.add_argument("--by"); sp.add_argument("--human", action="store_true")
    sp.add_argument("--evidence", action="append"); sp.add_argument("--reason"); sp.add_argument("--adopted-in"); sp.add_argument("--proposal")
    sp.add_argument("--approved-by"); sp.set_defaults(fn=cmd_memory)
    sp = sub.add_parser("dry-run", help="run the sample production end to end (scripted agents, real local media)")
    sp.add_argument("--out", help="where to run (default: a temp dir)"); sp.add_argument("--keep", action="store_true"); sp.set_defaults(fn=cmd_dry_run)
    sp = sub.add_parser("publish", help="copy published versions for human review"); proj(sp)
    sp.add_argument("--version", type=int, help="publish or check one version instead of all unpublished versions")
    sp.add_argument("--check", action="store_true", help="report unpublished or out-of-sync versions without writing")
    sp.set_defaults(fn=cmd_publish)
    from .quality_cli import register as register_quality
    register_quality(sub)
    args = p.parse_args(argv)
    from .controls import IntegrityViolation
    from .engine import EngineError
    from .lock import Locked, project_lock, rules_lock
    from .memory import DEFAULT_RULES, GovernanceError
    from .provenance import LedgerBehind, LedgerCorrupt
    try:
        if (args.cmd in MUTATING and not ((args.cmd == "publish" and args.check)   # a check or a preview only reads
                                          or (args.cmd == "change-request" and args.impact)
                                          or (args.cmd == "grant" and args.list)
                                          or (args.cmd == "calibrate" and getattr(args, "reviewers", False)))
                or (args.cmd == "telemetry" and args.write)):   # refreshing .vpg/telemetry/ writes into the run's folder
            with project_lock(args.project, args.cmd, args.lock_timeout):
                return args.fn(args)
        if args.cmd == "memory" and args.action != "status":
            with rules_lock(args.rules or DEFAULT_RULES, f"memory {args.action}"):
                return args.fn(args)
        return args.fn(args)
    except (EngineError, IntegrityViolation, GovernanceError, Locked, NotRegularFile, ConcurrentChange, LedgerBehind,
            LedgerCorrupt) as exc:
        print(f"vpg {args.cmd}: {exc}", file=sys.stderr)
        for note in getattr(exc, "__notes__", []):
            print(f"vpg {args.cmd}: {note}", file=sys.stderr)
        return 4
    except OSError as exc:   # a project file could not be read or written; a human action then recorded nothing
        what = f"{exc.filename}: {exc.strerror}" if exc.filename and exc.strerror else str(exc)
        print(f"vpg {args.cmd}: could not read or write {what}", file=sys.stderr)
        for note in getattr(exc, "__notes__", []):
            print(f"vpg {args.cmd}: {note}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
