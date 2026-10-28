"""Benchmark registry, envelopes and the ratchet.

A registry (`vpg-benchmarks/v1`, schema `schemas/benchmark-registry.schema.json`) lists the films a production is
judged against, each with its media (paths relative to the registry, with sha256) and its measurements:

  reference         a film whose craft defines what is in character. Envelopes come from these.
  degraded_anchor   a deliberately degraded copy of a reference (a negative control). It must fall outside at least
                    one envelope; otherwise the envelopes cannot tell good from bad and are not trusted.
  previous_best     the accepted baseline for the ratchet. Only a named human may set it, and setting a new one
                    supersedes the old one (kept, never deleted).

Envelopes: for each metric, the reference values widened by the metric's tolerance (vpg/metrics.py), checked on the
metric's defect side only ("lower", "upper" or "both"). A registry may override a metric's sides or tolerance, or add
an absolute floor or ceiling, in `envelope_policy`. `verify` re-checks that no reference falls outside an envelope
built from the references (the benchmark is never flagged) and that every anchor is caught.

Ratchet: a candidate may not be worse than the previous best on any metric beyond its tolerance, unless a named
human waived that metric. A two-sided metric is worse when it moves further outside its envelope.

Measurements of another version or basis are never compared: `stale` lists them and `vpg benchmark measure`
re-measures them from their media first.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .metrics import METRICS, comparable, current, ocr_not_run
from .util import PKG_ROOT, is_human_name, now, sha256_file, write_json_atomic

REGISTRY_SCHEMA = "vpg-benchmarks/v1"
SCHEMA_PATH = PKG_ROOT / "schemas" / "benchmark-registry.schema.json"
KINDS = ("reference", "degraded_anchor", "previous_best")
MEDIA_KEYS = ("master", "voice", "music", "text_layers", "narration")


class BenchmarkError(ValueError):
    pass


@dataclass
class Envelope:
    metric: str
    version: int
    basis: str
    lo: float | None
    hi: float | None
    sides: str
    tolerance: float
    values: dict[str, float] = field(default_factory=dict)

    def judge(self, value: float) -> tuple[str, float]:
        """("inside" | "below" | "above", distance outside the defect side; 0 inside)."""
        if self.sides in ("both", "lower") and self.lo is not None and value < self.lo:
            return "below", round(self.lo - value, 4)
        if self.sides in ("both", "upper") and self.hi is not None and value > self.hi:
            return "above", round(value - self.hi, 4)
        return "inside", 0.0


# ------------------------------------------------------------------------------------------------ load and save
def new_registry(registry_id: str, title: str = "") -> dict[str, Any]:
    return {"schema": REGISTRY_SCHEMA, "id": registry_id, "title": title or registry_id, "entries": [], "envelope_policy": {}}


def validate(data: dict[str, Any]) -> list[str]:
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text())
    errors = [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema).iter_errors(data)]
    ids = [e.get("id") for e in data.get("entries", [])]
    errors += [f"duplicate entry id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    known = set(ids)
    for e in data.get("entries", []):
        if e.get("kind") == "degraded_anchor" and e.get("degrades") not in known:
            errors.append(f"{e.get('id')}: a degraded anchor must name the reference it degrades (degrades)")
    live = [e["id"] for e in data.get("entries", []) if e.get("kind") == "previous_best" and not e.get("superseded_by")]
    if len(live) > 1:
        errors.append(f"more than one live previous_best: {live}")
    for mid in data.get("envelope_policy", {}):
        if mid not in METRICS:
            errors.append(f"envelope_policy names an unknown metric {mid}")
    for e in data.get("entries", []):
        refs = list((e.get("media") or {}).values()) + list(e.get("style_frames") or [])
        for ref in refs:
            rp = str((ref or {}).get("path", ""))
            if Path(rp).is_absolute() or ".." in Path(rp).parts:
                errors.append(f"{e.get('id')}: {rp} must be a path inside the registry folder (no absolute path, no '..')")
    return errors


def load(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    problems = validate(data)
    if problems:
        raise BenchmarkError(f"{path}: invalid benchmark registry: " + "; ".join(problems[:6]))
    return data


def save(path: str | Path, data: dict[str, Any]) -> None:
    problems = validate(data)
    if problems:
        raise BenchmarkError("refusing to save an invalid registry: " + "; ".join(problems[:6]))
    write_json_atomic(path, data)


def entry(data: dict[str, Any], entry_id: str) -> dict[str, Any]:
    for e in data.get("entries", []):
        if e["id"] == entry_id:
            return e
    raise BenchmarkError(f"no entry {entry_id}")


def live_baseline(data: dict[str, Any]) -> dict[str, Any] | None:
    return next((e for e in data.get("entries", []) if e["kind"] == "previous_best" and not e.get("superseded_by")), None)


# ------------------------------------------------------------------------------------------------ media and measuring
def inside(base: Path, relpath: str) -> Path | None:
    """`base / relpath` when it resolves inside `base` (links included), else None."""
    p = (Path(base) / relpath).resolve()
    return p if p.is_relative_to(Path(base).resolve()) else None


COPY_KINDS = ("reference", "degraded_anchor")   # other films; a previous_best is this production's own, free to reuse


def registered_files(data: dict[str, Any], base: Path | None = None,
                     kinds: tuple[str, ...] = COPY_KINDS) -> list[tuple[str, str, Path | None]]:
    """(label "<entry>.<media key or role>", registered sha256, path inside the registry folder or None) for every file
    of those entries: a candidate that copies one of them is a benchmark, not new work."""
    out = []
    for e in data.get("entries", []):
        if e.get("kind") not in kinds:
            continue
        refs = [(f"{e['id']}.{key}", ref) for key, ref in (e.get("media") or {}).items()]
        refs += [(f"{e['id']}.{sf.get('role')}", sf) for sf in e.get("style_frames") or []]
        for label, ref in refs:
            path = inside(base, ref["path"]) if base is not None and (ref or {}).get("path") else None
            out.append((label, str((ref or {}).get("sha256") or ""), path))
    return out


def media_paths(e: dict[str, Any], base: Path) -> dict[str, Path]:
    out = {}
    for k, v in (e.get("media") or {}).items():
        if k in MEDIA_KEYS and v.get("path") and (p := inside(base, v["path"])) is not None:
            out[k] = p
    return out


def media_problems(e: dict[str, Any], base: Path) -> list[str]:
    out = []
    refs = [(key, ref) for key, ref in (e.get("media") or {}).items()]
    refs += [(f"style_frames[{sf.get('role')}]", sf) for sf in e.get("style_frames") or []]
    for key, ref in refs:
        p = inside(base, ref["path"])
        if p is None:
            out.append(f"{e['id']}.{key}: {ref['path']} is outside the registry folder")
        elif not p.is_file():
            out.append(f"{e['id']}.{key}: {ref['path']} is missing")
        elif ref.get("sha256") and sha256_file(p) != ref["sha256"]:
            out.append(f"{e['id']}.{key}: {ref['path']} changed since it was registered")
    return out


def measure_entry(e: dict[str, Any], base: Path, ocr: bool = False,
                  measure_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    from .tools.craft import measure
    files = media_paths(e, base)
    frames = e.get("style_frames") or []
    frames = [sf for sf in frames if inside(base, sf["path"]) is not None]
    extra = {"stills": [inside(base, sf["path"]) for sf in frames], "still_roles": [sf["role"] for sf in frames]} if frames else {}
    report = (measure_fn or measure)(**files, ocr=ocr, **extra)
    return report["measurements"]


def _ocr_fillable(e: dict[str, Any], mid: str, rec: dict[str, Any]) -> bool:
    """An OCR record that a measurement with OCR can fill: OCR was never asked for, on media the entry has (a master
    for text.*, style frames for still.*). Too little readable text, or OCR that could not run, is not filled this way."""
    if ".ocr_" not in mid or rec.get("value") is not None or not ocr_not_run(rec.get("not_measured")):
        return False
    return bool(e.get("style_frames")) if mid.startswith("still.") else bool((e.get("media") or {}).get("master"))


def stale(data: dict[str, Any], ocr: bool = False) -> dict[str, list[str]]:
    """Per entry, the metrics whose stored measurement is missing or of another version or basis; with `ocr`, also the
    OCR metrics an entry recorded without OCR on media it has (see `_ocr_fillable`)."""
    out = {}
    for e in data.get("entries", []):
        recs = e.get("measurements") or {}
        bad = [mid for mid in METRICS if not current(recs.get(mid))
               or (ocr and _ocr_fillable(e, mid, recs[mid]))]
        if bad:
            out[e["id"]] = bad
    return out


def remeasure(path: str | Path, entry_ids: list[str] | None = None, ocr: bool = False, all_metrics: bool = False,
              measure_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, list[str]]:
    """Re-measure stale measurements from each entry's registered media (refusing media that changed)."""
    path = Path(path)
    data, base = load(path), path.parent
    todo = stale(data, ocr=ocr)
    done: dict[str, list[str]] = {}
    for e in data["entries"]:
        if entry_ids and e["id"] not in entry_ids:
            continue
        if not all_metrics and e["id"] not in todo:
            continue
        problems = media_problems(e, base)
        if problems:
            raise BenchmarkError("; ".join(problems))
        fresh = measure_entry(e, base, ocr=ocr, measure_fn=measure_fn)
        keep = e.setdefault("measurements", {})
        wanted = [mid for mid in METRICS if all_metrics or mid in todo.get(e["id"], [])]
        changed = [mid for mid in wanted if mid in fresh and _same(fresh[mid]) != _same(keep.get(mid))]
        for mid in changed:
            keep[mid] = fresh[mid]
        if changed:
            e["measured_at"] = now()
            done[e["id"]] = changed
    if done:   # an unchanged registry is not rewritten, so its pinned sha256 still holds
        save(path, data)
    return done


