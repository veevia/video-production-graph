"""P3: pairwise judging (order swap, anchors, Bradley-Terry with bootstrap intervals), calibration (Cohen's kappa,
Krippendorff's alpha) and the saturation alarm.

Acceptance (handoff brief, P3): unit tests for order swap, ties, BT ranking, kappa and alpha, and the saturation alarm;
documented judge packets (schemas/judge-packet.schema.json, schemas/judge-verdict.schema.json, docs/judging.md).
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess

import jsonschema
import pytest

from tests.conftest import PKG
from vpg import cli
from vpg import judging as jd

QUALITY = {"REF": 0.9, "C1": 0.7, "C2": 0.5, "C3": 0.3, "ANCHOR": 0.05}   # the truth a simulated judge sees


def _items(ids):
    return [{"id": i, "media": {"master": {"path": f"films/{i}.mp4", "sha256": f"{n:064x}"}}} for n, i in enumerate(ids, 1)]


FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_items(root, ids, av=False):
    """Items whose media exist on disk with true sha256s, at paths that name the film the way a project does; with
    `av`, a short clip whose container title names it too."""
    out = []
    for i in ids:
        f = root / "films" / i / ("master.MP4" if av else "notes.TXT")
        f.parent.mkdir(parents=True, exist_ok=True)
        if av:
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=0.5",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-metadata", f"title=film {i}", "-shortest", str(f)], check=True)
        else:
            f.write_bytes(f"film {i}".encode())
        out.append({"id": i, "media": {"master": {"path": str(f), "sha256": _sha(f)}}})
    return out


def _batch(seed=0):
    items = {i["id"]: i for i in _items(QUALITY)}
    return jd.make_batch([items[c] for c in ("C1", "C2", "C3")], items["REF"], items["ANCHOR"], "ground stays quiet",
                         ["DS-GROUND-001"], seed=seed)


def _judge(packet, key, policy):
    """A simulated judge that only sees the blinded packet; `policy(first_id, second_id)` uses the key to stand in for
    what the judge would perceive."""
    return {"schema": jd.VERDICT_SCHEMA, "batch": packet["batch"], "judge": "agent:test-judge",
            "verdicts": [{"presentation": p["id"], "pick": policy(*key["presentations"][p["id"]]), "rules": ["DS-GROUND-001"]}
                         for p in packet["presentations"]]}


def honest(first, second):
    return "first" if QUALITY[first] > QUALITY[second] else "second"


# ------------------------------------------------------------------------------------------------ order swap and ties
def test_order_swap_needs_both_orders_to_agree():
    assert jd.consolidate("A", "B", "first", "second")["winner"] == "A"        # A won in both orders
    biased = jd.consolidate("A", "B", "first", "first")                        # always the first shown
    assert biased["winner"] is None and biased["position_bias"] and not biased["consistent"]
    assert jd.consolidate("A", "B", "tie", "tie") == {"a": "A", "b": "B", "winner": None, "consistent": True, "position_bias": False}
    half = jd.consolidate("A", "B", "first", "tie")
    assert half["winner"] is None and not half["consistent"]
    with pytest.raises(jd.JudgingError):
        jd.consolidate("A", "B", "left", "first")


def test_ties_count_half_each_way():
    tie = [{"a": "A", "b": "B", "winner": None}] * 10
    s = jd.bradley_terry(["A", "B"], tie)
    assert s["A"] == pytest.approx(s["B"], abs=1e-6)
    one_win = tie + [{"a": "A", "b": "B", "winner": "A"}]
    assert jd.bradley_terry(["A", "B"], one_win)["A"] > 0 > jd.bradley_terry(["A", "B"], one_win)["B"]


# ------------------------------------------------------------------------------------------------ Bradley-Terry
def test_bradley_terry_recovers_the_true_order_with_intervals():
    rng = random.Random(3)
    ids = ["A", "B", "C", "D"]
    true = {"A": 1.5, "B": 0.5, "C": -0.5, "D": -1.5}
    comps = []
    for _ in range(40):
        for a, b in [(x, y) for i, x in enumerate(ids) for y in ids[i + 1:]]:
            pa = 1 / (1 + pow(2.718281828, true[b] - true[a]))
            comps.append({"a": a, "b": b, "winner": a if rng.random() < pa else b})
    rows = jd.rank(ids, comps, rounds=120, seed=1)
    assert [r["item"] for r in rows] == ids
    for r in rows:
        assert r["ci"][0] <= r["strength"] <= r["ci"][1]
    assert rows[0]["separated_from_next"] is True and rows[-1]["separated_from_next"] is None
    assert jd.rank(ids, comps, rounds=120, seed=1) == rows            # seeded: the same intervals every time


def test_fewer_comparisons_give_wider_intervals():
    many = [{"a": "A", "b": "B", "winner": "A"}] * 30 + [{"a": "A", "b": "B", "winner": "B"}] * 10
    few = many[::10]
    wide = lambda rows: rows[0]["ci"][1] - rows[0]["ci"][0]  # noqa: E731
    assert wide(jd.rank(["A", "B"], few, rounds=150)) > wide(jd.rank(["A", "B"], many, rounds=150))


def test_an_unbeaten_item_keeps_a_finite_strength():
    s = jd.bradley_terry(["A", "B", "C"], [{"a": "A", "b": "B", "winner": "A"}] * 5)
    assert all(abs(v) < 10 for v in s.values()) and s["C"] == pytest.approx(0.0, abs=0.3)


# ------------------------------------------------------------------------------------------------ batches and anchors
def test_a_batch_shows_every_pair_in_both_orders_and_blinds_the_items():
    packet, key = _batch()
    jsonschema.validate(packet, json.loads((PKG / "schemas/judge-packet.schema.json").read_text()))
    assert len(packet["presentations"]) == 2 * 10                      # 5 items: 10 pairs, each in both orders
    shown = [tuple(v) for v in key["presentations"].values()]
    assert {(b, a) for a, b in shown} == set(shown)
    text = json.dumps(packet)
    assert not any(i in text for i in QUALITY) and "films/" not in text   # no item id or file name reaches the judge
    staged = {s["to"]: s["from"] for s in key["staging"]}
    for p in packet["presentations"]:
        for side in ("first", "second"):
            path = p[side]["media"]["master"]["path"]
            assert path == f"media/{p[side]['label']}/master.mp4" and staged[path].startswith("films/")
    assert _batch(seed=0) == (packet, key) and _batch(seed=1)[0]["presentations"] != packet["presentations"]
    items = _items(QUALITY)
    items[1]["media"]["poster"] = {"sha256": "ab" * 32}                  # no path: nothing to show, and no hash either
    packet, _ = jd.make_batch(items[1:4], items[0], items[4], "ground", [])
    assert "ab" * 32 not in json.dumps(packet)
    items[1]["media"]["poster"] = {"path": "films/C1.MXF2"}
    with pytest.raises(jd.JudgingError, match="cannot be blinded"):     # a type pack cannot strip is refused
        jd.make_batch(items[1:4], items[0], items[4], "ground", [])


def test_an_honest_judge_ranks_the_truth_and_passes_the_anchors():
    packet, key = _batch()
    verdict = _judge(packet, key, honest)
    jsonschema.validate(verdict, json.loads((PKG / "schemas/judge-verdict.schema.json").read_text()))
    res = jd.score_batch(key, verdict)
    assert res["valid"] and res["below_anchor"] == [] and res["beats_reference"] == []
    assert [r["item"] for r in res["ranking"]] == ["REF", "C1", "C2", "C3", "ANCHOR"]
    assert res["position"]["inconsistent_pairs"] == 0


def test_a_position_biased_judge_cannot_pass_the_anchor_check():
    packet, key = _batch()
    res = jd.score_batch(key, _judge(packet, key, lambda f, s: "first"))
    assert not res["valid"] and "did not beat the degraded anchor" in res["why_invalid"]
    assert res["position"]["first_share"] == 1.0 and res["position"]["inconsistent_pairs"] == res["position"]["pairs"]
    assert all(c["winner"] is None for c in res["comparisons"])


def test_a_candidate_that_loses_to_the_degraded_anchor_is_flagged():
    packet, key = _batch()
    worse = dict(QUALITY, C3=0.01)
    res = jd.score_batch(key, _judge(packet, key, lambda f, s: "first" if worse[f] > worse[s] else "second"))
    assert res["valid"] and res["below_anchor"] == ["C3"]


def test_a_verdict_must_answer_every_presentation_for_its_batch():
    packet, key = _batch()
    verdict = _judge(packet, key, honest)
    verdict["verdicts"].pop()
    with pytest.raises(jd.JudgingError, match="no verdict"):
        jd.score_batch(key, verdict)
    with pytest.raises(jd.JudgingError, match="batch"):
        jd.score_batch(key, dict(_judge(packet, key, honest), batch="other"))


# ------------------------------------------------------------------------------------------------ agreement
def test_krippendorff_alpha_matches_the_published_example():
    """Krippendorff (2011), 'Computing Krippendorff's alpha-reliability': four coders, twelve units, missing values."""
    n = None
    coders = [[1, 2, 3, 3, 2, 1, 4, 1, 2, n, n, n], [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, n, 3],
              [n, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, n], [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, n]]
    units = [[c[i] for c in coders] for i in range(12)]
    assert jd.krippendorff_alpha(units, "nominal") == pytest.approx(0.743, abs=0.001)
    assert jd.krippendorff_alpha(units, "ordinal") == pytest.approx(0.815, abs=0.001)
    assert jd.krippendorff_alpha(units, "interval") == pytest.approx(0.849, abs=0.001)


