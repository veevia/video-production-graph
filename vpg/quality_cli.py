"""Commands for the quality layer: craft measurements and benchmarks (P0); later phases add theirs here.

Registered from `vpg.cli.main`. None of them changes a production run unless its help says so: they read media and
write only the file named by --out or the registry they are given.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


def _media_args(sp) -> None:
    sp.add_argument("--master", help="the rendered film (video)")
    sp.add_argument("--voice", help="the narration stem (audio)")
    sp.add_argument("--music", help="the music stem (audio)")
    sp.add_argument("--text-layers", dest="text_layers", help="the renderer's text-layers.json")
    sp.add_argument("--narration", help="script.json, timings.json or a text file with the spoken words")
    sp.add_argument("--ocr", action="store_true", help="also measure small text at player width (needs tesseract)")


def _files(args) -> dict[str, str | None]:
    return {k: getattr(args, k) for k in ("master", "voice", "music", "text_layers", "narration")}


# ------------------------------------------------------------------------------------------------ measure
def cmd_measure(args) -> int:
    from .metrics import catalog
    from .tools.craft import measure
    if args.catalog:
        _print(catalog())
        return 0
    files = {k: v for k, v in _files(args).items() if v}
    if not files:
        print("vpg measure: give at least one of --master, --voice, --text-layers (or --catalog)", file=sys.stderr)
        return 2
    report = measure(**files, ocr=args.ocr)
    if args.out:
        from .util import write_json_atomic
        write_json_atomic(args.out, report)
    rows = report["measurements"]
    for mid, rec in rows.items():
        val = rec["value"] if rec["value"] is not None else f"not measured ({rec.get('not_measured')})"
        print(f"{mid:36s} v{rec['version']} {rec['basis']}  {val} {rec['unit'] if rec['value'] is not None else ''}")
    if args.out:
        print(f"written: {args.out}")
    return 0


# ------------------------------------------------------------------------------------------------ benchmark
def cmd_benchmark(args) -> int:
    from dataclasses import asdict

    from . import benchmarks as bm
    path = Path(args.registry)
    try:
        if args.action == "init":
            if path.exists():
                raise bm.BenchmarkError(f"{path} exists")
            bm.save(path, bm.new_registry(args.id or path.stem, args.title or ""))
            print(f"created {path}")
        elif args.action == "add":
            frames = []
            for item in args.style_frame or []:
                role, sep, p = item.partition("=")
                if not sep:
                    raise bm.BenchmarkError(f"--style-frame takes role=path, not {item!r}")
                frames.append((role.strip(), p.strip()))
            e = bm.add_entry(path, args.id, args.kind, _files(args), by=args.by or "", title=args.title or "",
                             evidence=args.evidence, degrades=args.degrades, how=args.how or "", reason=args.reason or "",
                             ocr=args.ocr, style_frames=frames)
            print(f"added {e['id']} ({e['kind']}): {sum(r['value'] is not None for r in e['measurements'].values())} metrics measured")
        elif args.action == "measure":
            done = bm.remeasure(path, args.entry or None, ocr=args.ocr, all_metrics=args.all)
            print("re-measured:", json.dumps(done, sort_keys=True) if done else "nothing changed; the registry was not rewritten")
        elif args.action == "envelopes":
            _print({k: asdict(v) for k, v in sorted(bm.envelopes(bm.load(path)).items())})
        elif args.action == "verify":
            report = bm.verify(bm.load(path), base=path.parent)
            _print({k: v for k, v in report.items() if k != "envelopes"} | {"envelopes": sorted(report["envelopes"])})
            return 0 if report["ok"] else 1
        elif args.action == "check":
            data = bm.load(path)
            if not args.measurements:
                raise bm.BenchmarkError("check needs --measurements (written by `vpg measure --out`)")
            cand = json.loads(Path(args.measurements).read_text())["measurements"]
            envs = bm.envelopes(data)
            rows = bm.check(cand, envs)
            base = bm.live_baseline(data)
            waivers = json.loads(Path(args.waivers).read_text()) if args.waivers else []
            reg = bm.ratchet(base["measurements"], cand, envs, waivers) if base else []
            _print({"envelopes": rows, "ratchet": reg, "baseline": base["id"] if base else None})
            bad = [r for r in rows if r["status"] in ("below", "above", "not_comparable")]
            bad += [r for r in reg if r["status"] in ("regressed", "not_comparable")]
            return 1 if bad else 0
    except bm.BenchmarkError as exc:
        print(f"vpg benchmark {args.action}: {exc}", file=sys.stderr)
        return 4
    return 0


# ------------------------------------------------------------------------------------------------ spec
def cmd_spec(args) -> int:
    from . import spec as sp
    try:
        if args.action == "init":
            written = sp.init_family(args.dir, from_controls=args.from_controls, force=args.force)
            print("written:", ", ".join(p.name for p in written))
            if args.from_controls:
                print("seeded from", args.from_controls, "(fill every <fill: ...> before the family can be approved)")
            return 0
        fam = sp.load_family(args.dir)
        if args.action == "hash":
            print(sp.family_hash(fam))
            return 0
        if args.action == "lint":
            prev = sp.load_family(args.previous) if args.previous else None
            require = tuple(sp.FILES) if args.require_all else ("design", "tokens", "gates")
            issues = sp.lint(fam, previous=prev, require=require)
            for i in issues:
                print(i.line())
            errors = sum(i.level == "error" for i in issues)
            print(f"{len(fam.docs)} documents, {len(fam.rules())} rules, {len(fam.gates())} gates: "
                  f"{errors} errors, {len(issues) - errors} warnings")
            return 1 if errors else 0
        if args.action == "diff":
            if not args.new:
                raise sp.SpecError("diff needs the newer family folder as a second argument")
            d = sp.diff(fam, sp.load_family(args.new))
            if args.json:
                _print(d)
            else:
                print(*sp.diff_lines(d), sep="\n")
            return 0
    except sp.SpecError as exc:
        print(f"vpg spec {args.action}: {exc}", file=sys.stderr)
        return 4
    return 0


# ------------------------------------------------------------------------------------------------ telemetry and direct runs
def cmd_telemetry(args) -> int:
    from . import telemetry as tm
    if args.write or args.out or args.otlp:
        if args.write or args.out:
            paths = tm.write(args.project, out_dir=args.out, otlp=args.otlp)
        else:
            from .util import write_json_atomic
            write_json_atomic(args.otlp, tm.to_otlp(tm.derive(args.project), Path(args.project).name))
            paths = {"otlp": Path(args.otlp)}
        print("written:", ", ".join(str(p) for p in paths.values()))
    tel = tm.derive(args.project)
    if args.json:
        _print(tel)
    else:
        c = tel["coverage"]
        print(f"run {tel['run_id']}: {c['spans']} spans, {c['evaluations']} evaluation events; nodes with evidence "
              f"{c['nodes_with_evidence']}/{c['nodes_run']}, gate events {c['gate_events']}/{c['gates']}, human decisions "
              f"{c['decision_events']}/{c['decisions']}" + ("" if c["ok"] else f"; missing: {', '.join(c['missing'])}"))
    return 1 if args.check and not tel["coverage"]["ok"] else 0


def cmd_direct_run(args) -> int:
    import shlex

    from .engine import Engine, EngineError
    outputs = {}
    for item in args.output or []:
        key, sep, path = item.partition("=")
        if not sep or not key.strip() or not path.strip():
            raise EngineError(f"--output takes key=path, not {item!r}")
        outputs[key.strip()] = path.strip()
    cmd = shlex.split(args.run_command) if args.run_command else []
    rec = Engine(args.project).record_direct_run(args.node, args.actor, args.reason, outputs, label=args.label or "",
                                                 command=cmd or None, timeout_s=args.timeout)
    ran = f"; exit {rec['exit_code']} after {rec['duration_s']} s, log {rec['log']}" if rec["command"] else ""
    print(f"recorded {rec['id']} ({rec['node']}, outside the governed path){ran}; {len(rec['outputs'])} output(s) hashed")
    return 0 if rec["exit_code"] in (None, 0) else 5


# ------------------------------------------------------------------------------------------------ judging
def _validate_against(doc: dict, schema_name: str) -> None:
    import jsonschema

    from .util import PKG_ROOT
    schema = json.loads((PKG_ROOT / "schemas" / schema_name).read_text())
    errors = [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]
    if errors:
        from .judging import JudgingError
        raise JudgingError(f"does not match {schema_name}: " + "; ".join(errors[:5]))


def cmd_judge(args) -> int:
    from . import judging as jd
    from .util import write_json_atomic
    try:
        if args.action == "pack":
            items = json.loads(Path(args.items).read_text())
            by_id = {i["id"]: i for i in items}
            for need in (args.reference, args.anchor):
                if need not in by_id:
                    raise jd.JudgingError(f"{need} is not in {args.items}")
            cands = [i for i in items if i["id"] not in (args.reference, args.anchor)]
            out_dir = Path(args.out).resolve().parent
            media_dir = Path(args.media_dir).resolve() if args.media_dir else out_dir / (Path(args.out).stem + "-media")
            if out_dir not in media_dir.parents:
                raise jd.JudgingError(f"--media-dir must be inside the packet's directory ({out_dir})")
            packet, key = jd.make_batch(cands, by_id[args.reference], by_id[args.anchor], args.criterion, args.rule or [],
                                        seed=args.seed, media_root=media_dir.relative_to(out_dir).as_posix())
            staged = jd.stage_media(key, out_dir)
            jd.seal(packet, key, staged)
            _validate_against(packet, "judge-packet.schema.json")
            write_json_atomic(args.out, packet)
            write_json_atomic(args.key, key)
            if out_dir in Path(args.key).resolve().parents:
                print("note: the key is inside the packet's directory; move it where the judge cannot read it", file=sys.stderr)
            print(f"packet {packet['batch']}: {len(packet['presentations'])} presentations -> {args.out}; "
                  f"{len(key['staging'])} media files copied under neutral names ({len(staged)} remuxed without container "
                  f"metadata) -> {media_dir}; "
                  f"key (keep from the judge) -> {args.key}")
        elif args.action == "score":
            verdict = json.loads(Path(args.verdict).read_text())
            _validate_against(verdict, "judge-verdict.schema.json")
            res = jd.score_batch(json.loads(Path(args.key).read_text()), verdict, seed=args.seed)
            if args.out:
                write_json_atomic(args.out, res)
            print(f"batch {res['batch']} ({res['criterion']}): {'valid' if res['valid'] else 'NOT TRUSTED: ' + res['why_invalid']}")
            for r in res["ranking"]:
                print(f"  {r['rank']}. {r['item']:16s} strength {r['strength']:+.3f}  95% CI {r['ci']}")
            p = res["position"]
            print(f"  position: first chosen {p['first_share']} of decisive picks; {p['inconsistent_pairs']} of {p['pairs']} pairs "
                  "disagreed between orders (counted as ties)")
            if res["below_anchor"]:
                print("  below the degraded anchor:", ", ".join(res["below_anchor"]))
            return 0 if res["valid"] else 1
        elif args.action == "label":
            lb = jd.label(args.a, args.b, args.pick, args.by, note=args.note or "", t=args.t, version=args.version or "")
            with open(args.labels, "a") as f:
                f.write(json.dumps(lb, sort_keys=True) + "\n")
            print(f"label recorded: {lb['pair']} -> {lb['pick']} by {lb['by']}")
        elif args.action == "agreement":
            results = [json.loads(Path(r).read_text()) for r in args.result or []]
            _print(jd.agreement_from(results, jd.load_jsonl(args.labels)))
        elif args.action == "saturation":
            if args.scores:
                data = json.loads(Path(args.scores).read_text())
                _print(jd.saturation({k: v for k, v in data.items()}).as_dict())
            else:
                state = json.loads((Path(args.project) / ".vpg" / "state.json").read_text())
                _print(jd.review_saturation(state))
    except jd.JudgingError as exc:
        print(f"vpg judge {args.action}: {exc}", file=sys.stderr)
        return 4
    return 0


def register(sub) -> None:
    sp = sub.add_parser("measure", help="measure craft metrics on a film and its stems (read-only; writes only --out)")
    _media_args(sp)
    sp.add_argument("--out", help="write the measurements JSON here")
    sp.add_argument("--catalog", action="store_true", help="list every metric with its version and basis")
    sp.set_defaults(fn=cmd_measure)

    sp = sub.add_parser("benchmark", help="manage a benchmark registry: references, degraded anchors and the accepted baseline")
    sp.add_argument("action", choices=["init", "add", "measure", "envelopes", "verify", "check"])
    sp.add_argument("registry", help="the registry JSON (media paths are stored relative to it)")
    sp.add_argument("--id", help="init: the registry id; add: the entry id")
    sp.add_argument("--title")
    sp.add_argument("--kind", choices=["reference", "degraded_anchor", "previous_best"], default="reference")
    sp.add_argument("--evidence", choices=["observed", "reported", "assumed"], default="observed")
    sp.add_argument("--degrades", help="add, degraded_anchor: the reference it degrades")
    sp.add_argument("--how", help="add, degraded_anchor: what was degraded")
    sp.add_argument("--by", help="add: who registers it (a previous_best needs a named human)")
    sp.add_argument("--reason", help="add, previous_best: why this is the new accepted baseline")
    sp.add_argument("--entry", action="append", help="measure: only these entries")
    sp.add_argument("--all", action="store_true", help="measure: re-measure every metric, not only stale ones")
    sp.add_argument("--measurements", help="check: a measurements JSON from `vpg measure --out`")
    sp.add_argument("--waivers", help="check: a JSON list of {metric, by, reason, at} waivers (named humans only)")
    sp.add_argument("--style-frame", dest="style_frame", action="append",
                    help="add: role=path of an approved style frame (title, product_ui, data_callout, closing, other); repeatable")
    _media_args(sp)
    sp.set_defaults(fn=cmd_benchmark)

    sp = sub.add_parser("spec", help="the spec family: init from templates, lint, diff two versions by rule id, hash")
    sp.add_argument("action", choices=["init", "lint", "diff", "hash"])
    sp.add_argument("dir", help="the folder holding the family (a project's spec/ folder)")
    sp.add_argument("new", nargs="?", help="diff: the newer version's folder")
    sp.add_argument("--from-controls", dest="from_controls", help="init: seed tokens and bands from this controls/ folder")
    sp.add_argument("--force", action="store_true", help="init: overwrite existing files")
    sp.add_argument("--previous", help="lint: the previous version's folder (version bump and growth checks)")
    sp.add_argument("--require-all", action="store_true", help="lint: require all eight documents, not only design, tokens and gates")
    sp.add_argument("--json", action="store_true", help="diff: print JSON")
    sp.set_defaults(fn=cmd_spec)

    sp = sub.add_parser("telemetry", help="OpenTelemetry-GenAI-shaped spans and evaluation events for a run (read-only unless --write)")
    sp.add_argument("project", help="project directory")
    sp.add_argument("--lock-timeout", type=float, default=900.0, help="seconds to wait for the project lock (--write)")
    sp.add_argument("--write", action="store_true", help="refresh <project>/.vpg/telemetry/ (spans.jsonl, evaluations.jsonl, summary.json)")
    sp.add_argument("--out", help="write the same three files to this folder instead")
    sp.add_argument("--otlp", help="also write an OTLP/JSON traces file (for a local Phoenix or Langfuse)")
    sp.add_argument("--json", action="store_true", help="print the full telemetry")
    sp.add_argument("--check", action="store_true", help="exit 1 unless every node, gate and human decision has an event with evidence")
    sp.set_defaults(fn=cmd_telemetry)

    sp = sub.add_parser("direct-run", help="record (and optionally run) a build made outside the governed path, so it is observable")
    sp.add_argument("project", help="project directory")
    sp.add_argument("node", help="the node whose work ran directly, e.g. n13_motion_scenes")
    sp.add_argument("--lock-timeout", type=float, default=900.0, help="seconds to wait for the project lock")
    sp.add_argument("--actor", required=True, help="who ran it (an agent id or a person)")
    sp.add_argument("--reason", required=True, help="why it ran outside the graph")
    sp.add_argument("--label", help="the candidate's label, e.g. v26")
    sp.add_argument("--output", action="append", help="key=path of a file the run produced (repeatable)")
    sp.add_argument("--timeout", type=float, help="seconds before the command is stopped (default: command_timeout_s)")
    sp.add_argument("--run", dest="run_command", help="the command to run in the project folder, as one quoted string")
    sp.set_defaults(fn=cmd_direct_run)

    sp = sub.add_parser("judge", help="pairwise judging: pack a blinded batch, score a verdict, record human labels, calibrate, detect saturation")
    sp.add_argument("action", choices=["pack", "score", "label", "agreement", "saturation"])
    sp.add_argument("project", nargs="?", help="saturation: a project whose recorded review scores to read (read-only)")
    sp.add_argument("--items", help="pack: a JSON list of {id, media: {key: {path, sha256}}}")
    sp.add_argument("--reference", help="pack: the hidden reference's id (a benchmark or the previous best)")
    sp.add_argument("--anchor", help="pack: the degraded anchor's id")
    sp.add_argument("--criterion", help="pack: the one criterion this batch judges")
    sp.add_argument("--rule", action="append", help="pack: a spec rule id the judge must cite (repeatable)")
    sp.add_argument("--seed", type=int, default=0, help="pack: order and labels; score: the bootstrap resample")
    sp.add_argument("--out", help="pack: the packet; score: the result")
    sp.add_argument("--media-dir", help="pack: where the neutral media copies go, inside the packet's directory "
                                        "(default: <packet name>-media beside it)")
    sp.add_argument("--key", help="pack: where to write the unblinding key; score: the key to read")
    sp.add_argument("--verdict", help="score: the judge's verdict JSON")
    sp.add_argument("--labels", help="label, agreement: the human labels JSONL")
    sp.add_argument("--a", help="label: one item id"); sp.add_argument("--b", help="label: the other item id")
    sp.add_argument("--pick", help="label: the chosen item id, or tie")
    sp.add_argument("--by", help="label: the named human")
    sp.add_argument("--note", help="label: a note"); sp.add_argument("--t", type=float, help="label: the note's timecode in seconds")
    sp.add_argument("--version", help="label: the version the note is about")
    sp.add_argument("--result", action="append", help="agreement: a scored result JSON (repeatable)")
    sp.add_argument("--scores", help="saturation: a JSON {version: [scores]} instead of a project")
    sp.set_defaults(fn=cmd_judge)
