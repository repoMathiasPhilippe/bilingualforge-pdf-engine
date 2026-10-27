# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a page draws besides its text: paths, images, links, annotations.

Each order returns MuPDF's own answer in typed JSON (:mod:`pdf_engine.typed`):
a rectangle, a point or a quad is the tuple of its numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from pdf_engine.documents import Documents
from pdf_engine.protocol import OrderError
from pdf_engine.reading import _failed, _page
from pdf_engine.typed import encode


def drawings(
    documents: Documents, path: str | None, handle: str | None, index: int
) -> dict[str, Any]:
    """MuPDF's ``get_drawings()``: every path, in drawing order."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            return {"result": encode(page.get_drawings())}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture des tracés", exc) from exc


def images(
    documents: Documents, path: str | None, handle: str | None, index: int, full: bool
) -> dict[str, Any]:
    """MuPDF's ``get_images(full=…)``: the images the page's resources declare."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            return {"result": encode(page.get_images(full=full))}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture des images", exc) from exc


def image_rects(
    documents: Documents, path: str | None, handle: str | None, index: int, xref: int
) -> dict[str, Any]:
    """MuPDF's ``get_image_rects(xref)``: where the page places one image."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            return {"result": encode(page.get_image_rects(xref))}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Placement d'image", exc) from exc


def image_info(
    documents: Documents, path: str | None, handle: str | None, index: int, xrefs: bool
) -> dict[str, Any]:
    """MuPDF's ``get_image_info(xrefs=…)``: every image the page draws."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            return {"result": encode(page.get_image_info(xrefs=xrefs))}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture des images", exc) from exc


def extract_image(
    documents: Documents, path: str | None, handle: str | None, xref: int, content: bool
) -> dict[str, Any]:
    """MuPDF's ``extract_image(xref)``; without ``content``, its ``image`` bytes left out."""
    with documents.named(path, handle) as document:
        try:
            result = document.extract_image(xref)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Extraction d'image", exc) from exc
        if result and not content:
            result = {key: value for key, value in result.items() if key != "image"}
        return {"result": encode(result)}


def xref_key(
    documents: Documents, path: str | None, handle: str | None, xref: int, key: str
) -> dict[str, Any]:
    """MuPDF's ``xref_get_key(xref, key)``: the value's type and its source text."""
    with documents.named(path, handle) as document:
        try:
            return {"result": encode(document.xref_get_key(xref, key))}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture d'objet", exc) from exc


def links(
    documents: Documents, path: str | None, handle: str | None, index: int
) -> dict[str, Any]:
    """MuPDF's ``get_links()``: each link as its dictionary."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            return {"result": encode(page.get_links())}
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture des liens", exc) from exc


def annots(
    documents: Documents, path: str | None, handle: str | None, index: int,
    types: tuple[int, ...] | None,
) -> dict[str, Any]:
    """The page's annotations, of ``types`` when given: type, rectangle,
    vertices, colours and opacity, as the annotation objects report them."""
    with documents.named(path, handle) as document:
        page = _page(document, index)
        try:
            found = [
                {
                    "type": annot.type,
                    "rect": annot.rect,
                    "vertices": annot.vertices,
                    "colors": annot.colors,
                    "opacity": annot.opacity,
                    "xref": annot.xref,
                }
                for annot in page.annots(types=types)
            ]
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Lecture des annotations", exc) from exc
        return {"result": encode(found)}


def picture(path: str) -> dict[str, Any]:
    """A picture file's size and resolution, as MuPDF decodes it.

    Raises:
        OrderError: ``not_found``, or ``unreadable`` for a file MuPDF cannot
            decode as a picture.
    """
    try:
        data = Path(path).read_bytes()
    except PermissionError as exc:
        raise OrderError("forbidden", f"Accès refusé : {path}", 403) from exc
    except OSError as exc:
        raise OrderError("not_found", f"Fichier introuvable : {path}", 404) from exc
    try:
        pixmap = pymupdf.Pixmap(data)
    except Exception as exc:  # noqa: BLE001 - named for the client
        raise OrderError("unreadable", f"Image illisible : {exc}", 422) from exc
    return {
        "width": pixmap.width,
        "height": pixmap.height,
        "n": pixmap.n,
        "xres": pixmap.xres,
        "yres": pixmap.yres,
    }
