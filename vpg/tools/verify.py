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

import hashlib
import html
import json
import math
import random
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from ..context import CheckResult, NodeResult
from ..controls import doc_label, rel_path
from ..provenance import Ledger
from ..util import PKG_ROOT, is_human_name, read_json, rel, sha256_file, write_json_atomic
from .privacy import (PrivacyConfigError, load_names, masker, privacy_feature_enabled, release_problem, text_for_privacy_scan,
                      text_privacy_hits)

FAST_S = 60                  # a human answer faster than this is flagged, never refused
REVIEW_BATCH_S = 10          # three reviews submitted within this window are flagged
_DELEGATION = re.compile(r"delegat|standing instruction|on behalf of", re.I)
_REDACTION_SETTING = re.compile(r"names?_count$|_ratio$|_threshold$")   # #170: sizes and settings, not drawn redactions
_TAKES_REASON = {"decision", "config_accepted", "unblocked", "ledger_break_accepted", "gap_waived", "defect_resolved",
                 "change_request_rejected", "change request deferred", "grant issued", "grant revoked"}
_FLAG_TEXT = {"fast": "answered in {latency} s", "no_reason": "no reason recorded", "shared": "reason shared with other actions",
              "delegation": "cites delegation", "agent_filed": "filed by an agent", "no_actor": "no named actor",
              "released": "released a hold", "held": "held: it led to no open blocking defect",
              "adopted": "kept a pass on the actor's word (--adopt-code)",
              "granted": "an agent acting under {grant}, not the human",
              "relayed": "relayed by an agent for {principal}, not the human's own act",
              "undeclared": "actor is not one of the declared humans",
              "provisional": "provisional approval for release confirmation"}


def _seconds(start: str | None, end: str | None) -> int | None:
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def human_record(events: Iterable[dict], feedback: Iterable[Any] = (), mask: Callable[[str], str] = lambda t: t,
                 dry_run_actor: str = "dry-run-auto", declared_humans: Iterable[str] = ()) -> list[dict]:
    """Every ledger action recorded under a human name, with the prompt it answered, its latency, reason and flags.

    Latency pairs each answer with its own prompt: a decision with its gate opening, a change-request approval or rejection
    with its filing, an unblock with its node's escalation, an accepted configuration or a restored control document with the
    integrity escalation. Feedback is listed by id only, and listed names in free text are masked."""
    feedback_actor = {getattr(f, "id", None): getattr(f, "actor", "") for f in feedback}
    declared = {str(h).strip() for h in declared_humans}
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
        if t in ("fork_recorded", "build_verdict"):
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
        if t == "decision" and (d.get("human", True) or d.get("grant") or d.get("relayed") or d.get("provisional")):
            node = _text(d.get("node"))
            row = {"action": "decision", "subject": f"{node} → {_text(d.get('decision'))}", "actor": _text(d.get("actor")),
                   "since": awaiting.get(node), "prompt": "gate opened", "reason": _text(d.get("reason"))}
            if d.get("provisional"):
                row["provisional"] = True
        elif t == "human_choice":
            row = {"action": "human choice", "subject": _text(d.get("fork_id")), "actor": _text(d.get("actor")),
                   "since": None, "prompt": "end review", "reason": _text(d.get("why"))}
        elif t in ("provisional_confirmed", "provisional_rejected"):
            row = {"action": t.replace("_", " "), "subject": _text(d.get("node")), "actor": _text(d.get("actor")),
                   "since": None, "prompt": "release review", "reason": _text(d.get("feedback"))}
        elif t == "feedback_pending_confirmation":
            row = {"action": "feedback pending confirmation", "subject": _text(d.get("id")), "actor": _text(d.get("actor")),
                   "since": None, "prompt": "end review", "reason": _text(d.get("reason"))}
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
                   "actor": _text(d.get("actor") or (d.get("grantor") if t == "grant_issued" else d.get("by"))),
                   "since": None, "prompt": "",
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
            row = {"action": t, "subject": "configuration change" if t == "config_accepted" else rel_path(_text(d.get('doc'))),
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
            if (actor and (not actor.lower().startswith("agent") or d.get("grant"))) or d.get("relayed"):
                targets = d.get("targets") if isinstance(d.get("targets"), list) else []
                row = {"action": "invalidate", "subject": ", ".join(map(_text, targets)), "actor": actor or _text(d.get("actor")), "since": None,
                       "prompt": "", "reason": reason}
        granted = _text(d.get("grant")) if row is not None else ""
        relayed = bool(d.get("relayed")) if row is not None else False
        if row is None or (row["actor"] != "cli" and not is_human_name(row["actor"], dry_run_actor) and not granted and not relayed):
            continue
        if granted:   # stamped at append by the engine (Ledger.stamp): an agent's act on a human's delegation
            row["grant"] = f"grant {granted} from {_text(d.get('grantor')) or 'unknown'}"
        if relayed:
            row["relayed"] = True
            row["principal"] = _text(d.get("principal"))
            row["instruction"] = _text(d.get("instruction"))
            row["instruction_at"] = _text(d.get("instruction_at"))
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
        if _DELEGATION.search(row["reason"]) and not row.get("relayed"):
            codes.append("delegation")
        if row["action"] == "change_request_approved" and row.get("filer", "").lower().startswith("agent"):
            codes.append("agent_filed")
        if row.pop("adopted", None):
            codes.append("adopted")
        if row.pop("released", None):
            codes.append("released")
        if row.pop("provisional", None):
            codes.append("provisional")
        if row["action"] == "feedback" and row["subject"] in held:
            codes.append("held")
        if row.get("grant"):
            codes.append("granted")
        if row.get("relayed"):
            codes.append("relayed")
        if declared and is_human_name(row["actor"], dry_run_actor) and row["actor"] not in declared:
            codes.append("undeclared")
        row["codes"] = codes
        row["flags"] = [_FLAG_TEXT[c].format(latency=row["latency_s"], grant=row.get("grant", ""),
                                             principal=row.get("principal", "")) for c in codes]
    return rows


def _cell(text: Any, limit: int = 100) -> str:
    s = " ".join(_text(text).split()).replace("|", "/")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def release_warnings(ctx, wf, events: list[dict]) -> list[str]:
    """Checks that don't block a release but that the approver must look at personally. They print ids and counts
    only, never the content of a declared field."""
    st = ctx.state
    warnings = []
    if hasattr(ctx, "config") and "humans" not in (ctx.config or {}):
        warnings.append("humans is not declared, so relayed human-only acts cannot be distinguished from free-text human actors")
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
            # #170: a number declares redactions unless its key names a list size or a setting (the privacy scan list's
            # `redaction_name_count`, a `*_ratio`); `veil_frames: 120` or `redactions: 3` still says some were drawn
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and _REDACTION_SETTING.search(key.lower()) and not re.search(r"redacted|veiled", key.lower())):
                continue
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
    current_version = getattr(st.runtime, "current_version", None)
    blocking_severities = set(getattr(getattr(wf, "policies", None), "blocking_severities", []))
    open_defects = {d.id: d for d in getattr(getattr(st, "evaluation", None), "defects", [])
                    if getattr(d, "status", "") in ("open", "persisting", "in_rework")}
    for item in getattr(st.runtime, "end_review_items", []):
        if item.get("version") != current_version:
            continue
        severities = item.get("severities") if isinstance(item.get("severities"), dict) else {}
        serious = {d: s for d, s in severities.items() if d in open_defects and s in blocking_severities}
        if serious:
            warnings.append(f"end-review item {item.get('id')} holds {', '.join(f'{d} {s}' for d, s in serious.items())}; "
                            "acknowledge before release")
    for f in getattr(getattr(st, "evaluation", None), "feedback", []):
        if getattr(f, "hold", ""):
            warnings.append(f"feedback hold {f.id}: {f.hold}; acknowledge before release")
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