def test_cohen_kappa():
    assert jd.cohen_kappa(list("abab"), list("abab")) == 1.0
    assert jd.cohen_kappa(list("aabbcc"), list("aabbcb")) == pytest.approx(0.75)
    assert jd.cohen_kappa(["a", "b"] * 10, ["b", "a"] * 10) < 0


def test_judge_status_bands_and_the_advisory_rule():
    perfect = [("A", "A"), ("B", "B"), ("tie", "tie")] * 10
    assert jd.judge_status(perfect)["status"] == "reliable" and not jd.judge_status(perfect)["advisory"]
    assert jd.judge_status(perfect[:12])["status"] == "insufficient"          # fewer than 30 labels: advisory
    rng = random.Random(5)
    noisy = [(rng.choice("AB"), rng.choice("AB")) for _ in range(40)]
    s = jd.judge_status(noisy)
    assert s["status"] == "unreliable" and s["advisory"] and s["labels"] == 40
    mostly = [("A", "A")] * 18 + [("B", "B")] * 18 + [("A", "B")] * 3 + [("B", "A")] * 2
    assert jd.judge_status(mostly)["status"] in ("tentative", "reliable")


def test_labels_come_from_named_humans_and_feed_agreement(tmp_path):
    with pytest.raises(jd.JudgingError, match="named human"):
        jd.label("C1", "C2", "C1", "agent:critic")
    with pytest.raises(jd.JudgingError, match="pick is"):
        jd.label("C1", "C2", "C9", "Wei")
    packet, key = _batch()
    res = jd.score_batch(key, _judge(packet, key, honest))
    labels = [jd.label(c["a"], c["b"], c["winner"] or "tie", "Wei", note="ground is calmer", t=12.0, version="v3")
              for c in res["comparisons"]]
    agree = jd.agreement_from([res], labels)
    assert agree["labels"] == 10 and agree["alpha"] == 1.0 and agree["status"] == "insufficient"


