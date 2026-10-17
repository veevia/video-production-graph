"""Producer-aware reuse stamps for project commands.

Commands sometimes copy a previous attempt's expensive output. That is safe only when the producer that made the
old output is the same producer running now, including the files that decide its behaviour.
"""

from __future__ import annotations

from typing import Any

from .util import sha256_json


KEY_FIELD = "vpg_producer_key"
SCHEMA_FIELD = "vpg_schema"


def producer_key(ctx_payload: dict[str, Any]) -> str:
    """Stable key for the command producer in a VPG_CONTEXT payload: its node, its command line (a changed flag is a
    changed producer) and the hash of its code files. A caller must add its own inputs to what it compares: this key
    says the producer is the same, not that it was asked for the same thing."""
    producer = ctx_payload.get("producer")
    if not isinstance(producer, dict) or not producer.get("code_sha256"):
        return ""
    return sha256_json({
        "node": producer.get("node"),
        "command": producer.get("command"),
        "code_sha256": producer.get("code_sha256"),
    })


def stamp(record: dict[str, Any], ctx_payload: dict[str, Any], schema: str | None = None) -> dict[str, Any]:
    """Return a copy of `record` carrying the producer key, and optionally the output schema version."""
    out = dict(record)
    out[KEY_FIELD] = producer_key(ctx_payload)
    if schema is not None:
        out[SCHEMA_FIELD] = schema
    return out


def may_reuse(record: dict[str, Any], ctx_payload: dict[str, Any], schema: str | None = None) -> bool:
    """True when a saved record came from this producer and, when supplied, the same schema."""
    key = producer_key(ctx_payload)
    if not isinstance(record, dict) or not key:   # no code identity to compare (an engine before v1.6): never reuse
        return False
    if record.get(KEY_FIELD) != key:
        return False
    return schema is None or record.get(SCHEMA_FIELD) == schema
