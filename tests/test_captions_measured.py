"""Caption sync on measured word times (triage #70, the SRA side chat's #69).

`captions.word_times` spread each unit's duration over its words by character length. At clause breaks that estimate
was off from the measured speech by up to 0.75 s (SRA v10: p90 0.43 s over 242 words), so captions chunked on real
speech failed the tool, and captions chunked to satisfy it really lagged speech. The measured word times the voice node
already writes (`timings.words`) are used now, unit by unit, whenever they are consistent. Since v1.5.1 (#74) a word the
voice node could not measure no longer drops its whole unit back to the character spread: it is bounded by the measured
words around it."""

from __future__ import annotations

from tests.test_tools import _legibility
from vpg.tools import captions

RULES = {"max_lag_s": 0.0, "max_chain_lead_s": 0.2, "max_first_lead_s": 0.45, "max_early_clear_s": 0.1, "max_cps": 20,
         "min_duration_s": 0.8}
UNIT = {"unit": "u02", "text": "Trade policy, media and scientific experts.", "start": 0.0, "end": 3.0}
# A long breath after "policy," — the character spread puts "media" at 0.93 s; it is spoken at 1.60 s.
SPOKEN = [("Trade", 0.00, 0.30), ("policy", 0.30, 0.90), ("media", 1.60, 2.00), ("and", 2.00, 2.10),
          ("scientific", 2.10, 2.60), ("experts", 2.60, 3.00)]
MEASURED = [{"unit": "u02", "i": i, "word": w, "start": a, "end": b, "measured": True} for i, (w, a, b) in enumerate(SPOKEN)]
# Split at the clause break, where real speech pauses (r07 style).
CUES = [{"start": 0.0, "end": 1.2, "text": "Trade policy,"}, {"start": 1.5, "end": 3.1, "text": "media and scientific experts."}]


def test_cues_split_at_a_measured_onset_pass_and_the_character_spread_would_fail_them():
    """The positive control: the same split judged by the old word clock is a 0.57 s lag."""
    late = [dict(CUES[1], start=2.0, end=3.6)]                                                  # 0.40 s after "media"
    assert [f.check for f in captions.sync([CUES[0], *late], [UNIT], RULES, measured=MEASURED)["findings"]] == ["captions:lag"]
    nan = [dict(e, start=float("nan")) if e["i"] == 2 else e for e in MEASURED]                # critic: NaN must not hide it
    assert [f.check for f in captions.sync([CUES[0], *late], [UNIT], RULES, measured=nan)["findings"]] == ["captions:lag"]
    real = captions.sync(CUES, [UNIT], RULES, measured=MEASURED)
    assert real["findings"] == [] and real["measured_words"] == real["words"] == 6
    spread = captions.sync(CUES, [UNIT], RULES)
    assert [f.check for f in spread["findings"]] == ["captions:lag"] and spread["measured_words"] == 0
    assert "starts 0.57s after its first spoken word" in spread["findings"][0].message


def test_a_unit_falls_back_to_the_character_spread_unless_its_measured_times_are_consistent():
    spread = captions.word_times([UNIT])
    variants = {
        "no word measured": [dict(e, measured=False) for e in MEASURED],
        "no word says it is measured": [{k: v for k, v in e.items() if k != "measured"} for e in MEASURED],
        "measured is not a boolean": [dict(e, measured="true") for e in MEASURED],
        "a word missing": MEASURED[:-1],
        "other words": [dict(e, word="policies") if e["i"] == 1 else e for e in MEASURED],
        "out of order": [dict(e, start=0.2) if e["i"] == 2 else e for e in MEASURED],
        "ends before it starts": [dict(e, end=e["start"] - 0.1) if e["i"] == 4 else e for e in MEASURED],
        "a time that is not a number": [dict(e, start="1.6") if e["i"] == 2 else e for e in MEASURED],
        "a NaN start (json reads NaN)": [dict(e, start=float("nan")) if e["i"] == 2 else e for e in MEASURED],
        "an infinite end": [dict(e, end=float("inf")) if e["i"] == 5 else e for e in MEASURED],
        "a word outside the unit's span": [dict(e, end=3.2) if e["i"] == 5 else e for e in MEASURED],
        "times relative to the unit": [dict(e, start=e["start"] - 5.0, end=e["end"] - 5.0) for e in MEASURED],
        "another unit's words": [dict(e, unit="u03") for e in MEASURED],
    }
    for why, measured in variants.items():
        assert captions.word_times([UNIT], measured) == spread, why
    assert {w["source"] for w in spread} == {"estimated"}
    dashed = MEASURED[:2] + [{"unit": "u02", "i": 1.5, "word": "—", "start": 0.9, "end": 0.9, "measured": True}] + MEASURED[2:]
    assert [w["source"] for w in captions.word_times([UNIT], dashed)] == ["measured"] * 6          # a lone dash is not a word
    assert captions.word_times([UNIT], [dict(e, start=e["start"] + 0.08, end=min(e["end"] + 0.08, 3.1)) for e in MEASURED])[0]["source"] == "measured"
    mixed = captions.word_times([UNIT, {"unit": "u03", "text": "Next.", "start": 3.0, "end": 3.5}], MEASURED)
    assert [w["source"] for w in mixed] == ["measured"] * 6 + ["estimated"]
    assert [round(w["start"], 2) for w in mixed[:3]] == [0.0, 0.3, 1.6]


