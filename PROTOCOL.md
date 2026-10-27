# PDF engine protocol — version 1.11

The engine executes **orders** and returns **results**, over HTTP. An order
carries every parameter of the work; the same order on the same file always
returns the same result. Nothing in the protocol refers to the client's own
data model: it speaks of documents, pages, rectangles and pixels.

- Transport: HTTP/1.1, JSON bodies, on the address the engine was started with
  (`python -m pdf_engine --host 127.0.0.1 --port 9100`; `--port 0` lets the
  system choose, and the first line printed on stdout is
  `{"ready": true, "port": N, "protocol": "1.0"}`) — or, since 1.7, frames
  on the engine's stdin and stdout (« Pipe », below), for a client that
  starts the engine in a sandbox where it may not listen on a socket.
- Files are named by **absolute paths**: client and engine run on the same
  machine and share a folder.
- One order runs at a time; concurrent requests queue.
- Versioning: a MAJOR change breaks clients, a MINOR one adds orders or
  optional fields. `GET /v1/about` says which version is spoken; since 1.10
  its `backend` also names the MuPDF version (`mupdf`), so a client never
  imports the library to learn it.

## `GET /v1/about`

```json
{
  "engine": "bilingualforge-pdf-engine",
  "version": "0.1.0",
  "protocol": "1.0",
  "backend": {"name": "PyMuPDF", "version": "1.28.0", "mupdf": "1.29.0"},
  "license": "AGPL-3.0-or-later",
  "source": "<where the engine's code can be had>",
  "orders": ["open", "close", "render"]
}
```

## Naming a document: a path, or a handle

Every order that works on a document names it as `{"path": "<absolute>"}` or
`{"handle": "<id>"}`:

- by **path**, the file is opened for that order alone, as a fresh copy, and
  closed after it;
- by **handle**, the order works on the document `open` returned, until
  `close`. Several orders then share ONE open document.

The two are not interchangeable. MuPDF reuses, within one open document,
what it decoded for an earlier render — a scanned page's image, at the
scale it was last drawn — so a render that follows another on the same open
document may differ, by a few pixels, from the same render on a fresh copy.
A client that wants several renders to share one open document uses a
handle; one that wants each render on its own uses the path.

Files are read from their bytes: the engine never holds a file open, and a
client may rewrite or delete it at any time — a path is always read as the
file is NOW; a handle keeps the content it was opened with.

## `POST /v1/open` — hold a document

`{"document": {"path": "<absolute>"}}` → `{"handle": "<id>", "page_count": N}`.
The engine holds at most 64 documents; past that, the least recently used
is closed and its handle becomes unknown.

## `POST /v1/close` — release it

`{"handle": "<id>"}` → `{"closed": true}`, or `{"closed": false}` for a
handle already closed or never given.

## Documents as a whole (1.1)

A document an order CHANGES must be held (a handle): changing a path's fresh
copy would change nothing anyone could see. All answers are JSON.

| Order | Body | Answer |
|---|---|---|
| `POST /v1/create` | `{}` | `{"handle", "page_count": 0}` — a new, empty document |
| `POST /v1/describe` | `{"document"}` | `{"page_count", "pages": [{"rect", "mediabox", "cropbox", "rotation"}]}` |
| `POST /v1/insert_pages` | `{"document": {"handle"}, "source", "from_page", "to_page"}` | `{"page_count"}` — pages `from_page`..`to_page` (inclusive, from 0) of `source` appended |
| `POST /v1/set_cropbox` | `{"document": {"handle"}, "page", "rect"}` | `{"rect"}` — `rect` in the coordinates of `describe`'s `cropbox`: unrotated, top-left origin. A rectangle of the DISPLAYED page must be turned back first, and taken from the current crop box's origin |
| `POST /v1/set_rotation` | `{"document": {"handle"}, "page", "rotation"}` | `{"rotation"}` — 0, 90, 180 or 270; a declaration, the content is untouched |
| `POST /v1/redact` (1.4) | `{"document": {"handle"}, "page", "rects", "images"?, "graphics"?, "text"?}` | `{"redacted": N}` — each rectangle marked for redaction, then all applied at once. `images`: `none`, `remove`, `pixels`, `remove_unless_invisible`; `graphics`: `none`, `remove_if_covered`, `remove_if_touched`; `text`: `remove`, `none` — MuPDF's own options, its defaults when absent (`pixels`, `remove_if_covered`, `remove`) |
| `POST /v1/subset_fonts` (1.4) | `{"document": {"handle"}}` | `{"subset": true}` — every embedded font keeps only the glyphs the document uses |
| `POST /v1/save` | `{"document": {"handle"}, "path", "garbage"?, "deflate"?}` | `{"path", "page_count"}` — `garbage` 0–4, `deflate` false by default |