# ------------------------------------------------------------------------------------------------ saturation
def test_the_saturation_alarm():
    assert jd.saturation({"v1": [5, 5, 4, 5], "v2": [5, 4, 5, 5]}).ceiling_alarm             # 75% at the maximum
    collapsed = jd.saturation({"v1": [4, 4, 4, 4], "v2": [4, 4, 4, 4], "v3": [4, 4, 4, 4]})
    assert collapsed.collapse_alarm and not collapsed.ceiling_alarm and collapsed.alarm
    healthy = jd.saturation({"v1": [2, 3, 3, 2], "v2": [3, 4, 3, 4], "v3": [4, 5, 4, 3]})
    assert not healthy.alarm and healthy.ceiling_share <= jd.CEILING_SHARE
    assert jd.saturation({"v1": [5] * 15 + [4] * 85}).ceiling_alarm is False                  # exactly 15% is not above
    assert jd.saturation({}).alarm is False


def test_review_saturation_reads_recorded_scores():
    review = lambda v, scores: {"node": "n16_creative_critique", "version": v, "carried_forward": False,  # noqa: E731
                                "scores": [{"dimension": f"D{i}", "score": s, "threshold": 4} for i, s in enumerate(scores)]
                                + [{"dimension": "TOOL", "score": 1.0, "threshold": 1.0}]}
    state = {"evaluation": {"reviews": [review(1, [5, 5, 4]), review(2, [5, 4, 5]), review(3, [5, 5, 5])]}}
    out = jd.review_saturation(state)["n16_creative_critique"]
    assert out["alarm"] and out["versions"] == 3 and out["scores"] == 9


