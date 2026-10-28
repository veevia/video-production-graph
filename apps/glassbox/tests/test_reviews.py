from glassbox.reviews import normalize_defect, normalize_level, normalize_qa, normalize_review, verdict_class


def test_levels_from_varied_severity_strings():
    assert normalize_level("P3 (blocks)") == "P3"
    assert normalize_level("P4 (nit; carried, not re-argued)") == "P4"
    assert normalize_level("nit (P4)") == "P4"
    assert normalize_level("nit") == "P4"
    assert normalize_level(2) == "P2"
    assert normalize_level(None, "SHOULD (nit)") == "P4"
    assert normalize_level("MUST (blocks)") == "P?"
    assert normalize_level(None) == "P?"


def test_defect_from_dict_with_at_or_time():
    defect = normalize_defect({"id": "CRE-D1", "severity": "P3 (blocks)", "dimension": "CRE-STORY",
                               "at": "31.71-33.21 s (CH02 test)", "what": "Drags", "fix": "Trim",
                               "fix_node": "n13", "status": "new"}, "creative-critic", 0)
    assert {k: defect[k] for k in ("id", "level", "dimension", "at_text", "t0", "what", "fix", "status")} == {
        "id": "CRE-D1", "level": "P3", "dimension": "CRE-STORY", "at_text": "31.71-33.21 s (CH02 test)",
        "t0": 31.71, "what": "Drags", "fix": "Trim", "status": "new"}
    assert defect["uid"] == "creative-critic:CRE-D1"
    timed = normalize_defect({"severity": "nit", "time": "00:38.43-00:41.30; 00:56.40", "description": "Label", "smallest_fix": "Raise it",
                              "traced_node": "n18"}, "accessibility", 4)
    assert timed["level"] == "P4" and timed["t0"] == 38.43 and timed["what"] == "Label" and timed["fix"] == "Raise it"
    assert timed["fix_node"] == "n18" and timed["uid"] == "accessibility:#5"


def test_defect_from_string_and_level_keyed_findings():
    review = normalize_review({
        "reviewer": "independent agent review (read-only, delta against v6), 2030-01-01",
        "verdict": "changes requested",
        "findings": {"P1": [], "P2": ["43.23-43.30 s: camera snap"], "P3": ["no time here", {"time": "12 s", "what": "dict item"}]},
    }, "independent-review")
    assert review["verdict_class"] == "rework"
    assert review["label"] == "independent-review"
    levels = [(d["level"], d["t0"], d["what"]) for d in review["defects"]]
    assert levels == [("P2", 43.23, "camera snap"), ("P3", None, "no time here"), ("P3", 12.0, "dict item")]
    assert review["worst_level"] == "P2" and review["defect_count"] == 3


def test_review_scores_carried_and_dedupe():
    review = normalize_review({
        "reviewer": "agent:technical-qa", "verdict": "pass",
        "scores": [{"dimension": "TEC-A", "score": 5}, {"dimension": "TEC-B", "score": None, "result": "meets"},
                   {"name": "LEG-C", "score": "4"}, {"dimension": "TEC-D", "score": "pass"}],
        "defects": [{"id": "X", "severity": "P4"}, {"id": "X", "severity": "P3"}],
        "v24_defects": [{"id": "OLD-1", "status": "fixed", "evidence": "gone"}, "string carried"],
    }, "technical-qa")
    assert review["label"] == "technical-qa"
    assert [(s["dimension"], s["score"], s["result"]) for s in review["scores"]] == [
        ("TEC-A", 5, None), ("TEC-B", None, "meets"), ("LEG-C", 4, None), ("TEC-D", None, "pass")]
    assert [d["uid"] for d in review["defects"]] == ["technical-qa:X", "technical-qa:X~2"]
    assert review["carried"][0] == {"id": "OLD-1", "from": "v24", "status": "fixed", "evidence": "gone", "what": None}
    assert review["carried"][1]["what"] == "string carried"
    assert review["worst_level"] == "P3"


def test_verdict_classes():
    assert verdict_class("pass") == "pass"
    assert verdict_class("pass with nits") == "pass"
    assert verdict_class("rework") == "rework"
    assert verdict_class("revise") == "rework"
    assert verdict_class(None) == "none"
    assert verdict_class("maybe") == "unknown"


def test_qa_gates_pass_rule():
    qa = normalize_qa({"summary": {"findings": 2, "blocking": 1}, "gates": {
        "A": {"findings": []},
        "B": {"findings": ["one", {"check": "C1", "severity": "P3", "message": "bad"}]},
        "C": {"ok": False, "messages": ["broken"]},
        "D": {"ok": True, "messages": []},
    }})
    passes = {g["name"]: g["pass"] for g in qa["gates"]}
    assert passes == {"A": True, "B": False, "C": False, "D": True}
    gate_b = qa["gates"][1]
    assert gate_b["findings"][0] == {"text": "one"}
    assert ["check", "C1"] in gate_b["findings"][1]["fields"]
    assert gate_b["findings_norm"][1]["id"] == "C1" and gate_b["findings_norm"][1]["level"] == "P3"
    assert qa["summary"] == {"findings": 2, "blocking": 1}
    assert normalize_qa("not a dict") is None


def test_reviewer_label_is_short_and_falls_back_to_the_key():
    from glassbox.reviews import reviewer_label
    assert reviewer_label("agent:technical-qa", "technical-qa") == "technical-qa"
    assert reviewer_label("design-system-reviewer", "n17") == "design-system"
    assert reviewer_label("-reviewer", "n17") == "-reviewer"
    assert reviewer_label("", "takeaway-60") == "takeaway-60"
    assert reviewer_label("x" * 40, "n16") == "n16"
