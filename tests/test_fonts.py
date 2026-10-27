"""Fonts measured by the engine: MuPDF's own numbers (protocol 1.5, ADR-395).

The composer decides sizes from these measures — a width a hair off flips a
fit — so each is compared with ``==`` against the same ``pymupdf.Font`` call
in process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_engine import fonts
from pdf_engine.documents import Documents
from pdf_engine.protocol import (
    FontSpec,
    OrderError,
    font_info_order,
    glyphs_order,
    text_length_order,
)
from pdf_engine.typed import decode

TEXTS = ["Le chiffre d'affaires", "WWW iii", "Été 2024 — €1,234.56", "ﬁnance", "中文", ""]
CODES = [32, 65, 97, 0xE9, 0x20AC, 0x2014, 0xFB01, 0x4E2D, 0x10FFFF]


def _embedded_pdf(path: Path) -> tuple[Path, int]:
    """A page whose font is embedded — the subset a source document carries."""
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="F0", fontbuffer=pymupdf.Font("tiro").buffer)
        page.insert_text((72, 100), "Bonjour à tous", fontname="F0", fontsize=14)
        document.subset_fonts()
        document.save(path)
    with pymupdf.open(path) as document:
        xref = next(font[0] for font in document[0].get_fonts(full=True) if font[1] != "n/a")
    return path, xref


def _specs(tmp_path: Path) -> list[tuple[FontSpec, Any]]:
    """Each kind of spec, with the in-process ``pymupdf.Font`` it must equal."""
    pdf, xref = _embedded_pdf(tmp_path / "embedded.pdf")
    with pymupdf.open(pdf) as document:
        embedded = pymupdf.Font(fontbuffer=document.extract_font(xref)[3])
    font_file = tmp_path / "face.ttf"
    font_file.write_bytes(pymupdf.Font("figo").buffer)
    return [
        (FontSpec(builtin="helv"), pymupdf.Font("helv")),
        (FontSpec(builtin="tiro"), pymupdf.Font("tiro")),
        (FontSpec(builtin="figo"), pymupdf.Font("figo")),
        (FontSpec(file=str(font_file)), pymupdf.Font(fontfile=str(font_file))),
        (FontSpec(path=str(pdf), xref=xref), embedded),
    ]


def test_every_measure_is_mupdf_s_own(tmp_path: Path) -> None:
    documents = Documents()
    faces = fonts.Faces()
    for spec, inline in _specs(tmp_path):
        font = faces.get(documents, spec)
        info = decode(fonts.info(font, True))
        assert info == {
            "name": inline.name,
            "ascender": inline.ascender,
            "descender": inline.descender,
            "flags": inline.flags,
            "is_bold": inline.is_bold,
            "is_italic": inline.is_italic,
            "is_monospaced": inline.is_monospaced,
            "glyph_count": inline.glyph_count,
            "buffer": bytes(inline.buffer),
        }, spec
        for size in (1.0, 9.5, 11.0):
            assert fonts.text_lengths(font, TEXTS, size) == [
                inline.text_length(text, fontsize=size) for text in TEXTS
            ], spec
        assert fonts.glyphs(font, CODES) == {
            "glyphs": [inline.has_glyph(code) for code in CODES],
            "advances": [inline.glyph_advance(code) for code in CODES],
        }, spec


def test_a_base14_name_is_measured_as_get_text_length_measures_it() -> None:
    """``get_text_length`` is not ``Font(name).text_length``: they differ on
    « Été » (7.06 against 9.08 at 1 pt). Each call keeps its own measure."""
    for name in ("tiro", "helv", "cour", "tibo"):
        assert fonts.named_lengths(name, TEXTS[:3], 1.0) == [
            pymupdf.get_text_length(text, name, 1.0) for text in TEXTS[:3]
        ]
    assert fonts.named_lengths("tiro", ["Été"], 1.0) != fonts.text_lengths(
        pymupdf.Font("tiro"), ["Été"], 1.0
    )


def test_a_face_is_parsed_once(tmp_path: Path) -> None:
    faces = fonts.Faces()
    documents = Documents()
    first = faces.get(documents, FontSpec(builtin="helv"))
    assert faces.get(documents, FontSpec(builtin="helv")) is first


def test_an_unreadable_face_is_named(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.ttf"
    garbage.write_bytes(b"not a font")
    for spec in (FontSpec(builtin="no-such-face"), FontSpec(file=str(garbage))):
        with pytest.raises(OrderError) as refused:
            fonts.Faces().get(Documents(), spec)
        assert refused.value.code == "unreadable_font"


@pytest.mark.parametrize(
    ("parse", "payload"),
    [
        (font_info_order, {}),
        (font_info_order, {"font": {"builtin": ""}}),
        (font_info_order, {"font": {"file": "relative.ttf"}}),
        (font_info_order, {"font": {"document": {"handle": "h"}}}),
        (text_length_order, {"font": {"builtin": "helv"}, "fontname": "helv",
                             "texts": ["a"], "size": 1}),
        (text_length_order, {"fontname": "helv", "texts": "a", "size": 1}),
        (text_length_order, {"fontname": "helv", "texts": ["a"], "size": "11"}),
        (glyphs_order, {"font": {"builtin": "helv"}, "codes": [-1]}),
        (glyphs_order, {"font": {"builtin": "helv"}, "codes": ["A"]}),
    ],
)
def test_malformed_font_orders_are_refused(parse: Any, payload: dict[str, Any]) -> None:
    with pytest.raises(OrderError) as refused:
        parse(payload)
    assert refused.value.code == "bad_order"
