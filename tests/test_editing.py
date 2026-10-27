"""Documents as a whole, through the engine: what PyMuPDF itself does (ADR-388, step 2).

A save is not byte-reproducible — PyMuPDF writes variable identifiers, two
saves of the same document in a row already differ — so « the same » here
means the same structure (pages, boxes, rotations) and the same pixels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_engine import editing
from pdf_engine.documents import Documents
from pdf_engine.protocol import (
    OrderError,
    cropbox_order,
    insert_order,
    rotation_order,
    save_order,
)
from tests.helpers import make_pdf
from tests.test_render import rich_pdf


def structure(path: Path) -> list[tuple[Any, ...]]:
    """Each page's boxes, rotation and 40 dpi pixels."""
    with pymupdf.open(path) as document:
        return [
            (
                tuple(page.rect),
                tuple(page.mediabox),
                tuple(page.cropbox),
                page.rotation,
                page.get_pixmap(dpi=40).samples,
            )
            for page in document
        ]


def test_describe_reports_what_pymupdf_reads(tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")

    described = editing.describe(Documents(), str(pdf), None)

    with pymupdf.open(pdf) as document:
        assert described["page_count"] == document.page_count
        for page, seen in zip(document, described["pages"], strict=True):
            assert seen["rect"] == list(page.rect)
            assert seen["mediabox"] == list(page.mediabox)
            assert seen["cropbox"] == list(page.cropbox)
            assert seen["rotation"] == page.rotation
    assert described["pages"][1]["rotation"] == 90


def test_assembling_cropping_turning_matches_pymupdf(tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    inline, engine = tmp_path / "inline.pdf", tmp_path / "engine.pdf"
    crop = (40.0, 60.0, 400.0, 500.0)
    with pymupdf.open(pdf) as source, pymupdf.open() as made:
        made.insert_pdf(source, from_page=1, to_page=1)
        made.insert_pdf(source, from_page=0, to_page=0)
        made[-1].set_cropbox(pymupdf.Rect(*crop))
        made[0].set_rotation(180)
        made.save(inline)

    documents = Documents()
    source, _ = documents.open(str(pdf))
    target, _ = documents.create()
    editing.insert_pages(documents, insert_order(
        {"document": {"handle": target}, "source": {"handle": source},
         "from_page": 1, "to_page": 1}))
    count = editing.insert_pages(documents, insert_order(
        {"document": {"handle": target}, "source": {"handle": source},
         "from_page": 0, "to_page": 0}))["page_count"]
    editing.set_cropbox(documents, *cropbox_order(
        {"document": {"handle": target}, "page": count - 1, "rect": list(crop)}))
    editing.set_rotation(documents, *rotation_order(
        {"document": {"handle": target}, "page": 0, "rotation": 180}))
    editing.save(documents, save_order(
        {"document": {"handle": target}, "path": str(engine)}))

    assert count == 2
    assert structure(engine) == structure(inline)


def test_a_turned_copy_keeps_the_deposited_bytes_of_its_content(tmp_path: Path) -> None:
    """``/Rotate`` is a declaration: the opened document is saved turned."""
    pdf = make_pdf(tmp_path / "doc.pdf", pages=2)
    documents = Documents()
    handle, _ = documents.open(str(pdf))
    editing.set_rotation(documents, *rotation_order(
        {"document": {"handle": handle}, "page": 1, "rotation": 270}))
    target = tmp_path / "out" / "turned.pdf"
    editing.save(documents, save_order(
        {"document": {"handle": handle}, "path": str(target), "garbage": 3, "deflate": True}))

    with pymupdf.open(target) as turned, pymupdf.open(pdf) as original:
        assert [page.rotation for page in turned] == [0, 270]
        assert turned[1].read_contents() == original[1].read_contents()


def test_inserting_by_path_or_by_held_source_gives_the_same_document(tmp_path: Path) -> None:
    """PyMuPDF grafts each insertion on its own (measured: two insertions from
    one open document and from two fresh copies give the same objects): the
    way the source is named changes nothing in the result.

    Counted in OBJECTS, not bytes: a save is not byte-reproducible, and in a
    full suite — MuPDF's process-wide state warmed by other tests — the same
    files came out one byte apart (8 212 against 8 213), never alone."""
    pdf = rich_pdf(tmp_path / "rich.pdf")
    documents = Documents()
    source, _ = documents.open(str(pdf))
    results = []
    for name, reference in (("held", {"handle": source}), ("path", {"path": str(pdf)})):
        target, _ = documents.create()
        for page in (1, 0):
            editing.insert_pages(documents, insert_order(
                {"document": {"handle": target}, "source": reference,
                 "from_page": page, "to_page": page}))
        out = tmp_path / f"{name}.pdf"
        editing.save(documents, save_order({"document": {"handle": target}, "path": str(out)}))
        results.append(out)

    assert structure(results[0]) == structure(results[1])
    with pymupdf.open(results[0]) as held, pymupdf.open(results[1]) as by_path:
        assert held.xref_length() == by_path.xref_length()


def test_a_changed_document_must_be_held(tmp_path: Path) -> None:
    pdf = str(make_pdf(tmp_path / "doc.pdf", pages=1))
    for order in (
        lambda: rotation_order({"document": {"path": pdf}, "page": 0, "rotation": 90}),
        lambda: cropbox_order({"document": {"path": pdf}, "page": 0, "rect": [0, 0, 1, 1]}),
        lambda: save_order({"document": {"path": pdf}, "path": pdf}),
        lambda: insert_order({"document": {"path": pdf}, "source": {"path": pdf},
                              "from_page": 0, "to_page": 0}),
    ):
        with pytest.raises(OrderError) as refused:
            order()
        assert refused.value.code == "bad_order"


@pytest.mark.parametrize(
    "payload",
    [
        {"page": 0, "rotation": 45},
        {"page": 0, "rotation": True},
        {"page": -1, "rotation": 90},
    ],
)
def test_a_rotation_is_a_quarter_turn_of_a_real_page(payload: dict[str, Any]) -> None:
    with pytest.raises(OrderError):
        rotation_order({"document": {"handle": "h"}, **payload})


def test_refusals_name_the_page_and_the_fault(tmp_path: Path) -> None:
    documents = Documents()
    handle, _ = documents.open(str(make_pdf(tmp_path / "doc.pdf", pages=1)))

    with pytest.raises(OrderError) as beyond:
        editing.set_rotation(documents, handle, 3, 90)
    with pytest.raises(OrderError) as outside:
        editing.set_cropbox(documents, handle, 0, (0, 0, 9000, 9000))
    with pytest.raises(OrderError) as backwards:
        insert_order({"document": {"handle": handle}, "source": {"handle": handle},
                      "from_page": 2, "to_page": 1})

    assert (beyond.value.code, beyond.value.detail) == ("page_out_of_range", {"page_count": 1})
    assert outside.value.code == "edit_failed"
    assert backwards.value.code == "bad_order"


# -- redaction and font subsetting (protocol 1.4, ADR-394) ----------------------

REDACTIONS = [(60.0, 80.0, 320.0, 110.0), (55.0, 195.0, 310.0, 330.0), (340.0, 490.0, 510.0, 610.0)]


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"images": "none", "graphics": "none"},
        {"images": "none", "graphics": "remove_if_covered", "text": "none"},
        {"images": "pixels", "graphics": "remove_if_touched", "text": "remove"},
    ],
)
def test_a_redaction_is_mupdf_s_own(tmp_path: Path, options: dict[str, str]) -> None:
    """The same rectangles, the same options: the same page — text, pixels,
    boxes — as ``add_redact_annot`` then ``apply_redactions`` in process."""
    from pdf_engine.protocol import redact_order

    pdf = rich_pdf(tmp_path / "rich.pdf")
    constants = {
        "images": {"none": pymupdf.PDF_REDACT_IMAGE_NONE, "pixels": pymupdf.PDF_REDACT_IMAGE_PIXELS},
        "graphics": {
            "none": pymupdf.PDF_REDACT_LINE_ART_NONE,
            "remove_if_covered": pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            "remove_if_touched": pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
        },
        "text": {"none": pymupdf.PDF_REDACT_TEXT_NONE, "remove": pymupdf.PDF_REDACT_TEXT_REMOVE},
    }
    with pymupdf.open(pdf) as document:
        for rect in REDACTIONS:
            document[0].add_redact_annot(pymupdf.Rect(*rect))
        document[0].apply_redactions(
            **{key: constants[key][value] for key, value in options.items()}
        )
        document.save(tmp_path / "inline.pdf")

    documents = Documents()
    handle, _ = documents.open(str(pdf))
    answer = editing.redact(documents, redact_order(
        {"document": {"handle": handle}, "page": 0, "rects": [list(r) for r in REDACTIONS],
         **options}))
    editing.save(documents, save_order({"document": {"handle": handle},
                                        "path": str(tmp_path / "engine.pdf")}))

    assert answer == {"redacted": 3}
    assert structure(tmp_path / "engine.pdf") == structure(tmp_path / "inline.pdf")
    with pymupdf.open(tmp_path / "engine.pdf") as mine, pymupdf.open(tmp_path / "inline.pdf") as theirs:
        assert mine[0].get_text("words") == theirs[0].get_text("words")
        assert len(mine[0].get_drawings()) == len(theirs[0].get_drawings())
    if options.get("text") != "none":
        with pymupdf.open(tmp_path / "engine.pdf") as mine:
            assert "chiffre" not in mine[0].get_text(), "the redaction removed nothing"


