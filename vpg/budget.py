"""Loop budgets as project settings, and the quality trend that decides whether a spent budget blocks.

A loop bound measures effort, not quality. On the first real run (SRA, 2026-09-27) the bound stopped a converging run:
cycle 4 had resolved 15 defects when rework loop E43 reached 3 traversals, and the film kept improving outside the
graph for a day while it waited. So a project may declare, in vpg.project.yaml,

    loop_budget:
      profile: quality-first          # or `standard`: the workflow's own bounds, and a spent budget blocks
      max_traversals: 8               # every rework loop (the regeneration plan's loops); other loops keep their bound
      rework_cycles: 15               # replaces policies.max_rework_cycles
      on_exhaust: extend_if_converging   # or `block`
      extend_by: 1                    # traversals (or cycles) granted per extension
      max_hours: 96                   # optional wall-clock guard, from the run's creation: no extension after it

Explicit keys override the profile's. Without a `loop_budget` block nothing changes: the workflow's bounds hold and a
spent budget blocks, exactly as before.

When a rework loop or the rework-cycle budget is spent and `on_exhaust` is `extend_if_converging`, the engine extends it
(and ledgers why) only when the latest review round made progress on the blocking (P1–P3) defects:

  * it resolved at least one, and
  * it found no more new ones than it resolved, and
  * the open count fell, or their severity did (fewer P1, then P2, then P3).

Otherwise it escalates as before, with the trend in the escalation. The runaway guards never extend: a defect that
persisted through `max_same_defect_failures` corrections stops the gate, and so does `max_hours` when set. Other loops
(the intake loop, a human's rejection loops, the release's change loop) keep their bounds: running out of them means a
human has to act, not that quality stopped improving.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SEVERITIES = ("P1", "P2", "P3")
PROFILES: dict[str, dict[str, Any]] = {
    "standard": {"max_traversals": None, "rework_cycles": None, "on_exhaust": "block", "extend_by": 1, "max_hours": None},
    "quality-first": {"max_traversals": 8, "rework_cycles": 15, "on_exhaust": "extend_if_converging", "extend_by": 1,
                      "max_hours": None},
}
KEYS = {"profile", "max_traversals", "rework_cycles", "on_exhaust", "extend_by", "max_hours"}
ON_EXHAUST = ("block", "extend_if_converging")


@dataclass(frozen=True)
class LoopBudget:
    profile: str = "standard"
    max_traversals: int | None = None     # None: each loop's own bound
    rework_cycles: int | None = None      # None: policies.max_rework_cycles
    on_exhaust: str = "block"
    extend_by: int = 1
    max_hours: float | None = None
    declared: bool = False                # the project has a loop_budget block

    @property
    def extends(self) -> bool:
        return self.on_exhaust == "extend_if_converging"


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def budget_problems(config: dict[str, Any]) -> list[str]:
    """Shape errors in vpg.project.yaml's loop_budget block (an empty list when it is absent or valid)."""
    block = config.get("loop_budget")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["loop_budget must be a mapping, e.g. {profile: quality-first}"]
    problems = [f"loop_budget.{k} is not a setting (use {', '.join(sorted(KEYS))})" for k in sorted(set(block) - KEYS)]
    profile = block.get("profile", "standard")
    if profile not in PROFILES:
        problems.append(f"loop_budget.profile must be one of {', '.join(PROFILES)}, not {profile!r}")
    for key in ("max_traversals", "rework_cycles"):   # null: the workflow's own bound
        if key in block and block[key] is not None and not _whole(block[key]):
            problems.append(f"loop_budget.{key} must be a whole number ≥ 1, not {block[key]!r}")
    if "extend_by" in block and not _whole(block["extend_by"]):
        problems.append(f"loop_budget.extend_by must be a whole number ≥ 1, not {block['extend_by']!r}")
    if "on_exhaust" in block and block["on_exhaust"] not in ON_EXHAUST:
        problems.append(f"loop_budget.on_exhaust must be one of {', '.join(ON_EXHAUST)}, not {block['on_exhaust']!r}")
    hours = block.get("max_hours")
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0):
        problems.append(f"loop_budget.max_hours must be a positive number of hours, not {hours!r}")
    return problems


def load_budget(config: dict[str, Any]) -> LoopBudget:
    block = config.get("loop_budget")
    if not isinstance(block, dict) or budget_problems(config):
        return LoopBudget()   # an invalid block never loosens anything; config_problems refuses the run
    profile = block.get("profile", "standard")
    merged = {**PROFILES[profile], **{k: v for k, v in block.items() if k != "profile"}}
    return LoopBudget(profile=profile, max_traversals=merged["max_traversals"], rework_cycles=merged["rework_cycles"],
                      on_exhaust=merged["on_exhaust"], extend_by=merged["extend_by"], max_hours=merged["max_hours"],
                      declared=True)


# ---------------------------------------------------------------------------------------------------- the quality trend
UNRESOLVED = ("open", "persisting", "in_rework")
_OPEN_ITEM = re.compile(r"\b(D\d+) (P1|P2|P3|nit) ")