# ------------------------------------------------------------------------------------------------ the CLI
def test_cli_pack_stages_neutral_copies_and_refuses_a_changed_film(tmp_path, capsys):
    items, out = tmp_path / "items.json", tmp_path / "judge"
    real = _real_items(tmp_path, QUALITY)
    items.write_text(json.dumps(real))
    out.mkdir()
    args = ["judge", "pack", "--items", str(items), "--reference", "REF", "--anchor", "ANCHOR", "--criterion", "ground",
            "--out", str(out / "packet.json"), "--key", str(tmp_path / "key.json")]
    assert cli.main(args) == 0
    packet, key = json.loads((out / "packet.json").read_text()), json.loads((tmp_path / "key.json").read_text())
    assert not any(i in json.dumps(packet) for i in QUALITY) and "films/" not in json.dumps(packet)
    shown = {s["label"]: s["media"]["master"] for p in packet["presentations"] for s in (p["first"], p["second"])}
    for item_id, label in key["labels"].items():
        copy = out / "packet-media" / label / "master.txt"
        assert copy.read_bytes() == f"film {item_id}".encode()          # the right film behind each neutral name
        assert shown[label] == {"path": f"packet-media/{label}/master.txt"}   # a byte copy's hash would be the original's
    assert not any(i["media"]["master"]["sha256"] in json.dumps(packet) for i in real)
    assert "note:" not in capsys.readouterr().err                       # the key is outside the packet's directory
    (tmp_path / "films" / "C2" / "notes.TXT").write_bytes(b"re-rendered")
    assert cli.main(args) == 4                                          # the film changed since the items were listed
    assert "does not match its sha256" in capsys.readouterr().err
    assert cli.main(args[:-4] + ["--out", str(out / "p2.json"), "--key", str(tmp_path / "k2.json"),
                                 "--media-dir", str(tmp_path / "elsewhere")]) == 4   # copies stay beside the packet
    assert not (out / "p2.json").exists()


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg is required to stage audio and video")
def test_pack_strips_the_container_metadata_that_names_a_film(tmp_path):
    items = tmp_path / "items.json"
    real = _real_items(tmp_path, ["REF", "C1", "ANCHOR"], av=True)
    items.write_text(json.dumps(real))
    assert cli.main(["judge", "pack", "--items", str(items), "--reference", "REF", "--anchor", "ANCHOR", "--criterion",
                     "ground", "--out", str(tmp_path / "packet.json"), "--key", str(tmp_path / "keys" / "key.json")]) == 0
    key = json.loads((tmp_path / "keys" / "key.json").read_text())
    packet = json.loads((tmp_path / "packet.json").read_text())
    shown = {s["label"]: s["media"]["master"] for p in packet["presentations"] for s in (p["first"], p["second"])}
    assert not any(i["media"]["master"]["sha256"] in json.dumps(packet) for i in real)   # sealed: no original's hash

    def tags(f):
        return json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(f)],
                                         capture_output=True, check=True).stdout).get("format", {}).get("tags", {})

    def packets(f):     # the streams' packets: equal means the copy is lossless
        return subprocess.run([FFMPEG, "-v", "error", "-i", str(f), "-map", "0", "-c", "copy", "-f", "streamhash",
                               "-hash", "sha256", "-"], capture_output=True, check=True).stdout
    for item in real:
        src = tmp_path / "films" / item["id"] / "master.MP4"
        copy = tmp_path / "packet-media" / key["labels"][item["id"]] / "master.mp4"
        assert tags(src)["title"] == f"film {item['id']}" and "title" not in tags(copy)
        assert packets(copy) == packets(src) and _sha(copy) != _sha(src)
        assert shown[key["labels"][item["id"]]]["sha256"] == _sha(copy)