In `describe`, `rect` is the page as displayed — its crop box turned by its
rotation — in the coordinates every other order uses; `mediabox` and
`cropbox` are as stored.

A saved file is not byte-reproducible: the backend writes variable
identifiers, and two saves of the same document already differ. The same
document means the same pages, boxes, rotations and pixels.

## Reading (1.2)

Answers are **typed JSON**: MuPDF's own result, where a tuple is written
`{"__tuple__": [...]}` and bytes `{"__bytes__": "<base64>"}` — everything
else is plain JSON (non-finite floats as `NaN`/`Infinity`). A library object
(a rectangle, a point, a quad) is written as the tuple of its numbers.

| Order | Body | `result` |
|---|---|---|
| `POST /v1/text` | `{"document", "page", "kind", "clip"?, "sort"?, "image_bytes"?}` | `get_text(kind)` — `kind` is `text`, `words`, `blocks`, `dict` or `rawdict`, with MuPDF's default flags (a `dict` carries its image blocks and their bytes). `image_bytes: false` (1.11, `dict` and `rawdict` only): every block in its place, an image block without `image`, `mask`, `ext` and `size`, a text block without its block-level `flags` — MuPDF re-encodes a scan's image as PNG on every reading, about a second a page |
| `POST /v1/texttrace` | `{"document", "page"}` | every glyph as drawn, in drawing order |
| `POST /v1/search` | `{"document", "page", "needle", "quads"?}` | each hit as `(x0, y0, x1, y1)`, or with `quads` as four `(x, y)` corners (UL, UR, LL, LR) |
| `POST /v1/textbox` | `{"document", "page", "rect"}` | the text inside `rect` |
| `POST /v1/fonts` | `{"document", "page"}` | the page's fonts, as `get_fonts(full=True)` |
| `POST /v1/extract_font` | `{"document", "xref"}` | `(name, extension, type, file bytes)` |

Every answer is `{"result": ...}`.

## Drawings, images, links, annotations (1.3)

Same typed JSON. A rectangle, a point or a quad is the tuple of its numbers.

