# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A PDF engine that executes structured orders and returns structured results.

The engine knows documents, pages, rectangles and pixels. It decides
nothing: every order carries all of its parameters, and the same order on the
same file always gives the same result. It is reached over HTTP — or over a
pair of pipes, by a client that starts it — by any client that speaks the
protocol described in ``PROTOCOL.md``.

Run it with ``python -m pdf_engine --port 9100``, or ``python -m pdf_engine --pipe``.
"""

from __future__ import annotations

#: The engine's own release.
ENGINE_VERSION = "0.12.1"

#: The protocol it speaks. MAJOR changes break clients; MINOR ones add orders.
PROTOCOL_VERSION = "1.11"

#: The name it answers ``/v1/about`` with.
ENGINE_NAME = "bilingualforge-pdf-engine"
