# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m pdf_engine [--host H] [--port P] [--watch-stdin] [--system-trust] | --pipe``."""

from __future__ import annotations

import argparse
import sys

from pdf_engine.server import serve, serve_pipe


def trust_the_system_store() -> bool:
    """Verify TLS against the operating system's certificate store.

    The layout model's weights download on first use (``layout_load``).
    Behind a TLS-intercepting proxy, Python's own bundle refuses the proxy's
    certificate and the download fails; the system store, where such a
    proxy installs its root, accepts it. ``truststore`` comes with the
    ``layout`` extra; without it, nothing changes.

    Returns:
        True when the system store is in use.
    """
    try:
        import truststore  # noqa: PLC0415 - optional, with the layout extra
    except ImportError:
        print("pdf_engine: --system-trust sans truststore installé", file=sys.stderr)
        return False
    truststore.inject_into_ssl()
    return True


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
    parser.add_argument(
        "--system-trust",
        action="store_true",
        help="verify TLS against the system's certificate store (a proxy's root)",
    )
    arguments = parser.parse_args()
    if arguments.system_trust:
        trust_the_system_store()
    if arguments.pipe:
        serve_pipe()
        return
    serve(arguments.host, arguments.port, watch_stdin=arguments.watch_stdin)


if __name__ == "__main__":
    main()
