"""Drawings, images, links and annotations through the engine (ADR-392).

Each answer is compared with ``==`` against the in-process call, the
library's objects written as the tuples of their numbers — the protocol
speaks numbers (``PROTOCOL.md``).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image

from pdf_engine import objects
from pdf_engine.documents import Documents
from pdf_engine.protocol import (
    OrderError,
    annots_order,
    extract_image_order,
    image_rects_order,
    images_order,
    picture_order,
    xref_key_order,
)
from pdf_engine.typed import decode


def numbers(value: Any) -> Any:
    """``value`` with every Rect, Point and Quad written as its numbers."""
    if isinstance(value, pymupdf.Quad):
        return tuple(numbers(corner) for corner in value)
    if isinstance(value, pymupdf.Rect | pymupdf.Point):
        return tuple(float(item) for item in value)
    if isinstance(value, dict):
        return {key: numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [numbers(item) for item in value]
    if isinstance(value, tuple):
        return tuple(numbers(item) for item in value)
    return value


def objects_pdf(path: Path) -> Path:
    """Every kind of object step 4 reads: paths of each item type, one image
    placed twice, a JPEG, a stencil, a link and a highlight."""
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 100), "Le chiffre d'affaires progresse", fontsize=14)
        page.draw_rect(pymupdf.Rect(60, 200, 300, 320), color=(0, 0, 1), fill=(1, 0.8, 0))
        page.draw_line((50, 400), (540, 470), color=(1, 0, 0), width=3)
        page.draw_bezier((60, 500), (90, 450), (150, 550), (200, 500), color=(0, 0.5, 0))
        page.draw_quad(pymupdf.Rect(400, 200, 500, 260).quad, fill=(0.2, 0.2, 0.2))
        tile = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 12, 9), False)
        tile.set_rect(tile.irect, (20, 120, 200))
        xref = page.insert_image(pymupdf.Rect(350, 500, 400, 540), pixmap=tile)
        page.insert_image(pymupdf.Rect(420, 500, 470, 540), xref=xref)
        photo = BytesIO()
        Image.new("RGB", (64, 48), (200, 40, 40)).save(photo, format="JPEG", dpi=(150, 150))
        page.insert_image(pymupdf.Rect(60, 600, 188, 696), stream=photo.getvalue())
        stencil = BytesIO()
        Image.new("1", (16, 16), 0).save(stencil, format="PNG")
        page.insert_image(pymupdf.Rect(300, 600, 340, 640), stream=stencil.getvalue())
        page.insert_link(
            {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 85, 200, 105), "uri": "https://example.org"}
        )
        quad = page.search_for("chiffre", quads=True)[0]
        page.add_highlight_annot(quad).update()
        page.add_text_annot((500, 60), "note")
        mask = page.get_images(full=True)[-1][0]
        document.xref_set_key(mask, "ImageMask", "true")
        document.save(path)
    return path


def test_drawings_are_mupdf_s_own(tmp_path: Path) -> None:
    pdf = objects_pdf(tmp_path / "objects.pdf")

    answer = decode(objects.drawings(Documents(), str(pdf), None, 0)["result"])

    with pymupdf.open(pdf) as document:
        expected = document[0].get_drawings()
    assert answer == numbers(expected)
    assert {item[0] for path in answer for item in path["items"]} == {"l", "c", "re", "qu"}


def test_images_their_placements_and_objects_are_mupdf_s_own(tmp_path: Path) -> None:
    pdf = objects_pdf(tmp_path / "objects.pdf")
    documents = Documents()
    path = str(pdf)

    with pymupdf.open(pdf) as document:
        page = document[0]
        for full in (False, True):
            assert decode(objects.images(documents, path, None, 0, full)["result"]) == (
                page.get_images(full=full)
            )
        for image in page.get_images(full=True):
            xref = image[0]
            assert decode(objects.image_rects(documents, path, None, 0, xref)["result"]) == (
                numbers(page.get_image_rects(xref))
            )
            whole = document.extract_image(xref)
            assert decode(objects.extract_image(documents, path, None, xref, True)["result"]) == whole
            assert decode(objects.extract_image(documents, path, None, xref, False)["result"]) == {
                key: value for key, value in whole.items() if key != "image"
            }
            assert decode(objects.xref_key(documents, path, None, xref, "ImageMask")["result"]) == (
                document.xref_get_key(xref, "ImageMask")
            )
        # get_image_info keeps its answer on the PAGE OBJECT: each call here
        # gets a page of its own, as each order gets one in the engine.
        for xrefs in (False, True):
            assert decode(objects.image_info(documents, path, None, 0, xrefs)["result"]) == (
                document[0].get_image_info(xrefs=xrefs)
            )
    tile = decode(objects.images(documents, path, None, 0, False)["result"])[0][0]
    placed = decode(objects.image_rects(documents, path, None, 0, tile)["result"])
    assert len(placed) == 2, "the tile is placed twice"


def test_links_and_annotations_are_mupdf_s_own(tmp_path: Path) -> None:
    pdf = objects_pdf(tmp_path / "objects.pdf")
    documents = Documents()

    with pymupdf.open(pdf) as document:
        page = document[0]
        assert decode(objects.links(documents, str(pdf), None, 0)["result"]) == numbers(
            page.get_links()
        )
        for types in (None, (pymupdf.PDF_ANNOT_HIGHLIGHT,)):
            answer = decode(objects.annots(documents, str(pdf), None, 0, types)["result"])
            expected = [
                {
                    "type": annot.type,
                    "rect": numbers(annot.rect),
                    "vertices": annot.vertices,
                    "colors": annot.colors,
                    "opacity": annot.opacity,
                    "xref": annot.xref,
                }
                for annot in page.annots(types=types)
            ]
            assert answer == expected
            assert len(answer) == (2 if types is None else 1)


def test_a_picture_is_measured_as_mupdf_decodes_it(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (64, 48), (10, 200, 30)).save(photo, format="JPEG", dpi=(150, 150))

    answer = objects.picture(str(photo))

    pixmap = pymupdf.Pixmap(str(photo))
    assert answer == {
        "width": pixmap.width,
        "height": pixmap.height,
        "n": pixmap.n,
        "xres": pixmap.xres,
        "yres": pixmap.yres,
    }
    assert (answer["width"], answer["xres"]) == (64, 150)


def test_a_missing_or_unreadable_picture_is_named(tmp_path: Path) -> None:
    with pytest.raises(OrderError) as missing:
        objects.picture(str(tmp_path / "absent.png"))
    assert missing.value.code == "not_found"

    garbage = tmp_path / "garbage.png"
    garbage.write_bytes(b"not a picture at all")
    with pytest.raises(OrderError) as unreadable:
        objects.picture(str(garbage))
    assert unreadable.value.code == "unreadable"


@pytest.mark.parametrize(
    ("parse", "payload"),
    [
        (images_order, {"document": {"handle": "h"}, "page": 0, "full": "yes"}),
        (image_rects_order, {"document": {"handle": "h"}, "page": 0, "xref": 0}),
        (image_rects_order, {"document": {"handle": "h"}, "page": 0}),
        (extract_image_order, {"document": {"handle": "h"}, "xref": 4, "content": 1}),
        (xref_key_order, {"document": {"handle": "h"}, "xref": 4, "key": ""}),
        (annots_order, {"document": {"handle": "h"}, "page": 0, "types": ["Highlight"]}),
        (annots_order, {"document": {"handle": "h"}, "page": 0, "types": 8}),
        (picture_order, {"path": "relative.png"}),
        (picture_order, {}),
    ],
)
def test_malformed_object_orders_are_refused(parse: Any, payload: dict[str, Any]) -> None:
    with pytest.raises(OrderError) as refused:
        parse(payload)
    assert refused.value.code == "bad_order"


@pytest.mark.parametrize(
    ("read", "decodes"),
    [
        (lambda d, h, x: objects.image_rects(d, None, h, 0, x), True),
        (lambda d, h, x: objects.image_info(d, None, h, 0, True), True),
        (lambda d, h, x: objects.extract_image(d, None, h, x, False), True),
        (lambda d, h, x: objects.image_info(d, None, h, 0, False), False),
        (lambda d, h, x: objects.images(d, None, h, 0, True), False),
        (lambda d, h, x: objects.drawings(d, None, h, 0), False),
    ],
)
def test_which_reads_decode_the_image_and_change_the_next_render(
    tmp_path: Path, read: Any, decodes: bool
) -> None:
    """Why the closed code reads placements on a document of its own (ADR-392).

    Finding where an image is placed, or which xref a placement draws, makes
    MuPDF DECODE it (it hashes the pixels), and so does extracting it; the
    decode stays in the open document, and the strip rendered next starts
    from it — another strip than a fresh copy's. Listing images, reading
    plain image info or drawings decodes nothing. Measured, not assumed: the
    day MuPDF changes, this bench says which way.
    """
    from pdf_engine.protocol import render_order
    from pdf_engine.render import render
    from tests.test_render import NATIVE, STRIP, scan_pdf

    pdf = scan_pdf(tmp_path / "scan.pdf")
    strip = {"matrix": [0.75 * NATIVE, 0, 0, 0.75 * NATIVE, 0, 0], "clip": STRIP}
    documents = Documents()
    fresh = render(documents, render_order({"document": {"path": str(pdf)}, "page": 0, **strip}))
    handle, document = documents.open(str(pdf))
    xref = document[0].get_images()[0][0]

    read(documents, handle, xref)
    after = render(documents, render_order({"document": {"handle": handle}, "page": 0, **strip}))

    assert (after.body != fresh.body) is decodes
