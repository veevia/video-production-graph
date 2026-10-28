"""#154: an optional N13 artifact (inventory, layout_audit) counts only for the version it was produced for.

A re-run that does not emit an optional artifact leaves the older record current; judging, showing or packaging that
record would compare the wrong film. This is the one definition every reader uses (engine, tools, packaging).
"""
from __future__ import annotations

from typing import Any, Iterable

PRODUCER = "n13_motion_scenes"
OPTIONAL_KEYS = frozenset({"inventory", "layout_audit"})


def fresh(records: Iterable[Any], state: Any, producer: str = PRODUCER) -> list[Any]:
    """The records produced for the current version, or by the producer's latest passing attempt (a version is cut
    only at assembly, after N13 has passed). A bare test context without runtime state keeps every record."""
    recs = list(records)
    rt = getattr(state, "runtime", None)
    version = getattr(rt, "current_version", None)
    node = (getattr(rt, "nodes", None) or {}).get(producer)
    status = getattr(getattr(node, "status", None), "value", getattr(node, "status", None))
    latest = getattr(node, "attempts", None) if status == "STEP_PASS" else None
    if version is None and latest is None:
        return sorted(recs, key=lambda r: r.id)
    out = {r.id: r for r in recs
           if (version is not None and r.version == version) or (latest is not None and r.node == producer and r.attempt == latest)}
    return sorted(out.values(), key=lambda r: r.id)


def fresh_current(ctx: Any, key: str, producer: str = PRODUCER) -> list[Any]:
    """`fresh` over a node context's current records of `key`."""
    return fresh(ctx.current(key), ctx.state, producer) if hasattr(ctx, "current") else []
