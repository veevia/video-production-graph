"""Loaded by pytest before any test in this package, whichever path is run: the run writes no bytecode (pytest.ini also
turns off pytest's cache)."""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from vpg import _write_no_bytecode  # noqa: E402

_write_no_bytecode(__file__)
