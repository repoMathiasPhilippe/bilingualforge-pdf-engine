# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing onto a held document: text, rectangles, images, fonts, links, marks.

Each order calls ONE PyMuPDF method with the options the client named, and
only those: an option left out keeps MuPDF's default, an option sent as
``null`` is passed as ``None`` — for ``draw_rect`` the two differ (the
default colour is black, ``None`` is no stroke). The client decides what to
write and where; the engine writes it.
"""

from __future__ import annotations

import base64
from typing import Any

import pymupdf

from pdf_engine.documents import Documents
from pdf_engine.editing import _GRAPHICS, _IMAGES, _TEXT
from pdf_engine.protocol import EncodeOrder, OrderError, StoryOrder, WriteOrder
from pdf_engine.typed import encode

_COLOURS = frozenset({"color", "fill", "text_color"})


def _page(document: pymupdf.Document, index: int) -> pymupdf.Page:
    if index >= document.page_count:
        raise OrderError(
            "page_out_of_range",
            f"Page {index} hors du document ({document.page_count} pages).",
            422,
            {"page_count": document.page_count},
        )
    return document[index]


def _options(order: WriteOrder) -> dict[str, Any]:
    """The client's options, in PyMuPDF's types."""
    options: dict[str, Any] = {}
    for key, value in order.options.items():
        if key in _COLOURS:
            options[key] = None if value is None else tuple(float(v) for v in value)
        elif key == "clip":
            options[key] = None if value is None else pymupdf.Rect(*value)
        elif key == "morph":
            options[key] = (
                None
                if value is None
                else (pymupdf.Point(*value[0]), pymupdf.Matrix(*value[1]))
            )
        else:
            options[key] = value
    return options


def _draw_paths(page: pymupdf.Page, paths: list[dict[str, Any]]) -> None:
    """Every path on ONE shape, each finished with its own options, committed once."""
    shape = page.new_shape()
    for path in paths:
        for item in path["items"]:
            kind = item[0]
            if kind == "l":
                shape.draw_line(pymupdf.Point(*item[1]), pymupdf.Point(*item[2]))
            elif kind == "c":
                shape.draw_bezier(*(pymupdf.Point(*corner) for corner in item[1:]))
            elif kind == "re":
                shape.draw_rect(pymupdf.Rect(*item[1]))
            else:
                shape.draw_quad(pymupdf.Quad(*(pymupdf.Point(*corner) for corner in item[1:])))
        finish = dict(path["finish"])
        for key in ("fill", "color"):
            if finish.get(key) is not None:
                finish[key] = tuple(float(v) for v in finish[key])
        shape.finish(**finish)
    shape.commit()


def story(order: StoryOrder) -> dict[str, Any]:
    """``order.html`` flowed over sheets of ``order.paper``, inside ``order.frame``.

    Raises:
        OrderError: ``edit_failed`` when MuPDF cannot lay out or write it.
    """
    try:
        flowed = pymupdf.Story(html=order.html)
        sheet = pymupdf.Rect(*order.paper)
        frame = pymupdf.Rect(*order.frame)
        writer = pymupdf.DocumentWriter(order.path)
        pages = 0
        more = True
        while more:
            device = writer.begin_page(sheet)
            more, _filled = flowed.place(frame)
            flowed.draw(device)
            writer.end_page()
            pages += 1
        writer.close()
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 - named for the client
        raise _failed("story", exc) from exc
    return {"path": order.path, "page_count": pages}


def _failed(what: str, exc: Exception) -> OrderError:
    return OrderError("edit_failed", f"{what} impossible : {exc}", 422)


def write(documents: Documents, order: WriteOrder) -> dict[str, Any]:
    """Carry out one page order (``order.kind``) and return PyMuPDF's answer."""
    options = _options(order)
    with documents.named(None, order.handle) as document:
        page = _page(document, order.page)
        try:
            return {"result": _call(documents, page, order, options)}
        except OrderError:
            raise
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed(order.kind, exc) from exc