def _same(rec: dict[str, Any] | None) -> tuple[Any, ...] | None:
    """What a re-measurement must change for the stored record to be replaced (not the evidence's file paths)."""
    return None if not rec else tuple(rec.get(k) for k in ("metric", "version", "basis", "value", "not_measured"))


def add_entry(path: str | Path, entry_id: str, kind: str, files: dict[str, str | Path], by: str, title: str = "",
              evidence: str = "observed", degrades: str | None = None, how: str = "", reason: str = "",
              ocr: bool = False, measure_fn: Callable[..., dict[str, Any]] | None = None,
              style_frames: list[tuple[str, str | Path]] | None = None) -> dict[str, Any]:
    """Register a film (and its approved style frames, by role). Media paths are stored relative to the registry. A
    previous_best needs a named human; it supersedes the live one."""
    path = Path(path)
    data = load(path) if path.exists() else new_registry(path.stem)
    base = path.parent.resolve()
    if kind not in KINDS:
        raise BenchmarkError(f"kind must be one of {KINDS}")
    if any(e["id"] == entry_id for e in data["entries"]):
        raise BenchmarkError(f"entry {entry_id} already exists")
    if kind == "previous_best" and not is_human_name(by):
        raise BenchmarkError("only a named human may set the accepted baseline (previous_best); agents may not")
    if kind == "previous_best" and not reason.strip():
        raise BenchmarkError("setting the accepted baseline needs the human's reason")
    media = {}

    def stored(value: str | Path) -> dict[str, str]:
        p = Path(value).resolve()
        try:
            relp = p.relative_to(base)
        except ValueError:
            raise BenchmarkError(f"{p} is outside the registry folder {base}; copy it under the registry first") from None
        return {"path": str(relp), "sha256": sha256_file(p)}
    for key, value in files.items():
        if value is None:
            continue
        if key not in MEDIA_KEYS:
            raise BenchmarkError(f"unknown media key {key}")
        media[key] = stored(value)
    e: dict[str, Any] = {"id": entry_id, "kind": kind, "title": title or entry_id, "evidence": evidence, "media": media,
                         "added": {"by": by, "at": now()}}
    if style_frames:
        roles = {"title", "product_ui", "data_callout", "closing", "other"}
        bad = [r for r, _ in style_frames if r not in roles]
        if bad:
            raise BenchmarkError(f"style-frame roles are {', '.join(sorted(roles))}, not {', '.join(bad)}")
        e["style_frames"] = [{"role": r, **stored(p)} for r, p in style_frames]
    if degrades:
        e["degrades"], e["how"] = degrades, how
    if kind == "previous_best":
        e["accepted"] = {"by": by, "at": now(), "reason": reason.strip()}
    e["measurements"] = measure_entry(e, base, ocr=ocr, measure_fn=measure_fn)
    e["measured_at"] = now()
    if kind == "previous_best":
        old = live_baseline(data)
        if old is not None:
            old["superseded_by"] = entry_id
    data["entries"].append(e)
    save(path, data)
    return e


