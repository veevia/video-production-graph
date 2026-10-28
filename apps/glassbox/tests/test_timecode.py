from glassbox.fsio import version_key
from glassbox.timecode import parse_captions, parse_keyframe_name, parse_t0, split_leading_time


def test_mmss_converts_even_when_seconds_exceed_59():
    assert parse_t0("00:71.70-00:72.93") == 71.7
    assert parse_t0("01:09.58-01:29.83 (results board and end card)") == 69.58
    assert parse_t0("00:38.43-00:41.30; 00:56.40-00:58.40") == 38.43


def test_seconds_ranges_and_single_values():
    assert parse_t0("31.71-33.21 s (CH02 the pathfinder)") == 31.71
    assert parse_t0("56.50-58.10 s") == 56.5
    assert parse_t0("6.5\u20136.9 s") == 6.5
    assert parse_t0("poster.jpg (frame 64, 2.133 s)") == 2.133
    assert parse_t0("CH03 38.80-41.17 s (timeline)") == 38.8
    assert parse_t0("0-3.6 s and 53-55 s") == 0.0


def test_identifier_digits_are_not_timecodes():
    assert parse_t0("S18 29.767-30.267 s (frames 893-907)") == 29.767
    assert parse_t0("co-60 6.42-10.74 s") == 6.42
    assert parse_t0("3 steps and 12 frames") is None


def test_list_of_ranges_with_one_unit_starts_at_first_range():
    assert parse_t0("50.03-52.00, 52.17-54.43, 54.63-56.47 s (stepper)") == 50.03


def test_no_timecode_is_none():
    assert parse_t0("whole film") is None
    assert parse_t0("n14_video_assembly/outputs.json") is None
    assert parse_t0("controls/product.md (sha256 d73308d3...)") is None
    assert parse_t0("") is None
    assert parse_t0(None) is None


def test_numbers_and_lists():
    assert parse_t0(12.5) == 12.5
    assert parse_t0(-1) is None
    assert parse_t0([None, "4.0 s"]) == 4.0


def test_split_leading_time():
    assert split_leading_time("43.23-43.30 s: camera snap. The lean jumps") == ("43.23-43.30 s", "camera snap. The lean jumps")
    assert split_leading_time("00:38.43-00:41.30: a dead frame") == ("00:38.43-00:41.30", "a dead frame")
    assert split_leading_time("Score inaudible: fix the mix") == (None, "Score inaudible: fix the mix")


def test_keyframe_names():
    assert parse_keyframe_name("kf-02-opening-title-poster-f86-2.867s.png") == {
        "file": "kf-02-opening-title-poster-f86-2.867s.png", "index": 2, "label": "opening title poster",
        "frame": 86, "t": 2.867}
    parsed = parse_keyframe_name("kf-07-pathfinder-figure-out-name-in-f521-17.367s.png")
    assert parsed["label"] == "pathfinder figure out name in" and parsed["frame"] == 521 and parsed["t"] == 17.367
    loose = parse_keyframe_name("t-13.6.png")
    assert loose["t"] == 13.6 and loose["frame"] is None
    assert parse_keyframe_name("notes.txt") is None


def test_natural_version_order():
    names = ["v22r1", "v10", "v2", "v22", "v1", "v10a", "v3"]
    assert sorted(names, key=version_key) == ["v1", "v2", "v3", "v10", "v10a", "v22", "v22r1"]
    assert version_key("v2") < version_key("v10") < version_key("v22") < version_key("v22r1")


def test_captions_vtt_and_srt():
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.500\nHello <b>there</b> &amp; you\n\n2\n01:05.250 --> 01:07.000\nTwo\nlines\n"
    cues = parse_captions(vtt)
    assert cues == [{"start": 1.0, "end": 2.5, "text": "Hello there & you"}, {"start": 65.25, "end": 67.0, "text": "Two\nlines"}]
    srt = "1\n00:00:01,819 --> 00:00:04,849\nFirst\n\n2\n00:00:05,191 --> 00:00:08,941\nSecond\n"
    assert [c["start"] for c in parse_captions(srt)] == [1.819, 5.191]
