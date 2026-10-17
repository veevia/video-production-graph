"""The privacy scan must never pass a master it has not checked, must catch a listed name however it comes on screen,
must not block on innocent words, and must never write a name (first real run, #48: a full name showed for 5 frames
during a scroll because the veil followed sampled boxes; two independent reviews then found ways the scan could pass
without checking). All names below are fictional placeholders or on-screen phrases of the packaged sample film."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from types import SimpleNamespace as NS

import pytest
from PIL import Image, ImageDraw, ImageFont

from tests.conftest import PKG
from vpg.tools import privacy

FONT = PKG / "examples/sample-demo/assets/fonts/MonaSansVF.ttf"
NAME = "Jane Placeholder"
HAS_OCR = bool(shutil.which("tesseract"))
needs_ocr = pytest.mark.skipif(not HAS_OCR and os.environ.get("VPG_REQUIRE_OCR") != "1",
                               reason="tesseract needed (set VPG_REQUIRE_OCR=1 to make this a failure)")


# ---------------------------------------------------------------------------------------------- matching and the name list
def _words(*ws):
    return [(w, 10 + 90 * i, 10, 80, 20) for i, w in enumerate(ws)]


def test_names_match_in_any_order_split_or_merged_but_never_as_a_lone_part():
    assert privacy.match_names(_words("Owner:", "Jane", "Placeholder,"), [NAME]) == [(1, 1.0, [100, 10, 170, 20])]
    assert privacy.match_names(_words("Placeholder,", "Jane"), [NAME])[0][:2] == (1, 1.0)           # surname first
    assert privacy.match_names(_words("Jane", "Place", "holder"), [NAME])[0][:2] == (1, 1.0)        # split by OCR
    assert privacy.match_names(_words("JanePlaceholder"), [NAME])[0][:2] == (1, 1.0)                # merged by OCR
    assert privacy.match_names(_words("Placeholder", "record"), [NAME]) == []                       # a lone surname is not a hit
    assert privacy.match_names(_words("Johnson", "&", "Johnson"), ["Al Johnson"]) == []            # innocent words don't block
    assert privacy.match_names(_words("Seattle,", "Washington"), ["Ed Washington"]) == []
    assert privacy.match_names(_words("Europe", "Contact"), ["Europa", "Al"]) == []                 # near words, short entry


def test_a_declared_list_that_cannot_be_checked_is_refused(tmp_path):
    cfg = {"privacy": {"names": "names.json"}}
    assert privacy.load_names(tmp_path, {}) == ([], [])
    with pytest.raises(privacy.PrivacyConfigError, match="cannot read"):
        privacy.load_names(tmp_path, cfg)
    for bad in ({"Names": [NAME]}, NAME, [], [NAME, 3], ["Al", "Bo"]):
        (tmp_path / "names.json").write_text(json.dumps(bad))
        with pytest.raises(privacy.PrivacyConfigError):
            privacy.load_names(tmp_path, cfg)
    (tmp_path / "names.json").write_text(json.dumps({"names": [NAME, "Al"]}))
    assert privacy.load_names(tmp_path, cfg) == ([NAME, "Al"], [2])                                 # the short entry is reported
    outside = tmp_path / "tenant/names.json"                                                        # kept out of the repository
    outside.parent.mkdir()
    outside.write_text(json.dumps([NAME]))
    assert privacy.load_names(tmp_path / "project", {"privacy": {"names": str(outside)}}) == ([NAME], [])


def test_the_self_check_ends_with_its_verdict_so_a_pipe_cannot_hide_a_hit(tmp_path, capsys, monkeypatch):
    """#71: a producer's chain piped the check through `tail -3` and printed `privacy exit 0` right after a hit."""
    (tmp_path / "vpg.project.yaml").write_text("id: p\n")
    (tmp_path / "names.json").write_text(json.dumps([NAME]))
    hit = {"entry": 1, "start_s": 94.1, "end_s": 94.4, "reads": 3, "box": [1513, 141, 104, 12]}
    for windows, code, verdict in (([hit], 1, "privacy: FAIL (1 name window)"), ([hit, hit], 1, "privacy: FAIL (2 name windows)"),
                                   ([], 0, "privacy: PASS")):
        monkeypatch.setattr(privacy, "scan_video", lambda *a, **k: {"frames": 10, "fps": 10.0, "ocr_calls": 3, "windows": windows})
        assert privacy.main([str(tmp_path), "any.mp4", "--names", str(tmp_path / "names.json")]) == code
        assert capsys.readouterr().out.rstrip().splitlines()[-1] == verdict
    # critic S4: a scan that could not check anything says so last, so a pipe never reads it as a pass
    assert privacy.main([str(tmp_path), "any.mp4"]) == 2 and capsys.readouterr().out.rstrip().splitlines()[-1] == \
        "privacy: NOT CHECKED (no name list)"

    def unavailable(*a, **k):
        raise privacy.OcrUnavailable("no OCR engine")
    monkeypatch.setattr(privacy, "scan_video", unavailable)
    assert privacy.main([str(tmp_path), "any.mp4", "--names", str(tmp_path / "names.json")]) == 3
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == "privacy: NOT CHECKED (the scan could not complete)"
    (tmp_path / "bad.json").write_text(json.dumps({"not": "a list"}))
    assert privacy.main([str(tmp_path), "any.mp4", "--names", str(tmp_path / "bad.json")]) == 3
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == "privacy: NOT CHECKED (the name list was refused)"
    configs = {"gone": None, "bad-yaml": b"id: [unclosed\n", "a-list": b"- a\n- b\n", "not-utf8": b"id: \xff\xfe\n",
               "fps-word": b"privacy: {scan_fps: fast}\n", "privacy-list": b"privacy: [a, b]\n"}
    for broken, text in configs.items():                              # critic r2 N3, r3 N2/N3: no traceback, no exit 1
        where = tmp_path / broken
        where.mkdir()
        if text is not None:
            (where / "vpg.project.yaml").write_bytes(text)
        assert privacy.main([str(where), "any.mp4", "--names", str(tmp_path / "names.json")]) == 3, broken
        last = capsys.readouterr().out.rstrip().splitlines()[-1]
        assert last == ("privacy: NOT CHECKED (the check failed)" if broken in ("fps-word", "privacy-list")
                        else "privacy: NOT CHECKED (no readable project config)"), broken


