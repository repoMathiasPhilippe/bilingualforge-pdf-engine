# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A DXF's layouts drawn as PDF pages: ezdxf's drawing add-on, on PyMuPDF (1.8).

The client names each layout to draw, its paper and the part of the drawing
to show; the engine reads the file, draws exactly that, and says which
layouts showed something. Which layouts deserve printing, at what size, and
around which view, is the client's decision.
"""

from __future__ import annotations

from typing import Any

import pymupdf

from pdf_engine.protocol import DxfOrder, OrderError


def draw(order: DxfOrder) -> dict[str, Any]:
    """Draw ``order.sheets`` of the DXF at ``order.source`` into ``order.path``.

    Returns:
        ``{"drawn": [bool per sheet], "page_count": N}``; no file is written
        when nothing was drawn.

    Raises:
        OrderError: ``unavailable`` without ezdxf, ``forbidden``, ``not_found``,
            ``unreadable`` for a file ezdxf cannot recover, ``edit_failed``
            for a drawing that fails.
    """
    try:
        from ezdxf import recover  # noqa: PLC0415 - optional: only this order needs it
        from ezdxf.addons.drawing import config, layout  # noqa: PLC0415
        from ezdxf.addons.drawing.frontend import Frontend  # noqa: PLC0415
        from ezdxf.addons.drawing.properties import RenderContext  # noqa: PLC0415
        from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend  # noqa: PLC0415
        from ezdxf.lldxf.const import DXFStructureError  # noqa: PLC0415
        from ezdxf.math import BoundingBox2d  # noqa: PLC0415
    except ImportError as exc:
        raise OrderError("unavailable", f"ezdxf absent du moteur : {exc}", 501) from exc
    try:
        doc, _auditor = recover.readfile(order.source)
    except PermissionError as exc:
        raise OrderError("forbidden", f"Accès refusé : {order.source}", 403) from exc
    except FileNotFoundError as exc:
        raise OrderError("not_found", f"Fichier introuvable : {order.source}", 404) from exc
    except (OSError, DXFStructureError) as exc:
        raise OrderError("unreadable", f"DXF illisible : {order.source} ({exc})", 422) from exc
    drawn: list[bool] = []
    try:
        settings = config.Configuration(
            background_policy=config.BackgroundPolicy[order.background],
            color_policy=config.ColorPolicy[order.color],
        )
        with pymupdf.open() as merged:
            for sheet in order.sheets:
                name = sheet["layout"]
                target = doc.modelspace() if name == "Model" else doc.layouts.get(name)
                backend = PyMuPdfBackend()
                Frontend(RenderContext(doc), backend, config=settings).draw_layout(target)
                paper = sheet["page"]
                page = (
                    layout.Page.from_dxf_layout(target)
                    if paper is None
                    else layout.Page(
                        paper["width"], paper["height"], layout.Units[paper["units"]],
                        layout.Margins.all(paper["margins"]),
                    )
                )
                box = sheet["render_box"]
                render_box = (
                    None if box is None
                    else BoundingBox2d([(box[0], box[1]), (box[2], box[3])])
                )
                try:
                    one = bytes(backend.get_pdf_bytes(page, render_box=render_box))
                except ValueError:  # an empty bounding box: nothing drawn
                    drawn.append(False)
                    continue
                with pymupdf.open("pdf", one) as single:
                    merged.insert_pdf(single)
                drawn.append(True)
            if any(drawn):
                merged.save(order.path, garbage=3, deflate=True)
            pages = merged.page_count
    except OrderError:
        raise
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 - named for the client
        raise OrderError("edit_failed", f"Dessin du DXF impossible : {exc}", 422) from exc
    return {"drawn": drawn, "page_count": pages}
