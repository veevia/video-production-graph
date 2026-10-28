"""The quality workflow's checks on the default nodes it extends (graph/workflow-quality.yaml).

  check_bar        N03       GATES.yaml lints clean; the benchmark registry is pinned, unchanged and verifies; every
                             benchmark gate has an envelope; the references show every style-frame role the look judges
  check_spec       N06, N09  the spec documents the node writes lint clean, and a changed approved one bumps its version
  check_beats      N08       the storyboard gives each STORY.md beat its planned share of the runtime
  technical_qa     N15       media:technical_qa, plus the build-stage gates and the ratchet against the registry baseline
  check_proposals  N21       spec proposals in the interpretation name a document, a rule change, a reason and a stage

A writer validates what it writes, never another node's document. Each check reads the family where the control store
keeps it (spec/), and the node that runs it declares the documents in controls.read, so a change re-checks the node.
GAT-SPEC and GAT-RATCHET cite R-REG-001, so they stay advisory (nits) until a named human adopts that rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..context import CheckResult, NodeResult
from ..util import write_json_atomic
from ._common import finding

REPORT = "quality-gates.json"
BLOCKING = ("P1", "P2", "P3")
CHANGES = ("add", "modify", "retire")
# Which node made the artifact a metric is measured on: a build-gate finding is traced there.
TRACE = {"master": ("n14_video_assembly", "master"), "voice": ("n12_voice_audio", "voice"),
         "voice+music": ("n14_video_assembly", "master"), "voice+script": ("n12_voice_audio", "voice"),
         "text_layers": ("n13_motion_scenes", "text_layers"), "stills": ("n13_motion_scenes", "scene_frames")}


def _family(ctx):
    from ..spec import load_family
    return load_family(ctx.control_path("spec-gates").parent)


def _why_unmeasured(reg: dict[str, Any], mid: str, roles: list[str] | None = None) -> str:
    """Why the references give a metric no value (for a still metric: on these roles' frames), from their own records,
    and what fixes it."""
    from ..metrics import METRICS, current, ocr_not_run
    m = METRICS.get(mid) if isinstance(mid, str) else None
    if m is None:
        return " (not a known metric: see the spec lint error)"
    refs = [e for e in reg.get("entries", []) if e.get("kind") == "reference"]   # the entries envelopes come from
    if not refs:
        return " (the registry has no reference film yet: `vpg benchmark add --kind reference`)"
    recs = [(e, (e.get("measurements") or {}).get(mid)) for e in refs]
    if any(not current(r) for _, r in recs):
        return " (measure the references under the current metric version: `vpg benchmark measure`)"
    stills = m.source == "stills"
    why, fixes = [], []

    def fix(text: str) -> None:
        if text not in fixes:
            fixes.append(text)
    for e, r in recs:
        has_media = bool(e.get("style_frames")) if stills else bool((e.get("media") or {}).get("master"))
        if r.get("value") is None:
            reason = str(r.get("not_measured") or "no value")
            why.append(f"{e['id']}: {reason}")
            if ".ocr_" in mid and ocr_not_run(reason) and has_media:
                fix("measure them with OCR: `vpg benchmark measure --ocr`")
            elif reason.startswith("OCR unavailable"):
                fix("make tesseract work, then `vpg benchmark measure --ocr --all`")
            elif "narration" in reason:
                fix("register each reference with its narration (`vpg benchmark add --narration`); an entry cannot "
                    "gain media later, so register it afresh in a new registry")
            elif ".ocr_" in mid and has_media:
                fix(_too_little_text(stills))
            else:
                fix("register each reference with the media this metric reads")
            continue
        shown = {sf.get("role") for sf in e.get("style_frames") or []}
        per_role = (r.get("evidence") or {}).get("per_role") or {}
        for role in roles or []:
            if role not in shown:
                why.append(f"{e['id']} has no {role} style frame")
                fix("register references that show every role (`vpg benchmark add --style-frame role=path`; an entry "
                    "cannot gain frames later, so register it afresh in a new registry)")
            elif per_role.get(role) is None:
                why.append(f"{e['id']}: its {role} frame gives no value"
                           + (" (too little confidently read text)" if ".ocr_" in mid else ""))
                fix(_too_little_text(stills) if ".ocr_" in mid else "register references whose frames this metric can read")
    return f" ({'; '.join(sorted(set(why))[:4]) + '; ' if why else ''}{'; '.join(fixes) or 'verify the registry'})"


def _too_little_text(stills: bool) -> str:
    if stills:
        return ("choose reference frames whose text OCR reads, or judge the gate only on the roles that show text "
                "(`roles:` in GATES.yaml)")
    return "choose references that show text-dense UI, or state the gate's envelope in GATES.yaml"


def check_bar(ctx) -> CheckResult:
    """N03: the bar can judge. GATES.yaml lints clean; the registry it pins is there, unchanged and verifies (no
    reference outside its own envelope, every degraded anchor caught, the registered files intact); every gate with a
    benchmark envelope has one; and each reference shows a style frame for every role DESIGN.md asks the look to show."""
    from ..benchmarks import envelopes, missing_roles, role_envelopes, verify
    from ..metrics import METRICS
    from ..spec import lint, load_registry, registry_ref
    from .look import roles_of
    fam = _family(ctx)
    msgs = [f"spec: {i.line()}" for i in lint(fam, require=("gates",), only=("gates",)) if i.level == "error"]
    reg, why = load_registry(fam)
    if reg is None:
        msgs.append(f"benchmarks: {why} (`vpg benchmark init|add|measure`, then pin it in GATES.yaml)")
        return CheckResult(False, msgs, metrics={"registry": None})
    path, _ = registry_ref(fam)
    rep = verify(reg, base=path.parent)
    msgs += [f"benchmarks: a reference falls outside its own envelope: {x}" for x in rep["references_flagged"][:5]]
    msgs += [f"benchmarks: degraded anchor {a} is caught by no envelope, so the envelopes cannot tell bad from good"
             for a in rep["anchors_missed"]]
    msgs += [f"benchmarks: {p}" for p in rep["media_problems"][:5]]
    envs, by_role = envelopes(reg), role_envelopes(reg)
    roles = roles_of(fam)
    for g in fam.gates():
        if g.get("envelope") != "benchmark" or g.get("severity") not in BLOCKING:
            continue   # a nit gate that cannot be judged blocks nothing
        m = METRICS.get(g.get("metric"))
        if m is not None and m.source == "stills":
            judged = [str(r) for r in g.get("roles") or []] if isinstance(g.get("roles"), list) else []
            lack = [r for r in (judged or roles) if r not in (by_role.get(m.id) or {})]
            if lack:
                msgs.append(f"{g.get('id')}: no reference measures {m.id} on a {', '.join(lack)} style frame"
                            f"{_why_unmeasured(reg, m.id, lack)}")
        elif g.get("metric") not in envs:
            msgs.append(f"{g.get('id')}: no benchmark envelope for {g.get('metric')}{_why_unmeasured(reg, g.get('metric'))}")
    if any(g.get("stage") == "look" for g in fam.gates()):
        msgs += [f"benchmarks: reference {eid} has no style frame for {', '.join(lack)} (`vpg benchmark add "
                 f"--style-frame role=path`)" for eid, lack in missing_roles(reg, roles).items()]
    return CheckResult(not msgs, msgs, metrics={"registry": reg.get("id"), "envelopes": len(envs),
                                                "anchors_caught": len(rep["anchors_caught"])})


def _bump_issues(ctx, fam, docs: list[str]) -> list[str]:
    """A document changed since its approved version must say so: its spec.version goes up (the diff names what)."""
    from ..spec import CONTROL_IDS, read_doc
    out = []
    for doc in docs:
        st = ctx.state.controls.get(CONTROL_IDS.get(doc, ""))
        if doc == "tokens" or doc not in fam.docs or st is None or not st.snapshot:
            continue
        snap = Path(ctx.project_dir) / st.snapshot
        if not snap.is_file():
            continue
        old = read_doc(snap, doc)
        if old["sha256"] == fam.docs[doc]["sha256"]:
            continue
        ov = (old["front_matter"].get("spec") or {}).get("version")
        nv = (fam.fm(doc).get("spec") or {}).get("version")
        if isinstance(ov, int) and isinstance(nv, int) and nv <= ov:
            out.append(f"spec: error: {doc}: changed since version {ov} was approved, but spec.version is {nv}; bump it")
    return out


def _mix_bar_issues(ctx, fam) -> list[str]:
    """MIX.md's loudness mirrors the approved bar (evaluation.md over the defaults), which technical QA judges the
    master against: one source of truth, the bar."""
    from ._common import thresholds
    lo = (fam.fm("mix") or {}).get("loudness")
    if not isinstance(lo, dict):
        return []
    audio = thresholds(ctx).get("audio", {})
    pairs = (("integrated_lufs", "integrated_lufs"), ("tolerance_lu", "lufs_tolerance"), ("true_peak_max_dbtp", "true_peak_max_dbtp"))
    return [f"spec: error: mix: loudness.{mine} is {lo[mine]}, but the approved bar (evaluation.md, or the defaults) says "
            f"{audio[theirs]}; MIX.md must match it (change the bar through a change request on evaluation)"
            for mine, theirs in pairs if mine in lo and theirs in audio and float(lo[mine]) != float(audio[theirs])]


def check_spec(ctx, docs: list[str]) -> CheckResult:
    """The spec documents this node writes lint clean (rules, tokens, references, prose sections, no unfilled markers),
    one changed since its approved version bumps spec.version, and MIX.md's loudness matches the approved bar."""
    from ..spec import lint
    fam = _family(ctx)
    only = tuple(docs)
    errs = [i for i in lint(fam, require=only, only=only) if i.level == "error"]
    msgs = [f"spec: {i.line()}" for i in errs[:12]] + _bump_issues(ctx, fam, list(docs))
    if "mix" in docs and "mix" in fam.docs:
        msgs += _mix_bar_issues(ctx, fam)
    return CheckResult(not msgs, msgs, metrics={"docs": list(only), "errors": len(errs)})


def check_beats(ctx) -> CheckResult:
    """ST-BEATS: the storyboard's chapters belong to STORY.md beats, and each beat's planned duration is its share of the
    runtime within the tolerance (the animatic checks the same shares on measured time)."""
    from ..spec import beat_shares
    story = ctx.fm("spec-story")
    board = ctx.load("storyboard")
    if not story:
        return CheckResult(False, ["STORY.md is missing or has no front matter (N06 writes it)"])
    if board is None:
        return CheckResult(False, ["no storyboard to check against STORY.md"])
    timed = [(b.get("chapter"), float(b.get("duration_s") or 0)) for b in board.get("beats", [])]
    rows = beat_shares(story, timed)
    covered = {c for r in rows for c in r["chapters"]}
    msgs = [f"storyboard chapter {ch} belongs to no STORY.md beat (name the chapters after the beats, or list them in "
            "the beat's chapters)" for ch in sorted({ch for ch, _ in timed if ch not in covered}, key=str)]
    msgs += [f"story beat {r['beat']} gets {r['share']:.0%} of the planned runtime; STORY.md plans {r['spec']:.0%} (plus or "
             f"minus {r['tolerance']:.0%})" for r in rows if r["status"] == "fail"]
    return CheckResult(not msgs, msgs, metrics={"beats": rows})


def _measure_build(ctx, fam, metric_ids: set[str]) -> dict[str, Any]:
    from ..metrics import METRICS
    from ..spec import bar_settings
    from .craft import measure
    sources = {METRICS[m].source for m in metric_ids if m in METRICS}
    voice = ctx.artifact_path("voice_stem") or ctx.artifact_path("voice")
    kw: dict[str, Any] = {}
    if "master" in sources:
        kw["master"] = ctx.artifact_path("master")
    if sources & {"voice", "voice+music", "voice+script"} and voice is not None:
        kw["voice"] = voice
        if "voice+music" in sources:
            kw["music"] = ctx.artifact_path("music_stem")
        if "voice+script" in sources:
            kw["narration"] = ctx.artifact_path("script")
    if "text_layers" in sources:
        kw["text_layers"] = ctx.artifact_path("text_layers")
        kw["player_width_px"] = bar_settings(fam).get("player_width_px")
    kw = {k: v for k, v in kw.items() if v is not None}
    if kw.get("master") is not None and any(".ocr_" in m for m in metric_ids):
        kw["ocr"] = True   # a gated OCR metric is measured, never left "not measured" for want of the flag
    return measure(**kw)["measurements"] if kw else {}


def technical_qa(ctx) -> NodeResult:
    """N15 in the quality workflow: the technical QA, then every build-stage gate in GATES.yaml on the master, its stems
    and its text layers (GAT-SPEC), and the ratchet: a metric whose gate says `ratchet: true` may not get worse than the
    registry's accepted baseline, unless a named human waived it in GATES.yaml (GAT-RATCHET)."""
    from ..benchmarks import envelopes, live_baseline, ratchet
    from ..metrics import METRICS
    from ..spec import bar_settings, blocking, evaluate_gates, load_registry
    from .look import gate_line
    from .media import technical_qa as media_technical_qa
    res = media_technical_qa(ctx)
    fam = _family(ctx)
    gates = [g for g in fam.gates() if g.get("stage") == "build"]
    if not gates:
        return res
    registry, why = load_registry(fam)
    measurements = _measure_build(ctx, fam, {g.get("metric") for g in gates})
    rows = evaluate_gates(fam, measurements, stage="build", registry=registry)
    findings = []
    for r in blocking(rows):
        node, key = TRACE.get(METRICS[r["metric"]].source if r["metric"] in METRICS else "master", TRACE["master"])
        findings.append(finding("qualitygates:technical_qa", "GAT-SPEC", r["severity"], gate_line(r, why), evidence=json.dumps(
            {"value": r.get("value"), "envelope": r.get("envelope"), "status": r["status"]}), traced=node, key=key,
            fix="bring the measured value inside the approved envelope, or change the bar through a change request on spec-gates"))
    ratchet_rows: list[dict[str, Any]] = []
    base = live_baseline(registry) if registry else None
    if base is not None:
        wanted = {g.get("metric") for g in gates if g.get("ratchet")}
        cand = {m: rec for m, rec in measurements.items() if m in wanted}
        ratchet_rows = ratchet({m: rec for m, rec in (base.get("measurements") or {}).items() if m in wanted}, cand,
                               envelopes(registry), waivers=bar_settings(fam).get("waivers") or [])
        for r in ratchet_rows:
            if r["status"] != "regressed":
                continue
            node, key = TRACE.get(METRICS[r["metric"]].source, TRACE["master"])
            findings.append(finding("qualitygates:technical_qa", "GAT-RATCHET", "P2",
                                    f"{r['metric']} regressed against the accepted baseline {base['id']}: {r['baseline']} → "
                                    f"{r['candidate']} (tolerance {r['tolerance']})", evidence=json.dumps(r.get("evidence", {}))[:400],
                                    traced=node, key=key, fix="restore the baseline's value, or have a named human waive it in GATES.yaml"))
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ctx.workdir / REPORT, {"gates": rows, "ratchet": ratchet_rows, "baseline": base and base["id"],
                                            "registry_problem": why, "measurements": measurements})
    res.findings = list(res.findings) + findings
    res.outputs = {**res.outputs, "quality_gates": REPORT}
    res.data = {**(res.data or {}), "quality_gates": {"gates": len(rows), "findings": len(findings)}}
    return res


