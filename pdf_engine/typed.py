# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed JSON: what a backend returns, with its tuples and bytes kept.

JSON knows arrays, not tuples, and has no bytes. A client that compares a
coordinate to a tuple, uses it as a dictionary key, or reads an embedded
font needs them back exactly. So, in an answer:

* a tuple is written ``{"__tuple__": [...]}``;
* bytes are written ``{"__bytes__": "<base64>"}``;
* everything else is plain JSON (non-finite floats as ``NaN``/``Infinity``).

Any language can read it; :func:`decode` gives a Python client its types back.
"""

from __future__ import annotations

import base64
from typing import Any


def encode(value: Any) -> Any:
    """``value`` with every tuple and bytes object marked.

    Objects with a ``__iter__`` that are not lists, dicts, strings or bytes —
    PyMuPDF's ``Rect``, ``Point``, ``Quad`` — are written as tuples of their
    items: the protocol speaks numbers, not a library's classes.
    """
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, tuple):
        return {"__tuple__": [encode(item) for item in value]}
    if isinstance(value, bytes | bytearray | memoryview):
        return {"__bytes__": base64.b64encode(bytes(value)).decode("ascii")}
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return {"__tuple__": [encode(item) for item in value]}


def decode(value: Any) -> Any:
    """The inverse of :func:`encode`."""
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1:
            if "__tuple__" in value:
                return tuple(decode(item) for item in value["__tuple__"])
            if "__bytes__" in value:
                return base64.b64decode(value["__bytes__"])
        return {key: decode(item) for key, item in value.items()}
    return value
