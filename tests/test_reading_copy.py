"""The reading copy (1.12): one page alone, its byte-filtered images unfiltered, the same pixels.

A copy is only worth having if every reading of it is the reading of the
source: the same render at every zoom and clip — including the zooms where
MuPDF decodes an image at a reduced size — and the same text.
"""

from __future__ import annotations

import random
import zlib
from pathlib import Path

import pymupdf
import pytest

from pdf_engine import editing
from pdf_engine.documents import Documents
from pdf_engine.protocol import OrderError, reading_copy_order

#: Zooms from a fraction of the images' own resolution to above it.
ZOOMS = (0.3, 0.7, 1.0, 1.5, 4.0)


def _noise(width: int, height: int, channels: int, seed: int) -> bytes:
    generator = random.Random(seed)
    return bytes(generator.randrange(256) for _ in range(width * height * channels))


def _pixmap(width: int, height: int, colorspace: pymupdf.Colorspace, seed: int,
            alpha: bool = False) -> pymupdf.Pixmap:
    channels = colorspace.n + (1 if alpha else 0)
    return pymupdf.Pixmap(colorspace, width, height, _noise(width, height, channels, seed), alpha)


def scan_like(path: Path) -> Path:
    """Two pages; the second draws the kinds of image a scan's reading meets.

    * a Flate image with a PNG predictor, stretched (a page image);
    * an image with a soft mask (two byte-filtered streams);
    * a JPEG, which must keep its stream;
    * a line of text over them (a text layer).
    """
    document = pymupdf.open()
    document.new_page(width=400, height=300).insert_text((40, 60), "Première page")
    page = document.new_page(width=400, height=300)

    width, height = 600, 450
    rows = _noise(width, height, 3, 1)
    xref = page.insert_image(pymupdf.Rect(0, 0, 300, 225),
                             stream=_pixmap(width, height, pymupdf.csRGB, 1).tobytes("png"))
    stride = width * 3
    predicted = b"".join(b"\x00" + rows[i * stride:(i + 1) * stride] for i in range(height))
    document.update_stream(xref, zlib.compress(predicted), compress=0)
    document.xref_set_key(xref, "Filter", "/FlateDecode")
    document.xref_set_key(
        xref, "DecodeParms", f"<</Predictor 15/Colors 3/Columns {width}/BitsPerComponent 8>>"
    )

    # A gradient, not noise: MuPDF stores an image it cannot compress raw,
    # and the copy must meet a FILTERED image and a FILTERED soft mask.
    shaded = bytes((index // 7) % 256 for index in range(200 * 200 * 4))
    page.insert_image(pymupdf.Rect(220, 120, 380, 280),
                      stream=pymupdf.Pixmap(pymupdf.csRGB, 200, 200, shaded, True).tobytes("png"))
    page.insert_image(pymupdf.Rect(300, 10, 390, 100),
                      stream=_pixmap(240, 240, pymupdf.csRGB, 3).tobytes("jpeg"))
    page.insert_text((30, 260), "Délivré à Marseille, le 4 juillet 1994", fontsize=14)
    # Compressed at the save, as MuPDF does: inserted images are stored raw until then.
    document.save(path, deflate=True)
    document.close()
    return path


def _render(path: Path, index: int, zoom: float, clip: pymupdf.Rect | None) -> bytes:
    with pymupdf.open(stream=path.read_bytes()) as fresh:
        return fresh[index].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).samples


def test_the_copy_reads_as_the_source_at_every_zoom(tmp_path: Path) -> None:
    source = scan_like(tmp_path / "scan.pdf")
    target = tmp_path / "copy" / "page.pdf"

    answer = editing.reading_copy(
        Documents(), reading_copy_order(
            {"document": {"path": str(source)}, "page": 1, "path": str(target)}
        )
    )

    assert answer == {"path": str(target), "page_count": 1, "unfiltered": 3}
    clips = (None, pymupdf.Rect(40, 30, 260, 200), pymupdf.Rect(250, 140, 395, 295))
    with pymupdf.open(source) as original, pymupdf.open(target) as copy:
        assert copy.page_count == 1
        assert copy[0].rect == original[1].rect
        assert copy[0].rotation == original[1].rotation
        assert copy[0].get_text("words") == original[1].get_text("words")
        assert copy[0].get_text("rawdict", flags=pymupdf.TEXTFLAGS_TEXT) == original[1].get_text(
            "rawdict", flags=pymupdf.TEXTFLAGS_TEXT
        )
    for zoom in ZOOMS:
        for clip in clips:
            assert _render(target, 0, zoom, clip) == _render(source, 1, zoom, clip), (zoom, clip)


def test_only_byte_filtered_images_lose_their_filter(tmp_path: Path) -> None:
    source = scan_like(tmp_path / "scan.pdf")
    target = tmp_path / "page.pdf"
    editing.reading_copy(Documents(), reading_copy_order(
        {"document": {"path": str(source)}, "page": 1, "path": str(target)}
    ))

    with pymupdf.open(target) as copy:
        filters = {}
        for item in copy[0].get_images(full=True):
            for xref in {item[0], item[1]} - {0}:
                filters[xref] = (
                    copy.xref_get_key(xref, "Filter")[1],
                    copy.xref_get_key(xref, "DecodeParms")[1],
                )
    # The decoding parameters go with the filter they configured: MuPDF
    # ignores them alone, the PDF reference does not provide for them.
    assert sorted(filters.values()) == [
        ("/DCTDecode", "null"), ("null", "null"), ("null", "null"), ("null", "null"),
    ]


def test_the_source_file_is_left_untouched(tmp_path: Path) -> None:
    source = scan_like(tmp_path / "scan.pdf")
    before = source.read_bytes()
    editing.reading_copy(Documents(), reading_copy_order(
        {"document": {"path": str(source)}, "page": 0, "path": str(tmp_path / "first.pdf")}
    ))
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "payload",
    [
        {"document": {"handle": "abc"}, "page": 0, "path": "C:/x.pdf"},
        {"document": {"path": "C:/a.pdf"}, "page": 0, "path": "relative.pdf"},
        {"document": {"path": "C:/a.pdf"}, "page": -1, "path": "C:/x.pdf"},
    ],
)
def test_malformed_orders_are_refused(payload: dict[str, object]) -> None:
    with pytest.raises(OrderError) as refused:
        reading_copy_order(payload)
    assert refused.value.code == "bad_order"


def test_a_missing_page_is_named(tmp_path: Path) -> None:
    source = scan_like(tmp_path / "scan.pdf")
    with pytest.raises(OrderError) as refused:
        editing.reading_copy(Documents(), reading_copy_order(
            {"document": {"path": str(source)}, "page": 5, "path": str(tmp_path / "x.pdf")}
        ))
    assert refused.value.code == "page_out_of_range"