def proposal_problems(proposals: Any, fam) -> list[str]:
    """What is wrong with N21's spec proposals: each names a spec document, a change (add, modify or retire), the rule it
    changes (an existing one to modify or retire), what it proposes and the feedback or defect it answers; two
    proposals on one rule conflict."""
    from ..spec import FILES
    if proposals is None:
        return []
    if not isinstance(proposals, list):
        return ["spec_proposals must be a list"]
    rules = fam.rules()
    out, seen = [], {}
    for i, p in enumerate(proposals):
        if not isinstance(p, dict):
            out.append(f"spec proposal {i + 1} is not an object")
            continue
        doc, change, rule = p.get("doc"), p.get("change"), p.get("rule")
        tag = f"spec proposal {i + 1} ({doc}/{rule or 'new rule'})"
        if doc not in FILES:
            out.append(f"{tag}: doc is one of {', '.join(FILES)}")
        if change not in CHANGES:
            out.append(f"{tag}: change is add, modify or retire")
        if change in ("modify", "retire") and rule not in rules:
            out.append(f"{tag}: names rule {rule}, which the spec does not define")
        if change == "add" and rule and rule in rules:
            out.append(f"{tag}: rule {rule} already exists; modify it instead")
        for field in ("proposal", "because"):
            if not str(p.get(field) or "").strip():
                out.append(f"{tag}: {field} is required (what changes, and the feedback or defect it answers)")
        if rule:
            if rule in seen and seen[rule] != (change, p.get("proposal")):
                out.append(f"{tag}: conflicts with an earlier proposal on {rule}; keep one")
            seen[rule] = (change, p.get("proposal"))
    return out


def check_proposals(ctx) -> CheckResult:
    """SPEC-PROPOSALS: interpretation.json's optional spec_proposals are well formed. None is filed automatically;
    `vpg status` lists each with the change-request command a person runs."""
    data = ctx.load("interpretation") or {}
    msgs = proposal_problems(data.get("spec_proposals"), _family(ctx))
    return CheckResult(not msgs, msgs, metrics={"spec_proposals": len(data.get("spec_proposals") or [])})
