from glassbox.adapters import JobReader
from glassbox.mapping import aggregate, gate_node_num, governed_class, reviewer_node_num, score_node_num
from glassbox.needs import needs_you, sort_needs
from synthetic import write_reviews_only


def test_scores_map_by_dimension_prefix():
    cases = {
        ("TEC-LOUDNESS", 16): 15, ("CRE-STORY", 15): 16, ("DES-TOKENS", 16): 17, ("UNCHANGED-VISUALS", 19): 17,
        ("LEG-SIZE", 15): 18, ("A11Y-CONTRAST", 15): 18, ("CAP-SCRIPT", 15): 18, ("ACC-BOUNDARIES", 18): 19,
        ("PA-CLAIMS", 18): 19, ("NAR-ARC", 16): 20, ("TAKEAWAY-60", 19): 20, ("T3", 19): 20, ("VOI-ADULT", 16): 12,
        ("ACC-CAPTIONS", 15): 18, ("MOTION-COMFORT", 18): 18, ("TEXT-MAIN", 16): 16, ("PAC-RATE", 16): 16, ("T12", 19): 19,
    }
    for (dimension, reviewer), expected in cases.items():
        assert score_node_num(dimension, reviewer) == expected, dimension


def test_gates_map_by_name():
    assert gate_node_num("N15 technical") == 15
    assert gate_node_num("N18 legibility + captions") == 18
    assert gate_node_num("N12 voice") == 12
    assert gate_node_num("CAP-AUDIO + CAP-HEARD") == 18
    assert gate_node_num("VOI-SEAMS") == 12
    assert gate_node_num("PAC-RATE") == 20
    assert gate_node_num("RUNTIME") == 14
    for name in ("STILLS", "MOTION", "HOLD", "EDGES", "COVER", "LOUPE", "RINGS", "NATIVE", "DENSITY", "TEXT-MAIN", "CONCISE", "STYLE", "BEAT"):
        assert gate_node_num(name) == 13, name
    assert gate_node_num("MIX-FLOOR") == 14 and gate_node_num("SCORE") == 14
    assert gate_node_num("NAME") == 19
    assert gate_node_num("SOMETHING-NEW") == 15


def test_reviewer_files_map_to_nodes():
    keys = ("technical-qa", "creative-critic", "accessibility", "product-accuracy", "takeaway-60", "takeaway-90", "independent-review")
    assert [reviewer_node_num(key) for key in keys] == [15, 16, 18, 19, 20, 20, 16]


def test_defects_map_by_fix_node_else_reviewer(project, graph, store):
    reader = JobReader(project, graph, store)
    reviews = {review["key"]: review for review in reader.reviews("v10")}
    assert reviews["creative-critic"]["defects"][0]["node"] == "n13_motion_scenes"
    assert reviews["technical-qa"]["defects"][0]["node"] == "n14_video_assembly"
    assert reviews["accessibility"]["defects"][0]["node"] == "n18_accessibility_legibility"
    assert reviews["product-accuracy"]["defects"][0]["node"] == "n19_product_accuracy"
    scores = {s["dimension"]: s["node"] for review in reviews.values() for s in review["scores"]}
    assert scores["VOI-CLARITY"] == "n12_voice_audio"
    assert scores["DES-TOKENS"] == "n17_design_compliance"
    assert scores["T1"] == "n20_narrative_pacing"
    assert scores["ACC-CAPTIONS"] == "n18_accessibility_legibility"
    assert scores["MOTION-COMFORT"] == "n18_accessibility_legibility"
    gates = {gate["name"]: gate["node"] for gate in reader.qa("v10")["gates"]}
    assert gates == {"N15 technical": "n15_technical_qa", "STILLS": "n13_motion_scenes", "CAP-SCRIPT": "n18_accessibility_legibility",
                     "VOI-SEAMS": "n12_voice_audio", "MIX-FLOOR": "n14_video_assembly", "MYSTERY-GATE": "n15_technical_qa"}


def test_status_aggregation_is_worst_of():
    assert aggregate([])[0] == "not_run"
    assert aggregate([("passed", "a"), ("warnings", "b")])[0] == "warnings"
    assert aggregate([("passed", "a"), ("failed", "b"), ("waiting", "c")]) == ("failed", "b; c; a")
    assert aggregate([("direct", "d"), ("not_run", "n")])[0] == "direct"
    assert aggregate([("not_run", "x"), ("passed", "y")])[0] == "passed"
    assert aggregate([("unknown", "u"), ("passed", "p")])[0] == "unknown"
    assert governed_class("STEP_PASS") == "passed"
    assert governed_class("WAITING_HUMAN") == "waiting"
    assert governed_class("PLANNED") == "not_run"
    assert governed_class("RUNNING") == "running"
    assert governed_class("STEP_FAIL") == "failed"
    assert governed_class("SOMETHING_ELSE") == "unknown"
    assert governed_class(None) is None


