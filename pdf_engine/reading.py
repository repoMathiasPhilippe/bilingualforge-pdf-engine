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
            if order.image_bytes:
                result = page.get_text(order.kind, **options)
            else:
                result = _dict_without_image_bytes(
                    page, order.kind == "rawdict", options.get("clip"), bool(order.sort)
                )
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture du texte", exc) from exc
        return {"result": encode(result)}


#: What an image block keeps when its bytes are not produced (1.11).
_IMAGE_KEPT = ("width", "height", "colorspace", "xres", "yres", "bpc")


def _dict_without_image_bytes(
    page: pymupdf.Page, raw: bool, clip: pymupdf.Rect | None, sort: bool
) -> dict[str, Any]:
    """``get_text("dict"|"rawdict")``, image blocks without their bytes.

    MuPDF builds the whole answer in one call and encodes every image it
    meets — as PNG when the image is not stored in a format it can pass
    through, which is a scan's case: a second and ten megabytes a reading.
    The image block cannot simply be left out (``TEXT_PRESERVE_IMAGES``):
    an image also SEPARATES text blocks, and without it two blocks become
    one. So the page's structured text is walked here, block by block, as
    PyMuPDF's own ``get_text`` walks it: every line's spans are built by the
    very function MuPDF's dict uses (``extra.JM_make_spanlist``), and an
    image block keeps its place, number, box and geometry — only ``image``,
    ``mask``, ``ext`` and ``size`` are not produced. A text block lacks its
    block-level ``flags``, which MuPDF fills in C++ and no binding reaches;
    its spans keep theirs. Proved equal to ``get_text`` otherwise, block for
    block (``tests/test_reading.py``).
    """
    from pymupdf import extra, mupdf

    flags = pymupdf.TEXTFLAGS_RAWDICT if raw else pymupdf.TEXTFLAGS_DICT
    frame = page.cropbox if clip is None else None
    textpage = page.get_textpage(clip=clip, flags=flags)
    stext = textpage.this
    bounds = mupdf.FzRect(stext.m_internal.mediabox)
    buffer = mupdf.fz_new_buffer(128)
    blocks: list[dict[str, Any]] = []
    for number, block in enumerate(stext):
        # Every block the structured text holds, as MuPDF's dict gives it: the
        # clip already acted when the page was read — an image outside it
        # comes back with its box cut down to nothing, and is still a block.
        box = mupdf.FzRect(block.m_internal.bbox)
        image = block.m_internal.type == mupdf.FZ_STEXT_BLOCK_IMAGE
        entry: dict[str, Any] = {"number": number, "type": block.m_internal.type}
        if image:
            picture = block.i_image()
            entry["bbox"] = (box.x0, box.y0, box.x1, box.y1)
            entry.update(zip(_IMAGE_KEPT, (
                picture.w(), picture.h(), picture.n(),
                picture.xres(), picture.yres(), picture.bpc(),
            ), strict=True))
            matrix = block.i_transform()
            entry["transform"] = (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)
        else:
            lines: list[dict[str, Any]] = []
            extent = mupdf.FzRect(mupdf.FzRect.Fixed_EMPTY)
            for line in block:
                if mupdf.fz_is_empty_rect(
                    mupdf.fz_intersect_rect(bounds, mupdf.FzRect(line.m_internal.bbox))
                ) and not mupdf.fz_is_infinite_rect(bounds):
                    continue
                spans: dict[str, Any] = {}
                reach = extra.JM_make_spanlist(spans, line, raw, buffer, bounds)
                extent = mupdf.fz_union_rect(extent, reach)
                spans["wmode"] = line.m_internal.wmode
                spans["dir"] = (line.m_internal.dir.x, line.m_internal.dir.y)
                spans["bbox"] = (reach.x0, reach.y0, reach.x1, reach.y1)
                lines.append(spans)
            entry["bbox"] = (extent.x0, extent.y0, extent.x1, extent.y1)
            entry["lines"] = lines
        blocks.append(entry)
    width, height = (frame.width, frame.height) if frame is not None else (
        textpage.rect.width, textpage.rect.height
    )
    if sort:
        blocks.sort(key=lambda entry: (entry["bbox"][3], entry["bbox"][0]))
    return {"width": width, "height": height, "blocks": blocks}


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
