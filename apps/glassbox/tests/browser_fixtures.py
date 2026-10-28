"""Fictional, browser-decodable films and an isolated loopback server for UI checks."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from glassbox.web import make_server
from server import bundled_engine
from synthetic import (age_tree, make_governed_job, make_job, make_workflow_job,
                       write_full_candidate, write_json, write_quality_engine, write_telemetry)
from test_library import make_library_tree


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def create_films(base):
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("the synthetic browser check requires ffmpeg on PATH")
    home = base / "home"
    paths = make_library_tree(home)
    root = home / "GitHub" / "Demo_workspace" / "01_Project-Hubs"
    job = make_job(root, rel=paths["demo"].relative_to(root).as_posix(), name="Demo film")
    write_telemetry(job, run_id="demo-run-1", bad_line=False)
    for version in ("v15", "v25"):
        write_full_candidate(job, version, rework=False)
        assembly = job / "candidates" / version / "n14_video_assembly"
        mix = json.loads((assembly / "mix-report.json").read_text())
        mix["mix_measures"] = {"voice_over_music_lu_momentary": {
            "median": 16.5, "p10": 12.5, "basis": f"Fictional measurement basis {version}"}}
        write_json(assembly / "mix-report.json", mix)
        write_json(job / "candidates" / version / "plan" / "script.json",
                   {"runtime_target_s": 80.5, "units": []})
        gates = json.loads((job / "candidates" / version / "qa" / "qa-direct.json").read_text())
        gates["summary"] = {"findings": 0, "blocking": 0}
        for gate in gates["gates"].values():
            gate["findings"] = []
        write_json(job / "candidates" / version / "qa" / "qa-direct.json", gates)
        review_path = job / "candidates" / version / "reviews" / "accessibility.json"
        review = json.loads(review_path.read_text())
        review["defects"].extend([
            {"id": "ACC-N2", "severity": "P4", "time": "12.0", "what": "A fictional spacing nit"},
            {"id": "ACC-N3", "severity": "P4", "time": "24.0", "what": "A fictional caption nit"},
        ])
        write_json(review_path, review)
    make_governed_job(root, waiting_release=True)
    make_workflow_job(root, "quality-film", "quality", waiting_node="n26_look_development")

    movie = base / "fictional-master.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x243746:s=320x180:r=12:d=80.5", "-an", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(movie)],
                   check=True, capture_output=True, timeout=60)
    media = movie.read_bytes()
    for path in home.rglob("*.mp4"):
        if not path.is_symlink():
            path.write_bytes(media)
    for extension in ("png", "jpg"):
        still = base / f"fictional-poster.{extension}"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(movie), "-frames:v", "1", str(still)],
                       check=True, capture_output=True, timeout=30)
        for path in home.rglob(f"*.{extension}"):
            if not path.is_symlink():
                path.write_bytes(still.read_bytes())
    age_tree(home)
    return home, root


@contextmanager
def isolated_app():
    with tempfile.TemporaryDirectory(prefix="vpg-glassbox-ui-") as temp:
        base = Path(temp)
        home, root = create_films(base)
        engine = bundled_engine() or str(write_quality_engine(base))
        server = make_server([str(root)], engine, str(base / "data"), port=0, quiet=True,
                             library_roots=[str(home / "GitHub"), str(home / "Downloads")],
                             library_home=str(home))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        before = snapshot(home)
        try:
            server.app.registry.scan()
            status = server.app.library.scan_now()
            if status["status"] != "ready":
                raise RuntimeError(f"synthetic library scan failed: {status}")
            thread.start()
            url = f"http://127.0.0.1:{server.app.port}/"
            with urllib.request.urlopen(url + "api/meta", timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError("synthetic server did not become ready")
            yield url
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(10)
            server.server_close()
            server.app.registry.stop()
            server.app.library.wait(10)
            if thread.is_alive():
                raise RuntimeError("synthetic server failed to stop")
            if snapshot(home) != before:
                raise AssertionError("Glassbox modified a synthetic project or library file")
