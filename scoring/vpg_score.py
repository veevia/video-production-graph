#!/usr/bin/env python3
"""vpg score system v1: pure Python, with no dependencies; see scoring/README.md.

At a fork (concept, visual, voice and music, scene), the agent scores each candidate 1-5 on the fork's rubric
dimensions. This module turns those scores into a predicted pick for the principal and decides how many candidates to
build. It records the principal's end-review choice and learns from it. A model is a pure function of the recorded
decisions: the same data always gives the same model. Every refit is a new numbered version, and nothing is overwritten.

  score  FORK.json                        rank the candidates: pick probabilities, confidence, band, what to build
  record FORK.json --rank B,A,C ...       append the principal's end-review decision (the ground truth)
  fit                                     refit on every recorded decision, making a new model version
  report                                  prequential metrics (top-1, pairwise, Brier, ECE, each with n) and weights

Model, per fork type:
  q_c = sum_d w_d * (s'_cd - 1) / 4      s' is the agent's 1-5 score, corrected by the principal's per-dimension bias
  u_c = beta * q_c ;  P(pick c) = softmax(u)_c ;  P(a over b) = sigmoid(u_a - u_b)          (Bradley-Terry)
  w = softmax(theta) is non-negative and sums to 1. theta and beta maximise the pairwise log-likelihood of the
  principal's rankings under a fixed prior (equal weights, beta 4) worth PRIOR pseudo-observations.
Confidence that the principal picks the agent's top candidate:
  raw = 0.40*margin + 0.35*p_top + 0.15*coverage + 0.10*precedent       (the weights sum to 1)
  calibrated = sigmoid(a * logit(raw) + b), fitted on the principal's past decisions (Platt scaling). ECE is measured
  out of sample, each decision calibrated only by the ones before it; the target is ECE <= 0.10.
  assert >= 0.90 builds 1, flag 0.75-0.90 builds 2, refuse < 0.75 builds 3.
  Building 1 is allowed only once calibrated (>= 30 blind decisions with measured ECE <= 0.10), and never for a
  human-only fork type.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

CONF_WEIGHTS = {"margin": 0.40, "p_top": 0.35, "coverage": 0.15, "precedent": 0.10}
BANDS = (("assert", 0.90, 1), ("flag", 0.75, 2), ("refuse", 0.0, 3))
ECE_TARGET = 0.10
MIN_BLIND_DECISIONS = 30
HUMAN_ONLY_TYPES = {"voice_music"}   # listening dimensions (VOI-ADULT, CRE-MUSIC) are the principal's alone
MARGIN_SATURATION = 0.25             # one rubric point on the 0-1 quality scale saturates the margin factor
PRECEDENT_FULL = 10
PRIOR = 3.0                          # pseudo-observations pulling toward equal weights and beta 4
BETA0, BETA_MIN, BETA_MAX = 4.0, 0.5, 20.0


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def softmax(xs: list[float]) -> list[float]:
    m = max(xs)
    ex = [math.exp(x - m) for x in xs]
    s = sum(ex)
    return [e / s for e in ex]


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x >= 0 else math.exp(x) / (1 + math.exp(x))


def new_model(principal: str = "Wei") -> dict:
    return {"schema": "vpg-score-model/v1", "principal": principal, "version": 0, "parent": None, "created": now(),
            "types": {}, "beta": BETA0, "dim_bias": {}, "trained_on": {"decisions": 0, "sha256": None}, "metrics": {}}


def dim_ids(fork: dict) -> list[str]:
    return [d["id"] for d in fork["dims"]]


def weights(model: dict, fork_type: str, dims: list[str]) -> dict[str, float]:
    theta = model["types"].get(fork_type, {}).get("theta", {})
    return dict(zip(dims, softmax([theta.get(d, 0.0) for d in dims])))


def features(model: dict, fork: dict, cand: dict) -> dict[str, float]:
    bias = model.get("dim_bias", {})
    return {d: (min(5.0, max(1.0, float(cand["scores"][d]) - bias.get(d, 0.0))) - 1) / 4 for d in dim_ids(fork)}


def is_calibrated(model: dict) -> bool:
    m = model.get("metrics", {})
    ece = m.get("ece_calibrated", m.get("ece"))
    return m.get("n_blind", 0) >= MIN_BLIND_DECISIONS and ece is not None and ece <= ECE_TARGET


def logit(p: float) -> float:
    p = min(0.99, max(0.01, p))
    return math.log(p / (1 - p))


def platt(rows: list[dict], iters: int = 300, lr: float = 0.5) -> dict:
    """Map the raw composite to the observed hit rate: P(hit) = sigmoid(a * logit(conf) + b), starting from identity."""
    a, b = 1.0, 0.0
    for _ in range(iters):
        ga = gb = 0.0
        for r in rows:
            z = logit(r["confidence"])
            e = r["hit"] - sigmoid(a * z + b)
            ga, gb = ga + e * z, gb + e
        a += lr * (ga - 0.1 * (a - 1.0)) / len(rows)
        b += lr * (gb - 0.1 * b) / len(rows)
    return {"a": round(a, 6), "b": round(b, 6), "n": len(rows)}


def calibrate(conf: float, cal: dict | None) -> float:
    return sigmoid(cal["a"] * logit(conf) + cal["b"]) if cal else conf


def score(model: dict, fork: dict, calibrated: bool | None = None) -> dict:
    """Rank the rubric-passing candidates; only they can reach the principal (the rubric is the floor)."""
    dims = dim_ids(fork)
    eligible = [c for c in fork["candidates"] if c.get("passes_rubric", True)]
    dropped = [c["id"] for c in fork["candidates"] if not c.get("passes_rubric", True)]
    if not eligible:
        raise ValueError("no candidate passes the rubric, so nothing reaches the principal")
    w = weights(model, fork["fork_type"], dims)
    rows = [{"id": c["id"], "q": sum(w[d] * x for d, x in features(model, fork, c).items())} for c in eligible]
    for r, p in zip(rows, softmax([model["beta"] * r["q"] for r in rows])):
        r["p"] = p
    rows.sort(key=lambda r: (-r["q"], r["id"]))
    factors = {
        "margin": min(1.0, max(0.0, (rows[0]["q"] - rows[1]["q"]) / MARGIN_SATURATION)) if len(rows) > 1 else 1.0,
        "p_top": rows[0]["p"],
        "coverage": sum(1 for d in fork["dims"] if d.get("measured")) / len(fork["dims"]),
        "precedent": min(1.0, model["types"].get(fork["fork_type"], {}).get("n", 0) / PRECEDENT_FULL),
    }
    raw = sum(CONF_WEIGHTS[k] * v for k, v in factors.items())
    conf = calibrate(raw, model.get("calibration"))
    band, k = next((name, k) for name, lo, k in BANDS if conf >= lo)
    calibrated = is_calibrated(model) if calibrated is None else calibrated
    capped = []
    if k == 1 and fork["fork_type"] in HUMAN_ONLY_TYPES:
        k, capped = 2, ["a human-only fork type never builds just one"]
    elif k == 1 and not calibrated:
        k, capped = 2, ["not calibrated yet: fewer than 30 blind decisions or ECE above 0.10"]
    k = min(k, len(rows))
    return {"fork_id": fork.get("fork_id"), "fork_type": fork["fork_type"], "model_version": model["version"],
            "ranked": rows, "weights": w, "factors": factors, "confidence_raw": raw, "confidence": conf, "band": band,
            "build": [r["id"] for r in rows[:k]], "k": k, "k_capped": capped, "dropped_by_rubric": dropped}


def pairs_from_ranking(ranking: list[str]) -> list[tuple[str, str]]:
    return [(ranking[i], ranking[j]) for i in range(len(ranking)) for j in range(i + 1, len(ranking))]


def fit(decisions: list[dict], principal: str = "Wei", version: int = 1, parent: int | None = 0,
        epochs: int = 300, lr: float = 1.0) -> dict:
    """MAP fit on every decision: per-dimension bias from the principal's ratings, then weights and beta."""
    model = new_model(principal)
    model.update(version=version, parent=parent)
    diffs: dict[str, list[float]] = {}
    for dec in decisions:
        cands = {c["id"]: c for c in dec["candidates"]}
        for cid, rated in (dec.get("ratings") or {}).items():
            for dim, v in rated.items():
                if cid in cands and dim in cands[cid]["scores"]:
                    diffs.setdefault(dim, []).append(float(cands[cid]["scores"][dim]) - float(v))
    model["dim_bias"] = {d: sum(v) / (len(v) + 5) for d, v in sorted(diffs.items())}   # shrunk toward 0

    data, thetas = [], {}
    for dec in decisions:
        ft, dims = dec["fork_type"], dim_ids(dec)
        thetas.setdefault(ft, {d: 0.0 for d in dims})
        model["types"].setdefault(ft, {"theta": {}, "n": 0})["n"] += 1
        feats = {c["id"]: features(model, dec, c) for c in dec["candidates"]}
        data += [(ft, dims, feats[a], feats[b]) for a, b in pairs_from_ranking(dec["ranking"]) if a in feats and b in feats]
    beta, n = BETA0, len(data) + PRIOR
    for _ in range(epochs if data else 0):
        g = {ft: {d: 0.0 for d in th} for ft, th in thetas.items()}
        g_beta = 0.0
        for ft, dims, xa, xb in data:
            wd = dict(zip(dims, softmax([thetas[ft][d] for d in dims])))
            dx = {d: xa[d] - xb[d] for d in dims}
            wdx = sum(wd[d] * dx[d] for d in dims)
            r = sigmoid(-beta * wdx)                        # d/dz of log sigmoid(z) at z = beta * w.dx
            for k in dims:
                g[ft][k] += r * beta * wd[k] * (dx[k] - wdx)
            g_beta += r * wdx
        for ft, th in thetas.items():
            for d in th:
                th[d] += lr * (g[ft][d] - PRIOR * th[d]) / n
        beta = min(BETA_MAX, max(BETA_MIN, beta + lr * (g_beta - PRIOR * (beta - BETA0) / BETA0) / n))
    for ft, th in thetas.items():
        model["types"][ft]["theta"] = {d: round(v, 6) for d, v in th.items()}
    model["beta"] = round(beta, 6)
    blob = json.dumps(decisions, sort_keys=True).encode()
    model["trained_on"] = {"decisions": len(decisions), "sha256": hashlib.sha256(blob).hexdigest()[:16]}
    return model


