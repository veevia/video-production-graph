"""Judging: pairwise comparison with order swap, hidden anchors, Bradley-Terry ranking, judge calibration against
human labels, and a saturation alarm for pointwise scores.

Why pairwise: in a recent 90-second case-study film (NDA) the critic scored 4 or 5 on all 20 dimensions for good and
regressed versions alike (reported). Absolute 1-5 scores saturate; "is A better than B on this criterion?" against a
benchmark and the previous best does not, provided the judge's position bias is controlled and the batch carries
known-good and known-bad items it must rank correctly.

The deterministic parts live here; the judging itself is a task packet for an agent (`make_batch` writes it,
`score_batch` reads its verdict), so the engine still never calls a model.

  Blinding          neutral labels (S01, S02, ...) in a seeded order, and neutral copies of the media: the packet's
                    paths are <label>/<key><ext> files that `stage_media` makes (audio and video remuxed without their
                    container metadata, whose title tag can name the film); the original paths stay in the key, and
                    the packet never carries an original's sha256. Other file types are refused.
  Order swap        every pair is shown twice, in both orders. A judge that picks different items in the two orders
                    (position bias) gets a tie for that pair, never a win.
  Anchors           each batch hides a reference (a benchmark or the previous best) and a degraded anchor (MUSHRA
                    style). The reference must beat the anchor, or the batch is not trusted; a candidate that loses to the
                    anchor is flagged.
  Bradley-Terry     strengths from wins and ties (minorisation-maximisation, with a weak prior so an unbeaten item stays
                    finite), with bootstrap confidence intervals from a seeded resample.
  Calibration       Cohen's kappa and Krippendorff's alpha between a judge and a named human's picks. Alpha of 0.8 or
                    more is reliable, 0.667 to 0.8 tentative, below 0.667 the judge is advisory once 30 labels exist.
  Saturation        an alarm when more than 15% of a judge's scores sit at the maximum (the ceiling effect), or when
                    the spread of scores across versions collapses.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .util import is_human_name, sha256_json

PACKET_SCHEMA = "vpg-judge-packet/v1"
KEY_SCHEMA = "vpg-judge-key/v1"
VERDICT_SCHEMA = "vpg-judge-verdict/v1"
RESULT_SCHEMA = "vpg-judge-result/v1"
LABELS_SCHEMA = "vpg-judge-label/v1"
RELIABLE, TENTATIVE = 0.8, 0.667
MIN_LABELS = 30
CEILING_SHARE = 0.15
COLLAPSE_SD = 0.15


class JudgingError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------ order swap
def consolidate(a: str, b: str, pick_ab: str, pick_ba: str) -> dict[str, Any]:
    """Two judgements of one pair: shown as (a, b), then as (b, a). Each pick is "first", "second" or "tie", by
    position. The pair has a winner only when both orders name the same item; otherwise it is a tie."""
    for p in (pick_ab, pick_ba):
        if p not in ("first", "second", "tie"):
            raise JudgingError(f"a pick is first, second or tie, not {p!r}")
    item1 = {"first": a, "second": b, "tie": None}[pick_ab]
    item2 = {"first": b, "second": a, "tie": None}[pick_ba]
    if item1 is not None and item1 == item2:
        return {"a": a, "b": b, "winner": item1, "consistent": True}
    position_bias = item1 is not None and item2 is not None   # each order picked whatever was in one position
    return {"a": a, "b": b, "winner": None, "consistent": item1 is None and item2 is None, "position_bias": position_bias}


def position_stats(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the judge picked the first item shown, and the share of pairs whose two orders disagreed."""
    picks = [r["pick"] for r in raw if r["pick"] in ("first", "second")]
    first = sum(p == "first" for p in picks)
    return {"presentations": len(raw), "decisive": len(picks), "first_share": round(first / len(picks), 3) if picks else None}


