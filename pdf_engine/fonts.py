# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fonts: what a face measures — its widths, its glyphs, its metrics.

A font is named by a SPEC:

* ``{"builtin": "tiro"}`` — one of MuPDF's own faces (Base-14, Fira…);
* ``{"file": "<absolute path>"}`` — a font file;
* ``{"document": {"path"|"handle"}, "xref": N}`` — a font embedded in a
  document.

Each answer is MuPDF's own number, from the same ``pymupdf.Font`` calls.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pymupdf

from pdf_engine.documents import Documents
from pdf_engine.protocol import FontSpec, OrderError
from pdf_engine.typed import encode

#: Fonts kept parsed, by spec: a face costs a file parse, a composer measures
#: the same dozen faces thousands of times.
CAPACITY = 64


class Faces:
    """The parsed fonts, least recently used dropped first."""

    def __init__(self) -> None:
        self._faces: OrderedDict[tuple[Any, ...], pymupdf.Font] = OrderedDict()

    def get(self, documents: Documents, spec: FontSpec) -> pymupdf.Font:
        """The ``pymupdf.Font`` a spec names.

        A font embedded in a document opened by PATH is read again each time:
        the path names the file as it is NOW.

        Raises:
            OrderError: ``unreadable_font`` when MuPDF cannot build the face.
        """
        key = spec.key()
        if key is not None and key in self._faces:
            self._faces.move_to_end(key)
            return self._faces[key]
        try:
            if spec.builtin is not None:
                font = pymupdf.Font(spec.builtin)
            elif spec.file is not None:
                font = pymupdf.Font(fontfile=spec.file)
            else:
                with documents.named(spec.path, spec.handle) as document:
                    buffer = document.extract_font(spec.xref)[3]
                if not buffer:
                    raise ValueError(f"no font file at xref {spec.xref}")
                font = pymupdf.Font(fontbuffer=buffer)
        except OrderError:
            raise
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise OrderError("unreadable_font", f"Fonte illisible : {exc}", 422) from exc
        if key is not None:
            self._faces[key] = font
            while len(self._faces) > CAPACITY:
                self._faces.popitem(last=False)
        return font


def info(font: pymupdf.Font, buffer: bool) -> dict[str, Any]:
    """The face's name, metrics and flags — and its file, when asked."""
    answer: dict[str, Any] = {
        "name": font.name,
        "ascender": font.ascender,
        "descender": font.descender,
        "flags": dict(font.flags),
        "is_bold": font.is_bold,
        "is_italic": font.is_italic,
        "is_monospaced": font.is_monospaced,
        "glyph_count": font.glyph_count,
    }
    if buffer:
        answer["buffer"] = bytes(font.buffer or b"")
    encoded: dict[str, Any] = encode(answer)
    return encoded


def text_lengths(font: pymupdf.Font, texts: list[str], size: float) -> list[float]:
    """``font.text_length(text, fontsize=size)`` for each text."""
    return [float(font.text_length(text, fontsize=size)) for text in texts]


def named_lengths(fontname: str, texts: list[str], size: float) -> list[float]:
    """``pymupdf.get_text_length(text, fontname, size)`` for each text.

    Not the same measure as :func:`text_lengths` on ``Font(fontname)``: the
    function measures the Base-14 face through its own encoding, and differs
    from the Font object on every character outside ASCII.
    """
    return [float(pymupdf.get_text_length(text, fontname, size)) for text in texts]


def glyphs(font: pymupdf.Font, codes: list[int]) -> dict[str, list[Any]]:
    """For each code point, ``has_glyph`` and ``glyph_advance``."""
    return {
        "glyphs": [int(font.has_glyph(code)) for code in codes],
        "advances": [float(font.glyph_advance(code)) for code in codes],
    }
