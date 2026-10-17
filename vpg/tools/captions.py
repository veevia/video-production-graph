"""Caption tools: SRT parsing and caption/speech synchronisation.

The rule set was tightened across three Round 07 critic rounds (r07-v13 → v15):
  * a cue never starts after its first spoken word (no lag);
  * a sentence's first cue may lead by up to 0.45 s into cleared time; a mid-sentence cue by at most 0.20 s;
  * no cue clears more than 0.10 s before its last word ends;
  * reading rate ≤ 20 characters per second; minimum cue duration 0.8 s.
Spoken timings come in as spans (one per narration unit). When the timings carry the unit's words in order
(`words: [{unit, i, word, start, end, measured}]`), its measured words keep their times, and a word that was not measured
is given the whole gap between the measured words around it (or the unit's edges): it starts as early and ends as late as
it could, so the no-lag and early-clear checks flag every cue that could lag or clear early. A unit with no measured
word, or whose entries are not its words, is interpolated by character length over its whole span, which at clause
breaks can be off from real speech by most of a second. "Could" assumes a unit's words follow one another inside its
span (see _measured_unit).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from ._common import finding, words


def _ts(s: str) -> float:
    h, m, rest = s.strip().replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_srt(text: str) -> list[dict[str, Any]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        cues.append({"start": _ts(a), "end": _ts(b), "text": " ".join(lines[2:])})
    return cues


SPAN_TOLERANCE_S = 0.10   # measured words must lie within their unit's span, give or take this


def _key(word: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(word).lower())


def _measured_unit(ws: list[str], entries: list[dict[str, Any]], span: dict[str, Any]) -> list[tuple[float, float, str]] | None:
    """(start, end, source) of each word of a unit, or None unless the entries, less any with no letters or digits (a
    lone dash), are the unit's words in order and at least one is measured, and every measured time is finite, ordered
    (each word ends after it starts and starts no earlier than the measured word before) and inside the unit's span,
    which also rules out times in another time base. The time an entry not marked `measured: true` carries is never
    read: every word of such a run is given the whole interval it could have been spoken in ("bounded"), under the
    model that a unit's words follow one another inside its span, which a measured word the checks above accept outside
    it (by up to SPAN_TOLERANCE_S) extends. That is from the end of the measured word before to the start of the one
    after; at the unit's edge, from its start (or to its end), widened to the tolerance when the measured neighbour lies
    outside the span; and where the neighbours overlap, which that model cannot place, from the start of the one before
    to the end of the one after."""
    entries = [e for e in sorted(entries, key=lambda e: e.get("i", 0)) if _key(e.get("word"))]
    if len(entries) != len(ws) or [_key(e.get("word")) for e in entries] != [_key(w) for w in ws]:
        return None
    lo, hi = float(span["start"]) - SPAN_TOLERANCE_S, float(span["end"]) + SPAN_TOLERANCE_S
    anchors: list[tuple[float, float] | None] = []
    last = float("-inf")
    for e in entries:
        if e.get("measured") is not True:
            anchors.append(None)
            continue
        start, end = e.get("start"), e.get("end")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (start, end)):
            return None
        if end < start or start < last - 1e-6 or start < lo or end > hi:
            return None
        anchors.append((float(start), float(end)))
        last = float(start)
    if all(a is None for a in anchors):
        return None
    times: list[tuple[float, float, str]] = []
    k = 0
    while k < len(ws):
        if anchors[k] is not None:
            times.append((*anchors[k], "measured"))
            k += 1
            continue
        j = k
        while j < len(ws) and anchors[j] is None:
            j += 1
        prev, nxt = (anchors[k - 1] if k > 0 else None), (anchors[j] if j < len(ws) else None)
        if prev is None:
            first = lo if nxt[0] < float(span["start"]) else float(span["start"])
            gap = (first, nxt[0])
        elif nxt is None:
            gap = (prev[1], hi if prev[1] > float(span["end"]) else float(span["end"]))
        else:
            gap = (prev[1], nxt[0]) if prev[1] <= nxt[0] else (prev[0], nxt[1])
        times += [(*gap, "bounded")] * (j - k)
        k = j
    return times


def word_times(spans: list[dict[str, Any]], measured: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every spoken word with its start and end, whether it ends its unit, and its `source`: "measured" (the timings'
    `words`), "bounded" (not measured: the whole gap between the measured words around it; _measured_unit) or
    "estimated" (a unit with no usable measured words, whose duration is spread over its words by character length)."""
    by_unit: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for e in measured or []:
        if isinstance(e, dict) and "unit" in e:
            by_unit[e["unit"]].append(e)
    out = []
    for s in spans:
        ws = words(s["text"])
        if not ws:
            continue
        times = _measured_unit(ws, by_unit.get(s.get("unit"), []), s)
        if times is None:
            total, t, times = sum(len(w) + 1 for w in ws), s["start"], []
            for w in ws:
                d = (s["end"] - s["start"]) * (len(w) + 1) / total
                times.append((t, t + d, "estimated"))
                t += d
        out += [{"word": w, "start": a, "end": b, "sentence_end": k == len(ws) - 1, "source": source}
                for k, (w, (a, b, source)) in enumerate(zip(ws, times))]
    return out