| Order | Body | `result` |
|---|---|---|
| `POST /v1/drawings` | `{"document", "page"}` | `get_drawings()` — every path in drawing order; items `("l", p, p)`, `("c", p, p, p, p)`, `("re", rect, orientation)`, `("qu", quad)` |
| `POST /v1/images` | `{"document", "page", "full"?}` | `get_images(full=…)` — the images the page's resources declare |
| `POST /v1/image_rects` | `{"document", "page", "xref"}` | `get_image_rects(xref)` — where the page places that image |
| `POST /v1/image_info` | `{"document", "page", "xrefs"?}` | `get_image_info(xrefs=…)` — every image the page draws |
| `POST /v1/extract_image` | `{"document", "xref", "content"?}` | `extract_image(xref)`; `content: false` leaves the `image` bytes out |
| `POST /v1/xref_key` | `{"document", "xref", "key"}` | `xref_get_key(xref, key)` — the value's type and its source text |
| `POST /v1/links` | `{"document", "page"}` | `get_links()` |
| `POST /v1/annots` | `{"document", "page", "types"?}` | per annotation (of `types`, MuPDF's numbers, when given): `type`, `rect`, `vertices`, `colors`, `opacity`, `xref` |

`POST /v1/picture` — `{"path": "<absolute>"}` → `{"width", "height", "n",
"xres", "yres"}`: a picture file (PNG, JPEG, TIFF…) as MuPDF decodes it.

**Three of these decode the image** — `image_rects`, `image_info` with
`xrefs`, `extract_image` — and, like a render, leave the decode in the open
document: a render that follows on the same handle may differ from one on a
fresh copy (see « Naming a document »). `images`, `image_info` without
`xrefs` and `drawings` decode nothing.

`image_info` answers for a page opened for that order. In process, MuPDF
keeps the answer on the page object, and a plain call after an `xrefs` call
returns the richer one; the engine never does.

## Writing (1.6)

Every writing order works on a HELD document (a handle) and calls ONE
PyMuPDF method. Its operands are fields of the body; its keyword options go
in `options`, and **only the options named are passed**: an option left out
keeps MuPDF's default, an option sent as `null` is passed as `None`. The two
differ — `draw_rect` strokes in black by default, and draws no stroke with
`color: null`. Colours are lists of numbers; `morph` is
`[[x, y], [a, b, c, d, e, f]]`; `clip` four numbers.

| Order | Body | `result` |
|---|---|---|
| `POST /v1/insert_textbox` | `{"document", "page", "rect", "text", "options"?}` | the unused height — negative when the text did not fit (then nothing is written) |
| `POST /v1/insert_text` | `{"document", "page", "point", "text", "options"?}` | the lines written |
| `POST /v1/draw_rect` | `{"document", "page", "rect", "options"?}` | `null` |
| `POST /v1/insert_image` | `{"document", "page", "rect", "stream", "options"?}` | the image's xref; `stream` the file's bytes, base64 |
| `POST /v1/insert_htmlbox` | `{"document", "page", "rect", "text", "archive"?, "options"?}` | `[spare, scale]`; `archive` the fonts the CSS names, as `[absolute path, name]` pairs |
| `POST /v1/insert_font` | `{"document", "page", "fontname", "fontfile" \| "fontbuffer", "options"?}` | the font's xref |
| `POST /v1/insert_link` | `{"document", "page", "link"}` | `null` — `link` as `get_links` gives it |
| `POST /v1/add_markup_annot` | `{"document", "page", "maker", "quads", "stroke"?, "opacity"?}` | the annotation's xref — `maker` one of `add_highlight_annot`, `add_underline_annot`, `add_squiggly_annot`, `add_strikeout_annot`; colours, then opacity, then `update`, in that order |
| `POST /v1/show_pdf_page` | `{"document", "page", "rect", "source", "source_page", "options"?}` | the form's xref — `source` names a document by path or handle |
| `POST /v1/add_redact_annot` | `{"document", "page", "rect", "options"?}` | `null` |
| `POST /v1/apply_redactions` | `{"document", "page", "images"?, "graphics"?, "text"?}` | whether anything was applied — the option names of `redact` |
| `POST /v1/new_page` | `{"document", "width"?, "height"?}` | `{"page", "page_count"}` |
| `POST /v1/delete_page` | `{"document", "page"}` | `{"page_count"}` |
| `POST /v1/set_page_labels` | `{"document", "labels"}` | `{"labels"}` |
| `POST /v1/set_metadata` | `{"document", "metadata"}` | `{"metadata": true}` |
| `POST /v1/del_xml_metadata` | `{"document"}` | `{"xml_metadata": false}` |
| `POST /v1/encode_image` | `{"samples", "width", "height", "n", "output"?, "quality"?}` | the encoded file, typed bytes — MuPDF's own PNG or JPEG |

`POST /v1/open` takes `"in_place": true` to open the FILE rather than its
bytes: the document can then be saved incrementally
(`POST /v1/save` with `"incremental": true` and the same path), appended to
the file. The file stays open — locked on Windows — until `close`.

## Drawing, flowing, CAD (1.8)

Three more page orders of the writing family (same body, `options` named
only): `draw_line` `{"p1", "p2"}`, `draw_circle` `{"center", "radius"}` —
`color`, `fill`, `width`, opacities, `dashes`, `overlay` — and
`draw_paths` `{"paths": [{"items", "finish"}]}`: every path on ONE shape,
committed once. Items are as `drawings` gives them (`["l", p, p]`,
`["c", p, p, p, p]`, `["re", rect]`, `["qu", p, p, p, p]` as UL, UR, LL, LR),
and `finish` names `Shape.finish`'s options (`fill`, `color`, `even_odd`,
`closePath`, `width`, opacities). `draw_rect` gains `radius`.

