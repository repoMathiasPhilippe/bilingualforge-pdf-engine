"""The engine over a pair of pipes: HTTP's answers, without the socket (ADR-405)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_engine import PROTOCOL_VERSION, documents
from pdf_engine.server import ORDERS, Engine, read_frame, respond, write_frame

ROOT = Path(__file__).resolve().parents[1]


def malformed_pdf(path: Path) -> Path:
    """A page whose content stream makes MuPDF print errors — on stdout.

    The errors name what the FILE says (``Im9``): a hostile document chooses
    the text MuPDF prints.
    """
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Bonjour")
        document.update_stream(
            page.get_contents()[0],
            b"BT /F0 12 Tf 72 72 Td (x) Tj ET q /Im9 Do Q (((( ] ] } zzz Q Q",
        )
        document.save(path)
    return path


class _Pipe:
    """``python -m pdf_engine --pipe``, as its client drives it."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(  # noqa: S603 - our interpreter, our module
            [sys.executable, "-m", "pdf_engine", "--pipe"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self.process.stdout is not None
        self.hello, _ = read_frame(self.process.stdout)

    def ask(self, method: str, path: str, order: Any = None) -> tuple[dict[str, Any], bytes]:
        """One order. A corrupted frame announces lengths nothing will fill:
        it is read within a deadline, so the test fails instead of hanging."""
        assert self.process.stdin is not None and self.process.stdout is not None
        body = b"" if order is None else json.dumps(order).encode("utf-8")
        write_frame(self.process.stdin, {"method": method, "path": path}, body)
        stdout = self.process.stdout
        result: list[Any] = []

        def read() -> None:
            try:
                result.append(read_frame(stdout))
            except (EOFError, ValueError) as error:
                result.append(error)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(30)
        if reader.is_alive():
            self.process.kill()
            pytest.fail("trame illisible : aucune réponse complète en 30 s")
        if isinstance(result[0], Exception):
            pytest.fail(f"trame illisible : {result[0]!r}")
        answer: tuple[dict[str, Any], bytes] = result[0]
        return answer

    def close(self) -> bytes:
        assert self.process.stdin is not None and self.process.stderr is not None
        self.process.stdin.close()
        self.process.wait(timeout=30)
        return self.process.stderr.read()


@pytest.fixture
def pipe() -> Iterator[_Pipe]:
    engine = _Pipe()
    yield engine
    engine.close()


def test_the_first_frame_says_the_engine_is_ready(pipe: _Pipe) -> None:
    assert pipe.hello == {"ready": True, "protocol": PROTOCOL_VERSION}


def test_every_answer_is_the_one_http_gives(pipe: _Pipe, tmp_path: Path) -> None:
    """Une seule fonction répond, quel que soit le transport : les tubes
    rendent octet pour octet ce que rend ``respond``, que sert aussi HTTP."""
    pdf = tmp_path / "doc.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 100), "Le chiffre d'affaires atteint 1 234,56 €")
        document.save(pdf)
    here = Engine()
    for method, path, order in (
        ("GET", "/v1/about", None),
        ("POST", "/v1/describe", {"document": {"path": str(pdf)}}),
        ("POST", "/v1/text", {"document": {"path": str(pdf)}, "page": 0, "kind": "dict"}),
        ("POST", "/v1/render", {"document": {"path": str(pdf)}, "page": 0, "dpi": 100}),
        ("POST", "/v1/nothing", {}),
    ):
        header, body = pipe.ask(method, path, order)
        raw = b"" if order is None else json.dumps(order).encode("utf-8")
        reply = respond(here, method, path, raw)
        assert (header["status"], header["type"], header["headers"], body) == (
            reply.status, reply.kind, reply.headers, reply.body
        ), path


def test_what_mupdf_prints_never_reaches_the_frames(pipe: _Pipe, tmp_path: Path) -> None:
    """MuPDF écrit ses erreurs sur stdout, avec des mots choisis par le
    fichier : sans le déplacement de stdout, elles se glisseraient entre deux
    trames. Elles vont sur stderr, et le protocole reste intact."""
    pdf = malformed_pdf(tmp_path / "malformed.pdf")
    order = {"document": {"path": str(pdf)}, "page": 0, "dpi": 72}
    first, _ = pipe.ask("POST", "/v1/render", order)
    second, _ = pipe.ask("POST", "/v1/describe", {"document": {"path": str(pdf)}})
    assert first["status"] == 200 and second["status"] == 200
    assert b"cannot find XObject resource 'Im9'" in pipe.close()


def test_a_file_the_engine_may_not_read_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hors des droits d'un bac à sable, le fichier existe : le moteur dit
    « refusé », jamais « introuvable », pour que le correctif soit le bon."""
    def denied(self: Path) -> bytes:
        raise PermissionError(13, "Accès refusé", str(self))

    monkeypatch.setattr(Path, "read_bytes", denied)
    reply = respond(Engine(), "POST", "/v1/describe",
                    json.dumps({"document": {"path": str(tmp_path / "a.pdf")}}).encode())
    assert reply.status == 403
    assert json.loads(reply.body)["error"]["code"] == "forbidden"
    with pytest.raises(documents.OrderError, match="refusé"):
        documents.read(str(tmp_path / "a.pdf"))


def test_a_write_the_engine_may_not_make_is_refused_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(engine: Engine, payload: Any) -> dict[str, Any]:
        raise PermissionError(13, "Accès refusé", "C:/ailleurs/out.pdf")

    monkeypatch.setitem(ORDERS, "save", denied)
    reply = respond(Engine(), "POST", "/v1/save", b"{}")
    assert reply.status == 403
    assert json.loads(reply.body)["error"]["code"] == "forbidden"