def _release_node(wf) -> str | None:
    gate = next((n for n in wf.nodes if n.gate == "review_gate"), None)
    if gate is None:
        return None
    children = {e.target for e in wf.edges if e.source == gate.id and e.kind.value == "flow" and e.when == "pass"}
    return next((n.id for n in wf.nodes if n.id in children and n.human_review.required), None)


def _blind_candidates(fork: dict, mask: Callable[[str], str]) -> list[dict]:
    rng = random.Random(hashlib.sha256(str(fork.get("fork_id", "")).encode()).hexdigest())
    candidates = [{"id": c.get("id"), "description": c.get("description") or {"logline": c.get("label", ""), "structure": []}}
                  for c in (fork.get("candidates") or []) if c.get("passes_rubric", True)]
    rng.shuffle(candidates)
    for i, c in enumerate(candidates, 1):
        c["neutral_label"] = f"Option {i}"
        d = c["description"] if isinstance(c["description"], dict) else {}
        c["text"] = mask(f"{d.get('logline', '')}; structure: {', '.join(map(str, d.get('structure') or []))}".strip("; "))
    return candidates


def _review_packet(ctx, forks: list[dict], verification: dict, warnings: list[str], pending_human: list[str],
                   standing_provisional: list[Any], end_review_items: list[dict], masters: list[Any],
                   mask: Callable[[str], str]) -> tuple[str, dict]:
    film_paths = [m.path for m in masters]
    departures = _declared_departures(ctx, mask)
    lines = ["# End review packet", "", "## 1. The film (paths)",
             *([f"- {p}" for p in sorted(set(film_paths))] or ["- no film artifact registered"]), "",
             "## 1b. Declared baseline departures",
             *departures,
             "",
             "## 1c. Regression harness",
             *_harness_lines(ctx, mask),
             "",
             "## 2. Blind fork candidates"]
    decision = {"forks": {}, "release": "approve|changes", "ack_warnings": "all|[…]", "signoffs": [],
                "confirm_provisional": "all|[…]", "reject_provisional": {}, "confirm_items": [], "reject_items": [],
                "feedback": "", "item_map": {}}
    for fork in forks:
        fid = str(fork.get("fork_id"))
        blind_candidates = _blind_candidates(fork, mask)
        decision["forks"][fid] = {"sha256": fork.get("sha256"),
                                  "option_map": {c["neutral_label"]: c["id"] for c in blind_candidates},
                                  "ranking": [], "ratings": {}, "why": ""}
        lines += ["", f"### {fid} ({fork.get('fork_type')})"]
        for c in blind_candidates:
            lines.append(f"- {c['neutral_label']}: {c['text'] or 'description not provided'}")
    principal = _principal(ctx)
    model = _current_model(principal)
    lines += ["", "## 4. Standing provisional approvals to confirm",
              *([f"- {a.node}: {a.decision} by {a.actor}" for a in standing_provisional] or ["- none"]),
              "", "## 5. End-review items",
              *([f"- {i.get('id')}: {i.get('node')} defects {', '.join(i.get('defects') or []) or 'none'}" for i in end_review_items]
                or ["- none"]),
              "", "## 6. Human-judged dimensions to sign",
              *([f"- {d}" for d in pending_human] or ["- none"]),
              "", "## 7. Warnings to acknowledge",
              *([f"- W{i}: {mask(w)}" for i, w in enumerate(warnings, 1)] or ["- none"]),
              "", "## 8. How to answer",
              "Relay the principal's words in chat with `vpg review record <project> --from decision.json`, or fill in "
              "`decision.template.json` as `decision.json` and record that file.",
              "", "## 9. Reveal: agent scores, rationale and prediction",
              f"- Current model: v{model.get('version')} metrics {json.dumps(model.get('metrics') or {}, sort_keys=True)}"]
    if model.get("warnings"):
        lines.append(f"- Model warning: {'; '.join(map(str, model.get('warnings') or []))}")
    for fork in forks:
        scored = fork.get("scored") or {}
        lines += ["", f"### {fork.get('fork_id')} — model v{scored.get('model_version')} · band {scored.get('band')} · "
                  f"confidence {scored.get('confidence')}"]
        lines.append(f"- Predicted probabilities: {json.dumps(scored.get('predicted') or {}, sort_keys=True)}")
        if scored.get("agent_pick") or scored.get("model_top"):
            lines.append(f"- Agent pick: {scored.get('agent_pick') or '-'}; model top: {scored.get('model_top') or '-'}")
        lines.append(f"- Built candidate ids: {', '.join(scored.get('build') or []) or 'none'}")
        for c in fork.get("candidates") or []:
            lines.append(mask(f"- `{c.get('id')}` {c.get('label', '')}: scores {json.dumps(c.get('scores') or {}, sort_keys=True)}; "
                              f"rationale: {c.get('rationale', '')}"))
    return "\n".join(lines) + "\n", decision