def _call(
    documents: Documents, page: pymupdf.Page, order: WriteOrder, options: dict[str, Any]
) -> Any:
    args = order.args
    if order.kind == "insert_textbox":
        return float(page.insert_textbox(pymupdf.Rect(*args["rect"]), args["text"], **options))
    if order.kind == "insert_text":
        return int(page.insert_text(pymupdf.Point(*args["point"]), args["text"], **options))
    if order.kind == "draw_rect":
        page.draw_rect(pymupdf.Rect(*args["rect"]), **options)
        return None
    if order.kind == "draw_line":
        page.draw_line(pymupdf.Point(*args["p1"]), pymupdf.Point(*args["p2"]), **options)
        return None
    if order.kind == "draw_circle":
        page.draw_circle(pymupdf.Point(*args["center"]), args["radius"], **options)
        return None
    if order.kind == "draw_paths":
        _draw_paths(page, args["paths"])
        return None
    if order.kind == "insert_image":
        stream = base64.b64decode(args["stream"])
        return int(page.insert_image(pymupdf.Rect(*args["rect"]), stream=stream, **options))
    if order.kind == "insert_htmlbox":
        archive = None
        if args.get("archive") is not None:
            archive = pymupdf.Archive()
            for path, name in args["archive"]:
                archive.add(path, name)
        spare, scale = page.insert_htmlbox(
            pymupdf.Rect(*args["rect"]), args["text"], archive=archive, **options
        )
        return [float(spare), float(scale)]
    if order.kind == "add_redact_annot":
        page.add_redact_annot(pymupdf.Rect(*args["rect"]), **options)
        return None
    if order.kind == "insert_font":
        if args.get("fontfile") is not None:
            return int(page.insert_font(fontname=args["fontname"], fontfile=args["fontfile"],
                                        **options))
        buffer = base64.b64decode(args["fontbuffer"])
        return int(page.insert_font(fontname=args["fontname"], fontbuffer=buffer, **options))
    if order.kind == "insert_link":
        link = dict(args["link"])
        if link.get("from") is not None:
            link["from"] = pymupdf.Rect(*link["from"])
        if link.get("to") is not None:
            link["to"] = pymupdf.Point(*link["to"])
        page.insert_link(link)
        return None
    if order.kind == "add_markup_annot":
        quads = [pymupdf.Quad(*(pymupdf.Point(*corner) for corner in quad))
                 for quad in args["quads"]]
        annot = getattr(page, args["maker"])(quads)
        if args["has_stroke"]:
            stroke = args["stroke"]
            annot.set_colors(stroke=None if stroke is None else tuple(float(v) for v in stroke))
        if args.get("opacity") is not None:
            annot.set_opacity(float(args["opacity"]))
        annot.update()
        return int(annot.xref)
    if order.kind == "show_pdf_page":
        with documents.named(args["source_path"], args["source_handle"]) as source:
            xref = page.show_pdf_page(
                pymupdf.Rect(*args["rect"]), source, args["source_page"], **options
            )
        return int(xref)
    raise OrderError("unknown_order", f"Ordre d'écriture inconnu : {order.kind}", 404)


def new_page(documents: Documents, handle: str, width: float, height: float) -> dict[str, Any]:
    """Append a blank page of ``width`` × ``height`` points."""
    with documents.named(None, handle) as document:
        try:
            page = document.new_page(width=width, height=height)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Nouvelle page", exc) from exc
        return {"page": page.number, "page_count": document.page_count}


def apply_redactions(
    documents: Documents, handle: str, index: int, options: dict[str, str]
) -> dict[str, Any]:
    """Apply the page's pending redaction annotations, with MuPDF's options
    named as ``redact`` names them."""
    constants = {
        key: int(getattr(pymupdf, names[choice]))
        for key, choice, names in (
            (key, choice, {"images": _IMAGES, "graphics": _GRAPHICS, "text": _TEXT}[key])
            for key, choice in options.items()
        )
    }
    with documents.named(None, handle) as document:
        page = _page(document, index)
        try:
            return {"result": bool(page.apply_redactions(**constants))}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Caviardage", exc) from exc


def set_metadata(documents: Documents, handle: str, metadata: dict[str, str]) -> dict[str, Any]:
    """Replace the document's information dictionary."""
    with documents.named(None, handle) as document:
        try:
            document.set_metadata(metadata)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Métadonnées", exc) from exc
        return {"metadata": True}


def del_xml_metadata(documents: Documents, handle: str) -> dict[str, Any]:
    """Drop the document's XMP metadata."""
    with documents.named(None, handle) as document:
        try:
            document.del_xml_metadata()
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Métadonnées", exc) from exc
        return {"xml_metadata": False}


def encode_image(order: EncodeOrder) -> dict[str, Any]:
    """Samples encoded by MuPDF (``Pixmap.tobytes``): PNG, JPEG..."""
    colorspace = {1: pymupdf.csGRAY, 3: pymupdf.csRGB}.get(order.n)
    if colorspace is None:
        raise OrderError("bad_order", "« n » attend 1 (gris) ou 3 (RVB).")
    try:
        pixmap = pymupdf.Pixmap(colorspace, order.width, order.height, order.samples, False)
        return {"result": encode(bytes(pixmap.tobytes(order.output, jpg_quality=order.quality)))}
    except Exception as exc:  # noqa: BLE001 - named for the client
        raise OrderError("edit_failed", f"Encodage impossible : {exc}", 422) from exc
