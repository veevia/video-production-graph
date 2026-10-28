"""The spec family: versioned creative specifications with rule ids, design tokens, lint, diff and gates.

A production's spec family lives in its own folder, `<project>/spec/` (not `controls/`, where `DESIGN.md` and the
control document `design.md` would be one file on a case-insensitive file system):

  DESIGN.md + DESIGN.tokens.json   ground and figure, colour roles, type scale, grid, product-UI scale, components
  MOTION.md                        durations, easing, transitions, holds, motion share, reduced motion
  STORY.md                         beats as a share of runtime, one idea per scene, must-say terms, banned phrases
  VOICE.md                         persona, pace band, pause budget, emphasis, lexicon mapped to TTS parameters
  MUSIC.md                         role under the voice, sections, hit points, energy, tempo and key, references
  MIX.md                           loudness, true peak, the voice-over-music floor on a fixed basis, ducking
  GATES.yaml                       per gate: metric id and version, envelope, severity, stage, rule ids

Each Markdown document has YAML front matter (the machine contract) and a prose body (the intent); DESIGN.tokens.json
follows the W3C Design Tokens format (2025.10) and holds exact values. Every rule has an id, MUST or SHOULD, a
statement, a rationale, a source (the feedback or benchmark it answers), an enforcement (tool, judge or human) and Do
and Don't examples. A rule is never deleted: retiring it keeps it with `status: retired`, so a diff can say what
changed and why. `lint` keeps the family honest, `diff` names the rules that changed, and `family_hash` identifies the
version a gate approved.

The family is opt-in: nothing in the default workflow reads it. The existing control documents keep working.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .metrics import METRICS
from .util import PKG_ROOT, sha256_bytes, sha256_json

SPEC_FAMILY = "vpg-spec/v1"
SPEC_DIR = "spec"   # <project>/spec/: never controls/, where DESIGN.md and design.md are one file on a case-free file system
FILES: dict[str, str] = {"design": "DESIGN.md", "tokens": "DESIGN.tokens.json", "motion": "MOTION.md", "story": "STORY.md",
                         "voice": "VOICE.md", "music": "MUSIC.md", "mix": "MIX.md", "gates": "GATES.yaml"}
# Control-document ids when a workflow registers the family with the control store; distinct from the six base docs.
CONTROL_IDS: dict[str, str] = {doc: f"spec-{doc}" for doc in FILES}
PREFIX = {"design": "DS", "motion": "MO", "story": "ST", "voice": "VO", "music": "MU", "mix": "MX", "gates": "GT"}
# The earliest gate a change to each document re-opens (quality workflow): the look gate renders the visual spec,
# the animatic times the story, voice, music and mix.
STAGE_OF_DOC = {"design": "look", "tokens": "look", "motion": "look", "story": "animatic", "voice": "animatic",
                "music": "animatic", "mix": "animatic"}
STAGES = ("look", "animatic", "build", "release")
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}
# Which metric sources each stage can measure: the look gate sees stills and its motion test, the animatic a scratch
# read and a music sketch, a build the master, its stems and its text layers.
STAGE_SOURCES = {"look": {"stills"}, "animatic": {"voice", "voice+music", "voice+script"},
                 "build": {"master", "voice", "voice+music", "voice+script", "text_layers"},
                 "release": {"master", "voice", "voice+music", "voice+script", "text_layers"}}
RULE_ID = re.compile(r"^(DS|MO|ST|VO|MU|MX|GT)-[A-Z0-9]{2,12}-\d{3}$")
PLACEHOLDER = re.compile(r"\bTBD\b|<fill[^>]*>|\{\{[^}]+\}\}")
ALIAS = re.compile(r"^\{([A-Za-z0-9_.-]+)\}$")
TEMPLATES = PKG_ROOT / "templates" / "spec"
REQUIRED_BODY = {"design": ["Intent", "Do and don't"], "motion": ["Intent"], "story": ["Intent"], "voice": ["Intent"],
                 "music": ["Intent"], "mix": ["Intent"]}
GROWTH_WARN = 5   # more new rules than this in one version, with none retired, is the "rules only accumulate" smell


class SpecError(ValueError):
    pass


@dataclass
class Issue:
    level: str          # "error" or "warning"
    doc: str
    message: str
    rule: str = ""

    def line(self) -> str:
        return f"{self.level}: {self.doc}{('/' + self.rule) if self.rule else ''}: {self.message}"


@dataclass
class Family:
    root: Path
    docs: dict[str, dict[str, Any]] = field(default_factory=dict)   # doc → {path, sha256, front_matter, body}
    tokens: dict[str, Any] = field(default_factory=dict)

    def fm(self, doc: str) -> dict[str, Any]:
        return (self.docs.get(doc) or {}).get("front_matter") or {}

    def rules(self) -> dict[str, dict[str, Any]]:
        out = {}
        for doc in FILES:
            for r in self.fm(doc).get("rules") or []:
                if isinstance(r, dict) and r.get("id"):
                    out[r["id"]] = {**r, "doc": doc}
        return out

    def gates(self) -> list[dict[str, Any]]:
        return [g for g in (self.fm("gates").get("gates") or []) if isinstance(g, dict)]


# ------------------------------------------------------------------------------------------------ reading
def _split(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    m = re.search(r"\n---\s*\n", text[3:])
    if not m:
        return {}, text
    return (yaml.safe_load(text[3:3 + m.start()]) or {}), text[3 + m.end():]


def read_doc(path: Path, doc: str) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if doc == "tokens":
        fm, body = json.loads(text), ""
    elif path.suffix in (".yaml", ".yml"):
        fm, body = (yaml.safe_load(text) or {}), ""
    else:
        fm, body = _split(text)
    return {"path": path, "sha256": sha256_bytes(raw), "front_matter": fm if isinstance(fm, dict) else {}, "body": body}


def load_family(root: str | Path) -> Family:
    root = Path(root)
    fam = Family(root=root)
    for doc, name in FILES.items():
        p = root / name
        if p.is_file():
            try:
                fam.docs[doc] = read_doc(p, doc)
            except (ValueError, yaml.YAMLError) as exc:
                raise SpecError(f"{name}: cannot be parsed: {exc}") from None
    fam.tokens = fam.fm("tokens")
    return fam


def family_hash(fam: Family) -> str:
    """The identity of one version of the family: every present document's name and sha256."""
    return sha256_json({FILES[d]: fam.docs[d]["sha256"] for d in sorted(fam.docs)})