def test_subsetting_keeps_only_the_glyphs_used(tmp_path: Path) -> None:
    from pdf_engine.protocol import held_order

    pdf = tmp_path / "font.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="F0", fontbuffer=pymupdf.Font("helv").buffer)
        page.insert_text((72, 100), "Bonjour", fontname="F0", fontsize=14)
        document.save(pdf)
    with pymupdf.open(pdf) as document:
        document.subset_fonts()
        document.save(tmp_path / "inline.pdf", garbage=3, deflate=True)

    documents = Documents()
    handle, _ = documents.open(str(pdf))
    assert editing.subset_fonts(documents, held_order({"document": {"handle": handle}})) == {
        "subset": True
    }
    editing.save(documents, save_order({"document": {"handle": handle},
                                        "path": str(tmp_path / "engine.pdf"),
                                        "garbage": 3, "deflate": True}))

    assert structure(tmp_path / "engine.pdf") == structure(tmp_path / "inline.pdf")
    with pymupdf.open(tmp_path / "engine.pdf") as mine, pymupdf.open(tmp_path / "inline.pdf") as theirs:
        assert mine[0].get_fonts(full=True) == theirs[0].get_fonts(full=True)
        assert mine[0].get_text() == theirs[0].get_text()


@pytest.mark.parametrize(
    "payload",
    [
        {"document": {"path": "C:/x.pdf"}, "page": 0, "rects": [[0, 0, 1, 1]]},
        {"document": {"handle": "h"}, "page": 0, "rects": []},
        {"document": {"handle": "h"}, "page": 0, "rects": [[0, 0, 1]]},
        {"document": {"handle": "h"}, "page": 0, "rects": [[0, 0, 1, 1]], "images": "all"},
        {"document": {"handle": "h"}, "page": 0, "rects": [[0, 0, 1, 1]], "graphics": "none!"},
        {"document": {"handle": "h"}, "page": 0, "rects": [[0, 0, 1, 1]], "text": "keep"},
    ],
)
def test_malformed_redactions_are_refused(payload: dict[str, Any]) -> None:
    from pdf_engine.protocol import redact_order

    with pytest.raises(OrderError) as refused:
        redact_order(payload)
    assert refused.value.code == "bad_order"
