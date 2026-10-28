#!/usr/bin/env python3
"""VPG Glass Box: a read-only local viewer for VPG jobs, with an append-only reaction log.

    python server.py --root DIR [--root DIR ...] [--engine DIR] [--port 4420] [--data-dir DIR]
                     [--library-root DIR ... | --no-library] [--quiet]

Binds 127.0.0.1 only. Never writes under the project or library roots; reactions go to
<data-dir>/reactions.jsonl (created on the first reaction) and the video library index to
<data-dir>/library-index.json.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from glassbox import __version__, fsio  # noqa: E402
from glassbox.engine import Engine  # noqa: E402
from glassbox.web import make_server  # noqa: E402

DEFAULT_DATA_DIR = "~/.copilot/extension-data/vpg-glassbox"
DEFAULT_PORT = 4420


def bundled_engine():
    for parent in Path(HERE).parents:
        if (parent / "graph" / "workflow.yaml").is_file():
            return str(parent)
    return None


def port_answers(port):
    """True when something already accepts connections on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="VPG Glass Box: read-only viewer for VPG jobs.")
    parser.add_argument("--root", action="append", dest="roots", metavar="DIR", required=True,
                        help="folder to search for vpg.project.yaml (repeatable; required)")
    parser.add_argument("--engine", default=bundled_engine(), metavar="DIR",
                        help="engine folder holding graph/workflow.yaml (default: enclosing VPG checkout)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port on 127.0.0.1 (default 4420)")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, metavar="DIR",
                        help="where reactions.jsonl lives (default ~/.copilot/extension-data/vpg-glassbox)")
    parser.add_argument("--rescan", type=float, default=60.0, metavar="SECONDS",
                        help="how often to look for new projects (default 60)")
    library = parser.add_mutually_exclusive_group()
    library.add_argument("--library-root", action="append", dest="library_roots", metavar="DIR",
                         help="folder to index for the video library (repeatable; disabled unless supplied)")
    library.add_argument("--no-library", action="store_true", help="explicitly disable library indexing (the default)")
    parser.add_argument("--static-dir", default=None, metavar="DIR",
                        help="folder of the UI to serve (default: static next to server.py)")
    parser.add_argument("--quiet", action="store_true", help="do not log requests")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    roots = [os.path.abspath(os.path.expanduser(r)) for r in args.roots]
    data_dir = os.path.abspath(os.path.expanduser(args.data_dir))
    library_roots = [os.path.abspath(os.path.expanduser(r)) for r in args.library_roots] if args.library_roots else None
    for root in roots + (library_roots or []):
        if not os.path.isdir(root):
            print(f"vpg-glassbox: root {root} is not a directory", file=sys.stderr)
            return 2
    if args.engine is None:
        print("vpg-glassbox: --engine is required outside a VPG checkout", file=sys.stderr)
        return 2
    if not Engine(args.engine).graph().available:
        print(f"vpg-glassbox: no readable workflow graph in {args.engine}", file=sys.stderr)
        return 2
    if not 0 < args.port < 65536:
        print("vpg-glassbox: --port must be between 1 and 65535", file=sys.stderr)
        return 2
    if port_answers(args.port):
        print(f"vpg-glassbox: something already answers on 127.0.0.1:{args.port}; pick another --port.", file=sys.stderr)
        return 1
    static_dir = None
    if args.static_dir:
        static_dir = os.path.abspath(os.path.join(HERE, os.path.expanduser(args.static_dir)))
        if not os.path.isfile(os.path.join(static_dir, "index.html")):
            print(f"vpg-glassbox: --static-dir {static_dir} has no index.html", file=sys.stderr)
            return 2
    try:
        server = make_server(roots, args.engine, data_dir, port=args.port, quiet=args.quiet, library_roots=library_roots,
                             static_dir=static_dir)
    except (OSError, ValueError) as error:
        print(f"vpg-glassbox: cannot start on 127.0.0.1:{args.port}: {error}", file=sys.stderr)
        return 1
    app = server.app
    started = time.monotonic()
    projects = app.registry.scan()
    graph = app.engine.graph()
    print(f"vpg-glassbox {__version__}: {len(projects)} project(s) found in {time.monotonic() - started:.1f} s "
          f"(YAML via {fsio.yaml_backend()})")
    print(f"engine graph: {app.engine.path} ({len(graph.nodes)} nodes); workflows: {', '.join(app.engine.workflows())}")
    print(f"reactions: {app.store.path} (created on the first reaction)")
    if static_dir:
        print(f"ui: {static_dir}")
    if app.library is not None:
        app.library.start()
        print(f"library: indexing {', '.join(app.library.roots)} in the background (index {app.library.cache_path})")
    else:
        print("library: disabled (--no-library)")
    print(f"serving http://127.0.0.1:{app.port}/  (read-only view; Ctrl+C stops)", flush=True)
    app.registry.start_background(args.rescan)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.registry.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
