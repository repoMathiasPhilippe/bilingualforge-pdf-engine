"""A DXF's layouts drawn by the engine (protocol 1.8, ADR-388 step 7)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_engine import cad
from pdf_engine.protocol import OrderError, dxf_order
from tests.test_editing import structure

ezdxf = pytest.importorskip("ezdxf")

ROOT = Path(__file__).resolve().parents[1]


def _plan(path: Path) -> Path:
    from tests.dxf_fixture import plan

    return plan(path)


def _inline(source: Path, target: Path, sheets: list[dict[str, Any]]) -> list[bool]:
    """What ``dxf_codec`` did in process before the engine drew it."""
    from ezdxf import recover
    from ezdxf.addons.drawing import config, layout
    from ezdxf.addons.drawing.frontend import Frontend
    from ezdxf.addons.drawing.properties import RenderContext
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    from ezdxf.math import BoundingBox2d

    doc, _ = recover.readfile(str(source))
    settings = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                                    color_policy=config.ColorPolicy.BLACK)
    drawn = []
    with pymupdf.open() as merged:
        for sheet in sheets:
            target_layout = (doc.modelspace() if sheet["layout"] == "Model"
                             else doc.layouts.get(sheet["layout"]))
            backend = PyMuPdfBackend()
            Frontend(RenderContext(doc), backend, config=settings).draw_layout(target_layout)
            paper = sheet["page"]
            page = (layout.Page.from_dxf_layout(target_layout) if paper is None else
                    layout.Page(paper["width"], paper["height"], layout.Units.mm,
                                layout.Margins.all(paper["margins"])))
            box = sheet["render_box"]
            render_box = None if box is None else BoundingBox2d([box[:2], box[2:]])
            try:
                one = bytes(backend.get_pdf_bytes(page, render_box=render_box))
            except ValueError:
                drawn.append(False)
                continue
            with pymupdf.open("pdf", one) as single:
                merged.insert_pdf(single)
            drawn.append(True)
        merged.save(target, garbage=3, deflate=True)
    return drawn


@pytest.mark.parametrize("sheets", [
    [{"layout": "Plan RDC", "page": None, "render_box": None}],
    [{"layout": "Model", "page": {"width": 420, "height": 297, "units": "mm", "margins": 10},
      "render_box": None}],
    [{"layout": "Model", "page": {"width": 420, "height": 297, "units": "mm", "margins": 10},
      "render_box": [0, 0, 5000, 3500]},
     {"layout": "Plan RDC", "page": None, "render_box": None}],
])
def test_a_layout_is_drawn_as_ezdxf_draws_it(tmp_path: Path, sheets: list[dict[str, Any]]) -> None:
    """Même dessin, octet de pixel pour octet, que l'appel en processus."""
    source = _plan(tmp_path / "plan.dxf")
    expected = _inline(source, tmp_path / "inline.pdf", sheets)
    answer = cad.draw(dxf_order({"source": str(source), "path": str(tmp_path / "engine.pdf"),
                                 "sheets": sheets}))
    assert answer["drawn"] == expected
    assert structure(tmp_path / "engine.pdf") == structure(tmp_path / "inline.pdf")


def test_the_child_engine_answers_while_it_watches_its_stdin(tmp_path: Path) -> None:
    """Le moteur enfant surveille son stdin pour mourir avec son parent. Une
    lecture en attente sur un tube bloque, sous Windows, tout autre appel sur
    la même poignée : la chaîne d'import d'ezdxf en fait un, et l'ordre gelait
    sans fin. Il répond, puis s'arrête quand le parent ferme le tube."""
    import httpx

    source = _plan(tmp_path / "plan.dxf")
    engine = subprocess.Popen(  # noqa: S603 - our interpreter, our module
        [sys.executable, "-m", "pdf_engine", "--port", "0", "--watch-stdin"],
        cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    try:
        assert engine.stdout is not None and engine.stdin is not None
        port = json.loads(engine.stdout.readline())["port"]
        order = {"source": str(source), "path": str(tmp_path / "child.pdf"),
                 "sheets": [{"layout": "Plan RDC", "page": None, "render_box": None}]}
        try:
            answer = httpx.post(f"http://127.0.0.1:{port}/v1/dxf_pdf", json=order,
                                timeout=30, trust_env=False).json()
        except httpx.TimeoutException:
            pytest.fail("le moteur enfant a gelé sur un ordre DXF")
        assert answer["drawn"] == [True]
        engine.stdin.close()
        assert engine.wait(timeout=20) == 0
    finally:
        engine.kill()


@pytest.mark.parametrize("payload", [
    {"sheets": []},
    {"sheets": [{"layout": 3}]},
    {"sheets": [{"layout": "A", "page": {"width": 1}}]},
    {"sheets": [{"layout": "A"}], "background": "white"},
    {"sheets": [{"layout": "A", "render_box": [0, 0]}]},
])
def test_a_malformed_dxf_order_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(OrderError):
        dxf_order({"source": "C:/a.dxf", "path": "C:/a.pdf", **payload})


def test_a_file_that_is_not_a_dxf_is_unreadable(tmp_path: Path) -> None:
    bad = tmp_path / "x.dxf"
    bad.write_bytes(b"PK\x03\x04 not a drawing")
    with pytest.raises(OrderError) as raised:
        cad.draw(dxf_order({"source": str(bad), "path": str(tmp_path / "x.pdf"),
                            "sheets": [{"layout": "Model"}]}))
    assert raised.value.code == "unreadable"
