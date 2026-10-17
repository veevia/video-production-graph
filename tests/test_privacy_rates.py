"""v1.6 review C1/I6: the privacy scan never reads fewer frames than the video shows, and a live window's edges are
counted the way its frames are tested. A variable-frame-rate master averaged 4.6 fps while one second of it ran at 30 fps;
decoding at the average read 42 of its frames where v1.5.2 read 90, and the window reported "fully decoded"."""

from __future__ import annotations

import random
import subprocess

import pytest
from PIL import Image

from vpg.tools import privacy
from vpg.tools._common import FFMPEG

NAME = "Jane Placeholder"


def dark_corner_ocr(img):
    return [("Jane", 0, 0, 3, 3), ("Placeholder", 4, 0, 3, 3)] if img.getpixel((1, 1)) < 128 else []


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for media checks")
def test_a_variable_rate_master_is_decoded_at_its_fastest_rate(tmp_path):
    slow, fast, vfr = tmp_path / "slow.mp4", tmp_path / "fast.mp4", tmp_path / "vfr.mp4"
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-video_track_timescale", "30000"]
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:r=2:d=8", *enc, str(slow)], check=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:r=30:d=1",
                    "-vf", "drawbox=x=0:y=0:w=8:h=8:color=black:t=fill:enable='eq(n\\,15)'", *enc, str(fast)], check=True)
    (tmp_path / "list.txt").write_text(f"file '{slow}'\nfile '{fast}'\n")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "list.txt"), "-c", "copy",
                    str(vfr)], check=True)
    rates = privacy.stream_rates(vfr)
    avg, r = privacy._rate(rates["avg_frame_rate"]), privacy._rate(rates["r_frame_rate"])
    assert avg < 10 <= r                                                          # the average hides the fast second
    rate, arg, _ = privacy.decode_rate("native", vfr)
    assert rate == pytest.approx(r) and arg == rates["r_frame_rate"]             # the stream's own rational, exactly
    with pytest.raises(privacy.PrivacyScanError, match=r"dropped \d+ frame"):
        sum(1 for _ in privacy.gray_frames(vfr, rates["avg_frame_rate"], every_frame=True))   # at the average: fails closed
    report = privacy.scan_frames(privacy.gray_frames(vfr, arg, every_frame=True), [NAME], rate, dark_corner_ocr,
                                 live_windows=[{"start": 8.0, "end": 9.0, "what": "cast"}])
    assert report["windows"], "the one dark frame of the fast second is read"
    [w] = report["live_windows"]
    assert w["frames_in_window"] == w["frames_decoded"] == round(rate) and w["sampled_fraction"] == 1.0


def test_the_decode_rate_never_falls_below_ten_a_pinned_rate_or_the_stream(monkeypatch):
    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": "5/1", "avg_frame_rate": "4/1"})
    assert privacy.decode_rate(None, "v")[:2] == (10.0, "10") and privacy.decode_rate(3, "v")[0] == 10.0
    assert privacy.decode_rate(30, "v")[:2] == (30.0, "30")
    assert privacy.decode_rate("30000/1001", "v")[1] == "30000/1001"
    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001"})
    assert privacy.decode_rate(29.97, "v")[1] == "30000/1001"                   # never an approximation of the stream's rate
    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": "30/1", "avg_frame_rate": "30/1"})
    assert privacy.decode_rate(30, "v")[1] == "30/1"                             # a tie keeps the stream's own rational
    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": "60/1", "avg_frame_rate": "12/1"})
    assert privacy.decode_rate(30, "v")[0] == 60.0                                 # a pinned rate never lowers the stream's
    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": "90000/1", "avg_frame_rate": "30/1"})
    with pytest.raises(privacy.PrivacyScanError, match="above the 120 fps"):
        privacy.decode_rate(None, "v")
    privacy.validate_scan_fps("30000/1001")
    with pytest.raises(privacy.PrivacyConfigError):
        privacy.validate_scan_fps("fast")


@pytest.mark.parametrize("fps", [24, 25, 29.97, 30])
def test_a_fully_decoded_window_is_always_complete_whatever_its_edges(fps):
    rng = random.Random(7)
    frames = [Image.new("L", (4, 4), 255) for _ in range(int(10 * fps))]
    windows = []
    for _ in range(200):
        a = rng.randint(0, 95) / 10
        windows.append({"start": a, "end": min(10.0, a + rng.randint(1, 40) / 10), "what": "w"})
    report = privacy.scan_frames(frames, [NAME], fps, lambda img: [], live_windows=windows)
    assert [w["sampled_fraction"] for w in report["live_windows"]] == [1.0] * len(windows)
    assert privacy.live_window_problem(report) is None


def test_windows_past_the_end_are_clamped_or_named():
    frames = [Image.new("L", (4, 4), 255) for _ in range(30)]                     # 1 s at 30 fps
    report = privacy.scan_frames(frames, [NAME], 30, lambda img: [], live_windows=[
        {"start": 0.5, "end": 3.0, "what": "scroll"}, {"start": 2.0, "end": 2.5, "what": "late cast"}])
    first, late = report["live_windows"]
    assert first["sampled_fraction"] == 1.0 and first["frames_in_window"] == 15 and first["clamped_to_end_s"] == 1.0
    assert late["starts_after_end"] and late["sampled_fraction"] == 0.0
    assert privacy.live_window_problem(report) == "live window #2 (late cast, 2–2.5 s) starts after the video ends"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for media checks")
def test_a_stream_that_understates_its_rate_fails_closed(tmp_path, monkeypatch):
    """Review C1: r_frame_rate is ffprobe's guess from the first frames. A 60 fps burst after them decoded at the stated
    30 fps lost frames with no error; now the fps filter's own count of dropped frames stops the scan."""
    burst = tmp_path / "burst.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:r=60:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(burst)], check=True)
    monkeypatch.setattr(privacy, "ocr_self_test", lambda ocr: None)
    stated = privacy.stream_rates
    monkeypatch.setattr(privacy, "stream_rates", lambda video: {"r_frame_rate": "30/1", "avg_frame_rate": "30/1"})
    with pytest.raises(privacy.PrivacyScanError, match=r"dropped 30 frame"):
        privacy.scan_video(burst, [NAME], "native", ocr=lambda img: [])
    monkeypatch.setattr(privacy, "stream_rates", stated)
    report = privacy.scan_video(burst, [NAME], "native", ocr=lambda img: [])   # its own stated 60/1: nothing dropped
    assert report["frames"] == 60 and report["decode_arg"] == "60/1"


def test_a_name_faded_in_below_the_motion_threshold_is_read_on_a_windows_first_frame():
    """Review S7: a window frame identical to the frame before it was skipped even when that frame was never read in
    full. A name that appears in a step too small to count as motion, just before the window, was then never read."""
    shade = lambda v: Image.new("L", (8, 8), v)
    frames = [shade(140)] * 9 + [shade(120)] * 11 + [shade(140)] * 10      # 30 fps; the name shows at 0.30–0.63 s
    ocr = lambda img: [("Jane", 0, 0, 3, 3), ("Placeholder", 4, 0, 3, 3)] if img.getpixel((1, 1)) < 128 else []
    assert privacy.scan_frames(frames, [NAME], 30, ocr)["windows"]                        # no window: the drift read finds it
    report = privacy.scan_frames(frames, [NAME], 30, ocr, live_windows=[{"start": 10 / 30, "end": 20 / 30, "what": "drawer"}])
    assert report["windows"] and report["live_windows"][0]["ocr_reads"] == 1          # read once, then repeats are skipped
