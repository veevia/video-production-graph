"""Tests for the vpg score system v1 reference (run: python -m unittest test_vpg_score.py)."""
import importlib.util
import json
import random
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("vpg_score", HERE / "vpg_score.py")
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)

DIMS = [{"id": "CON-FIT", "measured": False}, {"id": "NAR-ARC", "measured": False}, {"id": "PAC-READ", "measured": True}]
TRUE_W = {"CON-FIT": 0.6, "NAR-ARC": 0.3, "PAC-READ": 0.1}   # the synthetic principal's hidden taste


def synth(n, seed=7, noise=0.35, k=3, fork_type="concept"):
    """Forks where the agent scores 1-5 with noise and the principal ranks by hidden weights plus noise."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        cands = []
        for j in range(k):
            true = {d["id"]: rng.uniform(1, 5) for d in DIMS}
            agent = {d: min(5, max(1, round(v + rng.gauss(0, 0.5)))) for d, v in true.items()}
            util = 6 * sum(TRUE_W[d] * (true[d] - 1) / 4 for d in true) + rng.gauss(0, noise)
            cands.append(({"id": "ABCDE"[j], "scores": agent, "passes_rubric": True}, util))
        ranking = [c["id"] for c, _ in sorted(cands, key=lambda t: -t[1])]
        out.append({"fork_id": f"F-{i:03d}", "fork_type": fork_type, "dims": DIMS,
                    "candidates": [c for c, _ in cands], "ranking": ranking, "blind": True})
    return out


def pairwise_accuracy(model, decisions):
    hits = total = 0
    for dec in decisions:
        p = {r["id"]: r["p"] for r in vs.score(model, dec, calibrated=False)["ranked"]}
        for a, b in vs.pairs_from_ranking(dec["ranking"]):
            hits += p[a] > p[b]
            total += 1
    return hits / total


def l1(w):
    return sum(abs(w[d] - TRUE_W[d]) for d in TRUE_W)


class ScoreSystemTests(unittest.TestCase):
    def test_pairs_from_ranking(self):
        self.assertEqual(vs.pairs_from_ranking(["B", "A", "C"]), [("B", "A"), ("B", "C"), ("A", "C")])

    def test_confidence_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(vs.CONF_WEIGHTS.values()), 1.0)

    def test_rubric_is_the_floor(self):
        fork = {"fork_id": "F", "fork_type": "concept", "dims": DIMS, "candidates": [
            {"id": "A", "scores": {"CON-FIT": 5, "NAR-ARC": 5, "PAC-READ": 5}, "passes_rubric": False},
            {"id": "B", "scores": {"CON-FIT": 3, "NAR-ARC": 3, "PAC-READ": 3}},
            {"id": "C", "scores": {"CON-FIT": 2, "NAR-ARC": 2, "PAC-READ": 2}}]}
        s = vs.score(vs.new_model(), fork)
        self.assertEqual(s["dropped_by_rubric"], ["A"])
        self.assertNotIn("A", [r["id"] for r in s["ranked"]])
        for c in fork["candidates"]:
            c["passes_rubric"] = False
        with self.assertRaises(ValueError):
            vs.score(vs.new_model(), fork)

    def test_builds_one_only_when_calibrated_and_never_for_listening(self):
        dims = [dict(d, measured=True) for d in DIMS]
        fork = {"fork_id": "F", "fork_type": "concept", "dims": dims, "candidates": [
            {"id": "A", "scores": {"CON-FIT": 5, "NAR-ARC": 5, "PAC-READ": 5}},
            {"id": "B", "scores": {"CON-FIT": 1, "NAR-ARC": 1, "PAC-READ": 1}},
            {"id": "C", "scores": {"CON-FIT": 1, "NAR-ARC": 2, "PAC-READ": 1}}]}
        model = vs.new_model()
        model["types"] = {"concept": {"theta": {}, "n": 10}, "voice_music": {"theta": {}, "n": 10}}
        s = vs.score(model, fork, calibrated=False)
        self.assertEqual(s["band"], "assert")
        self.assertEqual(s["k"], 2)                                  # uncalibrated: at least two
        self.assertEqual(vs.score(model, fork, calibrated=True)["k"], 1)
        listening = dict(fork, fork_type="voice_music")
        self.assertEqual(vs.score(model, listening, calibrated=True)["k"], 2)
        weak = dict(fork, candidates=[dict(c, scores={"CON-FIT": 3, "NAR-ARC": 3, "PAC-READ": 3}) for c in fork["candidates"]])
        self.assertEqual(vs.score(vs.new_model(), weak)["k"], 3)     # a tie: the agent can't predict, so it builds three

    def test_learning_moves_weights_toward_the_principal(self):
        model = vs.fit(synth(60, seed=1))
        w = vs.weights(model, "concept", [d["id"] for d in DIMS])
        uniform = {d: 1 / 3 for d in TRUE_W}
        self.assertLess(l1(w), l1(uniform))
        self.assertEqual(max(w, key=w.get), "CON-FIT")

    def test_fitted_model_predicts_held_out_choices_better_than_the_start(self):
        model = vs.fit(synth(60, seed=1))
        test = synth(60, seed=2)
        self.assertGreater(pairwise_accuracy(model, test), pairwise_accuracy(vs.new_model(), test))

    def test_prequential_metrics(self):
        m, rows = vs.evaluate(synth(30, seed=3), epochs=120)
        self.assertEqual(m["n_blind"], 30)
        self.assertEqual(len(rows), 30)
        self.assertTrue(0.0 <= m["ece"] <= 1.0)
        self.assertGreater(m["top1"], 1 / 3)                          # better than a random pick of three

    def test_retrospective_decisions_stay_out_of_calibration(self):
        decs = [dict(d, retrospective=True) for d in synth(5, seed=4)]
        m, _ = vs.evaluate(decs, epochs=50)
        self.assertEqual((m["n"], m["n_blind"], m["ece"]), (5, 0, None))

    def test_calibration_layer_lowers_out_of_sample_ece(self):
        m, rows = vs.evaluate(synth(60, seed=3), epochs=120)
        self.assertLess(m["ece_calibrated"], m["ece"])
        self.assertEqual(vs.calibrate(0.7, None), 0.7)
        self.assertAlmostEqual(vs.calibrate(0.7, {"a": 1.0, "b": 0.0}), 0.7, places=6)   # identity at the start

    def test_principal_bias_per_dimension(self):
        decs = synth(10, seed=5)
        for d in decs:
            for c in d["candidates"]:
                c["scores"]["PAC-READ"] = 5
            d["ratings"] = {c["id"]: {"PAC-READ": 3} for c in d["candidates"]}
        model = vs.fit(decs)
        self.assertGreater(model["dim_bias"]["PAC-READ"], 1.0)       # the agent over-scores pacing by 2; shrunk toward 0
        x = vs.features(model, decs[0], decs[0]["candidates"][0])
        self.assertLess(x["PAC-READ"], 1.0)

    def test_fit_is_deterministic_and_versioned(self):
        decs = synth(20, seed=6)
        a, b = vs.fit(decs, version=3, parent=2), vs.fit(decs, version=3, parent=2)
        self.assertEqual((a["types"], a["beta"], a["trained_on"]), (b["types"], b["beta"], b["trained_on"]))
        self.assertEqual((a["version"], a["parent"], a["trained_on"]["decisions"]), (3, 2, 20))

    def test_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, fork_path = Path(tmp) / "wei", Path(tmp) / "fork.json"
            fork = {k: v for k, v in synth(1, seed=8)[0].items() if k != "ranking"}
            fork_path.write_text(json.dumps(fork))
            self.assertEqual(vs.main(["--data", str(data), "score", str(fork_path)]), 0)
            self.assertEqual(vs.main(["--data", str(data), "record", str(fork_path), "--rank", "B,A,C",
                                      "--rate", "B:CON-FIT=5", "--why", "clearer story"]), 0)
            row = json.loads((data / "decisions.jsonl").read_text().splitlines()[0])
            self.assertEqual((row["ranking"], row["model_version"], row["blind"]), (["B", "A", "C"], 0, True))
            self.assertEqual(set(row["predicted"]), {"A", "B", "C"})
            self.assertEqual(vs.main(["--data", str(data), "fit"]), 0)
            self.assertEqual(json.loads((data / "current.json").read_text())["version"], 1)
            self.assertTrue((data / "models" / "model-v1.json").exists())
            self.assertEqual(vs.main(["--data", str(data), "report"]), 0)
            with self.assertRaises(SystemExit):
                vs.main(["--data", str(data), "record", str(fork_path), "--rank", "B,Z"])


if __name__ == "__main__":
    unittest.main()
