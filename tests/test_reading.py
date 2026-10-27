"""Reading through the engine: MuPDF's own answers, with their own types (ADR-391).

The closed code compares coordinates to tuples, keys dictionaries by them and
reads embedded fonts as bytes. JSON has neither tuples nor bytes; typed JSON
carries both, and these tests compare with ``==`` — under which a list is not
a tuple — against the in-process call.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_engine import reading
from pdf_engine.documents import Documents
from pdf_engine.protocol import OrderError, font_order, search_order, text_order
from pdf_engine.typed import decode, encode
from tests.test_render import rich_pdf


def test_typed_json_keeps_tuples_bytes_and_numbers() -> None:
    import json

    value: Any = {
        "bbox": (1.5, 2.25, 3.0, 4.125),
        "words": [(0.1, 0.2, "mot", 3, (4, 5))],
        "image": b"\x00\xff binary",
        "flags": 20,
        "nan": float("nan"),
        "none": None,
        "nested": [[1, 2], {"origin": (7.0, 8.0)}],
    }

    back = decode(json.loads(json.dumps(encode(value))))

    assert math.isnan(back.pop("nan"))
    value.pop("nan")
    assert back == value
    assert isinstance(back["bbox"], tuple)
    assert isinstance(back["words"][0][4], tuple)
    assert isinstance(back["nested"][0], list)


def test_a_library_object_is_written_as_its_numbers() -> None:
    assert encode(pymupdf.Rect(1, 2, 3, 4)) == {"__tuple__": [1.0, 2.0, 3.0, 4.0]}
    quad = encode(pymupdf.Rect(0, 0, 2, 1).quad)
    assert decode(quad) == ((0.0, 0.0), (2.0, 0.0), (0.0, 1.0), (2.0, 1.0))


@pytest.mark.parametrize("kind", ["text", "words", "blocks", "dict", "rawdict"])
@pytest.mark.parametrize("index", [0, 1])
def test_every_extraction_is_mupdf_s_own(tmp_path: Path, kind: str, index: int) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    documents = Documents()
    clip = (40.0, 60.0, 400.0, 380.0)

    whole = reading.text(
        documents, text_order({"document": {"path": str(pdf)}, "page": index, "kind": kind})
    )
    clipped = reading.text(documents, text_order(
        {"document": {"path": str(pdf)}, "page": index, "kind": kind, "clip": list(clip)}))

    with pymupdf.open(pdf) as document:
        page = document[index]
        assert decode(whole["result"]) == page.get_text(kind)
        assert decode(clipped["result"]) == page.get_text(kind, clip=pymupdf.Rect(*clip))


def test_trace_fonts_search_and_textbox_are_mupdf_s_own(tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    documents = Documents()
    path = str(pdf)

    with pymupdf.open(pdf) as document:
        page = document[0]
        assert decode(reading.texttrace(documents, path, None, 0)["result"]) == (
            page.get_texttrace()
        )
        fonts = page.get_fonts(full=True)
        assert decode(reading.fonts(documents, path, None, 0)["result"]) == fonts
        for xref in {font[0] for font in fonts}:
            assert decode(reading.extract_font(documents, path, None, xref)["result"]) == (
                document.extract_font(xref)
            )
        hits = decode(reading.search(documents, path, None, 0, "chiffre", False)["result"])
        assert hits == [tuple(rect) for rect in page.search_for("chiffre")]
        quads = decode(reading.search(documents, path, None, 0, "chiffre", True)["result"])
        assert quads == [
            tuple(tuple(corner) for corner in quad)
            for quad in page.search_for("chiffre", quads=True)
        ]
        rect = (60.0, 80.0, 300.0, 110.0)
        assert reading.textbox(documents, path, None, 0, rect)["result"] == (
            page.get_textbox(pymupdf.Rect(*rect))
        )


@pytest.mark.parametrize(
    ("parse", "payload"),
    [
        (text_order, {"document": {"handle": "h"}, "page": 0, "kind": "html"}),
        (text_order, {"document": {"handle": "h"}, "page": 0, "sort": "yes"}),
        (text_order, {"document": {"handle": "h"}, "page": 0, "clip": [1, 2]}),
        (search_order, {"document": {"handle": "h"}, "page": 0, "needle": ""}),
        (font_order, {"document": {"handle": "h"}, "xref": 0}),
        (font_order, {"document": {"handle": "h"}, "xref": True}),
    ],
)
def test_malformed_reading_orders_are_refused(parse: Any, payload: dict[str, Any]) -> None:
    with pytest.raises(OrderError) as refused:
        parse(payload)
    assert refused.value.code == "bad_order"


def test_a_page_beyond_the_document_is_named(tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")

    with pytest.raises(OrderError) as beyond:
        reading.text(Documents(), text_order({"document": {"path": str(pdf)}, "page": 5}))

    assert (beyond.value.code, beyond.value.detail) == ("page_out_of_range", {"page_count": 2})


def _image_between_text(path: Path) -> Path:
    """Text, a Flate image MuPDF must re-encode, text: the image splits two blocks."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "Première ligne avant l'image", fontsize=11)
    picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 90), False)
    picture.clear_with(180)
    page.insert_image(pymupdf.Rect(72, 105, 312, 285), pixmap=picture)
    page.insert_text((72, 112), "Seconde ligne après l'image", fontsize=11)
    page.insert_text((330, 200), "Une colonne à droite", fontsize=9)
    masked = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), True)
    masked.clear_with(90)
    page.insert_image(pymupdf.Rect(400, 400, 480, 480), pixmap=masked)
    page.insert_text((72, 500), "Pied de page", fontsize=8)
    rotated = document.new_page()
    rotated.insert_text((72, 100), "Page tournée", fontsize=12)
    rotated.insert_image(pymupdf.Rect(72, 120, 200, 220), pixmap=picture)
    rotated.set_rotation(90)
    document.save(path)
    return path


