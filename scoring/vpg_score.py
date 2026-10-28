#!/usr/bin/env python3
"""Compatibility CLI for the packaged vpg scoring model."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpg.scoring import *  # noqa: F403 - preserves the reference script's public API for existing callers.
from vpg.scoring import main


if __name__ == "__main__":
    sys.exit(main())