def test_the_self_check_takes_a_list_without_changing_the_project(tmp_path, capsys, monkeypatch):
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "vpg.project.yaml").write_text("id: p\n")
    (tmp_path / "tenant.json").write_text(json.dumps([NAME]))
    monkeypatch.chdir(tmp_path)                                                              # a relative list is found from here
    video = str(tmp_path / "missing.mp4")
    assert privacy.main([str(proj), video]) == 2 and "nothing to check" in capsys.readouterr().out
    assert privacy.main([str(proj), video, "--names", "tenant.json"]) == 3                   # the list is used; the video is unreadable
    assert "could not complete" in capsys.readouterr().out
    assert privacy.main([str(proj), video, "--names"]) == 2                                  # a flag without a list is a usage error
    assert (proj / "vpg.project.yaml").read_text() == "id: p\n"

# ---------------------------------------------------------------------------------------------- OCR that fails must never pass
def _fake_tesseract(tmp_path, monkeypatch, body: str):
    exe = tmp_path / "bin/tesseract"
    exe.parent.mkdir(exist_ok=True)
    exe.write_text("#!/bin/sh\n" + body + "\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}{os.environ['PATH']}")


HEADER_ONLY = "printf 'level\\tpage_num\\tblock_num\\tpar_num\\tline_num\\tword_num\\tleft\\ttop\\twidth\\theight\\tconf\\ttext\\n'"


