# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading: the text of a page, its fonts, the positions of a string.

Each order returns MuPDF's own answer, in typed JSON (:mod:`pdf_engine.typed`):
the same structure, the same numbers, the same tuples and bytes.
"""

from __future__ import annotations

from typing import Any

import pymupdf

from pdf_engine.documents import Documents
from pdf_engine.protocol import OrderError, TextOrder
from pdf_engine.typed import encode


def _page(document: pymupdf.Document, index: int) -> pymupdf.Page:
    if index >= document.page_count:
        raise OrderError(
            "page_out_of_range",
            f"Page {index} hors du document ({document.page_count} pages).",
            422,
            {"page_count": document.page_count},
        )
    return document[index]


def _failed(what: str, exc: Exception) -> OrderError:
    return OrderError("read_failed", f"{what} impossible : {exc}", 500)


def text(documents: Documents, order: TextOrder) -> dict[str, Any]:
    """MuPDF's ``get_text(kind, clip=…, sort=…)`` on one page."""
    with documents.named(order.path, order.handle) as document:
        page = _page(document, order.page)
        options: dict[str, Any] = {}
        if order.clip is not None:
            options["clip"] = pymupdf.Rect(*order.clip)
        if order.sort is not None:
            options["sort"] = order.sort
        try:
            result = page.get_text(order.kind, **options)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture du texte", exc) from exc
        return {"result": encode(result)}


def texttrace(
    documents: Documents, path: str | None, handle: str | None, index: int
) -> dict[str, Any]:
    """MuPDF's ``get_texttrace()``: every glyph as drawn, in drawing order."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            return {"result": encode(page.get_texttrace())}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Trace du texte", exc) from exc


def search(
    documents: Documents, path: str | None, handle: str | None, index: int,
    needle: str, quads: bool,
) -> dict[str, Any]:
    """MuPDF's ``search_for(needle, quads=…)``: rectangles, or quads as 4 points."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            hits = page.search_for(needle, quads=quads)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Recherche", exc) from exc
        return {"result": encode(list(hits))}


def textbox(
    documents: Documents, path: str | None, handle: str | None, index: int,
    rect: tuple[float, float, float, float],
) -> dict[str, Any]:
    """MuPDF's ``get_textbox(rect)``."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            return {"result": page.get_textbox(pymupdf.Rect(*rect))}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture du texte", exc) from exc


def fonts(
    documents: Documents, path: str | None, handle: str | None, index: int
) -> dict[str, Any]:
    """MuPDF's ``get_fonts(full=True)`` for one page."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        return {"result": encode(page.get_fonts(full=True))}


def extract_font(
    documents: Documents, path: str | None, handle: str | None, xref: int
) -> dict[str, Any]:
    """MuPDF's ``extract_font(xref)``: name, extension, type and the font file."""
    with documents.named(path, handle) as document:
        try:
            return {"result": encode(document.extract_font(xref))}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Extraction de fonte", exc) from exc
