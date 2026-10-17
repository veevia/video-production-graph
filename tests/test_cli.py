"""The CLI boundary that parallel agents use: process locking, read-only inspection and clean errors."""

import json
import shlex
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vpg import cli
from vpg.engine import Engine, EngineError
from vpg.executors import TaskPacketExecutor
from vpg.lock import holder, project_lock, project_lock_path

PKG = Path(__file__).resolve().parents[1]


def _vpg(*args, timeout=120):
    return subprocess.run([sys.executable, "-m", "vpg", *map(str, args)], cwd=PKG, capture_output=True, text=True,
                          timeout=timeout)


def _open_packet(proj: Path) -> tuple[str, Path]:
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    rt = eng.rt("a_intake")
    out = proj / "work/a_intake" / f"a{rt.attempts}" / "a_intake.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}")
    return rt.dispatch_token, out


def _node_status(proj: Path, node: str) -> str:
    return json.loads((proj / ".vpg/state.json").read_text())["runtime"]["nodes"][node]["status"]


def test_inspection_commands_are_read_only(project):
    proj, _ = project
    _open_packet(proj)
    state, ledger = (proj / ".vpg/state.json").read_bytes(), (proj / ".vpg/ledger.jsonl").read_bytes()
    assert cli.main(["status", str(proj)]) == 0
    assert cli.main(["tasks", str(proj)]) == 0
    assert cli.main(["ledger-verify", str(proj)]) == 0
    assert (proj / ".vpg/state.json").read_bytes() == state      # no resume bump, no save
    assert (proj / ".vpg/ledger.jsonl").read_bytes() == ledger   # no ledger events


def test_read_only_engine_refuses_changes(project):
    proj, _ = project
    _open_packet(proj)
    with pytest.raises((EngineError, PermissionError)):
        Engine(proj, read_only=True).run(max_steps=1)


def test_status_does_not_wait_for_a_running_command(project, capsys):
    proj, _ = project
    _open_packet(proj)
    with project_lock(proj, "run"):
        t0 = time.monotonic()
        assert cli.main(["status", str(proj)]) == 0
        assert time.monotonic() - t0 < 5
    assert "is changing this run" in capsys.readouterr().out


def test_parallel_submission_waits_for_the_lock_then_lands(project):
    proj, _ = project
    token, out = _open_packet(proj)
    with project_lock(proj, "run"):
        p = subprocess.Popen([sys.executable, "-m", "vpg", "submit", str(proj), "a_intake", "--token", token,
                              "--actor", "agent:test", "--output", f"a_intake={out}"],
                             cwd=PKG, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        seen, deadline = "", time.monotonic() + 60                 # hold the lock until the writer is waiting on it,
        while "waiting for the lock held by pid" not in seen and p.poll() is None and time.monotonic() < deadline:
            if select.select([p.stderr], [], [], 0.5)[0]:          # however slowly it starts on a loaded machine
                seen += p.stderr.readline()
        assert "waiting for the lock held by pid" in seen, seen   # a second writer waits instead of racing
        assert p.poll() is None
        assert _node_status(proj, "a_intake") == "WAITING_RUNTIME"
    _, err = p.communicate(timeout=120)
    assert p.returncode == 0, seen + err
    assert _node_status(proj, "a_intake") == "STEP_PASS"          # the submission was applied, not lost


def test_lock_timeout_is_a_clean_error(project):
    proj, _ = project
    token, out = _open_packet(proj)
    with project_lock(proj, "run"):
        r = _vpg("submit", proj, "a_intake", "--token", token, "--actor", "agent:test", "--output", f"a_intake={out}",
                 "--lock-timeout", "0.5")
    assert r.returncode == 4 and "vpg run" in r.stderr and "Traceback" not in r.stderr
    assert _node_status(proj, "a_intake") == "WAITING_RUNTIME"


def test_lock_is_released_when_its_holder_dies(project):
    proj, _ = project
    code = (f"import sys, time; sys.path.insert(0, {str(PKG)!r})\nfrom vpg.lock import project_lock\n"
            f"with project_lock({str(proj)!r}, 'run'):\n    print('held', flush=True); time.sleep(60)\n")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        assert holder(project_lock_path(proj))["command"] == "run"
    finally:
        p.kill()
        p.wait(timeout=30)
    assert holder(project_lock_path(proj)) is None                 # no stale lock after a crash


def test_agent_errors_are_clean(project):
    proj, _ = project
    _, out = _open_packet(proj)
    r = _vpg("submit", proj, "a_intake", "--token", "wrong", "--actor", "agent:test", "--output", f"a_intake={out}")
    assert r.returncode == 4 and "Traceback" not in r.stderr
    r = _vpg("unblock", proj, "no_such_node", "--actor", "Wei")
    assert r.returncode == 4 and "nothing to unblock called 'no_such_node'" in r.stderr and "Traceback" not in r.stderr


def test_task_packet_submit_line_runs_as_written(project, monkeypatch):
    proj, _ = project
    monkeypatch.chdir(PKG)                                         # agents run vpg from the package root
    _, out = _open_packet(proj)
    packet = (proj / ".vpg/tasks/a_intake-a1.md").read_text()
    line = next(ln for ln in packet.splitlines() if ln.startswith("python -m vpg submit"))
    assert cli.main(shlex.split(line.replace("<path>", str(out)))[3:]) == 0
    assert _node_status(proj, "a_intake") == "STEP_PASS"