def gate_snapshot(state, version: int, blocking_severities: list[str], this_round: list[Any]) -> dict[str, Any]:
    """What a gate saw, kept on its result: the blocking (P1–P3) defects open after it, by severity; its own round (the
    defects this gate's comparisons resolved or saw persist, and those filed since the previous gate), for the audit; and
    each involved defect's severity at the time, because a defect's severity follows its latest review."""
    ev = state.evaluation
    counted = [s for s in SEVERITIES if s in blocking_severities]
    blocking = [d for d in ev.defects if d.status in UNRESOLVED and d.severity in counted]
    by_id = {d.id: d for d in ev.defects}
    before = next((g.trend.get("defects_seen") for g in reversed(ev.gate_results) if getattr(g, "trend", None)), None)
    filed = ev.defects[before:] if before is not None else [d for d in ev.defects if d.first_seen_version == version]
    involved = {c.defect_id for c in this_round} | {d.id for d in filed} | {d.id for d in ev.defects if d.status in UNRESOLVED}
    return {"version": version, "cycle": state.runtime.rework_cycles, "open": len(blocking),
            "open_by_severity": {s: sum(1 for d in blocking if d.severity == s) for s in counted},
            "round": {"resolved": sorted({c.defect_id for c in this_round if c.resolved}),
                      "persisting": sorted({c.defect_id for c in this_round if not c.resolved}),
                      "new": sorted(d.id for d in filed)},
            "severities": {i: by_id[i].severity for i in sorted(involved) if i in by_id},
            "defects_seen": len(ev.defects), "estimated": False}


def _reason_split(gate, counted: list[str]) -> dict[str, int] | None:
    """A gate recorded before v1.6 kept no snapshot, but a rework gate's reason lists each open blocking defect with its
    severity at the time ("blocking defects open: D043 P2 LEG-DENSITY→…"). None unless it covers every one of them."""
    listed = {i: sev for r in gate.reasons if r.startswith("blocking defects open:") for i, sev in _OPEN_ITEM.findall(r)}
    if not gate.open_blocking or any(i not in listed for i in gate.open_blocking):
        return None
    return {s: sum(1 for i in gate.open_blocking if listed[i] == s) for s in counted}


def trend_rows(state, blocking_severities: list[str], latest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per reviewed version, oldest first, merging all of the version's gates: a version re-gated after an
    unblock or an accepted configuration keeps its progress (that it earns only one extension is the engine's rule, per
    version). `latest` is the gate being decided, not yet recorded.

    A row's open count and severity split are its last gate's. Resolved, new and persisting are the version's own: the
    defects its comparisons resolved, the defects first seen at it, and those it saw persist without resolving. A
    defect is counted by its severity at the time where a gate recorded it (v1.6 on), else by its current one; a row
    whose last gate was recorded before v1.6 is `estimated`, with a severity split only when its reason lists one."""
    counted = [s for s in SEVERITIES if s in blocking_severities]
    ev = state.evaluation
    gates: list[tuple[int, dict | None, Any]] = [(g.version, getattr(g, "trend", None), g) for g in ev.gate_results]
    if latest is not None:
        gates.append((latest["version"], latest, None))
    now_sev = {d.id: d.severity for d in ev.defects}
    rows = []
    for version in dict.fromkeys(v for v, _, _ in gates):
        mine = [(snap, g) for v, snap, g in gates if v == version]
        sev = dict(now_sev)
        for snap, _ in mine:
            if snap:
                sev.update(snap.get("severities") or {})
        counts = lambda i: sev.get(i) in counted
        resolved = {c.defect_id for c in ev.comparisons if c.after_version == version and c.resolved and counts(c.defect_id)}
        seen = {c.defect_id for c in ev.comparisons if c.after_version == version and not c.resolved and counts(c.defect_id)}
        new = {d.id for d in ev.defects if d.first_seen_version == version and counts(d.id)}
        snap, g = mine[-1]
        if snap:
            head = {"cycle": snap.get("cycle"), "open": snap["open"], "open_by_severity": snap.get("open_by_severity"),
                    "estimated": False}
        else:
            head = {"cycle": None, "open": len(g.open_blocking), "open_by_severity": _reason_split(g, counted), "estimated": True}
        rows.append({"version": version, **head, "resolved": len(resolved), "new": len(new),
                     "persisting": len(seen - resolved)})
    return rows


def _heavier(row: dict[str, Any]) -> tuple[int, ...] | None:
    split = row.get("open_by_severity")
    return tuple(split.get(s, 0) for s in SEVERITIES) if split else None


def convergence(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """Whether the latest review round made progress on the blocking defects, with the reason in plain words."""
    if len(rows) < 2:
        return False, "no earlier review round to compare with"
    prev, cur = rows[-2], rows[-1]
    step = (f"v{prev['version']} → v{cur['version']}: {prev['open']} → {cur['open']} open P1–P3, "
            f"{cur['resolved']} resolved, {cur['new']} new")
    if cur["resolved"] == 0:
        return False, f"{step}; nothing was resolved, so the same defects persist"
    if cur["new"] > cur["resolved"]:
        return False, f"{step}; more new defects than resolved ones"
    fewer = cur["open"] < prev["open"]
    before, after = _heavier(prev), _heavier(cur)
    lighter = before is not None and after is not None and after < before
    if fewer or lighter:
        return True, step + ("" if fewer else "; fewer severe ones open")
    return False, f"{step}; neither the open count nor their severity fell"


def trend_lines(rows: list[dict[str, Any]], last: int = 6) -> list[str]:
    """Human-readable rows, newest last: "v11 · cycle 3 · 4 open (P1 0, P2 1, P3 3) · 15 resolved · 1 new · 4 persisting"."""
    out = []
    for r in rows[-last:]:
        split = r.get("open_by_severity")
        detail = f" ({', '.join(f'{s} {n}' for s, n in split.items())})" if split else ""
        cycle = f" · cycle {r['cycle']}" if r.get("cycle") is not None else ""
        out.append(f"v{r['version']}{cycle} · {r['open']} open{detail} · {r['resolved']} resolved · {r['new']} new · "
                   f"{r['persisting']} persisting" + (" (estimated: recorded before v1.6)" if r.get("estimated") else ""))
    return out


def hours_since(stamp: str, clock: datetime | None = None) -> float | None:
    try:
        start = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return ((clock or datetime.now(timezone.utc)) - start).total_seconds() / 3600
