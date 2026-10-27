"""Reading through the engine: MuPDF's own answers, with their own types (ADR-391).

The closed code compares coordinates to tuples, keys dictionaries by them and
reads embedded fonts as bytes. JSON has neither tuples nor bytes; typed JSON
carries both, and these tests compare with ``==`` — under which a list is not
a tuple — against the in-process call.
"""

from __future__ import annotations

import math
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