def evaluate(decisions: list[dict], epochs: int = 300) -> tuple[dict, list[dict]]:
    """Prequential: predict each decision with a model fitted only on the decisions before it."""
    rows = []
    for i, dec in enumerate(decisions):
        s = score(fit(decisions[:i], epochs=epochs), dec, calibrated=False)
        pmap = {r["id"]: r["p"] for r in s["ranked"]}
        ranking = [c for c in dec["ranking"] if c in pmap]
        pairs = pairs_from_ranking(ranking)
        rows.append({"fork_id": dec.get("fork_id"), "blind": dec.get("blind", True) and not dec.get("retrospective", False),
                     "confidence": s["confidence"], "hit": s["ranked"][0]["id"] == ranking[0],
                     "pairs": len(pairs), "pair_hits": sum(1 for a, b in pairs if pmap[a] > pmap[b])})
    return metrics(rows), rows


def metrics(rows: list[dict]) -> dict:
    blind = [r for r in rows if r["blind"]]
    out = {"n": len(rows), "n_blind": len(blind), "top1": None, "pairwise": None, "brier": None, "ece": None,
           "ece_calibrated": None}
    if not blind:
        return out
    out["top1"] = sum(r["hit"] for r in blind) / len(blind)
    tp = sum(r["pairs"] for r in blind)
    out["pairwise"] = sum(r["pair_hits"] for r in blind) / tp if tp else None
    out["brier"] = sum((r["confidence"] - r["hit"]) ** 2 for r in blind) / len(blind)
    out["ece"] = ece([(r["confidence"], r["hit"]) for r in blind])
    # Out of sample: each decision's confidence is calibrated only by the decisions before it (identity for the first 10).
    cal = [(calibrate(r["confidence"], platt(blind[:i]) if i >= 10 else None), r["hit"]) for i, r in enumerate(blind)]
    out["ece_calibrated"] = ece(cal)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def ece(pairs: list[tuple[float, bool]], n_bins: int = 5) -> float:
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for c, hit in pairs:
        bins[min(n_bins - 1, int(c * n_bins))].append((c, hit))
    return sum(len(b) / len(pairs) * abs(sum(h for _, h in b) / len(b) - sum(c for c, _ in b) / len(b)) for b in bins if b)