def test_broken_ocr_raises_instead_of_reading_nothing(tmp_path, monkeypatch):
    img = Image.new("L", (40, 20), 255)
    _fake_tesseract(tmp_path, monkeypatch, "echo 'Error opening data file eng.traineddata' >&2; exit 1")
    with pytest.raises(privacy.OcrUnavailable, match="exited 1"):
        privacy.tesseract_words(img)
    _fake_tesseract(tmp_path, monkeypatch, "exit 0")
    with pytest.raises(privacy.OcrUnavailable, match="no TSV"):
        privacy.tesseract_words(img)
    _fake_tesseract(tmp_path, monkeypatch, HEADER_ONLY)
    assert privacy.tesseract_words(img) == []
    with pytest.raises(privacy.OcrUnavailable, match="self-test"):
        privacy.ocr_self_test(privacy.tesseract_words)                                              # runs, but reads nothing


def test_each_fail_closed_guard_holds_on_its_own(tmp_path, monkeypatch):
    """Review (v1.3 landing): the two guards masked each other in the tests. With tesseract missing, reading refuses; and
    a scan whose OCR runs but reads nothing fails its self-test before any frame could be judged clean."""
    monkeypatch.setattr(privacy.shutil, "which", lambda name: None)
    with pytest.raises(privacy.OcrUnavailable, match="not installed"):
        privacy.tesseract_words(Image.new("L", (40, 20), 255))
    monkeypatch.undo()
    with pytest.raises(privacy.OcrUnavailable, match="self-test"):
        privacy.scan_video(tmp_path / "never-opened.mp4", [NAME], fps=10, ocr=lambda img: [])


def test_a_release_is_blocked_when_ocr_runs_but_reads_nothing(tmp_path, monkeypatch):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"any bytes: the self-test runs before the video is opened")
    (tmp_path / "names.json").write_text(json.dumps([NAME]))
    _fake_tesseract(tmp_path, monkeypatch, HEADER_ONLY)
    ctx = NS(project_dir=tmp_path, config={"privacy": {"names": "names.json"}}, workdir=tmp_path / "w")
    problem = privacy.release_problem(ctx, master, privacy.sha256_file(master))
    assert "self-test failed" in problem and "cannot be ruled out" in problem


# ---------------------------------------------------------------------------------------------- how a name comes on screen
def _canvas() -> Image.Image:
    img = Image.new("L", (640, 360), 255)
    ImageDraw.Draw(img).text((40, 20), "Case record", fill=0, font=ImageFont.truetype(str(FONT), 30))
    return img


def _write(img: Image.Image, text: str, xy=(40, 200), fill: int = 0) -> Image.Image:
    img = img.copy()
    ImageDraw.Draw(img).text(xy, text, fill=fill, font=ImageFont.truetype(str(FONT), 30))
    return img


def _veil(img: Image.Image, box) -> Image.Image:
    img = img.copy()
    ImageDraw.Draw(img).rectangle(box, fill=90)
    return img


def _fade(base: Image.Image, final: int, steps: int, hold: int = 3) -> list[Image.Image]:
    return [base] * 3 + [_write(base, NAME, fill=255 - round((255 - final) * k / steps)) for k in range(1, steps + 1)] \
        + [_write(base, NAME, fill=final)] * hold


def _hits(frames, fps=10) -> list[tuple[int, float, float]]:
    report = privacy.scan_frames(frames, [NAME, "Zed Testname"], fps=fps)
    return [(w["entry"], w["start_s"], w["end_s"]) for w in report["windows"]]


@needs_ocr
def test_a_name_is_caught_however_it_comes_on_screen():
    base = _canvas()
    full = _write(base, NAME)
    scroll = [_veil(_write(base, NAME, (40, y)), (30, y - 6, 350, y + 42)) if y in (300, 100) else _write(base, NAME, (40, y))
              for y in [300] * 4 + [260, 220, 180, 140] + [100] * 4]                              # veiled only at rest
    typing = [base] * 2 + [_write(base, NAME[:k]) for k in range(1, len(NAME) + 1)] + [full] * 2
    first = 40 + round(ImageFont.truetype(str(FONT), 30).getlength("Jane ")) - 2
    lift = [_veil(full, (first, 194, 350, 242))] * 3 + [full] * 4                                  # the veil lifts off the surname
    assert [h[0] for h in _hits(scroll)] == [1] and 0.4 <= _hits(scroll)[0][1] <= 0.7
    assert [h[0] for h in _hits(_fade(base, 85, 10))] == [1]                                        # quick fade to grey
    assert [h[0] for h in _hits(typing)] == [1]
    assert [h[0] for h in _hits(lift)] == [1] and _hits(lift)[0][1] == 0.3
    assert _hits([full] * 5) == [(1, 0.0, 0.0)]                                                   # on screen from the first frame
    assert _hits([_veil(f, (30, 0, 350, 360)) for f in scroll]) == []                              # veiled throughout


