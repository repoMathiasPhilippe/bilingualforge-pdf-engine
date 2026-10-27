"""Writing through the engine: the same calls, the same document (protocol 1.6, ADR-396).

The composer's every write — text boxes, lines of text, cover rectangles,
repainted images, real fonts, emphasis in HTML boxes, redactions, links,
highlights — is replayed twice: in process, and as engine orders on a held
document. Both documents are saved; « the same » is the same structure,
pixels, words, drawings, annotations and links, and each call's own answer.
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image

from pdf_engine import editing, writing
from pdf_engine.documents import Documents
from pdf_engine.protocol import (
    OrderError,
    apply_order,
    encode_order,
    metadata_order,
    new_page_order,
    save_order,
    write_order,
)
from tests.test_editing import structure
from tests.test_render import rich_pdf


def _png() -> bytes:
    picture = Image.new("RGB", (24, 16), (200, 40, 40))
    for x in range(24):
        picture.putpixel((x, 8), (0, 0, 0))
    buffer = BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _steps(face: Path) -> list[tuple[str, dict[str, Any]]]:
    """Each write the composer makes, as (order, body without document)."""
    morph = [[100.0, 300.0], [0.9, 0.0, 0.0, 1.0, 0.0, 0.0]]
    return [
        ("add_redact_annot", {"page": 0, "rect": [60, 80, 320, 110]}),
        # Covers the yellow fill whole: kept by graphics=none, removed by
        # MuPDF's default (remove_if_covered).
        ("add_redact_annot", {"page": 0, "rect": [55, 195, 310, 330]}),
        ("apply_redactions", {"page": 0, "images": "none", "graphics": "none"}),
        ("insert_font", {"page": 0, "fontname": "bfsface", "fontfile": str(face)}),
        ("draw_rect", {"page": 0, "rect": [60, 80, 320, 110],
                       "options": {"color": [1, 1, 1], "fill": [1, 1, 1], "width": 0}}),
        ("draw_rect", {"page": 0, "rect": [10, 10, 40, 40],
                       "options": {"color": None, "fill": [0.2, 0.4, 0.6], "width": 3,
                                   "fill_opacity": 0.1}}),
        ("draw_rect", {"page": 0, "rect": [50, 10, 90, 40], "options": {"width": 0.7}}),
        ("insert_textbox", {"page": 0, "rect": [60, 80, 320, 110],
                            "text": "Revenue grew steadily", "options": {
                                "fontname": "bfsface", "fontsize": 12.5, "color": [0, 0, 0.5],
                                "align": 1, "lineheight": 1.0}}),
        ("insert_textbox", {"page": 0, "rect": [60, 130, 120, 140],
                            "text": "Far too long for this tiny box to hold", "options": {
                                "fontname": "tiro", "fontsize": 11}}),
        ("insert_textbox", {"page": 0, "rect": [60, 300, 300, 340],
                            "text": "Squeezed", "options": {
                                "fontname": "hebo", "fontsize": 10, "morph": morph,
                                "lineheight": 1.2}}),
        ("insert_text", {"page": 0, "point": [72, 400], "text": "One line", "options": {
            "fontname": "tibo", "fontsize": 9, "color": [0.3, 0.3, 0.3], "morph": morph}}),
        ("insert_text", {"page": 0, "point": [72, 420], "text": "Invisible layer",
                         "options": {"fontname": "helv", "fontsize": 8, "render_mode": 3}}),
        ("insert_image", {"page": 0, "rect": [350, 500, 500, 600],
                          "stream": base64.b64encode(_png()).decode("ascii")}),
        ("insert_htmlbox", {"page": 0, "rect": [60, 600, 340, 700],
                            "text": "<p>Plain <b>bold</b> words</p>",
                            "archive": [[str(face), "bf-roman"]],
                            "options": {"css": "@font-face {font-family: bf; src: url(bf-roman);} "
                                               "p {font-family: bf; font-size: 11px;}",
                                        "scale_low": 0.5}}),
        ("insert_link", {"page": 0, "link": {"kind": 2, "from": [72, 85, 200, 105],
                                              "uri": "https://example.org"}}),
        ("add_markup_annot", {"page": 0, "maker": "add_highlight_annot",
                              "quads": [[[72, 390], [140, 390], [72, 402], [140, 402]]],
                              "stroke": [1.0, 1.0, 0.0]}),
        ("add_markup_annot", {"page": 0, "maker": "add_underline_annot",
                              "quads": [[[72, 410], [140, 410], [72, 422], [140, 422]]],
                              "stroke": [0.0, 0.0, 1.0], "opacity": 0.5}),
        ("new_page", {"width": 300, "height": 200}),
        ("insert_textbox", {"page": 2, "rect": [20, 20, 280, 180], "text": "New sheet",
                            "options": {"fontname": "cour"}}),
        ("set_metadata", {"metadata": {}}),
    ]


def _inline(document: pymupdf.Document, order: str, body: dict[str, Any]) -> Any:
    """The same step, as the composer calls PyMuPDF in process."""
    if order == "new_page":
        return document.new_page(width=body["width"], height=body["height"]).number
    if order == "set_metadata":
        document.set_metadata(body["metadata"])
        document.del_xml_metadata()
        return None
    page = document[body["page"]]
    options = dict(body.get("options", {}))
    for key in ("color", "fill"):
        if key in options and options[key] is not None:
            options[key] = tuple(options[key])
    if options.get("morph") is not None:
        point, matrix = options["morph"]
        options["morph"] = (pymupdf.Point(*point), pymupdf.Matrix(*matrix))
    if order == "insert_font":
        return page.insert_font(fontname=body["fontname"], fontfile=body["fontfile"])
    if order == "add_redact_annot":
        page.add_redact_annot(pymupdf.Rect(*body["rect"]))
        return None
    if order == "apply_redactions":
        return page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                     graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    if order == "draw_rect":
        page.draw_rect(pymupdf.Rect(*body["rect"]), **options)
        return None
    if order == "insert_textbox":
        return page.insert_textbox(pymupdf.Rect(*body["rect"]), body["text"], **options)
    if order == "insert_text":
        return page.insert_text(pymupdf.Point(*body["point"]), body["text"], **options)
    if order == "insert_image":
        return page.insert_image(pymupdf.Rect(*body["rect"]),
                                 stream=base64.b64decode(body["stream"]))
    if order == "insert_htmlbox":
        archive = pymupdf.Archive()
        for path, name in body["archive"]:
            archive.add(path, name)
        spare, scale = page.insert_htmlbox(pymupdf.Rect(*body["rect"]), body["text"],
                                           archive=archive, **options)
        return [spare, scale]
    if order == "insert_link":
        link = dict(body["link"])
        link["from"] = pymupdf.Rect(*link["from"])
        page.insert_link(link)
        return None
    if order == "add_markup_annot":
        quads = [pymupdf.Quad(*(pymupdf.Point(*c) for c in q)) for q in body["quads"]]
        annot = getattr(page, body["maker"])(quads)
        annot.set_colors(stroke=tuple(body["stroke"]))
        if "opacity" in body:
            annot.set_opacity(body["opacity"])
        annot.update()
        return annot.xref
    raise AssertionError(order)


def _engine(documents: Documents, handle: str, order: str, body: dict[str, Any]) -> Any:
    payload = {"document": {"handle": handle}, **body}
    if order == "new_page":
        return writing.new_page(documents, *new_page_order(payload))["page"]
    if order == "apply_redactions":
        return writing.apply_redactions(documents, *apply_order(payload))["result"]
    if order == "set_metadata":
        writing.set_metadata(documents, *metadata_order(payload))
        writing.del_xml_metadata(documents, handle)
        return None
    return writing.write(documents, write_order(order, payload))["result"]


def _content(path: Path) -> list[tuple[Any, ...]]:
    with pymupdf.open(path) as document:
        return [
            (
                page.get_text("words"),
                len(page.get_drawings()),
                [(a.type, tuple(a.rect), a.colors) for a in page.annots()],
                [(link["kind"], tuple(link["from"]), link.get("uri")) for link in page.get_links()],
                sorted(str(font[3]).split("+")[-1] for font in page.get_fonts(full=True)),
            )
            for page in document
        ] + [document.metadata]


def test_every_write_is_mupdf_s_own(tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    face = tmp_path / "face.ttf"
    face.write_bytes(pymupdf.Font("figo").buffer)
    steps = _steps(face)

    answers_inline = []
    with pymupdf.open(pdf) as document:
        for order, body in steps:
            answers_inline.append(_inline(document, order, body))
        document.save(tmp_path / "inline.pdf", garbage=3, deflate=True)

    documents = Documents()
    handle, _ = documents.open(str(pdf))
    answers_engine = [_engine(documents, handle, order, body) for order, body in steps]
    editing.save(documents, save_order({"document": {"handle": handle},
                                        "path": str(tmp_path / "engine.pdf"),
                                        "garbage": 3, "deflate": True}))

    def comparable(value: Any) -> Any:
        return None if value is None else value

    for (order, _), mine, theirs in zip(steps, answers_engine, answers_inline, strict=True):
        if order in ("insert_font", "insert_image", "add_markup_annot"):
            continue  # object numbers: the same count, compared through the file
        assert comparable(mine) == comparable(theirs), order
    assert answers_engine[8] < 0, "the overflowing box reports its overflow"
    with pymupdf.open(tmp_path / "engine.pdf") as written:
        kinds = sorted(annot.type[1] for annot in written[0].annots())
    assert kinds == ["Highlight", "Underline"]
    assert structure(tmp_path / "engine.pdf") == structure(tmp_path / "inline.pdf")
    assert _content(tmp_path / "engine.pdf") == _content(tmp_path / "inline.pdf")


def test_an_image_is_encoded_as_mupdf_encodes_it() -> None:
    samples = bytes(range(256)) * 12  # 32 x 32 x 3
    for output, quality in (("png", 95), ("jpeg", 88)):
        answer = writing.encode_image(encode_order({
            "samples": base64.b64encode(samples).decode("ascii"),
            "width": 32, "height": 32, "n": 3, "output": output, "quality": quality,
        }))
        inline = pymupdf.Pixmap(pymupdf.csRGB, 32, 32, samples, False).tobytes(
            output, jpg_quality=quality
        )
        assert base64.b64decode(answer["result"]["__bytes__"]) == inline


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("insert_textbox", {"document": {"path": "C:/x.pdf"}, "page": 0, "rect": [0, 0, 1, 1],
                            "text": "x"}),
        ("insert_textbox", {"document": {"handle": "h"}, "page": 0, "rect": [0, 0, 1, 1],
                            "text": "x", "options": {"fontfile": "C:/f.ttf"}}),
        ("draw_rect", {"document": {"handle": "h"}, "page": 0, "rect": [0, 0, 1, 1],
                       "options": {"color": "red"}}),
        ("insert_text", {"document": {"handle": "h"}, "page": 0, "point": [0], "text": "x"}),
        ("insert_textbox", {"document": {"handle": "h"}, "page": 0, "rect": [0, 0, 1, 1],
                            "text": "x", "options": {"morph": [[0, 0], [1, 0, 0, 1]]}}),
        ("insert_font", {"document": {"handle": "h"}, "page": 0, "fontname": "F"}),
        ("insert_htmlbox", {"document": {"handle": "h"}, "page": 0, "rect": [0, 0, 1, 1],
                            "text": "x", "archive": [["relative.ttf", "a"]]}),
        ("add_markup_annot", {"document": {"handle": "h"}, "page": 0,
                              "maker": "add_highlight_annot", "quads": [[[0, 0]]]}),
        ("add_markup_annot", {"document": {"handle": "h"}, "page": 0, "maker": "add_text_annot",
                              "quads": [[[0, 0], [1, 0], [0, 1], [1, 1]]]}),
        ("show_pdf_page", {"document": {"handle": "h"}, "page": 0, "rect": [0, 0, 1, 1],
                           "source": {"handle": "s"}, "source_page": 0,
                           "options": {"clip": [0, 0]}}),
    ],
)
def test_malformed_writes_are_refused(kind: str, payload: dict[str, Any]) -> None:
    with pytest.raises(OrderError) as refused:
        write_order(kind, payload)
    assert refused.value.code == "bad_order"


def test_a_page_shown_on_a_new_sheet_is_mupdf_s_own(tmp_path: Path) -> None:
    """The paper path (ADR-036) shows each finished page, scaled, on a fresh
    sheet; the merge does the same with a clip. Both by the engine, from a
    held document, against the same calls in process."""
    pdf = rich_pdf(tmp_path / "rich.pdf")
    placements = [
        ({"rect": [20, 30, 590, 780]}, {}),
        ({"rect": [0, 0, 300, 400]}, {"clip": [0, 0, 300, 420], "keep_proportion": False}),
    ]

    with pymupdf.open(pdf) as source, pymupdf.open() as sheets:
        for index, (body, options) in enumerate(placements):
            sheet = sheets.new_page(width=612, height=792)
            inline = dict(options)
            if "clip" in inline:
                inline["clip"] = pymupdf.Rect(*inline["clip"])
            sheet.show_pdf_page(pymupdf.Rect(*body["rect"]), source, index, **inline)
        sheets.save(tmp_path / "inline.pdf", garbage=3, deflate=True)

    documents = Documents()
    held, _ = documents.open(str(pdf))
    sheets_handle, _ = documents.create()
    for index, (body, options) in enumerate(placements):
        writing.new_page(documents, *new_page_order(
            {"document": {"handle": sheets_handle}, "width": 612, "height": 792}))
        writing.write(documents, write_order("show_pdf_page", {
            "document": {"handle": sheets_handle}, "page": index, **body,
            "source": {"handle": held}, "source_page": index, "options": options,
        }))
    editing.save(documents, save_order({"document": {"handle": sheets_handle},
                                        "path": str(tmp_path / "engine.pdf"),
                                        "garbage": 3, "deflate": True}))

    assert structure(tmp_path / "engine.pdf") == structure(tmp_path / "inline.pdf")
    assert _content(tmp_path / "engine.pdf") == _content(tmp_path / "inline.pdf")