# ---- storage: <data>/decisions.jsonl, <data>/models/model-vN.json, <data>/current.json ----

def load_decisions(data: Path) -> list[dict]:
    f = data / "decisions.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def load_current(data: Path) -> dict:
    cur = data / "current.json"
    if not cur.exists():
        return new_model()
    v = json.loads(cur.read_text())["version"]
    return json.loads((data / "models" / f"model-v{v}.json").read_text())


def cmd_score(a) -> int:
    fork = json.loads(Path(a.fork).read_text())
    print(json.dumps(score(load_current(Path(a.data)), fork), indent=1))
    return 0


def cmd_record(a) -> int:
    data = Path(a.data)
    fork = json.loads(Path(a.fork).read_text())
    model = load_current(data)
    s = score(model, fork, calibrated=False)          # the prediction, made before this decision is learned from
    ranking = [x.strip() for x in a.rank.split(",") if x.strip()]
    unknown = [x for x in ranking if x not in {c["id"] for c in fork["candidates"]}]
    if unknown or len(set(ranking)) != len(ranking):
        sys.exit(f"--rank must list candidate ids once each; unknown: {unknown}")
    ratings: dict[str, dict[str, float]] = {}
    for item in a.rate or []:                          # B:CON-FIT=5
        cid, rest = item.split(":", 1)
        dim, v = rest.split("=", 1)
        ratings.setdefault(cid, {})[dim] = float(v)
    row = {**{k: fork[k] for k in ("fork_id", "fork_type", "dims", "candidates") if k in fork}, "run": fork.get("run"),
           "version": fork.get("version"), "principal": a.principal, "recorded_at": now(), "model_version": model["version"],
           "predicted": {r["id"]: round(r["p"], 4) for r in s["ranked"]}, "confidence": round(s["confidence"], 4),
           "confidence_raw": round(s["confidence_raw"], 4),
           "agent_top": s["ranked"][0]["id"], "ranking": ranking, "ratings": ratings, "why": a.why or "",
           "blind": not a.not_blind, "retrospective": a.retrospective, "source": a.source or "", "evidence": "observed"}
    data.mkdir(parents=True, exist_ok=True)
    with open(data / "decisions.jsonl", "a") as fh:
        fh.write(json.dumps(row) + "\n")
    print(f"recorded {row['fork_id']}: {a.principal} ranked {','.join(ranking)}; the model predicted {row['agent_top']} "
          f"(confidence {row['confidence']:.2f}, model v{model['version']})")
    return 0