@needs_ocr
def test_slow_fades_are_read_even_when_every_sample_looks_at_rest():
    base = _canvas()
    assert [h[0] for h in _hits(_fade(base, 0, 50))] == [1]                                        # black over 5 s at 10 fps
    assert [h[0] for h in _hits(_fade(base, 85, 30))] == [1]                                       # #555 over 3 s at 10 fps
    assert [h[0] for h in _hits(_fade(base, 118, 30), fps=30)] == [1]                              # #767676 over 1 s at 30 fps


@needs_ocr
def test_ocr_reads_through_a_pipe_and_never_writes_a_frame_to_disk(monkeypatch):
    """A frame on disk is a copy of any name in it; and macOS leptonica rewrites /tmp paths, so with TMPDIR unset
    (cron, launchd, env -i) OCR of a /tmp file failed and every release was blocked."""
    def no_files(*a, **k):
        raise AssertionError("OCR wrote a frame to disk")
    monkeypatch.delenv("TMPDIR", raising=False)                         # the tesseract child sees the cron condition too
    monkeypatch.setattr(privacy.tempfile, "tempdir", "/tmp")
    monkeypatch.setattr(privacy.tempfile, "NamedTemporaryFile", no_files)
    monkeypatch.setattr(privacy.tempfile, "mkstemp", no_files)
    words = privacy.tesseract_words(_write(_canvas(), NAME))
    assert [h[0] for h in privacy.match_names(words, [NAME])] == [1]


@needs_ocr
def test_a_fade_is_read_while_something_else_keeps_moving():
    base = _canvas()
    frames = []
    for k in range(40):                                                                           # a spinner never lets it rest
        opacity = min(1.0, k / 10) if k < 30 else max(0.0, (40 - k) / 10)
        img = _write(base, NAME, fill=255 - round(170 * opacity)) if opacity > 0 else base.copy()  # to #555: below motion
        ImageDraw.Draw(img).rectangle((560 + (k % 4) * 12, 320, 572 + (k % 4) * 12, 332), fill=0)
        frames.append(img)
    hits = _hits(frames)                                                                          # read about once a second
    assert hits and {h[0] for h in hits} == {1}


def test_still_frames_are_not_re_read_and_the_time_budget_blocks():
    calls = []
    frames = [_canvas()] * 20

    def stub(img):
        calls.append(img.size)
        return []
    report = privacy.scan_frames(frames, [NAME], fps=10, ocr=stub)
    assert report["frames"] == 20 and calls == [(640, 360)]
    with pytest.raises(privacy.PrivacyScanError, match="time budget"):
        privacy.scan_frames(frames, [NAME], fps=10, ocr=stub, deadline=time.monotonic() - 1)