def _time_label(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    if seconds < 0 or not math.isfinite(seconds):
        return ""
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _watch_lines(ctx, st, version: int, masters: list[Any], end_review_items: list[dict], mask: Callable[[str], str]) -> list[str]:
    film = sorted({m.path for m in masters})[:1]
    out = [f"- Film: {film[0]}" if film else "- Film: no film artifact registered"]
    candidates: list[str] = []
    for fb in st.evaluation.feedback:
        if fb.version == version:
            label = _time_label(fb.at)
            text = mask(fb.text)
            status = {"resolved_pending_confirmation": "resolved, pending principal confirmation",
                      "confirmed": "confirmed", "reopened": "reopened"}.get(fb.status, fb.status)
            candidates.append(f"{label + ' — ' if label else ''}{text} ({status})")
    for item in end_review_items:
        for did in item.get("defects") or []:
            d = next((x for x in st.evaluation.defects if x.id == did), None)
            if d is not None:
                candidates.append(mask(f"{d.id} — {d.description}"))
    for rec in candidates[:6]:
        out.append(f"- {rec}")
    return out


def _decision_digest(ctx, forks: list[dict], template: dict, verification: dict, warnings: list[str], pending_human: list[str],
                     standing_provisional: list[Any], end_review_items: list[dict], masters: list[Any],
                     mask: Callable[[str], str]) -> str:
    st, version = ctx.state, verification["version"]
    item_map = template.setdefault("item_map", {})
    departures = _declared_departures(ctx, mask)
    lines = [f"# Decision digest — {st.project.name}, version {version}", "", "## 0. TL;DR"]
    feedback = [f for f in st.evaluation.feedback if f.version <= version]
    changed = "; ".join(mask(f"{f.text} → {('pending confirmation' if f.status == 'resolved_pending_confirmation' else f.status)}")
                        for f in feedback[:2]) or "release checks and review packet prepared"
    defect_by_id = {d.id: d for d in st.evaluation.defects}
    eri_defects = [defect_by_id[d] for item in end_review_items for d in (item.get("defects") or []) if d in defect_by_id]
    blocking_eri = [d for d in eri_defects if d.status in ("open", "persisting", "in_rework") and d.severity in ("P1", "P2")]
    recommendation = "iterate" if blocking_eri else ("release" if verification.get("ok") else "iterate")
    if blocking_eri:
        why = "end-review item holds " + ", ".join(f"{d.id} {d.severity}" for d in blocking_eri)
    elif eri_defects:
        why = "release would accept " + ", ".join(f"{d.id} {d.severity}" for d in eri_defects)
    else:
        why = "all recomputed release checks pass" if verification.get("ok") else "; ".join(verification.get("problems") or [])
    lines += [f"- v{version}: {changed}", f"- Recommendation: {recommendation} — {mask(why)}", "", "## 1. Watch",
              *_watch_lines(ctx, st, version, masters, end_review_items, mask), "", "## 2. Decide (human-only)"]
    dno = 1
    if pending_human:
        item_map[f"D{dno}"] = {"section": "decide", "target": "signoffs", "options": pending_human}
        lines.append(f"- D{dno}: Sign off listening dimensions ({', '.join(pending_human)}). Answer: ok, or name changes.")
        dno += 1
    for fid, answer in sorted(template.get("forks", {}).items()):
        labels = list((answer.get("option_map") or {}).keys())
        item_map[f"D{dno}"] = {"section": "decide", "target": f"forks.{fid}.ranking", "options": labels}
        lines.append(f"- D{dno}: Rank {fid} blind options ({', '.join(labels) or 'none'}). Answer: best > next > last, with why.")
        dno += 1
    item_map[f"D{dno}"] = {"section": "decide", "target": "release", "options": ["approve", "changes"]}
    lines.append(f"- D{dno}: Release v{version}? Answer: release or iterate.")
    lines += ["", "## 2b. Declared baseline departures", *departures,
              "", "## 2c. Regression harness", *_harness_lines(ctx, mask),
              "", "## 3. Confirm or reject (the agent's provisional calls)"]
    pno = 1
    for a in standing_provisional:
        pid = f"P{pno}"
        item_map[pid] = {"section": "confirm", "target": f"confirm_provisional.{a.node}", "default": "confirm"}
        lines.append(f"- {pid}: Confirm {a.node} → {a.decision} by {a.actor}; evidence approval:{a.node}. Default: confirm.")
        pno += 1
    for item in end_review_items:
        pid = f"P{pno}"
        sev = ", ".join(f"{d.id} {d.severity}" for d in (defect_by_id[x] for x in item.get("defects") or [] if x in defect_by_id))
        entry = {"section": "confirm", "target": f"end_review_items.{item.get('id')}"}
        if not any((defect_by_id[x].severity in ("P1", "P2") and defect_by_id[x].status in ("open", "persisting", "in_rework"))
                   for x in item.get("defects") or [] if x in defect_by_id):
            entry["default"] = "confirm"
        item_map[pid] = entry
        lines.append(f"- {pid}: Decide {item.get('id')} from {item.get('node')}; evidence {item.get('gate_result')}"
                     + (f"; defects {sev}" if sev else "") + ".")
        pno += 1
    for fb in st.evaluation.feedback:
        if fb.status == "resolved_pending_confirmation":
            pid = f"P{pno}"
            item_map[pid] = {"section": "confirm", "target": f"feedback.{fb.id}", "default": "confirm"}
            evidence = ", ".join(fb.defect_ids) or mask(fb.no_action_reason)
            lines.append(f"- {pid}: Confirm {fb.id} was resolved by the agent; evidence {evidence}. Default: confirm.")
            pno += 1
    for rec in verification.get("human_record") or []:
        if rec.get("relayed"):
            pid = f"P{pno}"
            act_id = hashlib.sha256(json.dumps({k: rec.get(k) for k in ("action", "subject", "actor", "at")},
                                               sort_keys=True).encode()).hexdigest()[:12]
            item_map[pid] = {"section": "confirm", "target": f"relayed.{act_id}", "default": "confirm"}
            lines.append(f"- {pid}: Confirm relayed {mask(str(rec.get('action', 'act')))} {mask(str(rec.get('subject', '')))} "
                         f"for {mask(str(rec.get('principal', '')))}. Default: confirm.")
            pno += 1
    if pno == 1:
        lines.append("- none")
    lines += ["", "## 4. Acknowledge (known non-blocking issues and warnings)"]
    if warnings:
        for i, w in enumerate(warnings, 1):
            kid = f"K{i}"
            item_map[kid] = {"section": "ack", "target": f"ack_warnings.{i}", "severity": "warning", "default": "ack"}
            lines.append(f"- {kid}: warning — {mask(w)}. Default: ack.")
    else:
        lines.append("- none")
    lines += ["", "## 5. Your feedback → status"]
    if feedback:
        for i, fb in enumerate(feedback, 1):
            fid = f"F{i}"
            status = {"resolved_pending_confirmation": "resolved, pending principal confirmation",
                      "confirmed": "confirmed", "reopened": "reopened"}.get(fb.status, fb.status)
            item_map[fid] = {"section": "feedback", "target": f"feedback.{fb.id}", "status": status}
            lines.append(f"- {fid}: {mask(fb.text)} → {status}" + (f" ({', '.join(fb.defect_ids)})" if fb.defect_ids else ""))
    else:
        lines.append("- none")
    lines += ["", "## 6. How to answer",
              "- Example: `D1 ok · D2 Option 2 > Option 1 (why...) · confirm all · ack all · release`"]
    return "\n".join(lines) + "\n"


def _current_model(principal: str) -> dict:
    from .. import scoring
    return scoring.load_current(scoring.preference_store(PKG_ROOT, principal))


def _review_html(packet_lines: list[str], decision: dict) -> str:
    blind, reveal = [], []
    target = blind
    for line in packet_lines:
        if "Reveal: agent scores" in line:
            target = reveal
        target.append(line if target is reveal else html.escape(line))
    template = html.escape(json.dumps(decision, indent=2))
    reveal_json = json.dumps("\n".join(reveal)).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return f"""<!doctype html>
<meta charset=\"utf-8\">
<title>End review</title>
<style>body{{font:16px/1.45 system-ui;margin:2rem;max-width:72rem}}pre{{white-space:pre-wrap;background:#f6f6f6;padding:1rem}}button{{padding:.6rem 1rem}}</style>
<h1>End review</h1>
<section><h2>Blind review first</h2><pre>{chr(10).join(blind)}</pre></section>
<button onclick=\"const r=document.getElementById('reveal-section');r.hidden=false;r.querySelector('pre').textContent=JSON.parse(document.getElementById('reveal').textContent)\">Reveal scores</button>
<script id=\"reveal\" type=\"application/json\">{reveal_json}</script>
<section id=\"reveal-section\" hidden><h2>Reveal</h2><pre></pre></section>
<section><h2>decision.json template</h2><textarea id=\"decision\" style=\"width:100%;height:22rem\">{template}</textarea>
<p><button onclick=\"const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([document.getElementById('decision').value],{{type:'application/json'}}));a.download='decision.json';a.click()\">Export decision.json</button></p></section>
"""


def _declared_departures(ctx, mask: Callable[[str], str] = lambda s: s) -> list[str]:
    block = ctx.config.get("regression") if isinstance(ctx.config, dict) else None
    path = (block or {}).get("departures") if isinstance(block, dict) else None
    if not path:
        return ["- none"]
    p = Path(path)
    p = p if p.is_absolute() else ctx.project_dir / p
    try:
        data = read_json(p)
    except Exception as exc:
        return [f"- departures file unreadable: {type(exc).__name__}: {mask(str(exc))}"]
    rows = []
    for d in data.get("removals") or []:
        cites = ", ".join(map(str, d.get("cites") or [])) or "NO CITATIONS"
        rows.append(f"- {mask(str(d.get('chapter') or '*'))}: /{mask(str(d.get('match') or ''))}/ — "
                    f"{mask(str(d.get('why') or ''))} (cites: {cites})")
    return rows or ["- none"]


def _governed_warnings(events: list[dict], st, version: int, rubrics: dict[str, Any], rule_states: dict[str, str],
                       principal: str, mask: Callable[[str], str] = lambda s: s) -> list[str]:
    """#162: the reviewers' P1–P3 findings that the rubric's levels demoted to nits or dropped, in each review node's
    current judgment of this version, one warning per dimension. A carried-forward review is judged by the review it
    copies, and a re-run on the same version replaces its earlier attempt. Whether the dimension should block is the
    principal's call at the end review: the warning gives the steps the rule's current state allows, or says it's a
    rubric change. `rubrics` are the package's own (a dropped dimension isn't in the effective ones)."""
    by_node: dict[str, str] = {}
    for r in st.evaluation.reviews:
        if r.version <= version and not r.carried_forward:
            by_node[r.node] = r.id
    judging = set(by_node.values())
    rows = [e["data"] for e in events if isinstance(e, dict) and e.get("type") == "finding_governed"
            and isinstance(e.get("data"), dict) and e["data"].get("review") in judging]
    attempt = lambda d: d.get("attempt") if isinstance(d.get("attempt"), int) else 0   # noqa: E731
    passed = {str(e["data"].get("node")): attempt(e["data"]) for e in events   # a review node's latest judgment
              if isinstance(e, dict) and e.get("type") == "node_passed" and isinstance(e.get("data"), dict)}
    last_attempt: dict[str, int] = {}
    for d in rows:
        last_attempt[str(d.get("review"))] = max(last_attempt.get(str(d.get("review")), 0), attempt(d))
    closed = {x.id for x in st.evaluation.defects if x.status in ("resolved", "waived")}
    by_dim: dict[str, list[dict]] = {}
    for d in rows:
        current = passed.get(str(d.get("node")), last_attempt[str(d.get("review"))])
        if attempt(d) == current and d.get("defect") not in closed:
            by_dim.setdefault(str(d.get("dimension")), []).append(d)
    out = []
    for dim, items in sorted(by_dim.items()):
        last = items[-1]
        found = ", ".join(dict.fromkeys(f"{_text(d.get('defect')) or 'not recorded'} (filed {_text(d.get('filed'))} by "
                                        f"{_text(d.get('review'))})" for d in items))
        what = "dropped" if last.get("to") == "dropped" else "counted as nits"
        out.append(mask(f"{dim} {what} reviewer-filed blocking findings ({_text(last.get('reason'))}): {found}; "
                        + _block_steps(dim, _text(last.get("rule")), rule_states, rubrics, principal, _text(last.get("review")))))
    return out


def _block_steps(dim: str, rule: str, rule_states: dict[str, str], rubrics: dict[str, Any], principal: str, review: str) -> str:
    if not rule:
        return f"making {dim} block is a rubric change"
    state = rule_states.get(rule, "not in the rule base")
    rubric = next((rid for rid, rub in rubrics.items() if any(x.get("id") == dim for x in rub.get("dimensions", []))), "<rubric>")
    cmd, by = f"python -m vpg memory promote --rule {rule}", shlex.quote(principal)
    adopt = f"`{cmd} --to adopted --by {by} --human --evidence {review} --adopted-in rubrics/{rubric}.yaml`"
    accept = "; then accept the configuration change (`vpg accept-config`), so the reviews judge this version again"
    if state == "validated":
        return (f"to make {dim} block, promote rule {rule} to approved, then adopted: `{cmd} --to approved --by {by} "
                f"--human --evidence {review}`, then {adopt}{accept}")
    if state == "approved":
        return f"to make {dim} block, promote rule {rule} to adopted: {adopt}{accept}"
    if state in ("adopted", "monitored"):
        return f"rule {rule} is already {state}{accept}"
    if state in ("retired", "not in the rule base"):
        return f"rule {rule} is {state}: making {dim} block needs a new rule or a rubric change"
    return (f"rule {rule} is {state}: it needs evidence and validation before it can be approved and adopted "
            "(SOP, Rule lifecycle)")


def _principal(ctx) -> str:
    autonomy = ctx.config.get("autonomy") if isinstance(ctx.config, dict) else None
    return (autonomy.get("principal") if isinstance(autonomy, dict) else None) or "Wei"


def _harness_lines(ctx, mask: Callable[[str], str] = lambda s: s) -> list[str]:
    out = []
    for key in ("regression_report", "layout_report", "voice_seams", "accessibility_report"):
        recs = ctx.current(key) if hasattr(ctx, "current") else []
        if not recs:
            if key != "accessibility_report":
                out.append(f"- {key}: not measured")
            continue
        try:
            data = read_json(ctx.project_dir / recs[-1].path)
        except Exception as exc:
            out.append(f"- {key}: unreadable ({type(exc).__name__})")
            continue
        if key == "voice_seams":
            flagged = [u for u in data.get("units", []) if u.get("flags")]
            if flagged:
                out.append(f"- voice_seams: {len(flagged)} flagged unit(s)")
                out += [f"  - {mask(str(u.get('unit')))} at {float(u.get('end') or 0):.2f}s: {', '.join(map(str, u.get('flags') or []))}"
                        for u in flagged[:12]]
            else:
                out.append("- voice_seams: no flagged units")
            continue
        if key == "accessibility_report":
            cap = data.get("caption_script") if isinstance(data.get("caption_script"), dict) else {}
            missing, extra = cap.get("missing") or [], cap.get("extra") or []
            if missing or extra:
                out.append("- CAP-SCRIPT: caption/script word mismatch")
                if missing:
                    out.append("  - missing: " + ", ".join(mask(str(x)) for x in missing[:12]))
                if extra:
                    out.append("  - extra: " + ", ".join(mask(str(x)) for x in extra[:12]))
            elif cap.get("caption_script_measured") is True:   # "match" only when it was measured, never by default
                out.append("- CAP-SCRIPT: captions match the script")
            else:
                out.append("- CAP-SCRIPT: not measured")
            continue
        findings = data.get("findings") or []
        if findings:
            out.append(f"- {key}: {len(findings)} finding(s)")
            out += [f"  - {mask(str(f.get('dimension', f.get('check', 'finding'))))}: {mask(str(f.get('message', '')))}"
                    for f in findings[:8]]
        else:
            out.append(f"- {key}: no findings")
        if key == "regression_report":
            for cid, nums in sorted((data.get("seam_chrome_ignored") or {}).items()):
                out.append(f"  - seam chrome ignored in {mask(str(cid))}: {', '.join(mask(str(n)) for n in nums)} "
                           "(the previous chapter's number at the seam)")
            for d in data.get("declared_departures") or []:
                absorbed = d.get("absorbed") or []
                if absorbed:
                    out.append(f"  - departure /{mask(str(d.get('match')))}/ absorbed {len(absorbed)} text(s): "
                               + ", ".join(mask(str(x)) for x in absorbed[:8]))
    return out or ["- none"]


def final(ctx) -> NodeResult:
    st = ctx.state
    v = st.runtime.current_version
    problems = []
    gates = [g for g in st.evaluation.gate_results if g.version == v]
    if not gates or gates[-1].outcome != "pass":
        problems.append(f"no passing gate for version {v}")
    from ..graph import WORKFLOWS, load_workflow
    wf_ref = ctx.config.get("workflow")
    wf = load_workflow(WORKFLOWS[wf_ref] if wf_ref in WORKFLOWS else (ctx.project_dir / wf_ref if wf_ref else None))
    policies_blocking = set(wf.policies.blocking_severities)
    end_review_defects = {d for item in getattr(st.runtime, "end_review_items", [])
                          if item.get("version") == v for d in item.get("defects", [])}
    open_blocking = [d.id for d in st.evaluation.defects if d.status in ("open", "persisting")
                     and d.severity in policies_blocking and d.id not in end_review_defects]
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
    auto = [a["node"] for a in approvals if not a["human"] and not a.get("grant") and not a.get("relayed") and not a.get("provisional")]
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
    declared_humans = (ctx.config or {}).get("humans") if isinstance((ctx.config or {}).get("humans"), list) else []
    record = human_record(events, st.evaluation.feedback, mask, wf.policies.dry_run_actor, declared_humans)
    warnings = release_warnings(ctx, wf, events)
    warnings = [w for w in warnings if not str(w).startswith("end-review item ")]
    warnings += [mask(_text((e.get("data") or {}).get("warning"))) for e in events if e.get("type") == "privacy_text_warning"]
    from ..evaluation import load_rubrics
    from ..memory import RuleBase
    try:
        rule_states = {r["id"]: r["lifecycle"]["state"] for r in RuleBase(Path(st.knowledge.rules_path)).rules}
    except Exception:   # an unreadable rule base gives the generic step instead of a wrong one
        rule_states = {}
    warnings += _governed_warnings(events, st, v, load_rubrics(PKG_ROOT), rule_states, _principal(ctx), mask)
    if listed:
        text_scan_on = privacy_feature_enabled(ctx.config, "text_scan")
        spec_docs = [ctx.control_path(d) for d in getattr(ctx.controls, "docs", []) if d.startswith("spec-")]
        for doc in sorted((ctx.project_dir / "controls").glob("*.md")) + spec_docs:
            try:
                full, part = text_privacy_hits(text_for_privacy_scan(doc, json_mode=doc.suffix == ".json"), listed)
            except Exception:
                continue
            hits = full or (part if text_scan_on else [])
            if hits:
                name = doc.name if doc.parent.name == "controls" else rel(doc, ctx.project_dir)
                warnings.append(f"control doc {name} contains privacy-name text: "
                                + ", ".join(f"[list entry #{i}]" for i in hits))
    if skipped:
        warnings.append(f"privacy list entries #{', #'.join(map(str, skipped))} are too short to check (a single word needs 4+ letters)")
    flagged = [r for r in record if r["codes"]]
    relayed = [r for r in record if r.get("relayed")]
    provisional = [a for a in st.history.approvals if getattr(a, "provisional", False)]
    standing_provisional = [a for a in provisional if not getattr(a, "confirmed_at", "") and not getattr(a, "rejected_at", "")]
    end_review_items = getattr(st.runtime, "end_review_items", [])
    actors = sorted({str(r["actor"]) for r in record})
    count = lambda code: sum(code in r["codes"] for r in record)
    review_note = (f" Before deciding, review the {len(warnings)} warning(s) and the {len(flagged)} flagged human action(s) below."
                   if warnings or flagged else "")
    release_node = _release_node(wf)
    n06_claimed = any(a["node"] == "n06_creative_direction" and (a.get("relayed") or a.get("provisional")) for a in approvals)
    release_claimed = any(a["node"] == release_node and a.get("relayed") for a in approvals) if release_node else False
    unclaimed = ["human listening to voice and music"]
    if not n06_claimed:
        unclaimed.append("taste approval")
    if not release_claimed:
        unclaimed.append("release approval")
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
        f"- Not claimed by any agent: {', '.join(unclaimed)}.",
        "", "## Declared baseline departures",
        *_declared_departures(ctx, mask),
        "", "## Regression harness",
        *_harness_lines(ctx, mask),
        "", "## Warnings (non-blocking; judge them yourself)",
        *([f"- W{i}: {w}" for i, w in enumerate(warnings, 1)] or ["- none"]),
        "", "## Human dimension signoffs",
        f"- Pending from reviews: {', '.join(f'{d} ({names.get(d, d)})' for d in pending_human) or 'none'}",
        "", "## Relayed human-only acts",
        *([f"- {r['at'][:19].replace('T', ' ')}: {r['action']} {r['subject']} by {r['actor']} for {r.get('principal', '-')}; "
           f"instruction{(' at ' + r.get('instruction_at')) if r.get('instruction_at') else ''}: {_cell(mask(r.get('instruction', '')), 160)}"
           for r in relayed] or ["- none"]),
        "", "## Provisional approvals",
        *([f"- {a.node}: {a.decision} by {a.actor} for {a.principal or '-'} at {a.at[:19].replace('T', ' ')}"
           + (f"; confirmed {a.confirmed_at[:19].replace('T', ' ')}" if a.confirmed_at else "")
           + (f"; rejected {a.rejected_at[:19].replace('T', ' ')}: {_cell(mask(a.rejection_feedback), 160)}" if a.rejected_at else "")
           for a in provisional] or ["- none"]),
        f"- Standing unconfirmed: {', '.join(a.node for a in standing_provisional) or 'none'}",
        "", "## End review items",
        *([f"- {item.get('id')}: {item.get('node')} v{item.get('version')} defects {', '.join(item.get('defects') or []) or 'none'} — "
           f"{_cell('; '.join(item.get('reasons') or []), 180)}" for item in end_review_items] or ["- none"]),
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
        summary += [f"- {c.id} on {doc_label(c.doc)}: rejected; filed by {c.requested_by}; the human record gives who declined it and why. "
                    f"Request: {_cell(mask(c.reason), 200)}" if c.status == "rejected" else
                    f"- {c.id} on {doc_label(c.doc)}: deferred until: {_cell(mask(c.revisit), 120)}; filed by {c.requested_by}. "
                    f"Request: {_cell(mask(c.reason), 200)}" if c.status == "deferred" else
                    f"- {c.id} on {doc_label(c.doc)}: {c.status}; filed by {c.requested_by}, approved by {c.approved_by or '-'}: {_cell(mask(c.reason), 200)}"
                    for c in st.history.change_requests]
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    (ctx.workdir / "release-summary.md").write_text("\n".join(summary) + "\n")
    verification = {"version": v, "ok": not problems, "problems": problems,
                    "ledger_events": n, "auto_approvals": auto, "delegated_decisions": delegated,
                    "pending_human": pending_human, "auto_approved_human_dimensions": auto_human,
                    "warnings": warnings, "human_record": record}
    write_json_atomic(ctx.workdir / "verification.json", verification)
    from ..review_forks import offered_review_forks
    answered_forks = {str(e["data"].get("fork_id")) for e in events if e.get("type") == "human_choice"}
    unreadable: list[str] = []
    try:   # the graph's node ids, as the validator uses: a node dropped from the graph keeps its runtime entry
        forks = offered_review_forks(ctx.project_dir, getattr(st.production, "forks_ref", {}) or {}, st.artifacts.values(),
                                     answered_forks, {n.id for n in wf.nodes},
                                     {nid: rt.attempts for nid, rt in st.runtime.nodes.items()}, unreadable=unreadable)
    except (OSError, ValueError) as exc:
        forks = []
        warnings.append(f"fork records are unreadable; acknowledge before release ({exc.__class__.__name__})")
    if unreadable:
        warnings.append(f"fork records are unreadable; acknowledge before release ({', '.join(unreadable)})")
    packet, template = _review_packet(ctx, forks, verification, warnings, pending_human, standing_provisional,
                                      end_review_items, masters, mask)
    digest = _decision_digest(ctx, forks, template, verification, warnings, pending_human, standing_provisional,
                              end_review_items, masters, mask)
    review_html = _review_html(packet.splitlines(), template)
    (ctx.workdir / "decision-digest.md").write_text(digest)
    (ctx.workdir / "review-packet.md").write_text(packet)
    write_json_atomic(ctx.workdir / "decision.template.json", template)
    (ctx.workdir / "review.html").write_text(review_html)
    return NodeResult(outputs={"verification": "verification.json", "release_summary": "release-summary.md",
                               "decision_digest": "decision-digest.md",
                               "review_packet": "review-packet.md", "decision_template": "decision.template.json",
                               "review_html": "review.html"},
                      data={"ok": not problems, "problems": problems})


def check_final(ctx) -> CheckResult:
    p = ctx.artifact_path("verification")
    data = read_json(p) if p else {"ok": False, "problems": ["no verification report"]}
    return CheckResult(bool(data["ok"]), data.get("problems", []))