def test_node_status_for_a_version(project, graph, store):
    nodes = {node["id"]: node for node in JobReader(project, graph, store).node_entries("v10")}
    assert len(nodes) == 25
    assert (nodes["n01_context_intake"]["status"], nodes["n01_context_intake"]["provenance"]) == ("passed", "governed")
    assert nodes["n03_objective_audience"]["status"] == "waiting"
    assert nodes["n03_objective_audience"]["human_gate"]["decision"] == "Decide on node 3"
    assert nodes["n13_motion_scenes"]["status"] == "failed"
    assert "direct run (not in engine ledger)" in nodes["n13_motion_scenes"]["status_reason"]
    assert nodes["n13_motion_scenes"]["evidence_counts"]["defects_by_level"]["P3"] == 1
    assert nodes["n14_video_assembly"]["status"] == "warnings"
    assert nodes["n15_technical_qa"]["status"] == "passed"
    assert nodes["n16_creative_critique"]["status"] == "failed"
    assert nodes["n17_design_compliance"]["status"] == "direct"
    assert nodes["n18_accessibility_legibility"]["status"] == "warnings"
    assert nodes["n12_voice_audio"]["status"] == "passed"
    assert nodes["n23_final_verification"]["status"] == "not_run"
    assert "gates not passed yet" in nodes["n23_final_verification"]["status_reason"]
    assert nodes["n05_narrative_ideation"]["status"] == "not_run"
    assert nodes["n21_feedback_interpretation"]["owns_gates"] is True


def test_needs_ordering():
    items = [
        {"kind": "in_flight", "project": "B", "version": "v3"},
        {"kind": "open_defect", "level": "P3", "project": "A", "version": "v2"},
        {"kind": "unreviewed", "project": "A", "version": "v2"},
        {"kind": "human_gate", "project": "Z"},
        {"kind": "rework_verdict", "project": "A", "version": "v2"},
        {"kind": "open_defect", "level": "P1", "project": "C", "version": "v9"},
        {"kind": "open_defect", "level": "P2", "project": "A", "version": "v2"},
    ]
    assert [(i["kind"], i.get("level")) for i in sort_needs(items)] == [
        ("human_gate", None), ("open_defect", "P1"), ("open_defect", "P2"), ("open_defect", "P3"),
        ("rework_verdict", None), ("unreviewed", None), ("in_flight", None)]


def test_needs_for_a_job(project, graph, store):
    items = needs_you([JobReader(project, graph, store)])
    assert items[0]["kind"] == "human_gate" and items[0]["node"] == "n03_objective_audience"
    defect = next(item for item in items if item["kind"] == "open_defect")
    assert defect["level"] == "P3" and defect["version"] == "v10" and "t=31.71" in defect["link"]
    assert defect["action"] == "Seek to finding"
    kinds = [item["kind"] for item in items]
    assert "rework_verdict" in kinds and "in_flight" not in kinds


def test_release_gate_decisions_and_in_flight(job, project, graph, store):
    write_reviews_only(job, "v11", "pass")
    (job / "candidates" / "v11" / "n14_video_assembly").mkdir(parents=True)
    (job / "candidates" / "v11" / "n14_video_assembly" / "master.mp4").write_bytes(b"x" * 10)
    status = job / "work" / "film-v12" / "STATUS.md"
    status.parent.mkdir(parents=True)
    status.write_text("rendering\n")
    reader = JobReader(project, graph, store)
    items = needs_you([reader])
    release = [item for item in items if item["kind"] == "human_gate" and item.get("version") == "v11"]
    assert release and release[0]["node"] == "n23_final_verification" and release[0]["title"] == "Decide release for v11"
    assert [item["version"] for item in items if item["kind"] == "in_flight"] == ["v12"]
    assert items[-1]["kind"] == "in_flight"
    assert {n["id"]: n for n in reader.node_entries("v11")}["n23_final_verification"]["status"] == "waiting"
    store.append({"actor": "owner", "pid": project.id, "project_path": project.path, "kind": "approve", "version": "v11",
                  "target": {"type": "gate", "id": "n23_final_verification", "version": "v11"}, "t": None, "tags": [], "text": ""})
    reader = JobReader(project, graph, store)
    assert not [item for item in needs_you([reader]) if item["kind"] == "human_gate" and item.get("version") == "v11"]
    n23 = {n["id"]: n for n in reader.node_entries("v11")}["n23_final_verification"]
    assert n23["status"] == "passed" and "recorded" in n23["status_reason"]
    assert n23["gate_state"]["state"] == "recorded"