# ------------------------------------------------------------------------------------------------ envelopes and checks
def envelopes(data: dict[str, Any]) -> dict[str, Envelope]:
    """One envelope per metric, from the references that measured it under the current basis. A reference that could
    not measure it (no style frames, no stem) is left out; a reference holding a record of another version or basis
    withholds the envelope until it is re-measured, so an old number never widens or narrows a current envelope."""
    refs = [e for e in data.get("entries", []) if e["kind"] == "reference"]
    policy = data.get("envelope_policy") or {}
    out: dict[str, Envelope] = {}
    for mid, m in METRICS.items():
        recs = [((e.get("measurements") or {}).get(mid), e["id"]) for e in refs]
        if any(r is not None and not current(r) for r, _ in recs):
            continue
        vals = {eid: float(r["value"]) for r, eid in recs if r is not None and r.get("value") is not None}
        if not vals:
            continue
        pol = policy.get(mid, {})
        tol = float(pol.get("tolerance", m.tolerance))
        lo, hi = min(vals.values()) - tol, max(vals.values()) + tol
        if pol.get("floor") is not None:
            lo = max(lo, float(pol["floor"]))
        if pol.get("ceiling") is not None:
            hi = min(hi, float(pol["ceiling"]))
        out[mid] = Envelope(mid, m.version, m.basis_sha, round(lo, 4), round(hi, 4), pol.get("sides", m.sides), tol, vals)
    return out


