# bilingualforge-pdf-engine

A small PDF engine that executes structured orders and returns structured
results, over HTTP. It holds no business rule: every order carries all of its
parameters, and the same order on the same file always gives the same result.

It is the part of BilingualForge that uses AGPL components (PyMuPDF,
BabelDOC, and ezdxf's drawing on PyMuPDF), kept as a **separate program** and
published under the **GNU Affero General Public License v3.0 or later**
([LICENSE](LICENSE)). Its clients reach it only through the protocol
([PROTOCOL.md](PROTOCOL.md)); any program may.

## Install

```bash
pip install "bilingualforge-pdf-engine[cad,layout] @ git+https://github.com/repoMathiasPhilippe/bilingualforge-pdf-engine"
```

The `cad` extra adds ezdxf (`dxf_pdf`), the `layout` extra BabelDOC
(`layout_load`, `layout_detect`). Without them, those orders answer
`unavailable`.

## Run

```bash
python -m pdf_engine --host 127.0.0.1 --port 9100
python -m pdf_engine --port 0 --watch-stdin   # as a child: system-chosen port, stops when stdin closes
python -m pdf_engine --pipe                   # as a child: frames on stdin/stdout, no socket
```

Requirements: Python 3.12, `pymupdf`; `ezdxf` for `dxf_pdf`; `babeldoc` (and
`onnxruntime-gpu` for the card) for `layout_load` and `layout_detect`.

## Develop

```bash
uv sync --extra cad --extra layout
uv run ruff check . && uv run mypy pdf_engine && uv run pytest
```

Each order is tested against PyMuPDF called in-process: the engine must
answer exactly what the library answers. The `ADR-NNN` references in the code
and tests name the design records of BilingualForge, the application the
engine was written for; they explain why an order exists, not how to use it.

`GET /v1/about` gives this repository as the source address, as the AGPL asks
of a program its users reach over a network; `PDF_ENGINE_SOURCE` names another
for a modified engine.

## Orders

| Version | Orders |
|---|---|
| 1.0 | `open` / `close` — hold a document for several orders; `render` — a page or a rectangle of it, as samples, PNG, a file, or a shared file |
| 1.1 | `create`, `describe`, `insert_pages`, `set_cropbox`, `set_rotation`, `save` — documents as a whole |
| 1.2 | `text`, `texttrace`, `search`, `textbox`, `fonts`, `extract_font` — reading, in typed JSON |
| 1.3 | `drawings`, `images`, `image_rects`, `image_info`, `extract_image`, `xref_key`, `links`, `annots`, `picture` — what a page draws besides its text |
| 1.4 | `redact`, `subset_fonts` — remove what rectangles cover; keep only the glyphs used |
| 1.5 | `font_info`, `text_length`, `glyphs` — what a font measures |
| 1.6 | `insert_textbox`, `insert_text`, `draw_rect`, `insert_image`, `insert_htmlbox`, `insert_font`, `insert_link`, `add_markup_annot`, `show_pdf_page`, `add_redact_annot`, `apply_redactions`, `new_page`, `delete_page`, `set_page_labels`, `set_metadata`, `del_xml_metadata`, `encode_image`; `open` in place and incremental `save` — writing |
| 1.7 | the same orders over a pipe (`--pipe`) — for an engine started in a sandbox; `forbidden` for a file outside its grants |
| 1.8 | `draw_line`, `draw_circle`, `draw_paths` (one shape), `radius` on `draw_rect`; `story` — HTML flowed into a PDF; `dxf_pdf` — a DXF's layouts drawn by ezdxf on PyMuPDF |
| 1.9 | `layout_load`, `layout_detect` — page regions by DocLayout-YOLO (BabelDOC), from pixels, on a proven graphics card |
| 1.10 | `about` names the MuPDF version (`backend.mupdf`): the client asks instead of importing the library |

Further orders arrive family by family (documents, text reading, images and
drawings, redaction, writing), each as a MINOR version.

## Licence

Copyright (C) 2026 The BilingualForge PDF engine authors.

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
General Public License for more details.