# ---------------------------------------------------------------------------------------------- the release check
def test_release_problem_fails_closed_never_writes_names_and_never_reuses_a_report(tmp_path, monkeypatch):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"not really a video")
    sha = privacy.sha256_file(master)
    (tmp_path / "names.json").write_text(json.dumps([NAME]))
    ctx = NS(project_dir=tmp_path, config={"privacy": {"names": "names.json"}}, workdir=tmp_path / "w")
    calls = []

    def leaky(video, names, fps, budget):
        calls.append(fps)
        return {"frames": 1500, "ocr_calls": 90, "fps": fps,
                "windows": [{"entry": 1, "start_s": 121.7, "end_s": 121.9, "reads": 2, "best": 1.0, "box": [317, 286, 205, 23]}]}

    monkeypatch.setattr(privacy, "scan_video", leaky)
    problem = privacy.release_problem(ctx, master, sha)
    assert "list entry #1 readable from 121.70 s" in problem and "Jane" not in problem
    report = json.loads((tmp_path / "w/privacy-report.json").read_text())
    assert "Jane" not in json.dumps(report) and set(report) >= {"master_sha256", "scanner"} and "list_sha256" not in report
    assert privacy.release_problem(ctx, master, sha) == problem and calls == ["native", "native"]   # scanned again, not reused
    assert privacy.release_problem(ctx, master, "another-hash") is None and len(calls) == 2         # not the reviewed master
    for exc in (privacy.OcrUnavailable("tesseract is not installed"), privacy.PrivacyScanError("ffmpeg exited 1"), ValueError("x")):
        def failing(video, names, fps, budget, exc=exc):
            raise exc
        monkeypatch.setattr(privacy, "scan_video", failing)
        assert "cannot be ruled out" in privacy.release_problem(ctx, master, sha)
    (tmp_path / "names.json").write_text(json.dumps({"Names": [NAME]}))
    assert "name list is unusable" in privacy.release_problem(ctx, master, sha)
    assert privacy.release_problem(NS(project_dir=tmp_path, config={}, workdir=tmp_path / "w"), master, sha) is None


def test_streaming_fails_closed_on_a_failing_decoder_or_a_second_video_stream(tmp_path, monkeypatch):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nhead -c 64 /dev/zero\nexit 1\n")                                   # two 8x4 frames, then an error
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(privacy, "FFMPEG", str(fake))
    monkeypatch.setattr(privacy, "probe", lambda v: {"streams": [{"codec_type": "video", "width": 8, "height": 4}]})
    seen = []
    with pytest.raises(privacy.PrivacyScanError, match="ffmpeg exited 1"):
        for frame in privacy.gray_frames(tmp_path / "m.mp4", 5):
            seen.append(frame.size)
    assert seen == [(8, 4), (8, 4)]
    monkeypatch.setattr(privacy, "probe", lambda v: {"streams": [{"codec_type": "video", "width": 8, "height": 4}] * 2})
    with pytest.raises(privacy.PrivacyScanError, match="2 video streams"):
        list(privacy.gray_frames(tmp_path / "m.mp4", 5))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
def test_frames_stream_from_ffmpeg_and_an_unreadable_video_fails_closed(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=320x180:d=1:r=10", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    frames = list(privacy.gray_frames(clip, 5))
    assert len(frames) in (5, 6) and frames[0].size == (320, 180) and frames[0].mode == "L"
    (tmp_path / "broken.mp4").write_bytes(b"\x00" * 64)
    with pytest.raises(privacy.PrivacyScanError):
        list(privacy.gray_frames(tmp_path / "broken.mp4", 5))


@needs_ocr
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="the dry run renders real media with ffmpeg")
def test_the_release_is_blocked_when_the_master_shows_a_listed_name(tmp_path):
    from vpg.dryrun import dry_run
    from vpg.engine import Engine
    from vpg.tools import verify
    proj = tmp_path / "run"
    assert dry_run(out=str(proj), keep=True)["ok"]
    eng = Engine(proj, read_only=True)
    layers = json.loads((proj / next(a.path for a in eng.state.artifacts.values()
                                     if a.key == "text_layers" and a.status == "current")).read_text())["layers"]
    phrase = next(t for t in (l.get("text", "") for l in layers) if len(t.split()) == 2 and all(len(w) >= 5 for w in t.split()))
    for attempt, listed, blocked in ((8, [phrase], True), (9, ["Zed Testname"], False)):
        (proj / "names.json").write_text(json.dumps({"names": listed}))
        ctx = eng._context(eng.node("n23_final_verification"), attempt, "fp", "tok")
        ctx.config = {**ctx.config, "privacy": {"names": "names.json", "scan_fps": 4}}
        problems = verify.final(ctx).data["problems"]
        assert any("list entry #1 readable" in p for p in problems) is blocked
        assert phrase.lower() not in (ctx.workdir / "privacy-report.json").read_text().lower()
