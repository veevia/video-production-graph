"""The engine's documented commands leave no generated files in the package: no bytecode and no pytest cache.

Each command runs in a scratch copy of the package, with the environment's own bytecode settings removed, so these tests
measure the package's guards (vpg/__init__.py, conftest.py, pytest.ini) rather than the caller's flags."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import PKG

GENERATED = ("__pycache__", ".pytest_cache")
COMMANDS = {
    "cli": ["-m", "vpg", "validate"],
    "scoring-command": ["scoring/vpg_score.py", "--data", "{tmp}/prefs", "report"],
    "tests": ["-m", "pytest", "-q", "tests/test_graph.py"],
    "scoring-tests": ["-m", "pytest", "-q", "scoring"],
    # -P keeps the working directory off sys.path, as the `pytest` script does.
    "tests-importlib": ["-P", "-m", "pytest", "-q", "--import-mode=importlib", "tests/test_graph.py"],
}


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "pkg"
    shutil.copytree(PKG, dst, symlinks=True, ignore=shutil.ignore_patterns(
        *GENERATED, "*.pyc", ".DS_Store", ".git", ".venv", "node_modules", "work", ".vpg"))
    return dst


def _env() -> dict[str, str]:
    drop = {"PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTEST_ADDOPTS", "PYTHONPATH"}
    return {k: v for k, v in os.environ.items() if k not in drop}


def _generated(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.suffix == ".pyc" or any(part in GENERATED for part in p.relative_to(root).parts))


@pytest.mark.parametrize("name", list(COMMANDS))
def test_documented_commands_write_no_bytecode_or_cache(tmp_path, name):
    pkg = _copy(tmp_path)
    args = [a.replace("{tmp}", str(tmp_path)) for a in COMMANDS[name]]
    out = subprocess.run([sys.executable, *args], cwd=pkg, env=_env(), capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-2000:]
    assert _generated(pkg) == []


def test_the_guard_removes_only_the_module_s_own_cache(tmp_path):
    from vpg import _write_no_bytecode
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    for name in ("mod.cpython-311.pyc", "mod.cpython-314.pyc", "mod.cpython-311-pytest-9.1.1.pyc",
                 "other.cpython-311.pyc"):
        (cache / name).write_bytes(b"")
    _write_no_bytecode(str(tmp_path / "mod.py"))
    assert sys.dont_write_bytecode and sorted(p.name for p in cache.iterdir()) == ["other.cpython-311.pyc"]
    _write_no_bytecode(str(tmp_path / "other.py"))
    assert not cache.exists()
    _write_no_bytecode(str(tmp_path / "other.py"))   # no cache folder at all


def test_the_guard_survives_a_concurrent_removal_of_the_cache(tmp_path, monkeypatch):
    """Another process may remove __pycache__ while this one lists it; the import must not fail."""
    import pathlib
    from vpg import _write_no_bytecode
    (tmp_path / "__pycache__").mkdir()

    def vanished(self, pattern):
        raise FileNotFoundError(self)
    monkeypatch.setattr(pathlib.Path, "glob", vanished)
    _write_no_bytecode(str(tmp_path / "mod.py"))
    assert sys.dont_write_bytecode


def test_the_loop_review_imports_vpg_before_other_first_party_code():
    """loop/review.py runs as a script and later imports tests.history_fidelity; importing vpg first keeps that from
    writing bytecode. The public checkout has no loop/."""
    import ast
    review = PKG / "loop" / "review.py"
    if not review.is_file():
        pytest.skip("no loop/ in this checkout")
    first_party = []
    for node in ast.walk(ast.parse(review.read_text())):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
            [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        first_party += [(node.lineno, n) for n in names if n.split(".")[0] in ("vpg", "tests")]
    assert first_party and min(first_party)[1].split(".")[0] == "vpg"
