"""Observability: OpenTelemetry-GenAI-shaped spans and evaluation events for any run, old runs included.

Spans are derived from the hash-chained ledger (`.vpg/ledger.jsonl`): a run span, one span per node attempt (from
`node_dispatched` to `node_passed`, `node_failed`, `escalated` or `invalidated`), a span per memo hit, and a child span
for each wait on a human (from `awaiting_human` to its `decision`). Agent and review nodes are `invoke_agent <agent>`
spans and tool nodes `execute_tool <tool>` spans, following the OpenTelemetry GenAI semantic conventions.

Evaluation events follow the conventions' `gen_ai.evaluation.result` event: a name, a score value or a label, an
explanation, and here the evidence (artifacts with sha256, findings with timecodes, metrics) plus where the event came
from (the ledger event's sequence number and hash, or the state record). A run that opts in
(`observability: {evaluation_events: true}` in vpg.project.yaml) also writes them into the ledger as `evaluation`
events as each node passes or fails, each gate decides and each human decides (`payload_*` below), so they are
hash-chained with everything else. For other runs they are derived on demand from the ledger and the saved state.

Span and trace ids are derived from the run id, the node and the attempt, so an engine-written event and a derived span
always link. Nothing here calls the network: `vpg telemetry --otlp` writes an OTLP/JSON file that a local Phoenix or
Langfuse can import.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .util import read_json, write_json_atomic

SCHEMA = "vpg-telemetry/v1"
TELEMETRY_DIR = Path(".vpg") / "telemetry"
EVIDENCE_TAGS = ("observed", "reported", "assumed")
CLOSE = {"node_passed": "OK", "node_failed": "ERROR", "escalated": "ERROR", "invalidated": "UNSET",
         "preflight_requested": "UNSET", "resume_reset": "UNSET"}


def trace_id(run_id: str) -> str:
    return hashlib.sha256(f"vpg-trace:{run_id}".encode()).hexdigest()[:32]


def span_id(run_id: str, *parts: Any) -> str:
    return hashlib.sha256(("vpg-span:" + run_id + ":" + ":".join(str(p) for p in parts)).encode()).hexdigest()[:16]


def node_span_id(run_id: str, node: str, attempt: int | str) -> str:
    return span_id(run_id, "node", node, attempt)


def unix_ns(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
    except ValueError:
        return None


def operation(node: Any) -> tuple[str, dict[str, Any]]:
    """The GenAI operation of a node and its identifying attributes."""
    kind = getattr(getattr(node, "kind", None), "value", getattr(node, "kind", ""))
    if kind == "tool":
        tool = (node.run.tool if getattr(node, "run", None) and node.run.tool else node.id)
        return "execute_tool", {"gen_ai.tool.name": tool, "gen_ai.tool.type": "function"}
    return "invoke_agent", {"gen_ai.agent.name": getattr(node, "agent", "") or node.id, "gen_ai.agent.id": getattr(node, "agent", "")}


# ------------------------------------------------------------------------------------------------ evaluation payloads
def evaluation(name: str, *, label: str, node: str, attempt: int | None, run_id: str, value: float | None = None,
               explanation: str = "", evidence: list[dict[str, Any]] | None = None, evaluator: str = "engine",
               tag: str = "observed", parent: str | None = None) -> dict[str, Any]:
    """One `gen_ai.evaluation.result`-shaped record (also the `data` of an engine-written `evaluation` ledger event)."""
    if tag not in EVIDENCE_TAGS:
        raise ValueError(f"evidence tag must be one of {EVIDENCE_TAGS}")
    out = {"name": name, "gen_ai.evaluation.name": name, "gen_ai.evaluation.score.label": label,
           "gen_ai.evaluation.explanation": explanation[:2000], "node": node, "attempt": attempt, "evaluator": evaluator,
           "evidence_tag": tag, "evidence": evidence or [],
           "span_id": parent or (node_span_id(run_id, node, attempt) if attempt is not None else span_id(run_id, "run")),
           "trace_id": trace_id(run_id)}
    if value is not None:
        out["gen_ai.evaluation.score.value"] = float(value)
    return out


MAX_EVIDENCE = 50   # entries per event; a per-item node (one asset per item) would otherwise bloat a ledger event


def _cap(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(evidence) <= MAX_EVIDENCE:
        return evidence
    return evidence[:MAX_EVIDENCE] + [{"kind": "truncated", "more": len(evidence) - MAX_EVIDENCE}]


def _artifact_evidence(state: dict[str, Any], node: str, attempt: int | None) -> list[dict[str, Any]]:
    out = []
    for a in (state.get("artifacts") or {}).values():
        if a.get("node") == node and (attempt is None or a.get("attempt") == attempt):
            out.append({"kind": "artifact", "key": a.get("key"), "path": a.get("path"), "sha256": str(a.get("sha256", ""))[:16],
                        **({"item": a["item"]} if a.get("item") else {})})
    return sorted(out, key=lambda e: (e["key"] or "", e.get("item") or ""))


def payload_completion(run_id: str, state: dict[str, Any], node: str, attempt: int, outcome: str,
                       checks: list[str] | None = None) -> dict[str, Any]:
    """`state` is the saved state as a dict, or {"artifacts": {id: record-as-dict}} built from the live state."""
    ev = _artifact_evidence(state, node, attempt) + [{"kind": "check", "description": c} for c in checks or []]
    return evaluation("vpg.node.completion", label=outcome, node=node, attempt=attempt, run_id=run_id,
                      explanation=f"{node} attempt {attempt} passed its completion checks ({outcome})", evidence=_cap(ev))


def payload_failure(run_id: str, node: str, attempt: int, failures: list[str]) -> dict[str, Any]:
    return evaluation("vpg.node.completion", label="fail", node=node, attempt=attempt, run_id=run_id,
                      explanation="; ".join(failures)[:2000], evidence=[{"kind": "failure", "message": f} for f in failures[:20]])


def tool_dimensions() -> set[str]:
    from .evaluation import load_rubrics
    return {d["id"] for r in load_rubrics().values() for d in r["dimensions"] if (d.get("check") or {}).get("type") == "tool"}


def payload_review(run_id: str, review: dict[str, Any], attempt: int | None, tool_dims: set[str] | None = None) -> list[dict[str, Any]]:
    """One event per scored dimension and per tool finding of a review record. A tool's score is observed; an agent
    reviewer's score is its report (reported), never an observation."""
    tool_dims = tool_dimensions() if tool_dims is None else tool_dims
    out = []
    findings = review.get("tool_findings") or []
    for s in review.get("scores") or []:
        dim = s.get("dimension")
        ev = [{"kind": "finding", "check": f.get("check"), "severity": f.get("severity"), "message": f.get("message"),
               "evidence": f.get("evidence"), "element": f.get("element")} for f in findings if f.get("dimension") == dim]
        if s.get("evidence"):
            ev.insert(0, {"kind": "reviewer_note", "text": s.get("evidence")})
        out.append(evaluation(f"vpg.review.{dim}", label="pass" if s.get("passed") else "fail", value=s.get("score"),
                              node=review.get("node"), attempt=attempt, run_id=run_id, evaluator=review.get("actor") or review.get("reviewer", ""),
                              explanation=f"{dim} scored {s.get('score')} against {s.get('threshold')} ({s.get('level', 'MUST')})",
                              evidence=_cap(ev), tag="observed" if dim in tool_dims else "reported"))
    scored = {s.get("dimension") for s in review.get("scores") or []}
    for f in findings:
        if f.get("dimension") in scored:
            continue
        out.append(evaluation(f"vpg.finding.{f.get('dimension')}", label="nit" if f.get("severity") == "nit" else "fail",
                              node=review.get("node"), attempt=attempt, run_id=run_id, evaluator=f"tool:{f.get('check')}",
                              explanation=f.get("message", ""), evidence=[{"kind": "finding", **{k: f.get(k) for k in (
                                  "check", "severity", "evidence", "element", "traced_node", "artifact_key")}}]))
    return out


