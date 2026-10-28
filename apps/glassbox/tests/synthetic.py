"""Small synthetic VPG jobs and engine graphs for tests. Everything here is invented; no project data."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

NODE_IDS = {
    1: "n01_context_intake", 2: "n02_source_validation", 3: "n03_objective_audience",
    4: "n04_product_understanding", 5: "n05_narrative_ideation", 6: "n06_creative_direction",
    7: "n07_script_development", 8: "n08_storyboard_shots", 9: "n09_visual_system",
    10: "n10_asset_planning", 11: "n11_asset_generation", 12: "n12_voice_audio",
    13: "n13_motion_scenes", 14: "n14_video_assembly", 15: "n15_technical_qa",
    16: "n16_creative_critique", 17: "n17_design_compliance", 18: "n18_accessibility_legibility",
    19: "n19_product_accuracy", 20: "n20_narrative_pacing", 21: "n21_feedback_interpretation",
    22: "n22_targeted_regeneration", 23: "n23_final_verification", 24: "n24_packaging_delivery",
    25: "n25_learning_memory",
}
HUMAN_GATES = {3, 4, 6, 9, 23}
MASTER_BYTES = bytes(i % 251 for i in range(10000))


def phase_of(num):
    if num <= 4:
        return "Understand"
    if num <= 9:
        return "Design"
    if num <= 20:
        return "Build"
    return "Learn"


def kind_of(num):
    if 15 <= num <= 20:
        return "review"
    return "tool" if num in (2, 22, 23, 24) else "agent"


def engine_yaml():
    lines = ["id: video-production", 'name: "Synthetic engine"', 'version: "1.0.0"', "nodes:"]
    for num in range(1, 26):
        lines += [
            f"  - id: {NODE_IDS[num]}",
            f"    num: {num}",
            f"    name: Synthetic node {num}",
            f"    phase: {phase_of(num)}",
            f"    kind: {kind_of(num)}",
            f'    purpose: "Purpose of node {num}, for tests"',
            '    inputs: ["state:inputs.brief", "control:design"]',
            "    outputs:",
            f'      - {{key: out{num}, kind: json, schema: s{num}, description: "Output {num}"}}',
            "    completion:",
            f'      - {{type: tool, fn: "check:n{num}", description: "Check {num}"}}',
            "    rubric: [r1]",
            '    failure_conditions: ["Fails when broken"]',
            '    retry: {max_attempts: 3, behavior: "Retry with the findings"}',
            '    escalation: {after: "3 failures", to: Owner, report: "What failed"}  # a comment',
            "    controls: {read: [design], write: [], lock_on_approval: []}",
        ]
        if num in HUMAN_GATES:
            lines.append(f'    human_review: {{required: true, decision: "Decide on node {num}", approver: Owner, outcomes: [approved, rejected]}}')
        else:
            lines.append('    human_review: {required: false, decision: "Not required"}')
        if num == 21:
            lines.append("    gate: review_gate")
    lines += [
        "edges:",
        '  - {id: E01, from: START, to: n01_context_intake, kind: flow, when: "*"}',
        "  - {id: E02, from: n01_context_intake, to: n02_source_validation, kind: flow, when: pass}",
        "  - {id: E07, from: n03_objective_audience, to: n01_context_intake, kind: loop, when: rejected}",
    ]
    return "\n".join(lines) + "\n"


def write_engine(base):
    engine = Path(base) / "engine"
    (engine / "graph").mkdir(parents=True, exist_ok=True)
    (engine / "graph" / "workflow.yaml").write_text(engine_yaml(), encoding="utf-8")
    return engine


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")


def _ledger(run_id):
    events = [
        ("run_created", {"run": run_id}),
        ("node_dispatched", {"node": "n01_context_intake"}),
        ("node_passed", {"node": "n01_context_intake"}),
        ("node_dispatched", {"node": "n03_objective_audience"}),
        ("awaiting_human", {"node": "n03_objective_audience", "decision": "Decide on node 3"}),
    ]
    previous = "0" * 64
    lines = []
    for seq, (kind, data) in enumerate(events, start=1):
        record = {"seq": seq, "ts": f"2030-01-01T00:00:0{seq}+00:00", "type": kind, "data": data, "prev": previous}
        record["hash"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        previous = record["hash"]
        lines.append(json.dumps(record))
    return "\n".join(lines) + "\n"


def write_governed_run(job):
    nodes = {NODE_IDS[n]: {"status": "PLANNED", "attempts": 0, "outcome": None} for n in range(1, 26)}
    nodes[NODE_IDS[1]] = {"status": "STEP_PASS", "attempts": 1, "outcome": "pass", "started_at": "2030-01-01T00:00:01+00:00",
                          "finished_at": "2030-01-01T00:00:02+00:00", "task_packet": ".vpg/tasks/n01-a1.md"}
    nodes[NODE_IDS[3]] = {"status": "WAITING_HUMAN", "attempts": 1, "outcome": None, "started_at": "2030-01-01T00:00:04+00:00"}
    write_json(job / ".vpg" / "state.json", {"runtime": {
        "run_id": "demo-run-1", "status": "WAITING_HUMAN", "current_version": 0, "nodes": nodes}})
    (job / ".vpg" / "ledger.jsonl").write_text(_ledger("demo-run-1"), encoding="utf-8")
    (job / ".vpg" / "tasks").mkdir(parents=True, exist_ok=True)
    (job / ".vpg" / "tasks" / "n01-a1.md").write_text("Synthetic task packet for N01.\n", encoding="utf-8")


def write_full_candidate(job, version, rework=True):
    base = job / "candidates" / version
    assembly = base / "n14_video_assembly"
    assembly.mkdir(parents=True, exist_ok=True)
    (assembly / "master.mp4").write_bytes(MASTER_BYTES)
    (assembly / "poster.jpg").write_bytes(b"\xff\xd8\xff\xe0synthetic-poster")
    (assembly / "captions.en.vtt").write_text(
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.500\nHello <b>there</b> &amp; welcome\n\n"
        "2\n00:01:05.250 --> 00:01:07.000\nSecond line\nwraps here\n", encoding="utf-8")
    write_json(assembly / "outputs.json", {"outputs": {"master": "master.mp4"}, "data": {
        "integrated_lufs": -16.0, "true_peak_dbtp": -1.5, "bytes": len(MASTER_BYTES), "voice_over_music_p10_lu": 12.5}, "findings": []})
    write_json(assembly / "mix-report.json", {"timeline": {"film_seconds": 80.5, "bpm": 110.0},
                                              "voice_over_music_lu": {"median": 16.5, "p10": 12.5, "p90": 20.0},
                                              "master": {"integrated_lufs": -16.0, "true_peak_dbtp": -1.5}})
    write_json(assembly / "context.json", {"node": "n14_video_assembly", "attempt": 1,
                                           "controls": {"design": str(job / "controls" / "design.md")},
                                           "inputs": {"scenes": [1, 2, 3]},
                                           "producer": {"node": "n14_video_assembly", "command": "{python} pipeline/assemble.py"}})
    scenes = base / "n13_motion_scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    write_json(scenes / "scenes.json", {"beats": 3})
    (scenes / "film").mkdir(exist_ok=True)
    (base / "key-frames").mkdir(parents=True, exist_ok=True)
    (base / "key-frames" / "kf-01-opening-title-f30-1.000s.png").write_bytes(b"\x89PNG synthetic")
    (base / "key-frames" / "kf-02-closing-card-f90-3.000s.png").write_bytes(b"\x89PNG synthetic")
    write_json(base / "qa" / "qa-direct.json", {"version": version, "summary": {"findings": 1, "blocking": 1}, "gates": {
        "N15 technical": {"data": {"lufs": -16}, "findings": []},
        "STILLS": {"findings": [{"check": "STILL-1", "severity": "P3", "message": "A still holds too long", "at": "12.0 s"}]},
        "CAP-SCRIPT": {"findings": []},
        "VOI-SEAMS": {"ok": True, "messages": ["seams fine"], "findings": []},
        "MIX-FLOOR": {"findings": []},
        "MYSTERY-GATE": {"findings": []},
    }})
    reviews = base / "reviews"
    write_json(reviews / "reviewed.json", {"candidates/x/master.mp4": "abc123"})
    write_json(reviews / "technical-qa.json", {
        "reviewer": "agent:technical-qa", "version": version, "verdict": "pass",
        "scores": [{"dimension": "TEC-LOUDNESS", "score": 5, "evidence": "measured"},
                   {"dimension": "VOI-CLARITY", "score": 4, "evidence": "listened"},
                   {"dimension": "DEL-FILES", "score": None, "result": "pass"}],
        "defects": [{"id": "TQA-1", "severity": "P4 (nit)", "at": "00:71.70-00:72.93", "what": "Tiny level dip",
                     "fix_node": "n14_video_assembly", "status": "new"}],
        "summary": "Technical pass with one nit.",
    })
    write_json(reviews / "creative-critic.json", {
        "reviewer": "agent:creative-critic", "version": version, "verdict": "rework" if rework else "pass",
        "scores": [{"dimension": "CRE-STORY", "score": 3, "evidence": "story wobbles"},
                   {"dimension": "DES-TOKENS", "score": 4, "evidence": "tokens fine"},
                   {"dimension": "UNCHANGED-VISUALS", "score": 5, "evidence": "unchanged"}],
        "defects": [{"id": "CRE-D1", "severity": "P3 (blocks)", "dimension": "CRE-STORY", "at": "31.71-33.21 s (CH02 test)",
                     "what": "The middle beat drags", "fix": "Trim the hold", "fix_node": "n13 (pipeline/scenes.py)"}] if rework else [],
        "v1_defects": [{"id": "CRE-OLD", "status": "fixed", "evidence": "gone in this version"}],
        "summary": "Story needs one more pass." if rework else "Fine.",
    })
    write_json(reviews / "accessibility.json", {
        "reviewer": "agent:accessibility", "version": version, "verdict": "pass",
        "scores": [{"dimension": "ACC-CAPTIONS", "score": 5}, {"dimension": "LEG-SIZE", "score": None, "result": "meets"},
                   {"dimension": "MOTION-COMFORT", "score": "4"}],
        "defects": [{"id": "ACC-N1", "level": "SHOULD (nit)", "severity": "P4", "time": "56.50-58.10 s", "what": "Small label"}],
    })
    write_json(reviews / "product-accuracy.json", {
        "reviewer": "agent:product-accuracy", "version": version, "verdict": "pass",
        "scores": [{"dimension": "ACC-BOUNDARIES", "score": 5}, {"dimension": "PA-CLAIMS", "result": "pass"}],
        "findings": {"P1": [], "P3": [], "nit": ["40.0-41.0 s: a label is slightly early"]},
    })
    write_json(reviews / "takeaway-60.json", {
        "reviewer": "agent:takeaway-60", "version": version, "verdict": "pass",
        "scores": [{"dimension": "T1", "score": 5}, {"dimension": "TAKEAWAY-60", "score": 4}],
        "defects": [],
    })
    notes = job / "work" / f"film-{version}"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "brief.md").write_text("# Brief\n\nSynthetic brief.\n", encoding="utf-8")
    (notes / "STATUS.md").write_text("# Status\n\nRendering.\n", encoding="utf-8")
    (notes / "scratch.py").write_text("print('not a note')\n", encoding="utf-8")


def write_reviews_only(job, version, verdict="pass"):
    reviews = job / "candidates" / version / "reviews"
    for key in ("technical-qa", "creative-critic", "accessibility", "product-accuracy", "takeaway-60"):
        write_json(reviews / f"{key}.json", {"reviewer": f"agent:{key}", "version": version, "verdict": verdict,
                                              "scores": [], "defects": []})


def age_tree(path, seconds=7200):
    """Set every mtime under path to `seconds` ago, so nothing looks in flight."""
    stamp = time.time() - seconds
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            os.utime(os.path.join(dirpath, name), (stamp, stamp), follow_symlinks=False)
        os.utime(dirpath, (stamp, stamp))


def make_job(root, rel="hub/film/video/vpg", name="Demo film \u2014 synthetic", slug="demo-film", governed=True):
    job = Path(root) / rel
    job.mkdir(parents=True, exist_ok=True)
    (job / "vpg.project.yaml").write_text(
        f'id: {slug}\nname: "{name}"\nworkflow: default\nconstraints:\n'
        "- 'Runtime: 60 s target, read\n  across two lines'\ncommands:\n  n14_video_assembly: '{python} pipeline/assemble.py'\n",
        encoding="utf-8")
    (job / "README.md").write_text("\n".join(f"Line {i}" for i in range(1, 61)) + "\n", encoding="utf-8")
    if governed:
        write_governed_run(job)
    (job / "candidates" / "v1" / "n12_voice_audio").mkdir(parents=True, exist_ok=True)
    (job / "candidates" / "v1" / "n12_voice_audio" / "voice.wav").write_bytes(b"RIFF")
    write_reviews_only(job, "v2")
    (job / "candidates" / "v2" / "n14_video_assembly").mkdir(parents=True, exist_ok=True)
    (job / "candidates" / "v2" / "n14_video_assembly" / "master.mp4").write_bytes(MASTER_BYTES[:100])
    write_full_candidate(job, "v10")
    (job / "candidates" / "notes.json").write_text("{}", encoding="utf-8")
    age_tree(job)
    return job


SRT_TEXT = "1\n00:00:01,000 --> 00:00:02,500\nFirst line\n\n2\n00:01:05,250 --> 00:01:07,000\nSecond <i>line</i>\n"


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def make_governed_job(root, rel="hub/governed/video/vpg", waiting_release=False):
    """A job with no candidates/ folder: three governed versions in .vpg/state.json and files under work/."""
    job = Path(root) / rel
    job.mkdir(parents=True, exist_ok=True)
    (job / "vpg.project.yaml").write_text('id: gov-film\nname: "Governed film (synthetic)"\n', encoding="utf-8")
    artifacts = {}
    versions = []

    def add(key, node, attempt, path, version, body=None, status="current", item=None):
        artifact_id = f"{key}@{node}#a{attempt}" + (f"[{item}]" if item is not None else "")
        sha = _sha(artifact_id)
        artifacts[artifact_id] = {"id": artifact_id, "key": key, "path": path, "sha256": sha, "bytes": len(body or b""),
                                  "node": node, "attempt": attempt, "version": version, "status": status}
        if body is not None:
            target = job / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        return sha

    voice = add("voice", "n12_voice_audio", 1, "work/n12_voice_audio/a1/voice.wav", 0, b"RIFF....WAVEfmt ")
    for number in (1, 2, 3):
        folder = f"work/n14_video_assembly/a{number}"
        master_body = None if number == 1 else MASTER_BYTES
        master = add("master", "n14_video_assembly", number, f"{folder}/master.mp4", number, master_body,
                     status="current" if number == 3 else "superseded")
        captions = add("captions", "n14_video_assembly", number, f"{folder}/captions.srt", number, SRT_TEXT.encode())
        stem = add("voice_stem", "n14_video_assembly", number, f"{folder}/voice_stem.wav", number, b"RIFF....WAVEfmt ")
        write_json(job / folder / "outputs.json", {"outputs": {}, "data": {"duration_s": 40.0 + number}, "findings": []})
        write_json(job / folder / "context.json", {"node": "n14_video_assembly", "attempt": number,
                                                   "producer": {"node": "n14_video_assembly", "command": "{python} pipeline/assemble.py"}})
        versions.append({"version": number, "created_at": f"2030-01-0{number}T10:00:00+00:00",
                         "node_attempt": f"n14_video_assembly#a{number}",
                         "artifacts": {"master": master, "captions": captions, "voice_stem": stem, "voice": voice}})
    for index, name in enumerate(["B01-002.50.png", "B02-010.00.png"]):
        add("keyframes", "n16_creative_critique", 3, f"work/n16_creative_critique/a3/keyframes/{name}", 3, b"\x89PNG", item=index)
    verification = add("verification", "n23_final_verification", 1, "work/n23_final_verification/a1/verification.json", 3, b"{}")
    add("manifest", "n24_packaging_delivery", 1, "work/n24_packaging_delivery/a1/manifest.json", 3, b"{}")
    for rel_path, body in (("01-master/gov-master.mp4", MASTER_BYTES[:50]), ("04-voice-only/gov-voice.wav", b"RIFF")):
        target = job / "work" / "n24_packaging_delivery" / "a1" / "package" / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    (job / "work" / "n14_video_assembly" / "a3" / "secret.json").write_text("{}", encoding="utf-8")
    nodes = {NODE_IDS[n]: {"status": "STEP_PASS", "attempts": 1, "outcome": "pass"} for n in range(1, 26)}
    nodes[NODE_IDS[22]] = {"status": "PLANNED", "attempts": 0, "outcome": None}
    if waiting_release:
        nodes[NODE_IDS[23]] = {"status": "WAITING_HUMAN", "attempts": 1, "outcome": None}
    approvals = [{"node": NODE_IDS[3], "decision": "approved", "actor": "Owner (on behalf of Owner; provisional, to be confirmed)",
                  "at": "2030-01-01T09:00:00+00:00", "reason": "Brief is fine for now.", "human": True, "grant": "",
                  "outputs": {"brief": "0123456789abcdef"}}]
    if not waiting_release:
        approvals.append({"node": NODE_IDS[23], "decision": "approved", "actor": "Owner", "at": "2030-01-03T12:00:00+00:00",
                          "reason": "Ship it.", "human": True, "grant": "", "outputs": {"verification": verification[:16]}})
    state = {
        "runtime": {"run_id": "gov-run-1", "status": "WAITING_HUMAN" if waiting_release else "RUN_PASS",
                    "current_version": 3, "nodes": nodes},
        "history": {"versions": versions, "approvals": approvals, "decisions": []},
        "artifacts": artifacts,
        "deliverables": [{"key": "01-master/gov-master.mp4", "path": "01-master/gov-master.mp4", "sha256": "x"},
                         {"key": "04-voice-only/gov-voice.wav", "path": "04-voice-only/gov-voice.wav", "sha256": "y"},
                         {"key": "09-missing/none.json", "path": "09-missing/none.json", "sha256": "z"}],
        "evaluation": {
            "reviews": [
                {"id": "rev-n15-v1", "node": NODE_IDS[15], "version": 1, "reviewer": "technical-qa", "verdict": "rework",
                 "scores": [{"dimension": "TEC-FORMAT", "score": 0.0, "threshold": 1.0, "level": "MUST", "passed": False,
                             "evidence": "tool check"}], "defect_ids": [], "tool_findings": [], "pending_human": []},
                {"id": "rev-n15-v3", "node": NODE_IDS[15], "version": 3, "reviewer": "technical-qa", "verdict": "pass",
                 "scores": [{"dimension": "TEC-FORMAT", "score": 1.0, "threshold": 1.0, "level": "MUST", "passed": True,
                             "evidence": "tool check"}], "defect_ids": [], "tool_findings": [], "pending_human": []},
                {"id": "rev-n16-v3", "node": NODE_IDS[16], "version": 3, "reviewer": "creative-critic", "verdict": "pass",
                 "scores": [{"dimension": "CRE-HOOK", "score": 4, "threshold": 4, "level": "SHOULD", "passed": True,
                             "evidence": "hook lands"}], "defect_ids": ["D001"], "tool_findings": [],
                 "pending_human": ["Owner to judge the music"]},
            ],
            "defects": [
                {"id": "D001", "signature": "CRE-HOOK|n07|rev", "severity": "nit", "dimension": "CRE-HOOK",
                 "description": "The first line could be sharper", "evidence": "0:04.2-0:20.5", "source": "rev-n16-v3",
                 "traced_node": NODE_IDS[7], "status": "open", "first_seen_version": 3, "last_seen_version": 3,
                 "smallest_fix": "Rewrite the first line", "acceptance_check": "re-run review"},
                {"id": "D002", "severity": "P2", "dimension": "MOT-SNAP", "description": "Camera snap", "evidence": "1:05.0-1:06.0",
                 "traced_node": NODE_IDS[13], "status": "resolved", "first_seen_version": 1, "last_seen_version": 2},
                {"id": "D003", "severity": "P3", "dimension": "TEC-LOUD", "description": "A level dip", "evidence": "0:30.1",
                 "traced_node": NODE_IDS[14], "status": "open", "first_seen_version": 2, "last_seen_version": None},
            ],
            "gate_results": [
                {"id": "gate-v2-1", "node": NODE_IDS[21], "version": 2, "outcome": "fail", "reasons": ["D003 blocks"],
                 "open_blocking": ["D003"], "created_at": "2030-01-02T11:00:00+00:00"},
                {"id": "gate-v3-1", "node": NODE_IDS[21], "version": 3, "outcome": "pass", "reasons": ["all reviews pass"],
                 "open_blocking": [], "created_at": "2030-01-03T11:00:00+00:00", "trend": {"open": 0}},
            ],
        },
    }
    write_json(job / ".vpg" / "state.json", state)
    age_tree(job)
    return job


# ---------------------------------------------------------------------------- iteration 3: engine upgrade fixtures
QUALITY_NODES = {26: "n26_look_development", 27: "n27_animatic"}


def quality_engine_yaml():
    """A trimmed synthetic quality workflow: the 25 default nodes plus two human gates in the Design phase."""
    text = engine_yaml().replace("id: video-production\n", "id: video-production-quality\n", 1)
    text = text.replace('name: "Synthetic engine"', 'name: "Synthetic quality engine"', 1)
    head, edges = text.split("edges:\n", 1)
    extra = []
    for num, node_id in QUALITY_NODES.items():
        extra += [
            f"  - id: {node_id}",
            f"    num: {num}",
            f"    name: Synthetic quality gate {num}",
            "    phase: Design",
            "    kind: agent",
            f'    purpose: "Quality gate {num}, for tests"',
            "    inputs: [\"control:design\"]",
            f'    human_review: {{required: true, decision: "Approve the {"look" if num == 26 else "animatic"}", approver: Owner, outcomes: [approved, rejected]}}',
        ]
    return head + "\n".join(extra) + "\nedges:\n" + edges


def write_quality_engine(base):
    """An engine folder with graph/workflow.yaml (25 nodes) and graph/workflow-quality.yaml (27 nodes)."""
    engine = write_engine(base)
    (engine / "graph" / "workflow-quality.yaml").write_text(quality_engine_yaml(), encoding="utf-8")
    return engine


def make_workflow_job(root, rel, workflow, waiting_node=None):
    """A minimal job that selects a workflow; optionally a governed run waiting at one node."""
    job = Path(root) / rel
    job.mkdir(parents=True, exist_ok=True)
    slug = rel.replace("/", "-")
    lines = [f"id: {slug}", f'name: "Workflow job {slug}"']
    if workflow is not None:
        lines.append(f"workflow: {workflow}")
    (job / "vpg.project.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if waiting_node:
        nodes = {waiting_node: {"status": "WAITING_HUMAN", "attempts": 1, "outcome": None,
                                "started_at": "2030-01-01T00:00:04+00:00"}}
        write_json(job / ".vpg" / "state.json", {"runtime": {"run_id": f"{slug}-run", "status": "WAITING_HUMAN",
                                                             "current_version": 0, "nodes": nodes}})
    age_tree(job)
    return job


def ledger_events(events, start_seq=1, previous="0" * 64):
    """Hash-chained ledger lines for [(type, data, ts)], continuing from `previous`."""
    lines = []
    for offset, (kind, data, ts) in enumerate(events):
        record = {"seq": start_seq + offset, "ts": ts, "type": kind, "data": data, "prev": previous}
        record["hash"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        previous = record["hash"]
        lines.append(json.dumps(record))
    return lines, previous


def append_ledger(job, events):
    """Append hash-chained events to a job's .vpg/ledger.jsonl (keeps the prev links consistent)."""
    path = Path(job) / ".vpg" / "ledger.jsonl"
    existing = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    last = existing[-1] if existing else {"seq": 0, "hash": "0" * 64}
    lines, _ = ledger_events(events, start_seq=last["seq"] + 1, previous=last["hash"])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def update_state(job, change):
    """Read .vpg/state.json, let `change(state)` edit it, write it back."""
    path = Path(job) / ".vpg" / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    change(state)
    write_json(path, state)
    return state


TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def write_telemetry(job, run_id="gov-run-1", bad_line=True):
    """.vpg/telemetry/ shaped like the engine's glass-box data contract: spans, evaluation events, a summary."""
    folder = Path(job) / ".vpg" / "telemetry"
    folder.mkdir(parents=True, exist_ok=True)
    spans = [
        {"trace_id": TRACE, "span_id": "00f067aa0ba902b7", "parent_span_id": "a3ce929d0e0e4736", "kind": "CLIENT",
         "name": "invoke_agent creative-critic", "start": "2030-01-03T10:00:01+00:00", "end": "2030-01-03T10:04:40+00:00",
         "status": "OK", "attributes": {"gen_ai.operation.name": "invoke_agent", "gen_ai.provider.name": "vpg",
                                        "gen_ai.agent.name": "creative-critic", "vpg.node.id": "n16_creative_critique",
                                        "vpg.attempt": 1, "vpg.outcome": "pass"},
         "source": {"ledger_seq": 3, "ledger_hash": "0123456789abcdef"}},
        {"trace_id": TRACE, "span_id": "11f067aa0ba902b7", "parent_span_id": "a3ce929d0e0e4736", "kind": "INTERNAL",
         "name": "vpg.direct_run n13_motion_scenes", "start": "2030-01-02T09:00:00+00:00", "end": "2030-01-02T09:10:00+00:00",
         "status": "OK", "attributes": {"vpg.node.id": "n13_motion_scenes", "vpg.direct_run.id": "DR-001", "vpg.governed": False},
         "source": {"ledger_seq": 9, "ledger_hash": "fedcba9876543210"}},
    ]
    evaluations = [
        {"name": "vpg.review.CRE-PACING", "gen_ai.evaluation.name": "vpg.review.CRE-PACING",
         "gen_ai.evaluation.score.value": 3.0, "gen_ai.evaluation.score.label": "fail",
         "gen_ai.evaluation.explanation": "CRE-PACING scored 3.0 against 4 (MUST)", "node": "n16_creative_critique",
         "attempt": 1, "evaluator": "agent:creative-critic", "evidence_tag": "reported",
         "evidence": [{"kind": "reviewer_note", "text": "the second chapter has no pause after the claim at 0:41"}],
         "span_id": "00f067aa0ba902b7", "trace_id": TRACE, "time": "2030-01-03T10:20:11+00:00",
         "source": {"state": "evaluation.reviews[2]"}},
        {"name": "vpg.node.completion", "gen_ai.evaluation.name": "vpg.node.completion",
         "gen_ai.evaluation.score.label": "pass", "gen_ai.evaluation.explanation": "n16_creative_critique attempt 1 passed its completion checks (pass)",
         "node": "n16_creative_critique", "attempt": 1, "evaluator": "engine", "evidence_tag": "observed",
         "evidence": [{"kind": "artifact", "key": "keyframes", "path": "work/n16_creative_critique/a3/keyframes/B01-002.50.png",
                       "sha256": "0123456789abcdef"},
                      {"kind": "check", "description": "review has a verdict"},
                      {"kind": "metric", "metric": "voice_over_music_p10", "value": 16.4, "unit": "LU", "t": 12.5, "frame": 375}],
         "span_id": "00f067aa0ba902b7", "trace_id": TRACE, "time": "2030-01-03T10:04:40+00:00",
         "source": {"ledger_seq": 4, "ledger_hash": "0123456789abcdef"}},
        {"name": "vpg.direct_run", "gen_ai.evaluation.name": "vpg.direct_run", "gen_ai.evaluation.score.label": "ungoverned",
         "gen_ai.evaluation.explanation": "the gate waited", "node": "n13_motion_scenes", "attempt": None,
         "evaluator": "agent:motion", "evidence_tag": "observed",
         "evidence": [{"kind": "output", "key": "film", "path": "candidates/v3/scenes.mp4", "sha256": "abcdef0123456789"}],
         "span_id": "11f067aa0ba902b7", "trace_id": TRACE, "time": "2030-01-02T09:10:00+00:00",
         "source": {"ledger_seq": 9, "ledger_hash": "fedcba9876543210"}},
    ]
    text = "\n".join(json.dumps(event) for event in evaluations)
    if bad_line:
        text += "\n{not json\n[1, 2]"
    (folder / "evaluations.jsonl").write_text(text + "\n", encoding="utf-8")
    (folder / "spans.jsonl").write_text("\n".join(json.dumps(span) for span in spans) + "\n", encoding="utf-8")
    write_json(folder / "summary.json", {"schema": "vpg-telemetry/v1", "run_id": run_id, "trace_id": TRACE, "coverage": {
        "nodes_run": 24, "nodes_with_evidence": 23, "gates": 2, "gate_events": 1, "decisions": 2, "decision_events": 2,
        "evaluations": 3, "spans": 2, "orphan_events": [], "missing": ["node n22_targeted_regeneration", "1 gate decision(s)"],
        "ok": False}})
    return folder
