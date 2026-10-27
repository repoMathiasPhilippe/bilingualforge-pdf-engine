# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rendering: one page, or a rectangle of it, drawn as pixels by MuPDF."""

from __future__ import annotations

import mmap
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdf_engine.documents import Documents
from pdf_engine.protocol import OrderError, RenderOrder


@dataclass(frozen=True)
class Rendered:
    """What a render gives back: the raster's shape, and its bytes or file."""

    width: int
    height: int
    n: int
    x: int = 0
    y: int = 0
    body: bytes = b""
    path: str | None = None


def _share(target: Path, samples: memoryview) -> None:
    """Write ``samples`` at the start of ``target``, growing it if too short.

    The file is never shrunk: a client reuses it from order to order, and
    reads exactly the length the answer gives.
    """
    size = len(samples)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "r+b" if target.exists() else "w+b") as handle:
        if handle.seek(0, 2) < size:
            handle.truncate(size)
        if size:
            with mmap.mmap(handle.fileno(), size) as mapped:
                mapped[:size] = samples


def render(documents: Documents, order: RenderOrder) -> Rendered:
    """Execute ``order``.

    Args:
        documents: The open documents.
        order: A checked render order.

    Returns:
        The raster: its samples (rows top to bottom, ``n`` bytes per pixel,
        no padding), its PNG encoding, or the file written — for ``shared``,
        the samples' length is ``width * height * n``.

    Raises:
        OrderError: ``page_out_of_range``, or ``render_failed`` naming
            MuPDF's own message.
    """
    with documents.named(order.path, order.handle) as document:
        return _draw(document, order)


def _draw(document: pymupdf.Document, order: RenderOrder) -> Rendered:
    if order.page >= document.page_count:
        raise OrderError(
            "page_out_of_range",
            f"Page {order.page} hors du document ({document.page_count} pages).",
            422,
            {"page_count": document.page_count},
        )
    try:
        page = document[order.page]
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(*order.matrix) if order.matrix else pymupdf.Identity,
            dpi=order.dpi,
            colorspace=pymupdf.csGRAY if order.colorspace == "gray" else pymupdf.csRGB,
            clip=pymupdf.Rect(*order.clip) if order.clip else None,
            alpha=order.alpha,
            annots=order.annots,
        )
        shape = {"width": pixmap.width, "height": pixmap.height, "n": pixmap.n,
                 "x": pixmap.x, "y": pixmap.y}
        if order.output.kind == "samples":
            return Rendered(**shape, body=bytes(pixmap.samples))
        if order.output.kind == "png":
            return Rendered(**shape, body=pixmap.tobytes("png"))
        if order.output.kind == "shared":
            _share(Path(str(order.output.path)), pixmap.samples_mv)
            return Rendered(**shape, path=str(order.output.path))
        target = Path(str(order.output.path))
        target.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(target))
        return Rendered(**shape, path=str(target))
    except OrderError:
        raise
    except Exception as exc:  # noqa: BLE001 - named for the client
        raise OrderError("render_failed", f"Rendu impossible : {exc}", 500) from exc