def sync(cues: list[dict[str, Any]], spans: list[dict[str, Any]], rules: dict[str, float], traced: str = "n14_video_assembly",
         measured: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    wt = word_times(spans, measured)
    findings, rows, j = [], [], 0
    for i, c in enumerate(cues):
        n = len(words(c["text"]))
        seg = wt[j:j + n]
        if len(seg) < n:
            findings.append(finding("captions:alignment", "ACC-CAPTIONS", "P2", f"cue {i + 1} '{c['text'][:40]}' has no matching speech", traced=traced, key="captions", element=f"cue-{i + 1}"))
            break
        first = j == 0 or wt[j - 1]["sentence_end"]
        lead = seg[0]["start"] - c["start"]
        clear = seg[-1]["end"] - c["end"]
        cps = len(c["text"]) / max(1e-6, c["end"] - c["start"])
        rows.append({"cue": i + 1, "lead": round(lead, 3), "clear": round(clear, 3), "cps": round(cps, 1), "first": first})
        where = f"cue {i + 1} at {c['start']:.2f}s"
        if lead < -rules["max_lag_s"] - 1e-6:
            bounded = seg[0]["source"] == "bounded"
            findings.append(finding("captions:lag", "ACC-CAPTIONS", "P2", f"{where} starts {-lead:.2f}s after its first spoken word"
                                    + (" could have started (it was not measured)" if bounded else ""),
                                    evidence=where, traced=traced, key="captions", element=f"cue-{i + 1}",
                                    fix="start the cue at or before the first word" + (", or start it at a measured word" if bounded else "")))
        if first and lead > rules["max_first_lead_s"] + 1e-6:
            findings.append(finding("captions:lead", "ACC-CAPTIONS", "P3", f"{where} leads its sentence by {lead:.2f}s (> {rules['max_first_lead_s']})",
                                    evidence=where, traced=traced, key="captions", element=f"cue-{i + 1}"))
        if not first and lead > rules["max_chain_lead_s"] + 1e-6:
            findings.append(finding("captions:lead", "ACC-CAPTIONS", "P3", f"{where} leads mid-sentence by {lead:.2f}s (> {rules['max_chain_lead_s']})",
                                    evidence=where, traced=traced, key="captions", element=f"cue-{i + 1}"))
        if clear > rules["max_early_clear_s"] + 1e-6:
            findings.append(finding("captions:early-clear", "ACC-CAPTIONS", "P3", f"{where} clears {clear:.2f}s before its last word"
                                    + (" could have ended (it was not measured)" if seg[-1]["source"] == "bounded" else " ends"),
                                    evidence=where, traced=traced, key="captions", element=f"cue-{i + 1}"))
        if cps > rules["max_cps"]:
            findings.append(finding("captions:rate", "ACC-CAPTIONS", "P3", f"{where} reads at {cps:.1f} cps (> {rules['max_cps']})",
                                    evidence=where, traced=traced, key="captions", element=f"cue-{i + 1}"))
        if c["end"] - c["start"] < rules["min_duration_s"]:
            findings.append(finding("captions:duration", "ACC-CAPTIONS", "P3", f"{where} lasts {c['end'] - c['start']:.2f}s (< {rules['min_duration_s']})",
                                    evidence=where, traced=traced, key="captions", element=f"cue-{i + 1}"))
        j += n
    lags = [r for r in rows if r["lead"] < -rules["max_lag_s"] - 1e-6]
    return {"cues": len(cues), "lags": len(lags), "worst_lead": min((r["lead"] for r in rows), default=0.0),
            "max_cps": max((r["cps"] for r in rows), default=0.0), "measured_words": sum(w["source"] == "measured" for w in wt),
            "bounded_words": sum(w["source"] == "bounded" for w in wt), "words": len(wt), "rows": rows, "findings": findings}