def payload_gate(run_id: str, node: str, attempt: int | None, version: int, outcome: str, reasons: list[str],
                 open_blocking: list[str]) -> dict[str, Any]:
    return evaluation("vpg.gate", label=outcome, node=node, attempt=attempt, run_id=run_id, explanation="; ".join(reasons)[:2000],
                      evidence=[{"kind": "version", "version": version}] + [{"kind": "defect", "id": d} for d in open_blocking])


def decision_tag(provisional: bool, relayed: bool, human: bool = True, grant: str = "") -> str:
    """Only a named human's own recorded decision is observed. One an agent relays (the human's instruction, quoted) is
    reported. A provisional one, one made under a delegation grant, and one no human made (a dry-run auto-approval) are
    assumed: no human judged that output."""
    if provisional:
        return "assumed"
    if relayed:
        return "reported"
    return "observed" if human and not grant else "assumed"


def payload_decision(run_id: str, node: str, attempt: int | None, decision: str, actor: str, reason: str,
                     provisional: bool, relayed: bool, outputs: dict[str, str], expires_at: str = "", human: bool = True,
                     grant: str = "") -> dict[str, Any]:
    """The decision at a human gate. `vpg.human` says whether a named human made it; when none did (a grant, a dry-run
    auto-approval), the explanation says so and the tag is assumed."""
    ev = [{"kind": "decided_on", "key": k, "sha256": v} for k, v in sorted((outputs or {}).items())]
    if expires_at:
        ev.append({"kind": "expiry", "expires_at": expires_at})
    if grant:
        ev.append({"kind": "grant", "id": grant})
    by_human = human and not grant and not provisional
    why = reason or ""
    if not by_human and not relayed:
        why = f"not a human's decision: recorded by {actor}" + (f" under grant {grant}" if grant else "") + (f" ({why})" if why else "")
    out = evaluation("vpg.human_decision", label=decision, node=node, attempt=attempt, run_id=run_id,
                     explanation=why, evaluator=actor, tag=decision_tag(provisional, relayed, human, grant), evidence=ev)
    out["vpg.human"] = by_human or relayed
    return out