def test_a_word_not_measured_is_bounded_by_the_measured_words_around_it():
    """#74: v1.5 dropped a whole unit to the character spread when one of its words was not measured. On SRA's a6
    timings that was 4 units, and 3 of the side chat's 35 speech-chunked cues were flagged for it."""
    guessed = [dict(e, measured=False, start=0.0, end=0.05) if e["i"] == 3 else e for e in MEASURED]   # a producer's guess
    wt = captions.word_times([UNIT], guessed)
    assert [w["source"] for w in wt] == ["measured"] * 3 + ["bounded"] + ["measured"] * 2
    assert [(w["start"], w["end"]) for w in wt] == [(a, b) for _, a, b in SPOKEN]      # "and" sits between "media" and "scientific"
    for why, junk in {"a NaN": float("nan"), "a string": "2.0", "another time base": 900.0}.items():
        assert captions.word_times([UNIT], [dict(e, start=junk) if e["i"] == 3 else e for e in guessed]) == wt, why
    real = captions.sync(CUES, [UNIT], RULES, measured=guessed)
    assert real["findings"] == [] and (real["measured_words"], real["bounded_words"], real["words"]) == (5, 1, 6)
    # The positive control: the whole-unit character spread (what v1.5 fell back to) judges the same cues a 0.57 s lag.
    assert [f.check for f in captions.sync(CUES, [UNIT], RULES)["findings"]] == ["captions:lag"]


def test_a_cue_that_could_lag_is_flagged_even_inside_a_run_not_measured():
    """Every word of a run not measured gets the whole gap, so the no-lag and early-clear checks miss nothing the
    measured words allow. Here "media and" was not measured and was in fact said straight after "policy,", before a
    long pause: "and" at about 1.05 s. A cue that starts at 1.5 s lags it. Spreading the run by characters would place
    "and" at 1.62 s and pass that cue."""
    unmeasured = [dict(e, measured=False) if e["i"] in (2, 3) else e for e in MEASURED]
    wt = captions.word_times([UNIT], unmeasured)
    assert [(w["start"], w["end"], w["source"]) for w in wt[2:4]] == [(0.9, 2.1, "bounded")] * 2
    cues = [{"start": 0.0, "end": 1.5, "text": "Trade policy, media"}, {"start": 1.5, "end": 3.1, "text": "and scientific experts."}]
    found = {f.check: f for f in captions.sync(cues, [UNIT], RULES, measured=unmeasured)["findings"]}
    assert sorted(found) == ["captions:early-clear", "captions:lag"]
    assert "could have started (it was not measured)" in found["captions:lag"].message
    assert "could have ended (it was not measured)" in found["captions:early-clear"].message
    assert "start it at a measured word" in found["captions:lag"].smallest_fix
    # Split at measured words instead, and the same run is judged only where it can be.
    whole = [{"start": 0.0, "end": 1.2, "text": "Trade policy,"}, {"start": 0.9, "end": 3.1, "text": "media and scientific experts."}]
    assert captions.sync(whole, [UNIT], RULES, measured=unmeasured)["findings"] == []
    after_pause = captions.sync(CUES, [UNIT], RULES, measured=unmeasured)       # a cue that waits for the pause's end
    assert [f.check for f in after_pause["findings"]] == ["captions:lag"]
    edges = captions.word_times([UNIT], [dict(e, measured=False) if e["i"] in (0, 1, 4, 5) else e for e in MEASURED])
    assert [(w["start"], w["end"]) for w in edges] == [(0.0, 1.6), (0.0, 1.6), (1.6, 2.0), (2.0, 2.1), (2.1, 3.0), (2.1, 3.0)]


