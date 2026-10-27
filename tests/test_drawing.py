"""Drawing and flowing through the engine (protocol 1.8, ADR-388 step 7).

The seal's circles, the reader's note rules, the glyph canvas of the mute
zones and the IFC note's flowed table, each made twice: in process, and as
engine orders. « The same » is the same structure, pixels, words and drawings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_engine import editing, writing
from pdf_engine.documents import Documents
from pdf_engine.protocol import OrderError, save_order, story_order, write_order
from tests.test_editing import structure
from tests.test_render import rich_pdf

_BLUE = [0.13, 0.20, 0.62]


def _drawn(path: Path) -> list[Any]:
    with pymupdf.open(path) as document:
        return [
            (page.get_text("words"), [
                (d.get("type"), tuple(d["rect"]), d.get("color"), d.get("fill"),
                 d.get("even_odd"), len(d["items"]))
                for d in page.get_drawings()
            ])
            for page in document
        ]


def _paths_of(page: pymupdf.Page) -> list[dict[str, Any]]:
    """A page's drawings as ``draw_paths`` takes them, filled black as the canvas does."""
    paths = []
    for drawing in page.get_drawings():
        items = []
        for item in drawing["items"]:
            if item[0] == "re":
                items.append(["re", list(item[1])])
            elif item[0] == "qu":
                items.append(["qu", *([p.x, p.y] for p in item[1])])
            else:
                items.append([item[0], *([p.x, p.y] for p in item[1:])])
        paths.append({"items": items, "finish": {
            "fill": [0, 0, 0], "color": None,
            "even_odd": bool(drawing.get("even_odd")),
            "closePath": bool(drawing.get("closePath", True)),
        }})
    return paths


def test_lines_circles_rounded_boxes_and_paths_are_mupdf_s_own(tmp_path: Path) -> None:
    """Le cercle du tampon, le filet de la fiche, la boîte arrondie du titre
    et le canevas des glyphes : chaque ordre fait ce que fait l'appel."""
    with pymupdf.open(rich_pdf(tmp_path / "rich.pdf")) as source:
        paths = _paths_of(source[0])
    steps: list[tuple[str, dict[str, Any]]] = [
        ("draw_circle", {"center": [150, 150], "radius": 54,
                         "options": {"color": _BLUE, "width": 1.6, "stroke_opacity": 0.85}}),
        ("draw_circle", {"center": [150, 150], "radius": 37,
                         "options": {"color": _BLUE, "width": 1.0}}),
        ("draw_line", {"p1": [72, 400], "p2": [520, 400],
                       "options": {"color": [0.5, 0.5, 0.5], "width": 0.7}}),
        ("draw_rect", {"rect": [110, 140, 190, 170],
                       "options": {"color": _BLUE, "width": 0.9, "radius": 0.28,
                                   "stroke_opacity": 0.85}}),
        ("draw_paths", {"paths": paths}),
    ]

    inline = tmp_path / "inline.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        for order, body in steps:
            options = {key: tuple(value) if isinstance(value, list) else value
                       for key, value in body.get("options", {}).items()}
            if order == "draw_circle":
                page.draw_circle(pymupdf.Point(*body["center"]), body["radius"], **options)
            elif order == "draw_line":
                page.draw_line(pymupdf.Point(*body["p1"]), pymupdf.Point(*body["p2"]),
                               **options)
            elif order == "draw_rect":
                page.draw_rect(pymupdf.Rect(*body["rect"]), **options)
            else:
                shape = page.new_shape()
                for path in body["paths"]:
                    for item in path["items"]:
                        if item[0] == "l":
                            shape.draw_line(item[1], item[2])
                        elif item[0] == "c":
                            shape.draw_bezier(item[1], item[2], item[3], item[4])
                        elif item[0] == "re":
                            shape.draw_rect(pymupdf.Rect(*item[1]))
                        else:
                            shape.draw_quad(pymupdf.Quad(*item[1:]))
                    finish = path["finish"]
                    shape.finish(fill=(0, 0, 0), color=None, even_odd=finish["even_odd"],
                                 closePath=finish["closePath"])
                shape.commit()
        document.save(inline, garbage=3, deflate=True)

    documents = Documents()
    handle, _ = documents.create()
    writing.new_page(documents, handle, 595, 842)
    for order, body in steps:
        writing.write(documents, write_order(order, {"document": {"handle": handle}, "page": 0,
                                                     **body}))
    engine = tmp_path / "engine.pdf"
    editing.save(documents, save_order({"document": {"handle": handle}, "path": str(engine),
                                        "garbage": 3, "deflate": True}))
    assert structure(engine) == structure(inline)
    assert _drawn(engine) == _drawn(inline)


def test_a_story_flows_as_mupdf_flows_it(tmp_path: Path) -> None:
    """La note IFC : un tableau HTML coulé sur autant de feuilles A4 paysage
    qu'il en faut — même nombre de pages, mêmes mots, mêmes pixels."""
    rows = "".join(f"<tr><td>Mur {n}</td><td>Niveau {n % 3}</td><td>Wall {n}</td></tr>"
                   for n in range(120))
    html = ("<h2>Note</h2><table border='1' cellpadding='3' "
            f"style='border-collapse:collapse;font-size:8pt'>{rows}</table>")
    sheet = pymupdf.paper_rect("a4-l")
    frame = sheet + (36, 36, -36, -36)

    inline = tmp_path / "inline.pdf"
    story = pymupdf.Story(html=html)
    writer = pymupdf.DocumentWriter(str(inline))
    more = True
    while more:
        device = writer.begin_page(sheet)
        more, _ = story.place(frame)
        story.draw(device)
        writer.end_page()
    writer.close()

    engine = tmp_path / "engine.pdf"
    answer = writing.story(story_order({"html": html, "path": str(engine),
                                        "paper": list(sheet), "frame": list(frame)}))
    assert answer["page_count"] == len(structure(inline)) > 1
    assert structure(engine) == structure(inline)
    assert _drawn(engine) == _drawn(inline)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("draw_line", {"p1": [0, 0]}),
        ("draw_circle", {"center": [0, 0], "radius": -1}),
        ("draw_paths", {"paths": [{"items": [["z", [0, 0]]]}]}),
        ("draw_paths", {"paths": [{"items": [["l", [0, 0]]]}]}),
        ("draw_paths", {"paths": [{"items": [], "finish": {"stroke": [0, 0, 0]}}]}),
        ("draw_rect", {"rect": [0, 0, 1, 1], "options": {"radius": [1]}}),
    ],
)
def test_a_malformed_drawing_is_refused(kind: str, payload: dict[str, Any]) -> None:
    with pytest.raises(OrderError):
        write_order(kind, {"document": {"handle": "h"}, "page": 0, **payload})


def test_a_story_needs_an_absolute_path() -> None:
    with pytest.raises(OrderError):
        story_order({"html": "<p>x</p>", "path": "relative.pdf",
                     "paper": [0, 0, 842, 595], "frame": [36, 36, 806, 559]})
