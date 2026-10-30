# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The engine's two faces: orders in JSON, results in JSON or raw bytes.

Over HTTP (``serve``), or over a pair of pipes (``serve_pipe``) — the same
replies, from the same function: a pipe is for an engine its client starts
in a sandbox that may not listen on a socket.

One order runs at a time. MuPDF is not thread-safe, and two simultaneous
entries into it freeze a process without an exception or a log; serializing
every order behind one lock is the engine's nature, not a setting.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, BinaryIO

import pymupdf

from pdf_engine import (
    ENGINE_NAME,
    ENGINE_VERSION,
    PROTOCOL_VERSION,
    cad,
    editing,
    fonts,
    layout,
    objects,
    reading,
    writing,
)
from pdf_engine.documents import Documents
from pdf_engine.protocol import (
    WRITE_KINDS,
    OrderError,
    annots_order,
    apply_order,
    close_order,
    cropbox_order,
    delete_page_order,
    describe_order,
    dxf_order,
    encode_order,
    extract_image_order,
    font_info_order,
    font_order,
    glyphs_order,
    held_order,
    image_info_order,
    image_rects_order,
    images_order,
    insert_order,
    labels_order,
    layout_detect_order,
    layout_load_order,
    metadata_order,
    new_page_order,
    open_order,
    page_order,
    picture_order,
    reading_copy_order,
    redact_order,
    render_order,
    rotation_order,
    save_order,
    search_order,
    story_order,
    text_length_order,
    text_order,
    textbox_order,
    write_order,
    xref_key_order,
)
from pdf_engine.render import render

#: Where the engine's code can be had, as the AGPL asks of a network service:
#: this repository, unless whoever deploys a modified engine names its own
#: (``PDF_ENGINE_SOURCE``).
SOURCE = os.environ.get(
    "PDF_ENGINE_SOURCE", "https://github.com/repoMathiasPhilippe/bilingualforge-pdf-engine"
)

#: Socket buffer, in bytes: a 400 dpi page is ~46 MB of samples.
SOCKET_BUFFER = 8 << 20


def backend() -> dict[str, str]:
    """The library the engine runs on: its versions, and where it was loaded from.

    The client asks rather than importing it: the closed code never loads
    the AGPL library, not even to read a version number (ADR-409).
    """
    return {
        "name": "PyMuPDF",
        "version": pymupdf.__version__,
        "mupdf": str(pymupdf.mupdf_version),
        "path": pymupdf.__file__,
    }


def about() -> dict[str, Any]:
    """What ``GET /v1/about`` answers: who the engine is and what it speaks."""
    library = backend()
    return {
        "engine": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "protocol": PROTOCOL_VERSION,
        "backend": {key: library[key] for key in ("name", "version", "mupdf")},
        "license": "AGPL-3.0-or-later",
        "source": SOURCE,
        "orders": sorted([*ORDERS, "render"]),
    }


def _open(engine: Engine, payload: Any) -> dict[str, Any]:
    handle, document = engine.documents.open(*open_order(payload))
    return {"handle": handle, "page_count": document.page_count}


def _create(engine: Engine, payload: Any) -> dict[str, Any]:
    handle, _ = engine.documents.create()
    return {"handle": handle, "page_count": 0}


def _close(engine: Engine, payload: Any) -> dict[str, Any]:
    return {"closed": engine.documents.close(close_order(payload))}


