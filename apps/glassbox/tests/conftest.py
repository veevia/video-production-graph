from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (str(APP_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from glassbox.discovery import Project, project_id  # noqa: E402
from glassbox.engine import Engine  # noqa: E402
from glassbox.reactions import ReactionStore  # noqa: E402
from synthetic import make_job, write_engine  # noqa: E402


@pytest.fixture
def engine_dir(tmp_path):
    return write_engine(tmp_path)


@pytest.fixture
def graph(engine_dir):
    return Engine(str(engine_dir)).graph()


@pytest.fixture
def job(tmp_path):
    return make_job(tmp_path / "roots")


@pytest.fixture
def project(job, tmp_path):
    root = str(tmp_path / "roots")
    return Project(id=project_id(str(job)), path=str(job), root=root, rel_path=os.path.relpath(str(job), root),
                   name="Demo film", slug="demo-film")


@pytest.fixture
def store(tmp_path):
    return ReactionStore(str(tmp_path / "data"))