def sections(body: str) -> dict[str, str]:
    out, current, buf = {}, None, []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


# ------------------------------------------------------------------------------------------------ tokens (DTCG 2025.10)
FONT_WEIGHTS = {"thin", "hairline", "extra-light", "ultra-light", "light", "normal", "regular", "book", "medium",
                "semi-bold", "demi-bold", "bold", "extra-bold", "ultra-bold", "black", "heavy", "extra-black", "ultra-black"}


def flatten_tokens(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Token path → {"type", "value", "description"}; a token inherits `$type` from its nearest group."""
    out: dict[str, dict[str, Any]] = {}

    def walk(node: dict[str, Any], path: list[str], inherited: str | None) -> None:
        here = node.get("$type", inherited)
        if "$value" in node:
            out[".".join(path)] = {"type": here, "value": node["$value"], "description": node.get("$description", "")}
            return
        for key, child in node.items():
            if not key.startswith("$") and isinstance(child, dict):
                walk(child, path + [key], here)
    walk(tree or {}, [], None)
    return out


def _alias(value: Any) -> str | None:
    m = ALIAS.match(value) if isinstance(value, str) else None
    return m.group(1) if m else None


def resolve(tokens: dict[str, dict[str, Any]], path: str, seen: tuple[str, ...] = ()) -> Any:
    if path in seen:
        raise SpecError(f"circular token reference: {' -> '.join(seen + (path,))}")
    if path not in tokens:
        raise SpecError(f"unknown token {{{path}}}")
    value = tokens[path]["value"]
    target = _alias(value)
    return resolve(tokens, target, seen + (path,)) if target else value


def _token_type_errors(kind: str | None, value: Any, tokens: dict[str, dict[str, Any]]) -> list[str]:
    if _alias(value):
        return []
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731

    def dim(v: Any, units: tuple[str, ...]) -> bool:
        return isinstance(v, dict) and num(v.get("value")) and v.get("unit") in units or bool(_alias(v))

    if kind is None:
        return ["no $type on the token or its groups (DTCG: tools must not infer a type)"]
    if kind == "color":
        if not (isinstance(value, dict) and isinstance(value.get("colorSpace"), str) and isinstance(value.get("components"), list)):
            return ["a color $value needs colorSpace and components"]
        comps = value["components"]
        if value["colorSpace"] in ("srgb", "srgb-linear", "display-p3") and (
                len(comps) != 3 or not all(c == "none" or (num(c) and 0 <= c <= 1) for c in comps)):
            return ["srgb components are three numbers from 0 to 1"]
        if "alpha" in value and not (num(value["alpha"]) and 0 <= value["alpha"] <= 1):
            return ["alpha is a number from 0 to 1"]
        if "hex" in value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(value["hex"])):
            return ["hex is a six-digit #RRGGBB fallback"]
        return []
    if kind == "dimension":
        return [] if dim(value, ("px", "rem")) else ["a dimension $value is {value, unit: px|rem}"]
    if kind == "duration":
        return [] if dim(value, ("ms", "s")) else ["a duration $value is {value, unit: ms|s}"]
    if kind == "number":
        return [] if num(value) else ["a number $value is a number"]
    if kind == "fontFamily":
        ok = isinstance(value, str) or (isinstance(value, list) and value and all(isinstance(v, str) for v in value))
        return [] if ok else ["a fontFamily $value is a name or a list of names"]
    if kind == "fontWeight":
        return [] if (num(value) and 1 <= value <= 1000) or value in FONT_WEIGHTS else ["fontWeight is 1-1000 or a DTCG keyword"]
    if kind == "cubicBezier":
        ok = isinstance(value, list) and len(value) == 4 and all(num(v) for v in value) and 0 <= value[0] <= 1 and 0 <= value[2] <= 1
        return [] if ok else ["a cubicBezier $value is [x1, y1, x2, y2] with x1 and x2 from 0 to 1"]
    if kind == "typography":
        if not isinstance(value, dict):
            return ["a typography $value is an object"]
        errs = []
        for key, sub in (("fontFamily", "fontFamily"), ("fontSize", "dimension"), ("fontWeight", "fontWeight"),
                         ("letterSpacing", "dimension"), ("lineHeight", "number")):
            if key not in value:
                errs.append(f"typography needs {key}")
            else:
                errs += [f"{key}: {e}" for e in _token_type_errors(sub, value[key], tokens)]
        return errs
    return [f"unsupported $type {kind} (the spec family uses color, dimension, duration, number, fontFamily, "
            "fontWeight, cubicBezier and typography)"]


def token_issues(tree: dict[str, Any]) -> list[Issue]:
    tokens = flatten_tokens(tree)
    out = []
    if not tokens:
        out.append(Issue("error", "tokens", "no tokens"))
    for path, tok in tokens.items():
        for e in _token_type_errors(tok["type"], tok["value"], tokens):
            out.append(Issue("error", "tokens", f"{path}: {e}"))
        refs = [tok["value"]] if _alias(tok["value"]) else (
            [v for v in tok["value"].values() if _alias(v)] if isinstance(tok["value"], dict) else [])
        for ref in refs:
            try:
                resolve(tokens, _alias(ref), (path,))
            except SpecError as exc:
                out.append(Issue("error", "tokens", f"{path}: {exc}"))
        if PLACEHOLDER.search(json.dumps(tok)):
            out.append(Issue("error", "tokens", f"{path}: unfilled <fill: ...> marker"))
    return out


def _refs_in(obj: Any) -> list[str]:
    if isinstance(obj, str):
        a = _alias(obj)
        return [a] if a else []
    if isinstance(obj, dict):
        return [r for k, v in obj.items() if k != "rules" for r in _refs_in(v)]
    if isinstance(obj, list):
        return [r for v in obj for r in _refs_in(v)]
    return []


# ------------------------------------------------------------------------------------------------ lint
def _rule_issues(doc: str, r: dict[str, Any], fam: Family, seen: dict[str, str]) -> list[Issue]:
    rid = str(r.get("id", ""))
    out: list[Issue] = []
    bad = lambda msg: out.append(Issue("error", doc, msg, rid))  # noqa: E731
    if not RULE_ID.match(rid):
        bad(f"rule id must look like {PREFIX.get(doc, 'XX')}-NAME-001")
    elif not rid.startswith(PREFIX.get(doc, "") + "-"):
        bad(f"a {doc} rule id starts with {PREFIX[doc]}-")
    if rid in seen:
        bad(f"duplicate rule id (also in {seen[rid]})")
    seen[rid] = doc
    if r.get("level") not in ("MUST", "SHOULD"):
        bad("level is MUST or SHOULD")
    for key in ("statement", "rationale", "source"):
        if not str(r.get(key) or "").strip():
            bad(f"{key} is required (every rule says what, why and which feedback or benchmark it answers)")
    enf = r.get("enforcement") or {}
    kind = enf.get("type")
    if kind not in ("tool", "judge", "human"):
        bad("enforcement.type is tool, judge or human")
    if kind == "tool":
        if enf.get("metric"):
            if enf["metric"] not in METRICS:
                bad(f"enforcement.metric {enf['metric']} is not a known metric (`vpg measure --catalog`)")
        elif enf.get("check"):
            mod, _, fn = str(enf["check"]).partition(":")
            try:
                from .graph import resolve_tool
                resolve_tool(enf["check"])
            except Exception:   # noqa: BLE001
                bad(f"enforcement.check {enf['check']} does not resolve to a function ({mod}:{fn})")
        else:
            bad("a tool enforcement names a metric or a check (module:function)")
        if enf.get("gate") and enf["gate"] not in {g.get("id") for g in fam.gates()}:
            bad(f"enforcement.gate {enf['gate']} is not a gate in GATES.yaml")
    ex = r.get("examples") or {}
    if r.get("status", "active") == "active" and r.get("level") == "MUST" and not (ex.get("do") or ex.get("dont")):
        bad("a MUST rule needs Do or Don't examples: they mark its boundary")
    if r.get("status", "active") not in ("active", "retired"):
        bad("status is active or retired")
    if r.get("status") == "retired" and not str(r.get("retired_reason") or "").strip():
        bad("a retired rule keeps its retired_reason")
    if PLACEHOLDER.search(json.dumps(r, default=str)):
        bad("unfilled <fill: ...> marker")
    return out


def registry_ref(fam: Family) -> tuple[Path | None, str | None]:
    """The benchmark registry GATES.yaml names, and the sha256 it pins (None when only a path is given)."""
    ref = fam.fm("gates").get("benchmarks")
    if isinstance(ref, dict):
        return ((fam.root / ref["path"]).resolve() if ref.get("path") else None), ref.get("sha256")
    if isinstance(ref, str) and ref.strip() and not PLACEHOLDER.search(ref):
        return (fam.root / ref).resolve(), None
    return None, None


def registry_problem(fam: Family, require_pin: bool = True) -> str:
    """Why the registry cannot be used as the approved one ('' when it can): missing, unpinned, or changed since GATES.yaml
    pinned it. A pinned registry is part of the approved bar: changing it, the baseline in it included, is a change
    request on spec-gates."""
    from .util import sha256_file
    path, pin = registry_ref(fam)
    if path is None:
        return "GATES.yaml names no benchmark registry"
    if not path.is_relative_to(fam.root.resolve().parent):
        return f"the benchmark registry {path.name} is outside the project folder; keep it inside (e.g. benchmarks/)"
    if not path.is_file():
        return f"the benchmark registry {path.name} is missing"
    if pin is None:
        return "GATES.yaml does not pin the registry's sha256 (benchmarks: {path, sha256})" if require_pin else ""
    if sha256_file(path) != pin:
        return ("the benchmark registry changed since GATES.yaml pinned it; re-pin it through a change request on "
                "spec-gates (the registry, its baseline included, is part of the approved bar)")
    return ""


def bar_settings(fam: Family) -> dict[str, Any]:
    """GATES.yaml's settings beside the gates: player_width_px, waivers, the registry pin."""
    return fam.fm("gates")


def load_registry(fam: Family, require_pin: bool = True) -> tuple[dict[str, Any] | None, str]:
    """The benchmark registry GATES.yaml pins, loaded, or None and why it cannot be used (missing, unpinned, changed
    since it was pinned, or invalid)."""
    from .benchmarks import BenchmarkError, load
    why = registry_problem(fam, require_pin=require_pin)
    if why:
        return None, why
    path, _ = registry_ref(fam)
    try:
        return load(path), ""
    except BenchmarkError as exc:
        return None, str(exc)


def beat_shares(story: dict[str, Any], timed: list[tuple[str | None, float]]) -> list[dict[str, Any]]:
    """Each STORY.md beat's share of the runtime against its planned share. `timed` is (script chapter, seconds) per
    storyboard or animatic beat; a story beat covers the chapters it names, or the chapter named like the beat."""
    total = sum(d for _, d in timed)
    tol = float(story.get("beat_tolerance", 0.05))
    rows = []
    for sb in story.get("beats") or []:
        if not isinstance(sb, dict):
            continue
        chapters = set(sb.get("chapters") or [sb.get("id")])
        got = sum(d for ch, d in timed if ch in chapters) / total if total else 0.0
        want = float(sb.get("share", 0))
        rows.append({"beat": sb.get("id"), "chapters": sorted(c for c in chapters if c), "share": round(got, 3),
                     "spec": want, "tolerance": tol, "status": "pass" if abs(got - want) <= tol else "fail"})
    return rows


def _story_issues(fm: dict[str, Any]) -> list[Issue]:
    out: list[Issue] = []
    beats = [b for b in fm.get("beats") or [] if isinstance(b, dict)]
    if not beats:
        return [Issue("error", "story", "beats: lists the story beats, each with an id and its share of the runtime")]
    ids = [b.get("id") for b in beats]
    if len(set(ids)) != len(ids) or not all(ids):
        out.append(Issue("error", "story", "every beat needs a distinct id"))
    try:
        total = sum(float(b.get("share", 0)) for b in beats)
    except (TypeError, ValueError):
        return out + [Issue("error", "story", "every beat share is a number")]
    if abs(total - 1.0) > 0.02:
        out.append(Issue("error", "story", f"the beat shares add up to {total:.2f}, not 1"))
    rt = fm.get("runtime_s") or {}
    if isinstance(rt, dict) and isinstance(rt.get("target"), (int, float)) and isinstance(rt.get("max"), (int, float)) \
            and rt["max"] < rt["target"]:
        out.append(Issue("error", "story", "runtime_s.max is below runtime_s.target"))
    return out


def _mix_issues(fm: dict[str, Any]) -> list[Issue]:
    lo = fm.get("loudness")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    if not isinstance(lo, dict) or not num(lo.get("integrated_lufs")) or not num(lo.get("true_peak_max_dbtp")):
        return [Issue("error", "mix", "loudness: needs integrated_lufs and true_peak_max_dbtp (numbers); the animatic's "
                                      "loudness pre-check and the bar check read them")]
    if "precheck_max_limiting_db" in lo and not (num(lo["precheck_max_limiting_db"]) and lo["precheck_max_limiting_db"] >= 0):
        return [Issue("error", "mix", "loudness.precheck_max_limiting_db is a number of dB, 0 or more")]
    return []


def _gate_issues(fam: Family, rules: dict[str, dict[str, Any]]) -> list[Issue]:
    from .util import is_human_name
    out: list[Issue] = []
    fm = fam.fm("gates")
    ids = []
    for g in fam.gates():
        gid = str(g.get("id", ""))
        bad = lambda msg, gid=gid: out.append(Issue("error", "gates", msg, gid))  # noqa: E731
        if not RULE_ID.match(gid) or not gid.startswith("GT-"):
            bad("gate id must look like GT-NAME-001")
        if gid in ids:
            bad("duplicate gate id")
        ids.append(gid)
        m = METRICS.get(g.get("metric"))
        if m is None:
            bad(f"unknown metric {g.get('metric')}")
        elif g.get("version") != m.version:
            bad(f"pinned to {g.get('metric')} v{g.get('version')}, but the metric is v{m.version}: its basis changed, so "
                "re-measure the benchmarks and baseline, then re-pin the gate")
        elif g.get("stage") in STAGE_SOURCES and m.source not in STAGE_SOURCES[g["stage"]]:
            bad(f"a {g['stage']}-stage gate cannot measure {g.get('metric')} (it needs {m.source}); the look gate measures "
                "still.* metrics, the animatic audio metrics")
        if "roles" in g:
            groles, asked = g.get("roles"), style_frame_roles(fam)
            if m is not None and m.source != "stills":
                bad("roles: only a still.* gate is judged per style-frame role")
            elif not (isinstance(groles, list) and groles and all(isinstance(r, str) for r in groles)
                      and len(set(groles)) == len(groles)):
                bad("roles is a list of distinct style-frame roles, e.g. [product_ui]")
            elif [r for r in groles if r not in asked]:
                bad(f"judges the {', '.join(r for r in groles if r not in asked)} frame, which DESIGN.md does not ask "
                    f"for (style_frames: {', '.join(asked)})")
        if g.get("severity") not in ("P1", "P2", "P3", "nit"):
            bad("severity is P1, P2, P3 or nit")
        if g.get("stage") not in STAGES:
            bad(f"stage is one of {', '.join(STAGES)}")
        env = g.get("envelope")
        if env == "benchmark":
            if not fm.get("benchmarks"):
                bad("envelope: benchmark needs `benchmarks:` (the registry path) in GATES.yaml")
        elif not (isinstance(env, dict) and ("lo" in env or "hi" in env)):
            bad("envelope is `benchmark` or {lo, hi} (either bound may be omitted)")
        for rid in g.get("rules") or []:
            if rid not in rules:
                bad(f"names rule {rid}, which no spec document defines")
        if not g.get("rules"):
            bad("a gate names the rule ids it enforces")
    ref = fm.get("benchmarks")
    if isinstance(ref, dict) and not (ref.get("path") and re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256", "")))):
        out.append(Issue("error", "gates", "benchmarks as a mapping is {path, sha256} with the registry's 64-hex sha256"))
    path, _ = registry_ref(fam)
    if path is not None and not path.is_relative_to(fam.root.resolve().parent):
        out.append(Issue("error", "gates", "the benchmark registry must be inside the project folder (the folder holding spec/)"))
    elif path is not None and not path.is_file():
        out.append(Issue("warning", "gates", f"benchmark registry {path.name} is not there yet"))
    for w in fm.get("waivers") or []:
        wid = str((w or {}).get("metric", ""))
        if wid not in METRICS:
            out.append(Issue("error", "gates", f"waiver names unknown metric {wid}"))
        if not is_human_name((w or {}).get("by")):
            out.append(Issue("error", "gates", f"a waiver of {wid} must name the human who granted it (by)"))
        if not str((w or {}).get("reason") or "").strip():
            out.append(Issue("error", "gates", f"a waiver of {wid} needs the human's reason"))
    if PLACEHOLDER.search(json.dumps(fm, default=str)):
        out.append(Issue("error", "gates", "unfilled <fill: ...> marker"))
    return out


def lint(fam: Family, previous: Family | None = None, require: tuple[str, ...] = ("design", "tokens", "gates"),
         only: tuple[str, ...] | None = None) -> list[Issue]:
    """Errors block an approval; warnings are shown. `previous` adds the version and growth checks. `only` keeps the
    issues of those documents (a writer validates what it writes, not what other nodes own)."""
    out = _lint(fam, previous, require)
    return [i for i in out if i.doc in only] if only else out


def _lint(fam: Family, previous: Family | None, require: tuple[str, ...]) -> list[Issue]:
    out: list[Issue] = []
    for doc in require:
        if doc not in fam.docs:
            out.append(Issue("error", doc, f"{FILES[doc]} is missing"))
    seen: dict[str, str] = {}
    for doc, info in fam.docs.items():
        fm = info["front_matter"]
        if doc != "tokens":
            spec = fm.get("spec") or {}
            if spec.get("family") != SPEC_FAMILY or spec.get("doc") != doc or not isinstance(spec.get("version"), int):
                out.append(Issue("error", doc, f"front matter needs spec: {{family: {SPEC_FAMILY}, doc: {doc}, version: <int>}}"))
            for r in fm.get("rules") or []:
                out += _rule_issues(doc, r if isinstance(r, dict) else {}, fam, seen)
            have = sections(info["body"])
            for s in REQUIRED_BODY.get(doc, []):
                if s not in have or not have[s].strip():
                    out.append(Issue("error", doc, f"the prose section '## {s}' is missing or empty"))
            if doc != "gates" and PLACEHOLDER.search(info["body"]):
                out.append(Issue("error", doc, "the prose still has unfilled <fill: ...> markers"))
            if doc != "gates":
                stripped = {k: v for k, v in fm.items() if k != "rules"}
                if PLACEHOLDER.search(json.dumps(stripped, default=str)):
                    out.append(Issue("error", doc, "the front matter still has unfilled <fill: ...> markers"))
    if "tokens" in fam.docs:
        out += token_issues(fam.tokens)
        known = flatten_tokens(fam.tokens)
        for doc in fam.docs:
            if doc == "tokens":
                continue
            for ref in _refs_in(fam.fm(doc)):
                if ref not in known:
                    out.append(Issue("error", doc, f"refers to unknown token {{{ref}}}"))
    if "story" in fam.docs:
        out += _story_issues(fam.fm("story"))
    if "mix" in fam.docs:
        out += _mix_issues(fam.fm("mix"))
    rules = fam.rules()
    if "gates" in fam.docs:
        out += _gate_issues(fam, rules)
    for rid, r in rules.items():
        gate = (r.get("enforcement") or {}).get("gate")
        metric = (r.get("enforcement") or {}).get("metric")
        if gate and metric:
            g = next((g for g in fam.gates() if g.get("id") == gate), None)
            if g and g.get("metric") != metric:
                out.append(Issue("error", r["doc"], f"enforced by gate {gate}, which measures {g.get('metric')}, not {metric}", rid))
    if previous is not None:
        d = diff(previous, fam)
        for doc, change in d["docs"].items():
            if change["changed"] and change["old_version"] == change["new_version"] and change["old_version"] is not None:
                out.append(Issue("error", doc, f"content changed but spec.version is still {change['old_version']}; bump it"))
            if (change["old_version"] or 0) > (change["new_version"] or 0):
                out.append(Issue("error", doc, "spec.version went backwards"))
        added, retired = len(d["rules"]["added"]), len(d["rules"]["retired"]) + len(d["rules"]["removed"])
        if added > GROWTH_WARN and not retired:
            out.append(Issue("warning", "family", f"{added} rules added and none retired: rules only accumulate; merge or "
                                                  "retire rules a new one replaces"))
        for rid in d["rules"]["removed"]:
            out.append(Issue("error", previous.rules()[rid]["doc"], "a rule was deleted; retire it instead "
                                                                    "(status: retired, retired_reason)", rid))
    return out


# ------------------------------------------------------------------------------------------------ diff
_RULE_FIELDS = ("level", "statement", "rationale", "source", "enforcement", "examples", "status")


def diff(old: Family, new: Family) -> dict[str, Any]:
    """What changed between two versions: documents, rules (added, modified with fields, retired, removed),
    tokens (added, removed, changed values) and gates. `stages` lists the earliest gate each change re-opens."""
    docs = {}
    for doc in FILES:
        a, b = old.docs.get(doc), new.docs.get(doc)
        if a is None and b is None:
            continue
        ver = lambda x: ((x or {}).get("front_matter") or {}).get("spec", {}).get("version") if x else None  # noqa: E731
        docs[doc] = {"old_version": ver(a), "new_version": ver(b), "changed": (a or {}).get("sha256") != (b or {}).get("sha256")}
    ro, rn = old.rules(), new.rules()
    added = sorted(set(rn) - set(ro))
    removed = sorted(set(ro) - set(rn))
    modified, retired = [], []
    for rid in sorted(set(ro) & set(rn)):
        fields = [f for f in _RULE_FIELDS if ro[rid].get(f) != rn[rid].get(f)]
        if rn[rid].get("status") == "retired" and ro[rid].get("status", "active") != "retired":
            retired.append(rid)
        elif fields:
            modified.append({"id": rid, "fields": fields, "doc": rn[rid]["doc"]})
    to, tn = flatten_tokens(old.tokens), flatten_tokens(new.tokens)
    tokens = {"added": sorted(set(tn) - set(to)), "removed": sorted(set(to) - set(tn)),
              "changed": [{"path": p, "old": to[p]["value"], "new": tn[p]["value"]} for p in sorted(set(to) & set(tn))
                          if to[p]["value"] != tn[p]["value"] or to[p]["type"] != tn[p]["type"]]}
    go, gn = {g.get("id"): g for g in old.gates()}, {g.get("id"): g for g in new.gates()}
    gates = {"added": sorted(set(gn) - set(go)), "removed": sorted(set(go) - set(gn)),
             "changed": sorted(g for g in set(go) & set(gn) if go[g] != gn[g])}
    stages: set[str] = set()
    for rid in added + removed + retired + [m["id"] for m in modified]:
        doc = (rn.get(rid) or ro.get(rid))["doc"]
        stages.add(STAGE_OF_DOC.get(doc, "build"))
    if tokens["added"] or tokens["removed"] or tokens["changed"]:
        stages.add("look")
    for gid in gates["added"] + gates["removed"] + gates["changed"]:
        stages.add(str((gn.get(gid) or go.get(gid)).get("stage", "build")))
    for doc, change in docs.items():
        if change["changed"] and doc in STAGE_OF_DOC:
            stages.add(STAGE_OF_DOC[doc])
    ordered = sorted(stages, key=lambda s: STAGE_ORDER.get(s, 99))
    return {"docs": docs, "rules": {"added": added, "modified": modified, "retired": retired, "removed": removed},
            "tokens": tokens, "gates": gates, "stages": ordered, "earliest_stage": ordered[0] if ordered else None,
            "old_hash": family_hash(old), "new_hash": family_hash(new)}


def diff_lines(d: dict[str, Any]) -> list[str]:
    out = []
    r = d["rules"]
    out += [f"rule {rid}: added" for rid in r["added"]]
    out += [f"rule {m['id']}: modified ({', '.join(m['fields'])})" for m in r["modified"]]
    out += [f"rule {rid}: retired" for rid in r["retired"]]
    out += [f"rule {rid}: deleted (retire rules instead)" for rid in r["removed"]]
    t = d["tokens"]
    out += [f"token {p}: added" for p in t["added"]] + [f"token {p}: removed" for p in t["removed"]]
    out += [f"token {c['path']}: {json.dumps(c['old'])} -> {json.dumps(c['new'])}" for c in t["changed"]]
    g = d["gates"]
    out += [f"gate {x}: added" for x in g["added"]] + [f"gate {x}: removed" for x in g["removed"]]
    out += [f"gate {x}: changed" for x in g["changed"]]
    out += [f"{doc}: v{c['old_version']} -> v{c['new_version']}" for doc, c in d["docs"].items() if c["changed"]]
    if d["earliest_stage"]:
        out.append(f"re-opens from the {d['earliest_stage']} gate ({', '.join(d['stages'])})")
    return out or ["no change"]


# ------------------------------------------------------------------------------------------------ gates
STYLE_FRAME_ROLES = ("title", "product_ui", "data_callout", "closing")   # when DESIGN.md names no style_frames


def style_frame_roles(fam: Family) -> list[str]:
    """The style-frame roles DESIGN.md asks the look gate to show (the template's four when it names none)."""
    roles = (fam.fm("design") or {}).get("style_frames")
    return [str(r) for r in roles] if isinstance(roles, list) and roles else list(STYLE_FRAME_ROLES)


def _role_gap(registry: dict[str, Any] | None, mid: str, role: str) -> str:
    """Why no reference gives a still metric a value for one role."""
    refs = [e for e in (registry or {}).get("entries", []) if e.get("kind") == "reference"]
    if not any(sf.get("role") == role for e in refs for sf in e.get("style_frames") or []):
        return f"no reference shows a {role} style frame"
    return (f"no reference's {role} style frame gives {mid} a value"
            + (" (too little confidently read text, or it was measured without OCR)" if ".ocr_" in mid else ""))


def gate_envelope(g: dict[str, Any], registry_envelopes: dict[str, Any]) -> tuple[float | None, float | None, str] | None:
    env = g.get("envelope")
    if env == "benchmark":
        e = registry_envelopes.get(g.get("metric"))
        return (e.lo, e.hi, e.sides) if e is not None else None
    if isinstance(env, dict):
        lo, hi = env.get("lo"), env.get("hi")
        sides = "both" if lo is not None and hi is not None else ("lower" if lo is not None else "upper")
        return (lo, hi, sides)
    return None


def evaluate_gates(fam: Family, measurements: dict[str, dict[str, Any]], stage: str | None = None,
                   registry: dict[str, Any] | None = None, roles: list[str] | None = None) -> list[dict[str, Any]]:
    """Every gate (of one stage, or all) on a set of measurements: pass, fail, not_measured, not_comparable or
    no_envelope. A gate pinned to another metric version is not_comparable until it is re-pinned. A still.* gate with a
    benchmark envelope is judged once per style-frame role (`roles`), each against the same role in the references. A
    still.* gate that names its own `roles` is judged on exactly those, whatever its envelope; a role with no frame or no
    value cannot be judged."""
    from .benchmarks import envelopes as reg_envelopes, role_envelopes
    envs = reg_envelopes(registry) if registry else {}
    by_role = role_envelopes(registry) if registry else {}
    rows = []
    for g in fam.gates():
        if stage and g.get("stage") != stage:
            continue
        mid = g.get("metric")
        m = METRICS.get(mid)
        rec = measurements.get(mid) or {}
        base = {"gate": g.get("id"), "metric": mid, "severity": g.get("severity"), "stage": g.get("stage"),
                "rules": list(g.get("rules") or [])}
        own = [str(r) for r in g.get("roles") or []] if isinstance(g.get("roles"), list) else []
        judged = own or (list(roles or []) if g.get("envelope") == "benchmark" else [])
        if m is not None and m.source == "stills" and judged:
            per = (rec.get("evidence") or {}).get("per_role") or {}
            for role in judged:
                row = {**base, "role": role, "value": per.get(role), "evidence": {"role": role}}
                if g.get("envelope") == "benchmark":
                    e = (by_role.get(mid) or {}).get(role)
                    env = (e.lo, e.hi, e.sides) if e else None
                else:
                    env = gate_envelope(g, envs)
                gap = rec.get("not_measured") or (f"the {role} frame gives {mid} no value"
                                                  + (" (too little confidently read text)" if ".ocr_" in mid else ""))
                rows.append(_judge_row(row, g, m, rec, env, value=per.get(role), why_none=_role_gap(registry, mid, role),
                                       why_unmeasured=gap))
            continue
        row = {**base, "value": rec.get("value"), "evidence": rec.get("evidence", {})}
        rows.append(_judge_row(row, g, m, rec, gate_envelope(g, envs), value=rec.get("value")))
    return rows


def _judge_row(row: dict[str, Any], g: dict[str, Any], m: Any, rec: dict[str, Any],
               env: tuple[float | None, float | None, str] | None, value: Any, why_none: str = "",
               why_unmeasured: str = "") -> dict[str, Any]:
    if m is None or g.get("version") != m.version:
        row.update(status="not_comparable", why=f"the gate is pinned to v{g.get('version')}; re-pin after re-measuring")
    elif env is None:
        row.update(status="no_envelope", why=why_none or "no benchmark envelope for this metric (verify the registry)")
    elif value is None:
        row.update(status="not_measured", why=why_unmeasured or rec.get("not_measured", "no measurement"))
    elif (rec.get("version"), rec.get("basis")) != (m.version, m.basis_sha):
        row.update(status="not_comparable", why="measured under another version or basis; re-measure")
    else:
        lo, hi, sides = env
        v = float(value)
        low = sides in ("both", "lower") and lo is not None and v < lo
        high = sides in ("both", "upper") and hi is not None and v > hi
        row.update(envelope=[lo, hi], sides=sides, status="fail" if (low or high) else "pass",
                   direction="below" if low else ("above" if high else ""))
    return row


def blocking(rows: list[dict[str, Any]], blocking_severities: tuple[str, ...] = ("P1", "P2", "P3")) -> list[dict[str, Any]]:
    """Rows that stop a gate: a failed blocking gate, or a blocking gate that could not be judged."""
    return [r for r in rows if r["severity"] in blocking_severities and r["status"] != "pass"]


# ------------------------------------------------------------------------------------------------ init and seeding
def init_family(dest: str | Path, from_controls: str | Path | None = None, force: bool = False) -> list[Path]:
    """Copy the templates into `dest`; with `from_controls`, seed tokens and bands from the existing control documents."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name in FILES.values():
        target = dest / name
        if target.exists() and not force:
            raise SpecError(f"{target} exists (use --force to overwrite)")
        target.write_bytes((TEMPLATES / name).read_bytes())
        written.append(target)
    if from_controls:
        seed_from_controls(dest, Path(from_controls))
    return written


def _hex_to_color(hexv: str) -> dict[str, Any]:
    h = hexv.lstrip("#")
    comps = [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)]
    return {"colorSpace": "srgb", "components": comps, "hex": "#" + h.lower()}


def seed_from_controls(dest: Path, controls: Path) -> dict[str, Any]:
    """Carry the existing control documents' machine contract into the family: design.md's palette and fonts become
    tokens, production.md's narration_wps becomes the voice pace band. Nothing is invented: what is absent stays a
    `<fill: ...>` marker for a person to fill."""
    from .controls import split_front_matter
    seeded: dict[str, Any] = {}
    design = controls / "design.md"
    if design.is_file():
        fm = split_front_matter(design.read_text())[0]
        tokens_path = dest / FILES["tokens"]
        tokens = json.loads(tokens_path.read_text())
        palette = {k: v for k, v in (fm.get("palette") or {}).items() if isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v)}
        if palette:
            group = tokens.setdefault("palette", {"$type": "color", "$description": "carried from design.md palette"})
            for name, hexv in palette.items():
                group[re.sub(r"[^A-Za-z0-9_-]", "-", name)] = {"$value": _hex_to_color(hexv)}
            seeded["palette"] = sorted(palette)
        fonts = [f for f in (fm.get("fonts") or []) if isinstance(f, str) and not PLACEHOLDER.search(f)]
        if fonts:
            tokens.setdefault("font", {"$type": "fontFamily"})["primary"] = {"$value": fonts + ["sans-serif"]}
            seeded["fonts"] = fonts
        tokens_path.write_text(json.dumps(tokens, indent=1) + "\n")
    production = controls / "production.md"
    if production.is_file():
        wps = split_front_matter(production.read_text())[0].get("narration_wps")
        if isinstance(wps, (int, float)) and wps > 0:
            voice = dest / FILES["voice"]
            text = voice.read_text()
            wpm = wps * 60
            text = re.sub(r"pace_wpm: \{[^}]*\}", f"pace_wpm: {{min: {round(wpm * 0.9)}, max: {round(wpm * 1.1)}}}", text, count=1)
            voice.write_text(text)
            seeded["pace_wpm"] = [round(wpm * 0.9), round(wpm * 1.1)]
    return seeded
