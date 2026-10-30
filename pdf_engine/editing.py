# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Documents as a whole: describe, assemble, crop, turn, redact, save.

Every operation does exactly what it names: the client decides which pages,
which box and which turn; the engine only carries it out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from pdf_engine.documents import Documents
from pdf_engine.protocol import (
    InsertOrder,
    OrderError,
    ReadingCopyOrder,
    RedactOrder,
    SaveOrder,
)


def _page(document: pymupdf.Document, index: int) -> pymupdf.Page:
    if index >= document.page_count:
        raise OrderError(
            "page_out_of_range",
            f"Page {index} hors du document ({document.page_count} pages).",
            422,
            {"page_count": document.page_count},
        )
    return document[index]


def _box(rect: pymupdf.Rect) -> list[float]:
    return [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]


def _failed(what: str, exc: Exception) -> OrderError:
    return OrderError("edit_failed", f"{what} impossible : {exc}", 422)


def describe(documents: Documents, path: str | None, handle: str | None) -> dict[str, Any]:
    """The document's page count and, per page, its boxes and rotation.

    ``rect`` is the page as displayed — crop box, turned by the rotation —
    in the coordinates every other order uses.
    """
    with documents.named(path, handle) as document:
        pages = []
        for index in range(document.page_count):
            page = document[index]
            pages.append(
                {
                    "rect": _box(page.rect),
                    "mediabox": _box(page.mediabox),
                    "cropbox": _box(page.cropbox),
                    "rotation": int(page.rotation),
                }
            )
        return {"page_count": document.page_count, "pages": pages}


def insert_pages(documents: Documents, order: InsertOrder) -> dict[str, Any]:
    """Append pages ``from_page``..``to_page`` of a source document to a held one."""
    with documents.named(None, order.into) as target, documents.named(
        order.source_path, order.source_handle
    ) as source:
        _page(source, order.to_page)
        try:
            target.insert_pdf(source, from_page=order.from_page, to_page=order.to_page)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Insertion de pages", exc) from exc
        return {"page_count": target.page_count}


def set_cropbox(
    documents: Documents, handle: str, index: int, rect: tuple[float, float, float, float]
) -> dict[str, Any]:
    """Set a page's crop box (in unrotated page coordinates, as PDF stores it)."""
    with documents.named(None, handle) as document:
        page = _page(document, index)
        try:
            page.set_cropbox(pymupdf.Rect(*rect))
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Recadrage", exc) from exc
        return {"rect": _box(page.rect)}


def set_rotation(documents: Documents, handle: str, index: int, rotation: int) -> dict[str, Any]:
    """Set a page's rotation (``/Rotate``): a declaration, the content is untouched."""
    with documents.named(None, handle) as document:
        page = _page(document, index)
        page.set_rotation(rotation)
        return {"rotation": int(page.rotation)}


def save(documents: Documents, order: SaveOrder) -> dict[str, Any]:
    """Write a held document to a file."""
    with documents.named(None, order.handle) as document:
        target = Path(order.path)
        if order.incremental:
            if not document.name or Path(document.name).resolve() != target.resolve():
                raise OrderError(
                    "bad_order",
                    "Un enregistrement incrémental s'écrit dans le fichier ouvert sur place.",
                )
            try:
                document.saveIncr()
            except Exception as exc:  # noqa: BLE001 - named for the client
                raise _failed("Enregistrement incrémental", exc) from exc
            return {"path": str(target), "page_count": document.page_count}
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            document.save(str(target), garbage=order.garbage, deflate=order.deflate)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Enregistrement", exc) from exc
        return {"path": str(target), "page_count": document.page_count}


#: The filters that only transform bytes, by their full and abbreviated names:
#: an image behind them alone decodes the same way at every size.
_BYTE_FILTERS = frozenset({
    "FlateDecode", "Fl", "LZWDecode", "LZW", "RunLengthDecode", "RL",
    "ASCIIHexDecode", "AHx", "ASCII85Decode", "A85",
})