def test_pack_refuses_media_it_cannot_blind(tmp_path, capsys):
    real = _real_items(tmp_path, QUALITY)
    odd = tmp_path / "films" / "C1" / "master"                          # no suffix: neither remuxed nor a known copy
    odd.write_bytes(b"film C1")
    real[1]["media"]["master"] = {"path": str(odd), "sha256": _sha(odd)}
    (tmp_path / "items.json").write_text(json.dumps(real))
    assert cli.main(["judge", "pack", "--items", str(tmp_path / "items.json"), "--reference", "REF", "--anchor", "ANCHOR",
                     "--criterion", "ground", "--out", str(tmp_path / "out" / "packet.json"),
                     "--key", str(tmp_path / "key.json")]) == 4
    assert "suffix-less file cannot be blinded" in capsys.readouterr().err
    assert not (tmp_path / "out").exists() and not (tmp_path / "key.json").exists()


def test_cli_pack_score_and_label(tmp_path, capsys):
    items = tmp_path / "items.json"
    items.write_text(json.dumps(_real_items(tmp_path, QUALITY)))
    packet, key = tmp_path / "packet.json", tmp_path / "key.json"
    assert cli.main(["judge", "pack", "--items", str(items), "--reference", "REF", "--anchor", "ANCHOR", "--criterion",
                     "ground stays quiet", "--rule", "DS-GROUND-001", "--out", str(packet), "--key", str(key)]) == 0
    p, k = json.loads(packet.read_text()), json.loads(key.read_text())
    verdict = tmp_path / "verdict.json"
    verdict.write_text(json.dumps(_judge(p, k, honest)))
    assert cli.main(["judge", "score", "--key", str(key), "--verdict", str(verdict), "--out", str(tmp_path / "r.json")]) == 0
    assert "1. REF" in capsys.readouterr().out
    verdict.write_text(json.dumps(_judge(p, k, lambda f, s: "first")))
    assert cli.main(["judge", "score", "--key", str(key), "--verdict", str(verdict)]) == 1    # not trusted
    labels = tmp_path / "labels.jsonl"
    assert cli.main(["judge", "label", "--labels", str(labels), "--a", "C1", "--b", "C2", "--pick", "C1", "--by", "Wei",
                     "--t", "14.5", "--note", "calmer ground"]) == 0
    assert cli.main(["judge", "label", "--labels", str(labels), "--a", "C1", "--b", "C2", "--pick", "C1",
                     "--by", "agent:x"]) == 4
    assert len(labels.read_text().splitlines()) == 1