def _describe(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.describe(engine.documents, *describe_order(payload))


def _insert(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.insert_pages(engine.documents, insert_order(payload))


def _cropbox(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.set_cropbox(engine.documents, *cropbox_order(payload))


def _rotation(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.set_rotation(engine.documents, *rotation_order(payload))


def _save(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.save(engine.documents, save_order(payload))


def _reading_copy(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.reading_copy(engine.documents, reading_copy_order(payload))


def _text(engine: Engine, payload: Any) -> dict[str, Any]:
    return reading.text(engine.documents, text_order(payload))


def _texttrace(engine: Engine, payload: Any) -> dict[str, Any]:
    return reading.texttrace(engine.documents, *page_order(payload))


def _search(engine: Engine, payload: Any) -> dict[str, Any]:
    return reading.search(engine.documents, *search_order(payload))


def _textbox(engine: Engine, payload: Any) -> dict[str, Any]:
    return reading.textbox(engine.documents, *textbox_order(payload))


def _fonts(engine: Engine, payload: Any) -> dict[str, Any]:
    return reading.fonts(engine.documents, *page_order(payload))


def _extract_font(engine: Engine, payload: Any) -> dict[str, Any]:
    return reading.extract_font(engine.documents, *font_order(payload))


def _drawings(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.drawings(engine.documents, *page_order(payload))


def _images(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.images(engine.documents, *images_order(payload))


def _image_rects(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.image_rects(engine.documents, *image_rects_order(payload))


def _image_info(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.image_info(engine.documents, *image_info_order(payload))


def _extract_image(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.extract_image(engine.documents, *extract_image_order(payload))


def _xref_key(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.xref_key(engine.documents, *xref_key_order(payload))


def _links(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.links(engine.documents, *page_order(payload))


def _annots(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.annots(engine.documents, *annots_order(payload))


def _picture(engine: Engine, payload: Any) -> dict[str, Any]:
    return objects.picture(picture_order(payload))


def _redact(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.redact(engine.documents, redact_order(payload))


def _subset_fonts(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.subset_fonts(engine.documents, held_order(payload))


def _font_info(engine: Engine, payload: Any) -> dict[str, Any]:
    spec, buffer = font_info_order(payload)
    return fonts.info(engine.faces.get(engine.documents, spec), buffer)


def _text_length(engine: Engine, payload: Any) -> dict[str, Any]:
    spec, fontname, texts, size = text_length_order(payload)
    if spec is None:
        return {"lengths": fonts.named_lengths(str(fontname), texts, size)}
    return {"lengths": fonts.text_lengths(engine.faces.get(engine.documents, spec), texts, size)}


def _glyphs(engine: Engine, payload: Any) -> dict[str, Any]:
    spec, codes = glyphs_order(payload)
    return fonts.glyphs(engine.faces.get(engine.documents, spec), codes)


def _writer(kind: str) -> Callable[[Engine, Any], dict[str, Any]]:
    def order(engine: Engine, payload: Any) -> dict[str, Any]:
        return writing.write(engine.documents, write_order(kind, payload))

    return order


def _new_page(engine: Engine, payload: Any) -> dict[str, Any]:
    return writing.new_page(engine.documents, *new_page_order(payload))


def _apply_redactions(engine: Engine, payload: Any) -> dict[str, Any]:
    return writing.apply_redactions(engine.documents, *apply_order(payload))


def _set_metadata(engine: Engine, payload: Any) -> dict[str, Any]:
    return writing.set_metadata(engine.documents, *metadata_order(payload))


def _del_xml_metadata(engine: Engine, payload: Any) -> dict[str, Any]:
    return writing.del_xml_metadata(engine.documents, held_order(payload))


def _delete_page(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.delete_page(engine.documents, *delete_page_order(payload))


def _set_page_labels(engine: Engine, payload: Any) -> dict[str, Any]:
    return editing.set_page_labels(engine.documents, *labels_order(payload))


def _encode_image(engine: Engine, payload: Any) -> dict[str, Any]:
    return writing.encode_image(encode_order(payload))


def _story(engine: Engine, payload: Any) -> dict[str, Any]:
    return writing.story(story_order(payload))


def _dxf_pdf(engine: Engine, payload: Any) -> dict[str, Any]:
    return cad.draw(dxf_order(payload))


def _layout_load(engine: Engine, payload: Any) -> dict[str, Any]:
    return engine.detector.load(layout_load_order(payload).gpu)


def _layout_detect(engine: Engine, payload: Any) -> dict[str, Any]:
    return engine.detector.detect(layout_detect_order(payload))


#: The orders answered in JSON, by name (``POST /v1/<name>``).
ORDERS: dict[str, Callable[[Engine, Any], dict[str, Any]]] = {
    "open": _open,
    "create": _create,
    "close": _close,
    "describe": _describe,
    "insert_pages": _insert,
    "set_cropbox": _cropbox,
    "set_rotation": _rotation,
    "save": _save,
    "reading_copy": _reading_copy,
    "text": _text,
    "texttrace": _texttrace,
    "search": _search,
    "textbox": _textbox,
    "fonts": _fonts,
    "extract_font": _extract_font,
    "drawings": _drawings,
    "images": _images,
    "image_rects": _image_rects,
    "image_info": _image_info,
    "extract_image": _extract_image,
    "xref_key": _xref_key,
    "links": _links,
    "annots": _annots,
    "picture": _picture,
    "redact": _redact,
    "subset_fonts": _subset_fonts,
    "font_info": _font_info,
    "text_length": _text_length,
    "glyphs": _glyphs,
    **{kind: _writer(kind) for kind in WRITE_KINDS},
    "new_page": _new_page,
    "apply_redactions": _apply_redactions,
    "set_metadata": _set_metadata,
    "del_xml_metadata": _del_xml_metadata,
    "delete_page": _delete_page,
    "set_page_labels": _set_page_labels,
    "encode_image": _encode_image,
    "story": _story,
    "dxf_pdf": _dxf_pdf,
    "layout_load": _layout_load,
    "layout_detect": _layout_detect,
}


class Engine:
    """The state behind the server: the open documents and the one lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.documents = Documents()
        self.faces = fonts.Faces()
        self.detector = layout.Detector()


@dataclass(frozen=True)
class Reply:
    """One answer, whatever carries it: a status, a media type, a body."""

    status: int
    kind: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


def _json_reply(status: int, payload: dict[str, Any]) -> Reply:
    return Reply(status, "application/json", json.dumps(payload).encode("utf-8"))


def _error_reply(error: OrderError) -> Reply:
    return _json_reply(
        error.status,
        {"error": {"code": error.code, "message": error.message, "detail": error.detail}},
    )


def respond(engine: Engine, method: str, path: str, raw: bytes) -> Reply:
    """The answer to one order — the same over HTTP and over a pipe.

    Args:
        engine: The open documents and the lock.
        method: ``GET`` or ``POST``.
        path: ``/v1/<order>``.
        raw: The request's body, JSON.

    Returns:
        The reply; a refusal is a reply too, never an exception.
    """
    if method == "GET":
        if path == "/v1/about":
            return _json_reply(200, about())
        return _error_reply(OrderError("unknown_order", f"Inconnu : GET {path}", 404))
    name = path.removeprefix("/v1/")
    if name != "render" and name not in ORDERS:
        return _error_reply(OrderError("unknown_order", f"Inconnu : POST {path}", 404))
    try:
        payload = json.loads(raw or b"null")
        if name in ORDERS:
            with engine.lock:
                answer = ORDERS[name](engine, payload)
            return _json_reply(200, answer)
        order = render_order(payload)
        with engine.lock:
            done = render(engine.documents, order)
    except OrderError as error:
        return _error_reply(error)
    except PermissionError as error:
        # A file the engine may not touch — outside a sandbox's grants.
        return _error_reply(OrderError("forbidden", f"Accès refusé : {error}", 403))
    except ValueError:
        return _error_reply(OrderError("bad_order", "Le corps n'est pas du JSON."))
    shape = {"width": done.width, "height": done.height, "n": done.n, "x": done.x, "y": done.y}
    if done.path is not None:
        return _json_reply(200, {**shape, "path": done.path})
    kind = "image/png" if order.output.kind == "png" else "application/octet-stream"
    headers = {f"X-Raster-{key.title()}": str(value) for key, value in shape.items()}
    return Reply(200, kind, done.body, headers)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def setup(self) -> None:
        """No Nagle delay, and room for a whole raster in the socket buffer.

        Headers and body leave in two writes: with Nagle's algorithm, the
        second waits for the client's delayed acknowledgement — 10 to 40 ms
        an order on Windows loopback, for nothing.
        """
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib name
        """Silent: the client logs what matters, and stdout is the ready line's."""

    def _send(self, reply: Reply) -> None:
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.kind)
        self.send_header("Content-Length", str(len(reply.body)))
        for name, value in reply.headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(reply.body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        self._send(respond(self.server.engine, "GET", self.path, b""))

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        length = int(self.headers.get("Content-Length") or 0)
        self._send(respond(self.server.engine, "POST", self.path, self.rfile.read(length)))


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that hangs up is not an engine fault; anything else is told."""
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)

    def __init__(self, address: tuple[str, int], engine: Engine) -> None:
        super().__init__(address, _Handler)
        self.engine = engine


def serve(host: str, port: int, *, watch_stdin: bool = False) -> None:
    """Serve until stopped; with ``watch_stdin``, until the parent closes stdin.

    The first line written on stdout is ``{"ready": true, "port": N, ...}``
    once the socket listens — ``port`` 0 lets the system choose, and the
    line says which.
    """
    server = _Server((host, port), Engine())
    ready = {"ready": True, "port": server.server_address[1], "protocol": PROTOCOL_VERSION}
    sys.stdout.write(json.dumps(ready) + "\n")
    sys.stdout.flush()
    if watch_stdin:
        # The parent holds our stdin: when it dies, the pipe closes, and an
        # engine nobody can reach any more stops instead of lingering.
        #
        # The watch reads a PRIVATE copy, and the process's own stdin becomes
        # the null device: on Windows, a synchronous read pending on a pipe
        # blocks every other call on that handle, and a library that merely
        # asks stdin a question (ezdxf's import chain does) froze the order
        # that imported it — forever, without an error.
        watched = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, sys.stdin.fileno())
        os.close(null)
        sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - the process's own stdin

        def watch() -> None:
            watched.read()
            server.shutdown()

        threading.Thread(target=watch, daemon=True, name="stdin-watch").start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        with server.engine.lock:
            server.engine.documents.close_all()


#: A frame's head: the lengths of its JSON header and of its body.
FRAME = struct.Struct("<II")


def _exactly(stream: BinaryIO, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return bytes(data)


def write_frame(stream: BinaryIO, header: dict[str, Any], body: bytes = b"") -> None:
    """Write one frame: :data:`FRAME`, the JSON header, then the body."""
    text = json.dumps(header).encode("utf-8")
    stream.write(FRAME.pack(len(text), len(body)) + text)
    if body:
        stream.write(body)
    stream.flush()


def read_frame(stream: BinaryIO) -> tuple[dict[str, Any], bytes]:
    """Read one frame.

    Raises:
        EOFError: The other side closed the pipe.
    """
    size, length = FRAME.unpack(_exactly(stream, FRAME.size))
    header: dict[str, Any] = json.loads(_exactly(stream, size))
    return header, _exactly(stream, length) if length else b""


def serve_pipe(hello: dict[str, Any] | None = None) -> None:
    """Serve frames on stdin and stdout until stdin closes.

    A request is the header ``{"method", "path"}`` with the order's JSON as
    body; the reply is ``{"status", "type", "headers"}`` with the reply's
    body — HTTP's semantics, without the socket. The first frame written is
    ``{"ready": true, "protocol": …}``, merged with ``hello``.

    The pipe is the protocol's alone: before the first frame, the process's
    standard output is moved onto its standard error, so a warning MuPDF
    prints from C cannot slip a byte into a frame.
    """
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    inp = sys.stdin.buffer
    engine = Engine()
    write_frame(out, {"ready": True, "protocol": PROTOCOL_VERSION, **(hello or {})})
    try:
        while True:
            try:
                request, body = read_frame(inp)
            except EOFError:
                return
            reply = respond(
                engine, str(request.get("method", "POST")), str(request.get("path", "")), body
            )
            write_frame(
                out, {"status": reply.status, "type": reply.kind, "headers": reply.headers},
                reply.body,
            )
    finally:
        with engine.lock:
            engine.documents.close_all()