def cmd_fit(a) -> int:
    data = Path(a.data)
    decisions = load_decisions(data)
    prev = load_current(data)
    model = fit(decisions, principal=prev["principal"], version=prev["version"] + 1, parent=prev["version"])
    model["metrics"], rows = evaluate(decisions)
    blind = [r for r in rows if r["blind"]]
    model["calibration"] = platt(blind) if len(blind) >= 10 else None
    (data / "models").mkdir(parents=True, exist_ok=True)
    (data / "models" / f"model-v{model['version']}.json").write_text(json.dumps(model, indent=1))
    (data / "current.json").write_text(json.dumps({"version": model["version"], "at": now()}))
    print(f"model v{model['version']} (parent v{model['parent']}) on {len(decisions)} decisions: {model['metrics']}")
    return 0


def cmd_report(a) -> int:
    model = load_current(Path(a.data))
    print(json.dumps({"version": model["version"], "metrics": model["metrics"], "beta": model["beta"],
                      "calibrated": is_calibrated(model), "dim_bias": model["dim_bias"],
                      "weights": {ft: dict(zip(t["theta"], softmax(list(t["theta"].values())))) for ft, t in model["types"].items()},
                      "n_by_type": {ft: t["n"] for ft, t in model["types"].items()}}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(Path(__file__).resolve().parents[1] / "knowledge" / "preferences" / "wei"),
                    help="the preference store (default: knowledge/preferences/wei, outside the run config fingerprint)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("score"); sp.add_argument("fork"); sp.set_defaults(fn=cmd_score)
    sp = sub.add_parser("record"); sp.add_argument("fork"); sp.add_argument("--rank", required=True)
    sp.add_argument("--rate", action="append", metavar="CAND:DIM=1-5"); sp.add_argument("--why")
    sp.add_argument("--principal", default="Wei"); sp.add_argument("--not-blind", action="store_true")
    sp.add_argument("--retrospective", action="store_true", help="hindsight-biased: learn weights from it, keep it out of ECE")
    sp.add_argument("--source", help="where the choice was made, e.g. the chat turn quoted"); sp.set_defaults(fn=cmd_record)
    sp = sub.add_parser("fit"); sp.set_defaults(fn=cmd_fit)
    sp = sub.add_parser("report"); sp.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