def test_a_bounded_word_misses_no_violation_at_a_units_edges_or_between_overlapping_neighbours():
    """Critic I2: with the gap taken as min/max of the neighbours, marking one word unmeasured hid a real violation in
    each of these units ("one two three", span 1.00-2.00, which the validator accepts give or take 0.10 s)."""
    unit = {"unit": "u9", "text": "one two three", "start": 1.0, "end": 2.0}

    def checks(cues, *entries):
        measured = [{"unit": "u9", "i": i, "word": w, "start": a, "end": b, "measured": m} for i, (w, a, b, m) in enumerate(entries)]
        return sorted(f.check for f in captions.sync(cues, [unit], RULES, measured=measured)["findings"]
                      if f.check in ("captions:lag", "captions:early-clear"))   # the timing checks, not duration

    one_cue = [{"start": 0.95, "end": 2.0, "text": "one two three"}]              # CE1: "one" said before the span starts
    assert checks(one_cue, ("one", 0.90, 0.95, True), ("two", 0.95, 1.40, True), ("three", 1.40, 2.00, True)) == ["captions:lag"]
    assert checks(one_cue, ("one", 0, 0, False), ("two", 0.95, 1.40, True), ("three", 1.40, 2.00, True)) == ["captions:lag"]
    early = [{"start": 1.0, "end": 1.98, "text": "one two three"}]                # CE2: "three" said after the span ends
    assert checks(early, ("one", 1.0, 1.4, True), ("two", 1.4, 2.05, True), ("three", 2.05, 2.10, True)) == ["captions:early-clear"]
    assert checks(early, ("one", 1.0, 1.4, True), ("two", 1.4, 2.05, True), ("three", 0, 0, False)) == ["captions:early-clear"]
    split = [{"start": 1.0, "end": 1.5, "text": "one"}, {"start": 1.4, "end": 1.9, "text": "two three"}]   # CE3: overlap
    assert checks(split, ("one", 1.0, 1.5, True), ("two", 1.2, 1.45, True), ("three", 1.4, 1.9, True)) == ["captions:lag"]
    assert checks(split, ("one", 1.0, 1.5, True), ("two", 0, 0, False), ("three", 1.4, 1.9, True)) == ["captions:lag"]

    def times(*entries):
        measured = [{"unit": "u9", "i": i, "word": w, "start": a, "end": b, "measured": m} for i, (w, a, b, m) in enumerate(entries)]
        return [(w["start"], w["end"], w["source"][0]) for w in captions.word_times([unit], measured)]

    assert times(("one", 1.0, 1.5, True), ("two", 0, 0, False), ("three", 1.4, 1.9, True))[1] == (1.0, 1.9, "b")   # overlap
    assert times(("one", 0, 0, False), ("two", 0.95, 1.4, True), ("three", 1.4, 2.0, True))[0] == (0.9, 0.95, "b")
    assert times(("one", 1.0, 1.4, True), ("two", 1.4, 2.08, True), ("three", 0, 0, False))[2] == (2.08, 2.1, "b")
    assert times(("one", 0, 0, False), ("two", 1.2, 1.4, True), ("three", 1.4, 2.0, True))[0] == (1.0, 1.2, "b")   # inside


def test_only_a_units_last_word_ends_it():
    """`w is ws[-1]` compared string identity, and CPython shares one-character strings, so "a b a" ended twice."""
    out = captions.word_times([{"unit": "u1", "text": "a b a", "start": 0.0, "end": 1.0}])
    assert [w["sentence_end"] for w in out] == [False, False, True]


def test_the_legibility_check_judges_captions_on_the_measured_words(tmp_path):
    srt = tmp_path / "c.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,200\nTrade policy,\n\n2\n00:00:01,500 --> 00:00:03,100\n"
                   "media and scientific experts.\n")

    def caption_checks(timings):
        from tests.test_tools import Ctx, layer
        from vpg.tools import legibility
        ctx = Ctx(tmp_path, loads={"text_layers": {"frame": {"w": 1920, "h": 1080}, "layers": [layer()]}, "timings": timings},
                  paths={"captions": srt, "master": None})
        return sorted(f.check for f in legibility.check(ctx).findings if f.check.startswith("captions:"))

    assert caption_checks({"units": [UNIT], "words": MEASURED}) == []
    assert caption_checks({"units": [UNIT]}) == ["captions:lag"]
    assert _legibility(tmp_path, [], captions_path=srt, spans=[UNIT]).findings                  # the old helper: no words


def test_the_timings_schema_needs_times_only_on_measured_words():
    """Critic N3: the time of a word not measured is never read, so the schema no longer requires one."""
    import json

    import jsonschema

    from vpg.util import PKG_ROOT
    schema = json.loads((PKG_ROOT / "schemas/artifacts/timings.schema.json").read_text())
    check = jsonschema.validators.validator_for(schema)(schema)
    base = {"units": [UNIT], "sample_rate": 48000}
    unmeasured = {"unit": "u02", "i": 3, "word": "and", "measured": False}
    assert not list(check.iter_errors(dict(base, words=MEASURED[:3] + [unmeasured] + MEASURED[4:])))
    assert list(check.iter_errors(dict(base, words=[{"unit": "u02", "i": 0, "word": "Trade", "measured": True}])))
