"""The engine draws exactly what MuPDF draws in-process (ADR-388).

The migration's gate is « zero difference »: the closed code moves its renders
to the engine only because the engine returns the same bytes, for every
parameter the closed code uses.
"""

from __future__ import annotations

import http.client
import json
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy
import pymupdf
import pytest

from pdf_engine import PROTOCOL_VERSION
from pdf_engine.documents import Documents
from pdf_engine.protocol import OrderError, render_order
from pdf_engine.render import render
from pdf_engine.server import Engine, _Server
from tests.helpers import make_pdf


def rich_pdf(path: Path) -> Path:
    """Text, a coloured fill, a vector stroke and an image; a rotated page."""
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 100), "Le chiffre d'affaires atteint 1 234,56 €", fontsize=14)
        page.draw_rect(pymupdf.Rect(60, 200, 300, 320), color=(0, 0, 1), fill=(1, 0.8, 0))
        page.draw_line((50, 400), (540, 470), color=(1, 0, 0), width=3)
        picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
        picture.set_rect(picture.irect, (20, 120, 200))
        page.insert_image(pymupdf.Rect(350, 500, 500, 600), pixmap=picture)
        second = document.new_page(width=842, height=595)
        second.insert_text((100, 100), "Page en paysage", fontsize=20)
        second.set_rotation(90)
        document.save(path)
    return path


#: The parameter sets the closed code uses (dpi or zoom, clip, grey, alpha).
CASES: list[dict[str, Any]] = [
    {},
    {"dpi": 150},
    {"dpi": 36, "clip": [50, 60, 300, 330]},
    {"matrix": [1.5, 0, 0, 1.5, 0, 0], "colorspace": "gray"},
    {"matrix": [2.5, 0, 0, 1.25, 0, 0], "clip": [340.25, 480.5, 512.75, 610.125]},
    {"dpi": 72, "alpha": True},
]


def inline(path: Path, index: int, case: dict[str, Any]) -> pymupdf.Pixmap:
    """The same render, done in this process as the closed code did it."""
    with pymupdf.open(path) as document:
        return document[index].get_pixmap(
            matrix=pymupdf.Matrix(*case["matrix"]) if "matrix" in case else pymupdf.Identity,
            dpi=case.get("dpi"),
            colorspace=pymupdf.csGRAY if case.get("colorspace") == "gray" else pymupdf.csRGB,
            clip=pymupdf.Rect(*case["clip"]) if "clip" in case else None,
            alpha=case.get("alpha", False),
        )


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("case", CASES)
def test_samples_and_png_are_byte_identical_to_in_process(
    tmp_path: Path, index: int, case: dict[str, Any]
) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    expected = inline(pdf, index, case)
    documents = Documents()
    base = {"document": {"path": str(pdf)}, "page": index, **case}

    samples = render(documents, render_order(base))
    png = render(documents, render_order({**base, "output": {"kind": "png"}}))

    assert (samples.width, samples.height, samples.n) == (
        expected.width,
        expected.height,
        expected.n,
    )
    assert samples.body == expected.samples
    assert png.body == expected.tobytes("png")