# ------------------------------------------------------------------------------------------------ derivation
def _load_workflow(project: Path):
    import yaml

    from .graph import load_workflow, resolve_workflow
    conf = yaml.safe_load((project / "vpg.project.yaml").read_text()) if (project / "vpg.project.yaml").exists() else {}
    try:
        return load_workflow(resolve_workflow((conf or {}).get("workflow"), project))
    except Exception:   # noqa: BLE001 - a missing workflow only loses node metadata, never the spans
        return None


def derive(project: str | Path) -> dict[str, Any]:
    """Spans, evaluation events and coverage for one project's run (read-only)."""
    from .provenance import Ledger
    project = Path(project)
    events = list(Ledger(project / ".vpg" / "ledger.jsonl", read_only=True).events())
    state = read_json(project / ".vpg" / "state.json") if (project / ".vpg" / "state.json").exists() else {}
    run_id = (state.get("runtime") or {}).get("run_id") or next((e["data"].get("run") for e in events if e["type"] == "run_created"), "") or project.name
    wf = _load_workflow(project)
    nodes = {n.id: n for n in wf.nodes} if wf else {}
    tid = trace_id(run_id)
    root = span_id(run_id, "run")
    spans: dict[str, dict[str, Any]] = {}
    first, last = (events[0]["ts"] if events else None), (events[-1]["ts"] if events else None)
    spans[root] = {"trace_id": tid, "span_id": root, "parent_span_id": None, "name": f"vpg.run {run_id}", "kind": "INTERNAL",
                   "start": first, "end": last, "status": "UNSET",
                   "attributes": {"vpg.run.id": run_id, "vpg.project": (state.get("project") or {}).get("id", project.name),
                                  "vpg.workflow": f"{wf.id} v{wf.version}" if wf else "", "gen_ai.provider.name": "vpg"},
                   "source": {"ledger_seq": events[0]["seq"] if events else None}}
    open_attempt: dict[str, int] = {}
    waits: dict[str, str] = {}
    evals: list[dict[str, Any]] = []
    explicit = [e for e in events if e["type"] == "evaluation"]
    memo_n: dict[str, int] = {}
    for e in events:
        d, t = e["data"], e["type"]
        nid = d.get("node")
        src = {"ledger_seq": e["seq"], "ledger_hash": e["hash"][:16]}
        if t == "node_dispatched" and nid:
            att = int(d.get("attempt") or 0)
            open_attempt[nid] = att
            node = nodes.get(nid)
            op, ident = operation(node) if node else ("invoke_agent", {"gen_ai.agent.name": nid})
            sid = node_span_id(run_id, nid, att)
            spans[sid] = {"trace_id": tid, "span_id": sid, "parent_span_id": root, "kind": "CLIENT",
                          "name": f"{op} {ident.get('gen_ai.agent.name') or ident.get('gen_ai.tool.name')}",
                          "start": e["ts"], "end": None, "status": "UNSET",
                          "attributes": {"gen_ai.operation.name": op, "gen_ai.provider.name": "vpg", **ident,
                                         "vpg.node.id": nid, "vpg.node.num": getattr(node, "num", None), "vpg.attempt": att,
                                         "vpg.fingerprint": d.get("fingerprint"), "vpg.executor": d.get("executor")},
                          "source": src}
        elif t == "memo_hit" and nid:
            memo_n[nid] = memo_n.get(nid, 0) + 1
            sid = span_id(run_id, "memo", nid, memo_n[nid])
            spans[sid] = {"trace_id": tid, "span_id": sid, "parent_span_id": root, "kind": "INTERNAL", "name": f"vpg.memo {nid}",
                          "start": e["ts"], "end": e["ts"], "status": "OK",
                          "attributes": {"vpg.node.id": nid, "vpg.memo": True, "vpg.fingerprint": d.get("fingerprint")}, "source": src}
            if not explicit:
                evals.append({**evaluation("vpg.node.memo", label="pass", node=nid, attempt=None, run_id=run_id,
                                           parent=sid, explanation=d.get("reason", "inputs unchanged"),
                                           evidence=[{"kind": "fingerprint", "value": d.get("fingerprint")}]), "time": e["ts"], "source": src})
        elif t == "node_submitted" and nid in open_attempt:
            spans[node_span_id(run_id, nid, open_attempt[nid])]["attributes"]["vpg.actor"] = d.get("actor")
        elif t in CLOSE and nid in open_attempt:
            att = int(d.get("attempt") or open_attempt[nid])
            sid = node_span_id(run_id, nid, att)
            if sid in spans and spans[sid]["end"] is None:
                spans[sid].update(end=e["ts"], status=CLOSE[t])
                spans[sid]["attributes"]["vpg.close"] = t
                if d.get("outcome"):
                    spans[sid]["attributes"]["vpg.outcome"] = d["outcome"]
            if not explicit and t == "node_passed":
                evals.append({**payload_completion(run_id, state, nid, att, d.get("outcome", "pass")), "time": e["ts"], "source": src})
            elif not explicit and t == "node_failed":
                evals.append({**payload_failure(run_id, nid, att, list(d.get("failures") or [])), "time": e["ts"], "source": src})
        elif t == "awaiting_human" and nid:
            att = open_attempt.get(nid, 0)
            sid = span_id(run_id, "await", nid, att, e["seq"])
            waits[nid] = sid
            spans[sid] = {"trace_id": tid, "span_id": sid, "parent_span_id": node_span_id(run_id, nid, att), "kind": "INTERNAL",
                          "name": f"vpg.await_human {nid}", "start": e["ts"], "end": None, "status": "UNSET",
                          "attributes": {"vpg.node.id": nid, "vpg.human.decision_asked": d.get("decision")}, "source": src}
        elif t == "decision" and nid:
            sid = waits.pop(nid, None)
            tag = decision_tag(bool(d.get("provisional")), bool(d.get("relayed")), bool(d.get("human", True)), str(d.get("grant") or ""))
            if sid and sid in spans:
                spans[sid].update(end=e["ts"], status="OK")
                spans[sid]["attributes"].update({"vpg.human.decision": d.get("decision"), "vpg.human.actor": d.get("actor"),
                                                 "vpg.human.provisional": bool(d.get("provisional")), "vpg.evidence_tag": tag,
                                                 "vpg.human": tag != "assumed"})
            if not explicit:
                evals.append({**payload_decision(run_id, nid, open_attempt.get(nid), d.get("decision", ""), d.get("actor", ""),
                                                 d.get("reason", ""), bool(d.get("provisional")), bool(d.get("relayed")),
                                                 d.get("outputs") or {}, d.get("expires_at", ""), bool(d.get("human", True)),
                                                 str(d.get("grant") or "")), "time": e["ts"], "source": src})
        elif t == "gate" and nid and not explicit:
            evals.append({**payload_gate(run_id, nid, open_attempt.get(nid), d.get("version", 0), d.get("outcome", ""),
                                         list(d.get("reasons") or []), list(d.get("open_blocking") or [])), "time": e["ts"], "source": src})
        elif t == "evaluation":
            evals.append({**d, "time": e["ts"], "source": src})
        elif t == "direct_run":
            sid = span_id(run_id, "direct", d.get("id"))
            spans[sid] = {"trace_id": tid, "span_id": sid, "parent_span_id": root, "kind": "INTERNAL",
                          "name": f"vpg.direct_run {nid}", "start": d.get("started_at") or e["ts"], "end": e["ts"],
                          "status": "OK" if d.get("exit_code") in (0, None) else "ERROR",
                          "attributes": {"vpg.node.id": nid, "vpg.direct_run.id": d.get("id"), "vpg.governed": False,
                                         "vpg.actor": d.get("actor"), "vpg.label": d.get("label")}, "source": src}
            evals.append({**evaluation("vpg.direct_run", label="ungoverned", node=nid or "", attempt=None, run_id=run_id,
                                       parent=sid, explanation=d.get("reason", ""), evaluator=d.get("actor", ""),
                                       evidence=[{"kind": "output", "key": k, **v} for k, v in (d.get("outputs") or {}).items()]),
                          "time": e["ts"], "source": src})
    if not explicit:   # reviews live in the state: one event per scored dimension and tool finding
        tool_dims = tool_dimensions()
        for i, r in enumerate((state.get("evaluation") or {}).get("reviews") or []):
            att = _attempt_at(events, r.get("node"), r.get("created_at"))
            for ev in payload_review(run_id, r, att, tool_dims):
                evals.append({**ev, "time": r.get("created_at"), "source": {"state": f"evaluation.reviews[{i}]"}})
    out = {"schema": SCHEMA, "run_id": run_id, "trace_id": tid, "spans": list(spans.values()), "evaluations": evals}
    out["coverage"] = coverage(out, events)
    return out