| Order | Body | Answer |
|---|---|---|
| `POST /v1/story` | `{"html", "path", "paper", "frame"}` | `{"path", "page_count"}` — the HTML flowed over as many sheets of `paper` as it needs, inside `frame`, written to `path` |
| `POST /v1/dxf_pdf` | `{"source", "path", "sheets", "background"?, "color"?}` | `{"drawn", "page_count"}` — each sheet `{"layout", "page", "render_box"}` drawn by ezdxf's drawing add-on on PyMuPDF: `page` null for the layout's own paper or `{"width", "height", "units", "margins"}`, `render_box` null to fit what is drawn or four drawing coordinates; `background` and `color` name ezdxf's `BackgroundPolicy` and `ColorPolicy` members (`WHITE`, `BLACK` by default). `drawn` says, per sheet, whether it showed something; no file is written when none did. Without ezdxf: `unavailable` (501) |

## Layout analysis (1.9)

DocLayout-YOLO, through BabelDOC. The model reads PIXELS only — rasters the
client wrote as BGR bytes, `height` rows of `width` pixels, 3 bytes each — so
the engine that answers these orders parses no client file and need not be
sandboxed; it can keep the graphics card.

| Order | Body | Answer |
|---|---|---|
| `POST /v1/layout_load` | `{"gpu": "auto" \| "never"}` | `{"device", "announced", "actual", "detail"}` — the model loaded once per process; with `auto`, moved to a CUDA session only when the BUILT session holds CUDA and one inference at 1024×1024 ran. `device` is `gpu` or `cpu`, `detail` says why not the card. Without BabelDOC or its weights: `unavailable` (503) |
| `POST /v1/layout_detect` | `{"rasters": [{"path", "width", "height"}]}` | `{"pages": [[[x0, y0, x1, y1, confidence, label]]], "device"}` — one list per raster, in its pixels, every candidate above 0.25, highest confidence first; all rasters in ONE launch, as BabelDOC's own preprocessing prepares them. Before `layout_load`: `unavailable` (409); a raster whose size disagrees: `bad_order` |

Which detections count, how they become paragraphs and how long a device may
stay silent are the client's decisions: a silent device is a request that
times out, and a process to restart.

## Fonts (1.5)

A font is named by a spec: `{"builtin": "<name>"}` (one of MuPDF's own
faces — Base-14, Fira…), `{"file": "<absolute path>"}`, or
`{"document": {"path"|"handle"}, "xref": N}` (a font embedded in a
document). A parsed face is kept — except a document read by path, which
names the file as it is now.