_BYTES = ("image", "mask", "ext", "size")


@pytest.mark.parametrize("kind", ["dict", "rawdict"])
@pytest.mark.parametrize("sort", [None, True])
@pytest.mark.parametrize(
    "clip", [None, (40.0, 60.0, 400.0, 380.0), (100.0, 150.0, 250.0, 250.0), (0, 0, 1, 1)]
)
@pytest.mark.parametrize("source", ["rich", "between"])
def test_a_reading_without_image_bytes_is_mupdf_s_own_less_the_bytes(
    tmp_path: Path,
    kind: str,
    sort: bool | None,
    clip: tuple[float, float, float, float] | None,
    source: str,
) -> None:
    """Every block MuPDF's ``get_text`` gives, in its place: an image block
    keeps its number, box and geometry and loses only its bytes; a text block
    loses only its block ``flags``, which no binding reaches (1.11)."""
    pdf = (
        rich_pdf(tmp_path / "rich.pdf") if source == "rich"
        else _image_between_text(tmp_path / "between.pdf")
    )
    documents = Documents()
    with pymupdf.open(pdf) as document:
        for index, page in enumerate(document):
            order: dict[str, Any] = {
                "document": {"path": str(pdf)}, "page": index, "kind": kind,
                "image_bytes": False,
            }
            options: dict[str, Any] = {}
            if clip is not None:
                order["clip"] = list(clip)
                options["clip"] = pymupdf.Rect(*clip)
            if sort is not None:
                order["sort"] = sort
                options["sort"] = sort
            bare = decode(reading.text(documents, text_order(order))["result"])
            expected = page.get_text(kind, **options)
            for block in expected["blocks"]:
                for key in _BYTES if block["type"] == 1 else ("flags",):
                    block.pop(key)
            assert bare == expected


def test_the_images_the_bare_reading_saw_are_the_ones_mupdf_saw(tmp_path: Path) -> None:
    """The bench above is not empty: it holds image blocks, a mask among them."""
    pdf = _image_between_text(tmp_path / "between.pdf")
    with pymupdf.open(pdf) as document:
        blocks = document[0].get_text("dict")["blocks"]
    assert [block["type"] for block in blocks].count(1) == 2
    assert any(block["mask"] is not None for block in blocks if block["type"] == 1)


def test_image_bytes_is_refused_where_it_means_nothing() -> None:
    with pytest.raises(OrderError):
        text_order({"document": {"handle": "h"}, "page": 0, "kind": "words",
                    "image_bytes": False})
    with pytest.raises(OrderError):
        text_order({"document": {"handle": "h"}, "page": 0, "kind": "dict",
                    "image_bytes": "no"})


def _stretched_noise(path: Path, side: int, form: str, mask: bool) -> Path:
    """Noise stretched over the whole page: Flate RGB or gray (masked or not),
    or a CMYK JPEG, which MuPDF's dict re-encodes rather than passes through."""
    noise = random.Random(side * 4 + mask * 2 + len(form))
    space, channels = {
        "rgb": (pymupdf.csRGB, 3), "gray": (pymupdf.csGRAY, 1), "cmyk": (pymupdf.csCMYK, 4)
    }[form]
    picture = pymupdf.Pixmap(space, side, side, noise.randbytes(side * side * channels), False)
    if mask:
        picture = pymupdf.Pixmap(picture, 1)
        picture.set_alpha(noise.randbytes(side * side))
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    if form == "cmyk":
        page.insert_image(page.rect, stream=picture.tobytes("jpg", jpg_quality=90))
    else:
        page.insert_image(page.rect, pixmap=picture)
    page.insert_text((72, 800), "Une ligne sous l'image", fontsize=9)
    document.save(path)
    return path


@pytest.mark.parametrize(("side", "zoom"), [(300, 0.3), (1200, 1.0), (2400, 2.0)])
@pytest.mark.parametrize(
    ("form", "mask"), [("rgb", False), ("rgb", True), ("gray", False), ("gray", True),
                       ("cmyk", False)]
)
def test_a_reading_without_bytes_leaves_the_next_renders_as_the_full_one_did(
    tmp_path: Path, side: int, zoom: float, form: str, mask: bool
) -> None:
    """MuPDF reuses an image it already decoded rather than decoding the
    region it draws, and the two differ: a reading that stopped decoding
    changed the next clipped renders of a held document — a delivery's
    ink-repair patches moved by a level. The bare reading decodes what the
    full one decoded, so the renders after it are the renders after it."""
    pdf = _stretched_noise(tmp_path / "noise.pdf", side, form, mask)
    clip = pymupdf.Rect(120, 130, 180, 170)
    samples: dict[bool, bytes] = {}
    for image_bytes in (True, False):
        documents = Documents()
        handle, document = documents.open(str(pdf))
        reading.text(documents, text_order(
            {"document": {"handle": handle}, "page": 0, "kind": "dict",
             "image_bytes": image_bytes}))
        samples[image_bytes] = document[0].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), clip=clip).samples
        documents.close(handle)
    with pymupdf.open(pdf) as fresh:
        untouched = fresh[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).samples
    assert samples[False] == samples[True]
    assert untouched != samples[True], "the bench must discriminate"