def _attempt_at(events: list[dict[str, Any]], node: str | None, when: str | None) -> int | None:
    """The attempt of `node` that was in flight (or last dispatched) when a review record was created."""
    att = None
    for e in events:
        if when and e["ts"] > when:
            break
        if e["type"] == "node_dispatched" and e["data"].get("node") == node:
            att = int(e["data"].get("attempt") or 0)
    return att


def coverage(telemetry: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Does every node that ran, every gate decision and every human decision have an evaluation event with evidence?"""
    ran = sorted({e["data"]["node"] for e in events if e["type"] in ("node_passed", "node_failed") and e["data"].get("node")})
    evals = telemetry["evaluations"]
    with_ev = {(ev.get("node"), ev.get("name")) for ev in evals if ev.get("evidence") or ev.get("gen_ai.evaluation.explanation")}
    nodes_ok = {n: any(node == n for node, _ in with_ev) for n in ran}
    gates = [e for e in events if e["type"] == "gate"]
    decisions = [e for e in events if e["type"] == "decision"]
    gate_events = [ev for ev in evals if ev.get("name") == "vpg.gate"]
    decision_events = [ev for ev in evals if ev.get("name") == "vpg.human_decision"]
    missing = [f"node {n}" for n, ok in nodes_ok.items() if not ok]
    if len(gate_events) < len(gates):
        missing.append(f"{len(gates) - len(gate_events)} gate decision(s)")
    if len(decision_events) < len(decisions):
        missing.append(f"{len(decisions) - len(decision_events)} human decision(s)")
    spans = {s["span_id"] for s in telemetry["spans"]}
    orphans = [ev.get("name") for ev in evals if ev.get("span_id") not in spans]
    return {"nodes_run": len(ran), "nodes_with_evidence": sum(nodes_ok.values()), "gates": len(gates),
            "gate_events": len(gate_events), "decisions": len(decisions), "decision_events": len(decision_events),
            "evaluations": len(evals), "spans": len(telemetry["spans"]), "orphan_events": orphans[:20],
            "missing": missing, "ok": not missing}


# ------------------------------------------------------------------------------------------------ export
def _attr(key: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def to_otlp(telemetry: dict[str, Any], project_id: str = "") -> dict[str, Any]:
    """OTLP/JSON traces with each evaluation as a `gen_ai.evaluation.result` span event on the span it evaluates."""
    from . import __version__
    by_span: dict[str, list[dict[str, Any]]] = {}
    for ev in telemetry["evaluations"]:
        by_span.setdefault(ev.get("span_id"), []).append(ev)
    spans = []
    codes = {"UNSET": 0, "OK": 1, "ERROR": 2}
    kinds = {"INTERNAL": 1, "CLIENT": 3}
    for s in telemetry["spans"]:
        start = unix_ns(s["start"]) or 0
        end = unix_ns(s["end"]) or start
        events = []
        for ev in by_span.get(s["span_id"], []):
            keys = ("gen_ai.evaluation.name", "gen_ai.evaluation.score.value", "gen_ai.evaluation.score.label",
                    "gen_ai.evaluation.explanation", "evaluator", "evidence_tag")
            attrs = [a for a in (_attr(k if k.startswith("gen_ai") else f"vpg.{k}", ev.get(k)) for k in keys) if a]
            if "vpg.human" in ev:
                attrs.append(_attr("vpg.human", bool(ev["vpg.human"])))
            attrs.append({"key": "vpg.evidence", "value": {"stringValue": json.dumps(ev.get("evidence") or [])[:4000]}})
            events.append({"timeUnixNano": str(unix_ns(ev.get("time")) or end), "name": "gen_ai.evaluation.result", "attributes": attrs})
        spans.append({"traceId": s["trace_id"], "spanId": s["span_id"], **({"parentSpanId": s["parent_span_id"]} if s["parent_span_id"] else {}),
                      "name": s["name"], "kind": kinds.get(s["kind"], 1), "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
                      "attributes": [a for a in (_attr(k, v) for k, v in s["attributes"].items()) if a],
                      "status": {"code": codes.get(s["status"], 0)}, "events": events})
    resource = [a for a in (_attr("service.name", "vpg"), _attr("vpg.project", project_id), _attr("vpg.run.id", telemetry["run_id"])) if a]
    return {"resourceSpans": [{"resource": {"attributes": resource},
                               "scopeSpans": [{"scope": {"name": "vpg", "version": __version__}, "spans": spans}]}]}


def write(project: str | Path, out_dir: str | Path | None = None, otlp: str | Path | None = None) -> dict[str, Path]:
    """Write spans.jsonl, evaluations.jsonl and summary.json (default `.vpg/telemetry/`), and an OTLP file if asked."""
    project = Path(project)
    tel = derive(project)
    out = Path(out_dir) if out_dir else project / TELEMETRY_DIR
    out.mkdir(parents=True, exist_ok=True)
    paths = {"spans": out / "spans.jsonl", "evaluations": out / "evaluations.jsonl", "summary": out / "summary.json"}
    for key in ("spans", "evaluations"):
        tmp = paths[key].with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in tel[key]))
        tmp.replace(paths[key])
    write_json_atomic(paths["summary"], {"schema": SCHEMA, "run_id": tel["run_id"], "trace_id": tel["trace_id"],
                                         "coverage": tel["coverage"]})
    if otlp:
        paths["otlp"] = Path(otlp)
        write_json_atomic(paths["otlp"], to_otlp(tel, project.name))
    return paths