def role_envelopes(data: dict[str, Any]) -> dict[str, dict[str, Envelope]]:
    """For still.* metrics: one envelope per style-frame role, from the references' own frames of that role (their
    measurement's evidence.per_role), so a title is compared with titles and a product-UI frame with product-UI frames."""
    refs = [e for e in data.get("entries", []) if e["kind"] == "reference"]
    policy = data.get("envelope_policy") or {}
    out: dict[str, dict[str, Envelope]] = {}
    for mid, m in METRICS.items():
        if m.source != "stills":
            continue
        recs = [((e.get("measurements") or {}).get(mid), e["id"]) for e in refs]
        if any(r is not None and not current(r) for r, _ in recs):
            continue
        by_role: dict[str, dict[str, float]] = {}
        for r, eid in recs:
            for role, v in ((r or {}).get("evidence") or {}).get("per_role", {}).items():
                if v is not None:
                    by_role.setdefault(role, {})[eid] = float(v)
        pol = policy.get(mid, {})
        tol = float(pol.get("tolerance", m.tolerance))
        for role, vals in by_role.items():
            lo, hi = min(vals.values()) - tol, max(vals.values()) + tol
            if pol.get("floor") is not None:
                lo = max(lo, float(pol["floor"]))
            if pol.get("ceiling") is not None:
                hi = min(hi, float(pol["ceiling"]))
            out.setdefault(mid, {})[role] = Envelope(mid, m.version, m.basis_sha, round(lo, 4), round(hi, 4),
                                                     pol.get("sides", m.sides), tol, vals)
    return out


def missing_roles(data: dict[str, Any], roles: list[str]) -> dict[str, list[str]]:
    """Per reference, the style-frame roles it lacks (the look gate cannot judge a role no reference shows)."""
    out = {}
    for e in data.get("entries", []):
        if e["kind"] == "reference":
            have = {sf.get("role") for sf in e.get("style_frames") or []}
            lack = [r for r in roles if r not in have]
            if lack:
                out[e["id"]] = lack
    return out


