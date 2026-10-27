# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m pdf_engine [--host H] [--port P] [--watch-stdin] | --pipe``."""

from __future__ import annotations

import argparse

from pdf_engine.server import serve, serve_pipe


def main() -> None:
    """Parse the command line and serve."""
    parser = argparse.ArgumentParser(prog="pdf_engine", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100, help="0 = chosen by the system")
    parser.add_argument(
        "--watch-stdin",
        action="store_true",
        help="stop when stdin closes (the engine was started by a parent process)",
    )
    parser.add_argument(
        "--pipe",
        action="store_true",
        help="serve frames on stdin/stdout instead of HTTP (PROTOCOL.md, « Pipe »)",
    )
    arguments = parser.parse_args()
    if arguments.pipe:
        serve_pipe()
        return
    serve(arguments.host, arguments.port, watch_stdin=arguments.watch_stdin)


if __name__ == "__main__":
    main()