def test_a_file_output_is_what_mupdf_itself_saves(tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    mine, theirs = tmp_path / "engine.png", tmp_path / "inline.png"
    inline(pdf, 0, {"dpi": 60}).save(theirs)

    render(
        Documents(),
        render_order(
            {
                "document": {"path": str(pdf)},
                "page": 0,
                "dpi": 60,
                "output": {"kind": "file", "path": str(mine)},
            }
        ),
    )

    assert mine.read_bytes() == theirs.read_bytes()


def test_a_shared_file_grows_but_never_shrinks(tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    shared = tmp_path / "raster.bin"
    order = {
        "document": {"path": str(pdf)},
        "page": 0,
        "output": {"kind": "shared", "path": str(shared)},
    }
    documents = Documents()

    big = render(documents, render_order({**order, "dpi": 100}))
    size = big.width * big.height * big.n
    assert shared.read_bytes()[:size] == inline(pdf, 0, {"dpi": 100}).samples

    small = render(documents, render_order({**order, "dpi": 20}))
    length = small.width * small.height * small.n
    assert shared.stat().st_size == size
    assert shared.read_bytes()[:length] == inline(pdf, 0, {"dpi": 20}).samples


def scan_pdf(path: Path) -> Path:
    """One page carrying one image, the way a scan does (42 KB)."""
    height, width = 566, 400
    y, x = numpy.mgrid[0:height, 0:width]
    band = (x * 7 + y * 3) % 256
    pixels = numpy.stack([band, (band * 3) % 256, 255 - band], -1).astype(numpy.uint8)
    pixels[numpy.random.default_rng(1).random((height, width)) < 0.05] = 0
    with pymupdf.open() as document:
        page = document.new_page(width=579.6, height=820.08)
        picture = pymupdf.Pixmap(pymupdf.csRGB, width, height, pixels.tobytes(), False)
        page.insert_image(page.rect, pixmap=picture)
        document.save(path, deflate=True)
    return path


#: A whole-page render at the scan's native resolution, then a strip of it
#: at three quarters of that: the order that diverged on the EDF bill.
NATIVE = 400 / 579.6
STRIP = [0, 39.5, 108.8, 51.1]


def test_a_handle_is_one_open_document_and_a_path_a_fresh_copy(tmp_path: Path) -> None:
    """MuPDF reuses, within ONE open document, the image it decoded for an
    earlier render: the strip drawn after the whole page differs from the
    strip drawn on a fresh copy. The engine reproduces each, as asked."""
    pdf = scan_pdf(tmp_path / "scan.pdf")
    with pymupdf.open(pdf) as document:
        document[0].get_pixmap(matrix=pymupdf.Matrix(NATIVE, NATIVE))
        reused = document[0].get_pixmap(
            matrix=pymupdf.Matrix(0.75 * NATIVE, 0.75 * NATIVE), clip=pymupdf.Rect(*STRIP)
        ).samples
    fresh = inline(pdf, 0, {"matrix": [0.75 * NATIVE, 0, 0, 0.75 * NATIVE, 0, 0], "clip": STRIP})
    assert reused != fresh.samples, "the bench no longer shows MuPDF's reuse"
    documents = Documents()
    handle, _ = documents.open(str(pdf))
    whole = {"matrix": [NATIVE, 0, 0, NATIVE, 0, 0]}
    strip = {"matrix": [0.75 * NATIVE, 0, 0, 0.75 * NATIVE, 0, 0], "clip": STRIP}

    render(documents, render_order({"document": {"handle": handle}, "page": 0, **whole}))
    held = render(documents, render_order({"document": {"handle": handle}, "page": 0, **strip}))
    render(documents, render_order({"document": {"path": str(pdf)}, "page": 0, **whole}))
    by_path = render(documents, render_order({"document": {"path": str(pdf)}, "page": 0, **strip}))

    assert held.body == reused
    assert by_path.body == fresh.samples


def test_a_path_is_read_as_the_file_is_now(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    documents = Documents()
    order = render_order({"document": {"path": str(pdf)}, "page": 0, "dpi": 30})
    make_pdf(pdf, pages=1)
    before = render(documents, order).body

    rich_pdf(pdf)
    after = render(documents, order).body

    assert after != before
    assert after == inline(pdf, 0, {"dpi": 30}).samples


def test_the_engine_never_holds_the_file_even_while_a_handle_is_open(tmp_path: Path) -> None:
    """On Windows, an open file handle forbids the client to delete its own file."""
    pdf = make_pdf(tmp_path / "doc.pdf", pages=1)
    documents = Documents()
    handle, _ = documents.open(str(pdf))
    render(documents, render_order({"document": {"handle": handle}, "page": 0}))

    pdf.unlink()

    assert not pdf.exists()
    assert render(documents, render_order({"document": {"handle": handle}, "page": 0})).body


def test_handles_past_capacity_close_the_least_recently_used(tmp_path: Path) -> None:
    documents = Documents(capacity=2)
    pdf = str(make_pdf(tmp_path / "doc.pdf", pages=1))
    first, _ = documents.open(pdf)
    second, _ = documents.open(pdf)
    render(documents, render_order({"document": {"handle": first}, "page": 0}))
    documents.open(pdf)

    with pytest.raises(OrderError) as gone:
        render(documents, render_order({"document": {"handle": second}, "page": 0}))
    assert gone.value.code == "unknown_handle"
    assert render(documents, render_order({"document": {"handle": first}, "page": 0})).body


def test_a_closed_handle_is_unknown(tmp_path: Path) -> None:
    documents = Documents()
    handle, _ = documents.open(str(make_pdf(tmp_path / "doc.pdf", pages=1)))

    assert documents.close(handle) is True
    assert documents.close(handle) is False
    with pytest.raises(OrderError) as gone:
        render(documents, render_order({"document": {"handle": handle}, "page": 0}))
    assert gone.value.code == "unknown_handle"


def test_a_page_beyond_the_document_says_how_many_there_are(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "doc.pdf", pages=2)

    with pytest.raises(OrderError) as refused:
        render(Documents(), render_order({"document": {"path": str(pdf)}, "page": 2}))

    assert refused.value.code == "page_out_of_range"
    assert refused.value.detail == {"page_count": 2}


def test_a_missing_or_unreadable_file_is_named(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf at all")

    for path, code in ((tmp_path / "absent.pdf", "not_found"), (junk, "unreadable")):
        with pytest.raises(OrderError) as refused:
            render(Documents(), render_order({"document": {"path": str(path)}, "page": 0}))
        assert refused.value.code == code


# -- over HTTP ------------------------------------------------------------------


#: One kept-open connection per test server, as the real client keeps one.
#: Closing the connection after every answer is not how the engine is used,
#: and on this machine a loopback connection closed by the server is cut
#: short now and then (measured: 2 to 8 in 40, a bare stdlib server
#: included) — keep-alive: 0 in 200.
_CONNECTIONS: dict[str, http.client.HTTPConnection] = {}


@pytest.fixture
def served() -> Iterator[str]:
    server = _Server(("127.0.0.1", 0), Engine())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    for connection in _CONNECTIONS.values():
        connection.close()
    _CONNECTIONS.clear()
    server.shutdown()
    server.server_close()


def _call(url: str, method: str, body: bytes | None) -> tuple[int, dict[str, str], bytes]:
    parts = urllib.parse.urlsplit(url)
    connection = _CONNECTIONS.setdefault(
        parts.netloc, http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    )
    connection.request(method, parts.path, body, {"Content-Type": "application/json"})
    answer = connection.getresponse()
    return answer.status, dict(answer.headers), answer.read()


def _post(url: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
    return _call(url, "POST", body)


def test_about_names_the_engine_its_protocol_and_its_licence(served: str) -> None:
    about = json.loads(_call(f"{served}/v1/about", "GET", None)[2])

    assert about["protocol"] == PROTOCOL_VERSION
    assert about["license"] == "AGPL-3.0-or-later"
    assert about["backend"]["name"] == "PyMuPDF"
    assert "render" in about["orders"]


def test_a_render_over_http_carries_its_shape_in_headers(served: str, tmp_path: Path) -> None:
    pdf = rich_pdf(tmp_path / "rich.pdf")
    order = {"document": {"path": str(pdf)}, "page": 0, "dpi": 40, "colorspace": "gray"}

    status, headers, body = _post(f"{served}/v1/render", json.dumps(order).encode())

    expected = inline(pdf, 0, {"dpi": 40, "colorspace": "gray"})
    assert status == 200
    assert (headers["X-Raster-Width"], headers["X-Raster-Height"], headers["X-Raster-N"]) == (
        str(expected.width),
        str(expected.height),
        "1",
    )
    assert body == expected.samples


def test_refusals_over_http_are_structured(served: str, tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "doc.pdf", pages=1)
    beyond = json.dumps({"document": {"path": str(pdf)}, "page": 5}).encode()
    cases = [
        ("/v1/render", b"{not json", 400, "bad_order"),
        ("/v1/render", beyond, 422, "page_out_of_range"),
        ("/v1/nothing", b"{}", 404, "unknown_order"),
    ]
    for path, body, status, code in cases:
        got, _, raw = _post(f"{served}{path}", body)
        assert got == status
        assert json.loads(raw)["error"]["code"] == code


def test_open_render_close_over_http(served: str, tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "doc.pdf", pages=3)

    status, _, raw = _post(
        f"{served}/v1/open", json.dumps({"document": {"path": str(pdf)}}).encode()
    )
    opened = json.loads(raw)
    order = {"document": {"handle": opened["handle"]}, "page": 2, "dpi": 20}
    rendered, _, body = _post(f"{served}/v1/render", json.dumps(order).encode())
    _, _, closed = _post(
        f"{served}/v1/close", json.dumps({"handle": opened["handle"]}).encode()
    )
    after, _, refused = _post(f"{served}/v1/render", json.dumps(order).encode())

    assert (status, opened["page_count"]) == (200, 3)
    assert (rendered, body) == (200, inline(pdf, 2, {"dpi": 20}).samples)
    assert json.loads(closed) == {"closed": True}
    assert (after, json.loads(refused)["error"]["code"]) == (404, "unknown_handle")