def _byte_filtered(document: pymupdf.Document, xref: int) -> bool:
    """Whether the image ``xref`` is filtered, and by byte transforms only."""
    kind, value = document.xref_get_key(xref, "Filter")
    if kind == "name":
        names = [value.lstrip("/")]
    elif kind == "array":
        names = [name.strip() for name in value.strip("[] ").split("/") if name.strip()]
    else:
        return False
    return bool(names) and all(name in _BYTE_FILTERS for name in names)


def reading_copy(documents: Documents, order: ReadingCopyOrder) -> dict[str, Any]:
    """Write one page alone, its byte-filtered images stored unfiltered.

    A scan's page image is the cost of every reading that renders it: a
    400 dpi page stored as Flate with a PNG predictor is undone in full —
    about 0.4 s — by each render of a document opened afresh, whatever the
    size of the clip. Stored unfiltered, the same samples render in a
    fifteenth of that. The copy holds the page alone, because every order
    reads its whole file. Nothing else changes: the page object, its content
    and its resources are the source's own (``select``).
    """
    with documents.named(order.source, None) as document:
        _page(document, order.page)
        try:
            document.select([order.page])
            stored = 0
            for item in document[0].get_images(full=True):
                for xref in {item[0], item[1]} - {0}:
                    if not _byte_filtered(document, xref):
                        continue
                    # Uncompressed, ``update_stream`` drops the filter and
                    # its parameters with it.
                    document.update_stream(xref, document.xref_stream(xref), compress=0)
                    stored += 1
            target = Path(order.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            document.save(str(target), garbage=1)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Copie de lecture", exc) from exc
    return {"path": str(target), "page_count": 1, "unfiltered": stored}


#: MuPDF's constant for each redaction option the protocol names.
_IMAGES = {
    "none": "PDF_REDACT_IMAGE_NONE",
    "remove": "PDF_REDACT_IMAGE_REMOVE",
    "pixels": "PDF_REDACT_IMAGE_PIXELS",
    "remove_unless_invisible": "PDF_REDACT_IMAGE_REMOVE_UNLESS_INVISIBLE",
}
_GRAPHICS = {
    "none": "PDF_REDACT_LINE_ART_NONE",
    "remove_if_covered": "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED",
    "remove_if_touched": "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED",
}
_TEXT = {"remove": "PDF_REDACT_TEXT_REMOVE", "none": "PDF_REDACT_TEXT_NONE"}


def redact(documents: Documents, order: RedactOrder) -> dict[str, Any]:
    """Mark each rectangle for redaction, then apply them all at once."""
    options: dict[str, int] = {}
    for keyword, choice, names in (
        ("images", order.images, _IMAGES),
        ("graphics", order.graphics, _GRAPHICS),
        ("text", order.text, _TEXT),
    ):
        if choice is not None:
            options[keyword] = int(getattr(pymupdf, names[choice]))
    with documents.named(None, order.handle) as document:
        page = _page(document, order.page)
        try:
            for rect in order.rects:
                page.add_redact_annot(pymupdf.Rect(*rect))
            page.apply_redactions(**options)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Caviardage", exc) from exc
        return {"redacted": len(order.rects)}


def subset_fonts(documents: Documents, handle: str) -> dict[str, Any]:
    """Keep, of every embedded font, only the glyphs the document uses."""
    with documents.named(None, handle) as document:
        try:
            document.subset_fonts()
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Réduction des fontes", exc) from exc
        return {"subset": True}


def delete_page(documents: Documents, handle: str, index: int) -> dict[str, Any]:
    """Remove page ``index`` of a held document."""
    with documents.named(None, handle) as document:
        _page(document, index)
        try:
            document.delete_page(index)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Suppression de page", exc) from exc
        return {"page_count": document.page_count}


def set_page_labels(
    documents: Documents, handle: str, labels: list[dict[str, Any]]
) -> dict[str, Any]:
    """Set the document's page labels (``Document.set_page_labels``)."""
    with documents.named(None, handle) as document:
        try:
            document.set_page_labels(labels)
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise _failed("Étiquettes de pages", exc) from exc
        return {"labels": len(labels)}
