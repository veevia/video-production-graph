import os

import pytest

from glassbox import media


def test_resolves_files_inside_the_candidate(job):
    path, convert = media.resolve(str(job), "v10", ["n14_video_assembly", "master.mp4"])
    assert path == os.path.realpath(job / "candidates" / "v10" / "n14_video_assembly" / "master.mp4") and convert is False
    assert media.content_type(path) == "video/mp4"
    assert media.content_type("captions.en.vtt").startswith("text/vtt")
    assert media.resolve(str(job), "v10", "key-frames/kf-01-opening-title-f30-1.000s.png")[0].endswith(".png")
    (job / "candidates" / "v10" / "n14_video_assembly" / "captions.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    path, convert = media.resolve(str(job), "v10", ["n14_video_assembly", "captions.srt.vtt"])
    assert path.endswith("captions.srt") and convert is True and media.content_type(path, True).startswith("text/vtt")


@pytest.mark.parametrize("version, segments", [
    ("v10", ["..", "..", "vpg.project.yaml"]),
    ("v10", ["n14_video_assembly", "..", "..", "v2", "n14_video_assembly", "master.mp4"]),
    ("..", ["vpg.project.yaml"]),
    ("v10/..", ["master.mp4"]),
    ("v10", ["n14_video_assembly", "context.py"]),
    ("v10", [".hidden.json"]),
    ("v10", ["n14_video_assembly/master.mp4"]),
    ("v10", ["missing.mp4"]),
    ("v99", ["master.mp4"]),
    ("v10", []),
    ("v10", ["n14_video_assembly", "master.mp4\x00.png"]),
])
def test_refuses_unsafe_paths(job, version, segments):
    with pytest.raises(media.MediaError):
        media.resolve(str(job), version, segments)


def test_refuses_symlink_escapes(job, tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text("{}")
    (job / "candidates" / "v10" / "qa" / "leak.json").symlink_to(secret)
    with pytest.raises(media.MediaError):
        media.resolve(str(job), "v10", ["qa", "leak.json"])
    outside = tmp_path / "elsewhere" / "n14_video_assembly"
    outside.mkdir(parents=True)
    (outside / "master.mp4").write_bytes(b"x")
    (job / "candidates" / "v77").symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(media.MediaError):
        media.resolve(str(job), "v77", ["n14_video_assembly", "master.mp4"])
    (job / "candidates" / "v10" / "qa" / "alias.json").symlink_to(job / "candidates" / "v10" / "qa" / "qa-direct.json")
    assert media.resolve(str(job), "v10", ["qa", "alias.json"])[0].endswith("qa-direct.json")


def test_range_header_parsing():
    assert media.parse_range("bytes=100-199", 1000) == (100, 199)
    assert media.parse_range("bytes=900-", 1000) == (900, 999)
    assert media.parse_range("bytes=-10", 1000) == (990, 999)
    assert media.parse_range("bytes=990-5000", 1000) == (990, 999)
    assert media.parse_range("bytes=1000-", 1000) == "unsatisfiable"
    assert media.parse_range("bytes=-0", 1000) == "unsatisfiable"
    assert media.parse_range("bytes=5-1", 1000) is None
    assert media.parse_range("bytes=0-1,5-6", 1000) is None
    assert media.parse_range("items=0-1", 1000) is None
    assert media.parse_range("bytes=abc", 1000) is None
    assert media.parse_range(None, 1000) is None