# ------------------------------------------------------------------------------------------------ Bradley-Terry
def bradley_terry(items: list[str], comparisons: list[dict[str, Any]], iterations: int = 500, prior: float = 0.5,
                  tol: float = 1e-9) -> dict[str, float]:
    """Strengths (log scale, mean 0) from consolidated comparisons. A tie is half a win each way. `prior` adds that many
    virtual wins and losses against a fixed average item, so an unbeaten or unseen item has a finite strength."""
    wins: dict[str, float] = defaultdict(float)
    n: dict[tuple[str, str], float] = defaultdict(float)
    for c in comparisons:
        a, b = c["a"], c["b"]
        if a == b:
            continue
        n[(a, b)] += 1
        n[(b, a)] += 1
        if c["winner"] == a:
            wins[a] += 1
        elif c["winner"] == b:
            wins[b] += 1
        else:
            wins[a] += 0.5
            wins[b] += 0.5
    p = {i: 1.0 for i in items}
    for _ in range(iterations):
        new = {}
        for i in items:
            denom = sum(n[(i, j)] / (p[i] + p[j]) for j in items if j != i and n[(i, j)]) + 2 * prior / (p[i] + 1.0)
            new[i] = (wins[i] + prior) / denom if denom else p[i]
        g = math.exp(sum(math.log(v) for v in new.values()) / len(new))   # geometric mean 1: scale is not identified
        new = {i: v / g for i, v in new.items()}
        done = max(abs(math.log(new[i]) - math.log(p[i])) for i in items) < tol
        p = new
        if done:
            break
    return {i: round(math.log(p[i]), 6) for i in items}


def bootstrap(items: list[str], comparisons: list[dict[str, Any]], rounds: int = 200, seed: int = 7,
              level: float = 0.95) -> dict[str, tuple[float, float]]:
    """Confidence intervals for each strength from a seeded resample of the comparisons."""
    if not comparisons:
        return {i: (0.0, 0.0) for i in items}
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {i: [] for i in items}
    for _ in range(rounds):
        sample = [comparisons[rng.randrange(len(comparisons))] for _ in comparisons]
        for i, s in bradley_terry(items, sample, iterations=200).items():
            draws[i].append(s)
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    out = {}
    for i, xs in draws.items():
        xs.sort()
        out[i] = (round(xs[int(lo_q * (len(xs) - 1))], 4), round(xs[int(math.ceil(hi_q * (len(xs) - 1)))], 4))
    return out


def rank(items: list[str], comparisons: list[dict[str, Any]], rounds: int = 200, seed: int = 7) -> list[dict[str, Any]]:
    s = bradley_terry(items, comparisons)
    ci = bootstrap(items, comparisons, rounds, seed)
    ordered = sorted(items, key=lambda i: (-s[i], i))
    rows = []
    for pos, i in enumerate(ordered, 1):
        rows.append({"rank": pos, "item": i, "strength": s[i], "ci": list(ci[i]),
                     "comparisons": sum(1 for c in comparisons if i in (c["a"], c["b"])), "separated_from_next": None})
    for r, nxt in zip(rows, rows[1:]):   # adjacent items whose intervals overlap are not separated by this batch
        r["separated_from_next"] = r["ci"][0] > nxt["ci"][1]
    return rows


# ------------------------------------------------------------------------------------------------ batches and anchors
AV_SUFFIXES = {".mp4", ".mov", ".m4v", ".m4a", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".mxf",
               ".3gp", ".wav", ".aif", ".aiff", ".caf", ".mp3", ".aac", ".flac", ".ogg", ".opus"}   # remuxed
COPY_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".vtt", ".srt", ".txt", ".json", ".md"}             # copied as they are


