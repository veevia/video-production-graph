"""vpg — a graph-based, agentic, self-improving video production workflow (see ../README.md)."""
import sys as _sys
from pathlib import Path as _Path

__version__ = "1.0.0"


def _write_no_bytecode(module_file: str) -> None:
    """Stop this process writing bytecode, and remove the cache that importing `module_file` just wrote.

    Bytecode is machine- and interpreter-specific; in a shared repository it keeps reappearing as changes. Python caches
    a module before running it, so that module's own cache files are removed here; nothing is written after the flag is
    set."""
    _sys.dont_write_bytecode = True
    module = _Path(module_file)
    cache = module.parent / "__pycache__"
    try:
        stale = list(cache.glob(f"{module.stem}.*.pyc"))
    except OSError:  # another process may remove the folder while this one lists it
        stale = []
    for path in stale:
        try:
            path.unlink()
        except OSError:
            pass
    try:
        cache.rmdir()
    except OSError:
        pass


_write_no_bytecode(__file__)