def check(measurements: dict[str, dict[str, Any]], envs: dict[str, Envelope]) -> list[dict[str, Any]]:
    """Each envelope's verdict on a set of measurements: inside, below, above, not_measured or not_comparable."""
    rows = []
    for mid, env in sorted(envs.items()):
        rec = measurements.get(mid)
        row = {"metric": mid, "version": env.version, "basis": env.basis, "envelope": [env.lo, env.hi], "sides": env.sides,
               "value": rec.get("value") if rec else None, "evidence": (rec or {}).get("evidence", {})}
        if not rec or rec.get("value") is None:
            row.update(status="not_measured", why=(rec or {}).get("not_measured", "no measurement"))
        elif (rec.get("version"), rec.get("basis")) != (env.version, env.basis):
            row.update(status="not_comparable", why=f"measured under v{rec.get('version')}/{rec.get('basis')}, the "
                                                     f"envelope is v{env.version}/{env.basis}; re-measure")
        else:
            status, dist = env.judge(float(rec["value"]))
            row.update(status=status, distance=dist)
        rows.append(row)
    return rows


def ratchet(baseline: dict[str, dict[str, Any]], candidate: dict[str, dict[str, Any]], envs: dict[str, Envelope] | None = None,
            waivers: list[dict[str, Any]] | None = None, tolerances: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Metrics on which the candidate is worse than the accepted baseline. A waiver counts only from a named human."""
    envs, waivers, tolerances = envs or {}, waivers or [], tolerances or {}
    waived = {w["metric"]: w for w in waivers if is_human_name(w.get("by")) and str(w.get("reason", "")).strip()}
    rows = []
    for mid, m in METRICS.items():
        b, c = baseline.get(mid), candidate.get(mid)
        if b is None or c is None or (b.get("value") is None and c.get("value") is None):
            continue   # neither side measured it: nothing to compare (a value the candidate lost is still flagged)
        why = comparable(b, c)
        if why:
            rows.append({"metric": mid, "status": "not_comparable", "why": why})
            continue
        tol = float(tolerances.get(mid, envs[mid].tolerance if mid in envs else m.tolerance))
        bv, cv = float(b["value"]), float(c["value"])
        sides = envs[mid].sides if mid in envs else m.sides
        if sides == "lower":
            worse, delta = cv < bv - tol, cv - bv
        elif sides == "upper":
            worse, delta = cv > bv + tol, cv - bv
        elif mid in envs:
            bd, cd = envs[mid].judge(bv)[1], envs[mid].judge(cv)[1]
            worse, delta = cd > bd + tol, cd - bd
        else:
            continue   # a two-sided metric without an envelope has no direction to ratchet
        if not worse:
            continue
        row = {"metric": mid, "baseline": bv, "candidate": cv, "delta": round(delta, 4), "tolerance": tol, "sides": sides,
               "evidence": c.get("evidence", {})}
        if mid in waived:
            row.update(status="waived", waiver={k: waived[mid].get(k) for k in ("by", "reason", "at")})
        else:
            row["status"] = "regressed"
        rows.append(row)
    return rows


def verify(data: dict[str, Any], base: Path | None = None) -> dict[str, Any]:
    """The registry's self-check: no reference is flagged by the envelopes, every anchor is caught, nothing is stale,
    and (given `base`) every registered file is still the registered bytes."""
    envs = envelopes(data)
    flagged, missed, caught = [], [], {}
    for e in data.get("entries", []):
        rows = check(e.get("measurements") or {}, envs)
        out = [r for r in rows if r["status"] in ("below", "above")]
        if e["kind"] == "reference" and out:
            flagged += [f"{e['id']} {r['metric']} {r['value']} outside {r['envelope']}" for r in out]
        if e["kind"] == "degraded_anchor":
            if out:
                caught[e["id"]] = [r["metric"] for r in out]
            else:
                missed.append(e["id"])
    problems = []
    if base is not None:
        for e in data.get("entries", []):
            problems += media_problems(e, base)
    report = {"envelopes": {k: asdict(v) for k, v in sorted(envs.items())}, "references_flagged": flagged,
              "anchors_caught": caught, "anchors_missed": missed, "stale": stale(data), "media_problems": problems}
    report["ok"] = not flagged and not missed and not problems and bool(envs)
    return report