| Order | Body | Answer |
|---|---|---|
| `POST /v1/font_info` | `{"font", "buffer"?}` | `{"name", "ascender", "descender", "flags", "is_bold", "is_italic", "is_monospaced", "glyph_count", "buffer"?}` — the font file only when `buffer` is true |
| `POST /v1/text_length` | `{"font" \| "fontname", "texts", "size"}` | `{"lengths"}` — with `font`, `Font.text_length`; with a Base-14 `fontname`, `get_text_length`. **They are two measures**: MuPDF measures a Base-14 name through its own encoding, and the two differ outside ASCII (« Été » at 1 pt: 7.06 against 9.08) |
| `POST /v1/glyphs` | `{"font", "codes"}` | `{"glyphs", "advances"}` — per code point, `has_glyph` (the glyph's number, 0 when absent) and `glyph_advance` in ems |

A face MuPDF cannot build is refused with `unreadable_font` (422).

## `POST /v1/render` — a page, or a rectangle of it, as pixels

| Field | Type | Default | Meaning |
|---|---|---|---|
| `document` | `{"path"}` or `{"handle"}` | — | the document (above) |
| `page` | integer ≥ 0 | — | page index, from 0 |
| `dpi` | number > 0 | — | resolution; excludes `matrix` |
| `matrix` | 6 numbers | identity (72 dpi) | `[a, b, c, d, e, f]` transform; excludes `dpi` |
| `clip` | 4 numbers | whole page | `[x0, y0, x1, y1]` in page coordinates |
| `colorspace` | `"rgb"` \| `"gray"` | `"rgb"` | |
| `alpha` | boolean | `false` | add an alpha channel |
| `annots` | boolean | `true` | draw the page's annotations |
| `output` | object | `{"kind": "samples"}` | where the raster goes (below) |

`output.kind`:

- `samples` — the response body is the raw samples, rows top to bottom,
  `n` bytes per pixel, no padding; headers `X-Raster-Width`,
  `X-Raster-Height`, `X-Raster-N` give the shape, `X-Raster-X` and
  `X-Raster-Y` its origin (1.6) — a clipped render starts at its clip.
- `png` — the response body is the PNG encoding (`image/png`), same headers.
- `file` — written to `output.path` in the format its suffix names (`png`,
  `pnm`, `pgm`, `ppm`, `pbm`, `pam`, `psd`, `ps`, `jpg`); the response is
  `{"width", "height", "n", "path"}`.
- `shared` — the samples are written at the start of the file
  `output.path`, which is grown if too short and never shrunk, for a client
  on the same machine to map; the response is `{"width", "height", "n",
  "x", "y", "path"}` and the samples' length is `width × height × n`. Loopback HTTP
  carries large rasters about three times slower than a mapped file.

## Pipe (1.7)

`python -m pdf_engine --pipe` serves the same orders on its standard input
and output, one at a time, until its input closes. Each message is a
**frame**: two little-endian unsigned 32-bit lengths (`<II`: the JSON
header's, then the body's), the JSON header, then the body.

- A request's header is `{"method": "GET" | "POST", "path": "/v1/<order>"}`;
  its body is the order's JSON — what HTTP would POST.
- A reply's header is `{"status", "type", "headers"}` — HTTP's status, media
  type and extra headers; its body is the reply's body, JSON or raw bytes.
- The first frame the engine writes is `{"ready": true, "protocol": …}`,
  before any request.

Every reply is the one HTTP gives, byte for byte: both transports call the
same function. The engine's standard output belongs to the frames alone —
before the first one, it is moved onto standard error, where everything
else the engine or MuPDF prints goes: MuPDF prints its errors on standard
output, in words a document chooses.

## Errors

Any refusal is `{"error": {"code", "message", "detail"}}` with an HTTP status:

| Code | Status | When | `detail` |
|---|---|---|---|
| `bad_order` | 400 | the body is not JSON, or a field is missing or malformed | — |
| `unknown_order` | 404 | no such route | — |
| `not_found` | 404 | `document.path` does not exist | — |
| `forbidden` | 403 | the engine may not read or write that file — outside a sandbox's grants (1.7) | — |
| `unknown_handle` | 404 | the handle was closed, evicted, or never given | — |
| `unreadable` | 422 | the file cannot be opened as a document | — |
| `page_out_of_range` | 422 | `page` ≥ the document's page count | `{"page_count": N}` |
| `render_failed` | 500 | the backend failed; the message is its own | — |
| `read_failed` | 500 | the backend failed to read; the message is its own | — |
| `edit_failed` | 422 | the backend refused a change (a crop box outside the media box…); the message is its own | — |