def _blind_media(label: str, media: dict[str, Any], media_root: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """What the judge sees of one item's media: a neutral path, <media_root>/<label>/<key><ext> (a file name such as
    candidates/<version>/master.mp4 or a benchmark's title would give the film away), and the copies to stage. The original
    sha256 stays in the key too: `seal` gives the packet a copy's sha256 only when it differs from the original's. A
    file type that cannot be blinded is refused."""
    shown, staging = {}, []
    for n, (k, m) in enumerate(sorted((media or {}).items()), 1):
        m = m or {}
        if not m.get("path"):
            shown[k] = {}          # nothing to show; an original's sha256 alone would name it
            continue
        suffix = PurePosixPath(str(m["path"])).suffix.lower()
        if suffix not in AV_SUFFIXES | COPY_SUFFIXES:
            raise JudgingError(f"media {k} ({m['path']}): a {suffix or 'suffix-less'} file cannot be blinded; give audio "
                               f"or video ({' '.join(sorted(AV_SUFFIXES))}) or a still or text file "
                               f"({' '.join(sorted(COPY_SUFFIXES))})")
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", str(k)).strip("-") or f"m{n}"
        neutral = str(PurePosixPath(media_root, label, name + suffix))
        shown[k] = {"path": neutral}
        staging.append({"from": str(m["path"]), "to": neutral, "sha256": m.get("sha256") or ""})
    return shown, staging


def make_batch(candidates: list[dict[str, Any]], reference: dict[str, Any], anchor: dict[str, Any], criterion: str,
               rules: list[str], instructions: str = "", seed: int = 0, batch_id: str = "",
               media_root: str = "media") -> tuple[dict[str, Any], dict[str, Any]]:
    """A blinded packet for the judge and the key that unblinds it. Items are {id, media: {key: {path, sha256}}, ...}.
    Every pair among the candidates, each candidate against the hidden reference and the anchor, and the reference
    against the anchor, each shown in both orders, in a seeded random order with neutral labels. Media paths in the
    packet are neutral copies under `media_root` (relative to the packet); `stage_media` makes them from the key."""
    items = {c["id"]: c for c in candidates}
    for extra in (reference, anchor):
        if extra["id"] in items:
            raise JudgingError(f"{extra['id']} is both a candidate and an anchor")
        items[extra["id"]] = extra
    ids = sorted(items)
    rng = random.Random(seed)
    labels = [f"S{n:02d}" for n in range(1, len(ids) + 1)]
    rng.shuffle(labels)
    blind = dict(zip(ids, labels))
    pairs = sorted({tuple(sorted(p)) for p in itertools.combinations(ids, 2)})
    shows = [(a, b) for a, b in pairs] + [(b, a) for a, b in pairs]
    rng.shuffle(shows)
    bid = batch_id or "J-" + sha256_json({"ids": ids, "criterion": criterion, "seed": seed})[:10]
    shown, staging = {}, []
    for i in ids:
        shown[i], copies = _blind_media(blind[i], items[i].get("media", {}), media_root)
        staging += copies
    presentations, key = [], {}
    for n, (first, second) in enumerate(shows, 1):
        pid = f"{bid}-P{n:03d}"
        presentations.append({"id": pid, "first": {"label": blind[first], "media": shown[first]},
                              "second": {"label": blind[second], "media": shown[second]}})
        key[pid] = [first, second]
    packet = {"schema": PACKET_SCHEMA, "batch": bid, "criterion": criterion, "rules": list(rules),
              "instructions": instructions or ("Watch both. Which better meets the criterion and the named rules? Answer first, "
                                               "second or tie for each presentation, cite rule ids and timecodes. Judge each "
                                               "presentation on its own: the same films may come back in the other order."),
              "presentations": presentations}
    key_doc = {"schema": KEY_SCHEMA, "batch": bid, "criterion": criterion, "reference": reference["id"], "anchor": anchor["id"],
               "candidates": sorted(c["id"] for c in candidates), "labels": blind, "presentations": key,
               "staging": sorted(staging, key=lambda s: s["to"]), "packet_sha256": sha256_json(packet)}
    return packet, key_doc


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _remux_without_metadata(src: Path, dest: Path) -> None:
    """A lossless copy of the audio and video streams without the container's metadata or chapters."""
    from .tools._common import FFMPEG
    part = dest.with_name(dest.stem + ".part" + dest.suffix)
    cmd = [FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0:v?", "-map", "0:a?", "-map_metadata", "-1",
           "-map_chapters", "-1", "-c", "copy", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
           str(part)]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        part.unlink(missing_ok=True)
        raise JudgingError(f"could not copy {src} without its metadata: {exc}") from None
    if p.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        raise JudgingError(f"could not copy {src} without its metadata: {p.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    part.replace(dest)


def stage_media(key: dict[str, Any], packet_dir: Path) -> dict[str, str]:
    """Copy each item's media to its neutral path under the packet's directory: audio and video remuxed losslessly
    without their container metadata, stills and text byte for byte (content only, no file-system metadata). A file
    that is missing or whose sha256 differs from the item's is refused before anything is copied. Returns the sha256 of
    each copy whose bytes differ from its original: a byte copy's hash is the original's, which the items and a
    benchmark registry list, so it would name the film."""
    root = Path(packet_dir).resolve()
    plan = []
    for s in key.get("staging") or []:
        src, dest = Path(s["from"]), (root / s["to"]).resolve()
        if root not in dest.parents:
            raise JudgingError(f"{s['to']} is outside {root}")
        if not src.is_file():
            raise JudgingError(f"{src} does not exist")
        original = _file_sha256(src)
        if s.get("sha256") and original != s["sha256"]:
            raise JudgingError(f"{src} does not match its sha256 in the items")
        plan.append((s["to"], src, dest, original))
    published = {}
    for to, src, dest, original in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() in AV_SUFFIXES:
            _remux_without_metadata(src, dest)
        else:
            shutil.copyfile(src, dest)
        copy = _file_sha256(dest)
        if copy != original:
            published[to] = copy
    return published


def seal(packet: dict[str, Any], key: dict[str, Any], staged: dict[str, str]) -> None:
    """Give the packet the sha256 of each copy that `stage_media` publishes, and re-seal the key over the final packet."""
    for p in packet["presentations"]:
        for side in ("first", "second"):
            for m in p[side]["media"].values():
                if m.get("path") in staged:
                    m["sha256"] = staged[m["path"]]
    key["packet_sha256"] = sha256_json(packet)


def score_batch(key: dict[str, Any], verdict: dict[str, Any], rounds: int = 200, seed: int = 7) -> dict[str, Any]:
    """Unblind a verdict, consolidate the two orders of each pair, check the anchors and rank."""
    if verdict.get("batch") != key["batch"]:
        raise JudgingError(f"the verdict is for batch {verdict.get('batch')}, the key for {key['batch']}")
    picks = {v["presentation"]: v for v in verdict.get("verdicts", [])}
    missing = sorted(set(key["presentations"]) - set(picks))
    if missing:
        raise JudgingError(f"{len(missing)} presentation(s) have no verdict: {', '.join(missing[:5])}")
    raw = []
    by_pair: dict[tuple[str, str], dict[str, str]] = {}
    for pid, (first, second) in key["presentations"].items():
        v = picks[pid]
        raw.append({"presentation": pid, "first": first, "second": second, "pick": v.get("pick"),
                    "rules": v.get("rules", []), "evidence": v.get("evidence", []), "rationale": v.get("rationale", "")})
        pair = tuple(sorted((first, second)))
        by_pair.setdefault(pair, {})["ab" if (first, second) == pair else "ba"] = v.get("pick")
    comparisons = [consolidate(a, b, o["ab"], o["ba"]) for (a, b), o in sorted(by_pair.items())]
    ref, anc = key["reference"], key["anchor"]
    ids = sorted({x for p in by_pair for x in p})
    ranking = rank(ids, comparisons, rounds, seed)

    def result(x: str, y: str) -> str:
        c = next(c for c in comparisons if {c["a"], c["b"]} == {x, y})
        return "win" if c["winner"] == x else ("loss" if c["winner"] == y else "tie")
    anchors_ok = result(ref, anc) == "win"
    below_anchor = [c for c in key["candidates"] if result(c, anc) == "loss"]
    inconsistent = [c for c in comparisons if not c["consistent"]]
    beats_reference = [c for c in key["candidates"] if result(c, ref) == "win"]
    return {"schema": RESULT_SCHEMA, "batch": key["batch"], "criterion": key["criterion"], "judge": verdict.get("judge", ""),
            "valid": anchors_ok, "why_invalid": "" if anchors_ok else f"the hidden reference {ref} did not beat the degraded "
                                                                       f"anchor {anc}: this batch's comparisons are not trusted",
            "below_anchor": below_anchor, "beats_reference": beats_reference,
            "position": {**position_stats(raw), "inconsistent_pairs": len(inconsistent), "pairs": len(comparisons)},
            "comparisons": comparisons, "ranking": ranking, "raw": raw}


# ------------------------------------------------------------------------------------------------ agreement
def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    if len(a) != len(b) or not a:
        return None
    cats = sorted(set(a) | set(b))
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return 1.0 if pe == 1 else round((po - pe) / (1 - pe), 4)


def krippendorff_alpha(units: list[list[Any]], level: str = "nominal") -> float | None:
    """Krippendorff's alpha over units (each a list of the values its raters gave; None is missing). Nominal, ordinal
    (on ranks of the values) or interval distance."""
    vals = [[v for v in u if v is not None] for u in units]
    vals = [u for u in vals if len(u) >= 2]
    if not vals:
        return None
    values = sorted({v for u in vals for v in u}, key=lambda x: (str(type(x)), x))
    if len(values) == 1:
        return 1.0
    o: dict[tuple[Any, Any], float] = defaultdict(float)
    for u in vals:
        m = len(u)
        for i, j in itertools.permutations(range(m), 2):
            o[(u[i], u[j])] += 1 / (m - 1)
    nc = {c: sum(o[(c, k)] for k in values) for c in values}
    n = sum(nc.values())
    if level == "nominal":
        def d(c: Any, k: Any) -> float:
            return 0.0 if c == k else 1.0
    elif level == "interval":
        def d(c: Any, k: Any) -> float:
            return float(c - k) ** 2
    elif level == "ordinal":
        idx = {c: i for i, c in enumerate(values)}

        def d(c: Any, k: Any) -> float:
            lo, hi = sorted((idx[c], idx[k]))
            return (sum(nc[values[g]] for g in range(lo, hi + 1)) - (nc[values[lo]] + nc[values[hi]]) / 2) ** 2
    else:
        raise JudgingError("level is nominal, ordinal or interval")
    do = sum(o[(c, k)] * d(c, k) for c in values for k in values)
    de = sum(nc[c] * nc[k] * d(c, k) for c in values for k in values) / (n - 1)
    return 1.0 if de == 0 else round(1 - do / de, 4)


def judge_status(pairs: Iterable[tuple[str, str]], min_labels: int = MIN_LABELS) -> dict[str, Any]:
    """Agreement between a judge and a named human over the same items (each a pick: an item id or "tie"). Below 0.667
    alpha after `min_labels` labels the judge is advisory: its verdicts inform, never gate."""
    pairs = list(pairs)
    judge, human = [p[0] for p in pairs], [p[1] for p in pairs]
    kappa = cohen_kappa(judge, human)
    alpha = krippendorff_alpha([[j, h] for j, h in pairs])
    if len(pairs) < min_labels:
        status = "insufficient"
    elif alpha is None or alpha < TENTATIVE:
        status = "unreliable"
    elif alpha < RELIABLE:
        status = "tentative"
    else:
        status = "reliable"
    return {"labels": len(pairs), "kappa": kappa, "alpha": alpha, "status": status,
            "advisory": status in ("insufficient", "unreliable"), "min_labels": min_labels,
            "thresholds": {"tentative": TENTATIVE, "reliable": RELIABLE}}


def label(item_a: str, item_b: str, pick: str, by: str, note: str = "", t: float | None = None, version: str = "",
          at: str = "") -> dict[str, Any]:
    """One human label (tier T3): a pairwise pick, optionally with a timecoded note. Only a named human labels."""
    if not is_human_name(by):
        raise JudgingError("only a named human gives a calibration label; an agent's pick is a verdict, not a label")
    if pick not in (item_a, item_b, "tie"):
        raise JudgingError(f"pick is {item_a}, {item_b} or tie")
    from .util import now
    out = {"schema": LABELS_SCHEMA, "pair": sorted([item_a, item_b]), "pick": pick, "by": by, "at": at or now()}
    if note:
        out["note"] = note
    if t is not None:
        out["t"] = round(float(t), 2)
    if version:
        out["version"] = version
    return out


def agreement_from(results: list[dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Match a judge's consolidated comparisons to human labels of the same pairs (the latest label per pair)."""
    human = {tuple(lb["pair"]): lb["pick"] for lb in labels}
    pairs = []
    for res in results:
        for c in res.get("comparisons", []):
            key = tuple(sorted((c["a"], c["b"])))
            if key in human:
                pairs.append((c["winner"] or "tie", human[key]))
    return judge_status(pairs)


# ------------------------------------------------------------------------------------------------ saturation
@dataclass
class Saturation:
    scores: int
    ceiling_share: float
    ceiling_alarm: bool
    between_version_sd: float | None
    collapse_alarm: bool

    @property
    def alarm(self) -> bool:
        return self.ceiling_alarm or self.collapse_alarm

    def as_dict(self) -> dict[str, Any]:
        return {"scores": self.scores, "ceiling_share": self.ceiling_share, "ceiling_alarm": self.ceiling_alarm,
                "between_version_sd": self.between_version_sd, "collapse_alarm": self.collapse_alarm, "alarm": self.alarm,
                "thresholds": {"ceiling_share": CEILING_SHARE, "collapse_sd": COLLAPSE_SD}}


def saturation(by_version: dict[Any, list[float]], max_score: float = 5.0, ceiling_share: float = CEILING_SHARE,
               collapse_sd: float = COLLAPSE_SD) -> Saturation:
    """A judge's pointwise scores per version. Ceiling: more than `ceiling_share` at the maximum (Terwee et al. 2007).
    Collapse: with two or more versions, the standard deviation of the per-version means falls below `collapse_sd`, so
    the scores no longer tell versions apart."""
    allv = [float(s) for v in by_version.values() for s in v]
    if not allv:
        return Saturation(0, 0.0, False, None, False)
    share = sum(s >= max_score for s in allv) / len(allv)
    means = [sum(v) / len(v) for v in by_version.values() if v]
    sd = None
    if len(means) >= 2:
        mu = sum(means) / len(means)
        sd = round(math.sqrt(sum((m - mu) ** 2 for m in means) / (len(means) - 1)), 4)
    return Saturation(len(allv), round(share, 4), share > ceiling_share, sd, sd is not None and sd < collapse_sd)


def review_saturation(state: dict[str, Any], review_dims: set[str] | None = None, max_score: float = 5.0) -> dict[str, Any]:
    """Saturation of each review node's agent scores across the versions a saved state recorded (read-only)."""
    per: dict[str, dict[Any, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in (state.get("evaluation") or {}).get("reviews") or []:
        if r.get("carried_forward"):
            continue
        for s in r.get("scores") or []:
            if review_dims is not None and s.get("dimension") not in review_dims:
                continue
            if s.get("score") is not None and float(s["threshold"] or 0) > 1:   # a 1-5 review score, not a tool's 0/1
                per[r["node"]][r.get("version")].append(float(s["score"]))
    return {node: saturation(dict(v), max_score).as_dict() | {"versions": len(v)} for node, v in sorted(per.items())}


def load_jsonl(path) -> list[dict[str, Any]]:
    from pathlib import Path
    p = Path(path)
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []
