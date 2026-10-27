# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The orders the engine accepts, read from JSON and checked before any work.

Pure: no PDF library here. An order that does not parse is refused with a
code and a message naming what is wrong, before a document is touched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The ways a raster can come back.
OUTPUT_KINDS = ("samples", "png", "file", "shared")

#: The colour spaces a raster can be drawn in.
COLORSPACES = ("rgb", "gray")


class OrderError(Exception):
    """An order the engine refuses, with a stable code a client can test."""

    def __init__(
        self, code: str, message: str, status: int = 400, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}


@dataclass(frozen=True)
class Output:
    """Where a raster goes.

    ``samples`` and ``png`` come back as the response body; ``file`` is
    written to ``path`` in the format its suffix names; ``shared`` writes the
    samples into the file at ``path``, grown if needed, for a client on the
    same machine to map — loopback HTTP carries ~300 MB/s here, a mapped
    file three times that.
    """

    kind: str
    path: str | None = None


@dataclass(frozen=True)
class RenderOrder:
    """Draw one page, or a rectangle of it, as pixels.

    The parameters are those of MuPDF's own page rendering: a resolution
    given either as ``dpi`` or as a ``matrix``, an optional ``clip`` in page
    coordinates, a colour space, an alpha channel, annotations drawn or not.
    """

    path: str | None
    handle: str | None
    page: int
    dpi: float | None
    matrix: tuple[float, float, float, float, float, float] | None
    clip: tuple[float, float, float, float] | None
    colorspace: str
    alpha: bool
    annots: bool
    output: Output


def _number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _numbers(payload: dict[str, Any], key: str, count: int) -> tuple[float, ...] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != count or not all(map(_number, value)):
        raise OrderError("bad_order", f"« {key} » attend {count} nombres.")
    return tuple(value)


def _flag(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise OrderError("bad_order", f"« {key} » attend true ou false.")
    return value


def _path(document: dict[str, Any]) -> str:
    path = document.get("path")
    if not isinstance(path, str) or not path:
        raise OrderError("bad_order", "« document.path » attend le chemin d'un fichier.")
    if not Path(path).is_absolute():
        raise OrderError("bad_order", f"« document.path » doit être absolu : {path}")
    return path


def _handle(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise OrderError("bad_order", f"« {field} » attend l'identifiant rendu par open.")
    return value


def _document(
    payload: dict[str, Any], key: str = "document"
) -> tuple[str | None, str | None]:
    """The document an order names: ``{"path": …}`` or ``{"handle": …}``."""
    document = payload.get(key)
    if not isinstance(document, dict) or ("path" in document) == ("handle" in document):
        raise OrderError("bad_order", f"« {key} » attend soit « path », soit « handle ».")
    if "handle" in document:
        return None, _handle(document["handle"], f"{key}.handle")
    return _path(document), None


def _held(payload: dict[str, Any], key: str = "document") -> str:
    """A document the order CHANGES: it must be held (a handle), never a path."""
    path, handle = _document(payload, key)
    if handle is None:
        raise OrderError(
            "bad_order", f"« {key} » modifié : il attend la poignée d'un document ouvert."
        )
    return handle


def _index(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OrderError("bad_order", f"« {key} » attend un index entier, à partir de 0.")
    return value


def _object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise OrderError("bad_order", "L'ordre attend un objet JSON.")
    return payload


@dataclass(frozen=True)
class InsertOrder:
    """Copy pages ``from_page``..``to_page`` (inclusive) of ``source`` to the
    end of the held document ``into``."""

    into: str
    source_path: str | None
    source_handle: str | None
    from_page: int
    to_page: int


@dataclass(frozen=True)
class SaveOrder:
    """Write the held document ``handle`` to ``path``."""

    handle: str
    path: str
    garbage: int
    deflate: bool
    incremental: bool = False


def describe_order(payload: Any) -> tuple[str | None, str | None]:
    """The document ``POST /v1/describe`` asks about.

    Raises:
        OrderError: ``bad_order``.
    """
    return _document(_object(payload))


def insert_order(payload: Any) -> InsertOrder:
    """``POST /v1/insert_pages``.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    path, handle = _document(payload, "source")
    first, last = _index(payload, "from_page"), _index(payload, "to_page")
    if last < first:
        raise OrderError("bad_order", "« to_page » précède « from_page ».")
    return InsertOrder(_held(payload), path, handle, first, last)


def cropbox_order(payload: Any) -> tuple[str, int, tuple[float, float, float, float]]:
    """``POST /v1/set_cropbox``: the held document, its page, the new crop box.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    rect = _numbers(payload, "rect", 4)
    if rect is None:
        raise OrderError("bad_order", "« rect » attend 4 nombres.")
    return _held(payload), _index(payload, "page"), rect  # type: ignore[return-value]


def rotation_order(payload: Any) -> tuple[str, int, int]:
    """``POST /v1/set_rotation``: the held document, its page, the rotation.

    Raises:
        OrderError: ``bad_order`` — a rotation is 0, 90, 180 or 270.
    """
    payload = _object(payload)
    rotation = payload.get("rotation")
    if rotation not in (0, 90, 180, 270) or isinstance(rotation, bool):
        raise OrderError("bad_order", "« rotation » attend 0, 90, 180 ou 270.")
    return _held(payload), _index(payload, "page"), int(rotation)


def save_order(payload: Any) -> SaveOrder:
    """``POST /v1/save``.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    path = payload.get("path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise OrderError("bad_order", "« path » attend un chemin absolu.")
    garbage = payload.get("garbage", 0)
    if garbage not in (0, 1, 2, 3, 4) or isinstance(garbage, bool):
        raise OrderError("bad_order", "« garbage » attend un entier de 0 à 4.")
    return SaveOrder(
        _held(payload), path, int(garbage), _flag(payload, "deflate", False),
        _flag(payload, "incremental", False),
    )


def open_order(payload: Any) -> tuple[str, bool]:
    """The path ``POST /v1/open`` asks to open, and whether in place.

    Raises:
        OrderError: ``bad_order``.
    """
    document = payload.get("document") if isinstance(payload, dict) else None
    if not isinstance(document, dict):
        raise OrderError("bad_order", "« document.path » attend le chemin d'un fichier.")
    return _path(document), _flag(payload, "in_place", False)


def close_order(payload: Any) -> str:
    """The handle ``POST /v1/close`` asks to close.

    Raises:
        OrderError: ``bad_order``.
    """
    return _handle(payload.get("handle") if isinstance(payload, dict) else None, "handle")


def _output(payload: dict[str, Any]) -> Output:
    output = payload.get("output", {"kind": "samples"})
    if not isinstance(output, dict) or output.get("kind") not in OUTPUT_KINDS:
        raise OrderError("bad_order", f"« output.kind » attend l'un de {OUTPUT_KINDS}.")
    if output["kind"] not in ("file", "shared"):
        return Output(output["kind"])
    path = output.get("path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise OrderError("bad_order", "« output.path » attend un chemin absolu.")
    return Output(output["kind"], path)


def render_order(payload: Any) -> RenderOrder:
    """The render order ``payload`` describes.

    Args:
        payload: The JSON body of ``POST /v1/render``.

    Returns:
        The checked order.

    Raises:
        OrderError: ``bad_order``, naming the field at fault.
    """
    if not isinstance(payload, dict):
        raise OrderError("bad_order", "L'ordre attend un objet JSON.")
    page = payload.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 0:
        raise OrderError("bad_order", "« page » attend un index entier, à partir de 0.")
    dpi = payload.get("dpi")
    if dpi is not None and (not _number(dpi) or dpi <= 0):
        raise OrderError("bad_order", "« dpi » attend un nombre positif.")
    matrix = _numbers(payload, "matrix", 6)
    if dpi is not None and matrix is not None:
        raise OrderError("bad_order", "« dpi » et « matrix » ne vont pas ensemble.")
    colorspace = payload.get("colorspace", "rgb")
    if colorspace not in COLORSPACES:
        raise OrderError("bad_order", f"« colorspace » attend l'un de {COLORSPACES}.")
    path, handle = _document(payload)
    return RenderOrder(
        path=path,
        handle=handle,
        page=page,
        dpi=dpi,
        matrix=matrix,  # type: ignore[arg-type]
        clip=_numbers(payload, "clip", 4),  # type: ignore[arg-type]
        colorspace=colorspace,
        alpha=_flag(payload, "alpha", False),
        annots=_flag(payload, "annots", True),
        output=_output(payload),
    )


#: The text extractions ``POST /v1/text`` offers, as MuPDF names them.
TEXT_KINDS = ("text", "words", "blocks", "dict", "rawdict")


@dataclass(frozen=True)
class TextOrder:
    """Read the text of one page, or of a rectangle of it.

    ``kind`` is MuPDF's own extraction (``text``, ``words``, ``blocks``,
    ``dict``, ``rawdict``) with its default flags; ``clip`` limits it to a
    rectangle, ``sort`` orders it top-left to bottom-right; ``None`` leaves
    MuPDF's default.
    """

    path: str | None
    handle: str | None
    page: int
    kind: str
    clip: tuple[float, float, float, float] | None
    sort: bool | None


def text_order(payload: Any) -> TextOrder:
    """``POST /v1/text``.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    path, handle = _document(payload)
    kind = payload.get("kind", "text")
    if kind not in TEXT_KINDS:
        raise OrderError("bad_order", f"« kind » attend l'un de {TEXT_KINDS}.")
    sort = payload.get("sort")
    if sort is not None and not isinstance(sort, bool):
        raise OrderError("bad_order", "« sort » attend true ou false.")
    return TextOrder(
        path, handle, _index(payload, "page"), kind,
        _numbers(payload, "clip", 4),  # type: ignore[arg-type]
        sort,
    )


def page_order(payload: Any) -> tuple[str | None, str | None, int]:
    """An order on one page: its document and its index.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    path, handle = _document(payload)
    return path, handle, _index(payload, "page")


def search_order(payload: Any) -> tuple[str | None, str | None, int, str, bool]:
    """``POST /v1/search``: document, page, the text sought, quads or rectangles.

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, page = page_order(payload)
    needle = payload.get("needle")
    if not isinstance(needle, str) or not needle:
        raise OrderError("bad_order", "« needle » attend le texte cherché.")
    return path, handle, page, needle, _flag(payload, "quads", False)


def textbox_order(
    payload: Any,
) -> tuple[str | None, str | None, int, tuple[float, float, float, float]]:
    """``POST /v1/textbox``: the text inside a rectangle of a page.

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, page = page_order(payload)
    rect = _numbers(payload, "rect", 4)
    if rect is None:
        raise OrderError("bad_order", "« rect » attend 4 nombres.")
    return path, handle, page, rect  # type: ignore[return-value]


def font_order(payload: Any) -> tuple[str | None, str | None, int]:
    """``POST /v1/extract_font``: the document and the font's object number.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    path, handle = _document(payload)
    xref = payload.get("xref")
    if not isinstance(xref, int) or isinstance(xref, bool) or xref <= 0:
        raise OrderError("bad_order", "« xref » attend un numéro d'objet positif.")
    return path, handle, xref


def _xref(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise OrderError("bad_order", f"« {key} » attend un numéro d'objet positif.")
    return value


def images_order(payload: Any) -> tuple[str | None, str | None, int, bool]:
    """``POST /v1/images``: a page, and whether to list images ``full``-y.

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, page = page_order(payload)
    return path, handle, page, _flag(payload, "full", False)


def image_rects_order(payload: Any) -> tuple[str | None, str | None, int, int]:
    """``POST /v1/image_rects``: a page and the image's object number.

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, page = page_order(payload)
    return path, handle, page, _xref(payload, "xref")


def image_info_order(payload: Any) -> tuple[str | None, str | None, int, bool]:
    """``POST /v1/image_info``: a page, and whether to add each image's xref.

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, page = page_order(payload)
    return path, handle, page, _flag(payload, "xrefs", False)


def extract_image_order(payload: Any) -> tuple[str | None, str | None, int, bool]:
    """``POST /v1/extract_image``: the image's object number; ``content`` false
    leaves its bytes out.

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, xref = font_order(payload)
    return path, handle, xref, _flag(payload, "content", True)


def xref_key_order(payload: Any) -> tuple[str | None, str | None, int, str]:
    """``POST /v1/xref_key``: an object number and one key of its dictionary.

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, xref = font_order(payload)
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        raise OrderError("bad_order", "« key » attend le nom d'une clé.")
    return path, handle, xref, key


def annots_order(
    payload: Any,
) -> tuple[str | None, str | None, int, tuple[int, ...] | None]:
    """``POST /v1/annots``: a page, and the annotation types wanted (all if absent).

    Raises:
        OrderError: ``bad_order``.
    """
    path, handle, page = page_order(payload)
    types = payload.get("types")
    if types is None:
        return path, handle, page, None
    if not isinstance(types, list) or not all(
        isinstance(kind, int) and not isinstance(kind, bool) for kind in types
    ):
        raise OrderError("bad_order", "« types » attend une liste d'entiers.")
    return path, handle, page, tuple(types)


def picture_order(payload: Any) -> str:
    """``POST /v1/picture``: the picture file's absolute path.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    path = payload.get("path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise OrderError("bad_order", "« path » attend le chemin absolu d'une image.")
    return path


#: What a redaction does to the images, the line art and the text it covers,
#: by name — MuPDF's own options (``apply_redactions``).
REDACT_IMAGES = ("none", "remove", "pixels", "remove_unless_invisible")
REDACT_GRAPHICS = ("none", "remove_if_covered", "remove_if_touched")
REDACT_TEXT = ("remove", "none")


@dataclass(frozen=True)
class RedactOrder:
    """Remove what ``rects`` cover on one page of a held document.

    Each rectangle becomes a redaction annotation, then all are applied at
    once with the options given — ``None`` leaves MuPDF's own default.
    """

    handle: str
    page: int
    rects: tuple[tuple[float, float, float, float], ...]
    images: str | None
    graphics: str | None
    text: str | None


def _choice(payload: dict[str, Any], key: str, allowed: tuple[str, ...]) -> str | None:
    value = payload.get(key)
    if value is not None and value not in allowed:
        raise OrderError("bad_order", f"« {key} » attend l'un de {allowed}.")
    return value


def redact_order(payload: Any) -> RedactOrder:
    """``POST /v1/redact``.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    rects = payload.get("rects")
    if not isinstance(rects, list) or not rects:
        raise OrderError("bad_order", "« rects » attend une liste de rectangles.")
    boxes: list[tuple[float, float, float, float]] = []
    for rect in rects:
        if not isinstance(rect, list) or len(rect) != 4 or not all(_number(v) for v in rect):
            raise OrderError("bad_order", "« rects » : chaque rectangle attend 4 nombres.")
        boxes.append((float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])))
    return RedactOrder(
        _held(payload),
        _index(payload, "page"),
        tuple(boxes),
        _choice(payload, "images", REDACT_IMAGES),
        _choice(payload, "graphics", REDACT_GRAPHICS),
        _choice(payload, "text", REDACT_TEXT),
    )


def held_order(payload: Any) -> str:
    """An order on a whole held document (``POST /v1/subset_fonts``).

    Raises:
        OrderError: ``bad_order``.
    """
    return _held(_object(payload))


@dataclass(frozen=True)
class FontSpec:
    """A font, by name: a built-in face, a file, or a font embedded in a document."""

    builtin: str | None = None
    file: str | None = None
    path: str | None = None
    handle: str | None = None
    xref: int = 0

    def key(self) -> tuple[Any, ...] | None:
        """What a parsed face is cached under — ``None`` for a document read
        by path, which names the file as it is now."""
        if self.builtin is not None:
            return ("builtin", self.builtin)
        if self.file is not None:
            return ("file", self.file)
        if self.handle is not None:
            return ("handle", self.handle, self.xref)
        return None


def _font(payload: dict[str, Any]) -> FontSpec:
    spec = payload.get("font")
    if not isinstance(spec, dict):
        raise OrderError("bad_order", "« font » attend builtin, file, ou document et xref.")
    if "builtin" in spec:
        name = spec["builtin"]
        if not isinstance(name, str) or not name:
            raise OrderError("bad_order", "« font.builtin » attend le nom d'une face.")
        return FontSpec(builtin=name)
    if "file" in spec:
        file = spec["file"]
        if not isinstance(file, str) or not Path(file).is_absolute():
            raise OrderError("bad_order", "« font.file » attend un chemin absolu.")
        return FontSpec(file=file)
    path, handle = _document(spec)
    return FontSpec(path=path, handle=handle, xref=_xref(spec, "xref"))


def font_info_order(payload: Any) -> tuple[FontSpec, bool]:
    """``POST /v1/font_info``: the font, and whether its file is wanted.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    return _font(payload), _flag(payload, "buffer", False)


def _texts(payload: dict[str, Any]) -> list[str]:
    texts = payload.get("texts")
    if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
        raise OrderError("bad_order", "« texts » attend une liste de textes.")
    return texts


def _size(payload: dict[str, Any]) -> float:
    size = payload.get("size")
    if not isinstance(size, int | float) or not _number(size):
        raise OrderError("bad_order", "« size » attend un nombre.")
    return float(size)


def text_length_order(payload: Any) -> tuple[FontSpec | None, str | None, list[str], float]:
    """``POST /v1/text_length``: a font OR a Base-14 ``fontname``, texts, a size.

    ``font`` measures with the Font object, ``fontname`` with
    ``get_text_length`` — two different measures in MuPDF.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    if ("font" in payload) == ("fontname" in payload):
        raise OrderError("bad_order", "« font » ou « fontname », l'un des deux.")
    if "fontname" in payload:
        name = payload["fontname"]
        if not isinstance(name, str) or not name:
            raise OrderError("bad_order", "« fontname » attend un nom de face Base-14.")
        return None, name, _texts(payload), _size(payload)
    return _font(payload), None, _texts(payload), _size(payload)


def glyphs_order(payload: Any) -> tuple[FontSpec, list[int]]:
    """``POST /v1/glyphs``: a font and code points.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    codes = payload.get("codes")
    if not isinstance(codes, list) or not all(
        isinstance(code, int) and not isinstance(code, bool) and code >= 0 for code in codes
    ):
        raise OrderError("bad_order", "« codes » attend une liste de points de code.")
    return _font(payload), codes


#: The options each page order accepts, as PyMuPDF names them.
OPTIONS: dict[str, frozenset[str]] = {
    "insert_textbox": frozenset(
        {"fontname", "fontsize", "color", "fill", "align", "lineheight", "morph",
         "render_mode", "rotate", "fill_opacity", "stroke_opacity", "border_width"}
    ),
    "insert_text": frozenset(
        {"fontname", "fontsize", "color", "fill", "lineheight", "morph", "render_mode",
         "rotate", "fill_opacity", "stroke_opacity", "border_width"}
    ),
    "draw_rect": frozenset(
        {"color", "fill", "width", "fill_opacity", "stroke_opacity", "dashes", "overlay",
         "radius"}
    ),
    "draw_line": frozenset({"color", "width", "stroke_opacity", "dashes", "overlay"}),
    "draw_circle": frozenset(
        {"color", "fill", "width", "fill_opacity", "stroke_opacity", "dashes", "overlay"}
    ),
    "insert_image": frozenset({"keep_proportion", "overlay", "rotate", "alpha"}),
    "insert_htmlbox": frozenset({"css", "scale_low", "rotate", "opacity", "overlay"}),
    "add_redact_annot": frozenset({"fill", "text", "fontname", "fontsize", "align",
                                   "text_color", "cross_out"}),
    "insert_font": frozenset({"set_simple", "wmode", "encoding"}),
    "show_pdf_page": frozenset({"keep_proportion", "overlay", "rotate", "clip"}),
}


@dataclass(frozen=True)
class WriteOrder:
    """One page order of the writing family: what to call, on which page.

    ``args`` are the call's own operands (a rectangle, a text...), ``options``
    the keyword options the client named -- and only those.
    """

    kind: str
    handle: str
    page: int
    args: dict[str, Any]
    options: dict[str, Any]


def _rect(value: Any, field: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 4 or not all(_number(v) for v in value):
        raise OrderError("bad_order", f"« {field} » attend 4 nombres.")
    return [float(v) for v in value]


def _point(value: Any, field: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2 or not all(_number(v) for v in value):
        raise OrderError("bad_order", f"« {field} » attend 2 nombres.")
    return [float(v) for v in value]


#: Each path item, by its letter: how many points it carries (``re`` a rectangle).
PATH_ITEMS = {"l": 2, "c": 4, "re": 0, "qu": 4}

#: What a path's ``finish`` may name — ``Shape.finish``'s own options.
PATH_FINISH = frozenset({"fill", "color", "even_odd", "closePath", "width",
                         "fill_opacity", "stroke_opacity"})


def _paths(value: Any) -> list[dict[str, Any]]:
    """``draw_paths``'s paths: items as ``get_drawings`` gives them, then a finish."""
    if not isinstance(value, list) or not value:
        raise OrderError("bad_order", "« paths » attend une liste de tracés.")
    paths = []
    for path in value:
        if not isinstance(path, dict) or not isinstance(path.get("items"), list):
            raise OrderError("bad_order", "Un tracé attend « items ».")
        items: list[list[Any]] = []
        for item in path["items"]:
            if not isinstance(item, list) or not item or item[0] not in PATH_ITEMS:
                raise OrderError("bad_order", f"Élément de tracé inconnu : {item!r:.40}")
            kind = item[0]
            if kind == "re":
                items.append([kind, _rect(item[1] if len(item) > 1 else None, "re")])
            else:
                if len(item) != PATH_ITEMS[kind] + 1:
                    raise OrderError("bad_order", f"« {kind} » attend {PATH_ITEMS[kind]} points.")
                items.append([kind, *(_point(corner, kind) for corner in item[1:])])
        finish = path.get("finish", {})
        if not isinstance(finish, dict) or set(finish) - PATH_FINISH:
            raise OrderError("bad_order", f"« finish » n'accepte que {sorted(PATH_FINISH)}.")
        for key in ("fill", "color"):
            colour = finish.get(key)
            if colour is not None and (
                not isinstance(colour, list) or not all(_number(v) for v in colour)
            ):
                raise OrderError("bad_order", f"« {key} » attend une couleur ou null.")
        paths.append({"items": items, "finish": dict(finish)})
    return paths


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise OrderError("bad_order", f"« {field} » attend un texte.")
    return value


def _base64(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise OrderError("bad_order", f"« {field} » attend des octets en base64.")
    return value


def _absolute(value: Any, field: str) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise OrderError("bad_order", f"« {field} » attend un chemin absolu.")
    return value


def _write_args(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind in ("insert_textbox", "insert_htmlbox"):
        args: dict[str, Any] = {"rect": _rect(payload.get("rect"), "rect"),
                                "text": _text(payload.get("text"), "text")}
        if kind == "insert_htmlbox":
            archive = payload.get("archive")
            if archive is not None:
                if not isinstance(archive, list) or not all(
                    isinstance(entry, list) and len(entry) == 2 for entry in archive
                ):
                    raise OrderError("bad_order", "« archive » attend des paires [chemin, nom].")
                archive = [[_absolute(path, "archive"), _text(name, "archive")]
                           for path, name in archive]
            args["archive"] = archive
        return args
    if kind == "insert_text":
        point = payload.get("point")
        if not isinstance(point, list) or len(point) != 2 or not all(_number(v) for v in point):
            raise OrderError("bad_order", "« point » attend 2 nombres.")
        return {"point": [float(v) for v in point], "text": _text(payload.get("text"), "text")}
    if kind in ("draw_rect", "add_redact_annot"):
        return {"rect": _rect(payload.get("rect"), "rect")}
    if kind == "draw_line":
        return {"p1": _point(payload.get("p1"), "p1"), "p2": _point(payload.get("p2"), "p2")}
    if kind == "draw_circle":
        radius: Any = payload.get("radius")
        if not _number(radius) or radius <= 0:
            raise OrderError("bad_order", "« radius » attend un nombre positif.")
        return {"center": _point(payload.get("center"), "center"), "radius": float(radius)}
    if kind == "draw_paths":
        return {"paths": _paths(payload.get("paths"))}
    if kind == "insert_image":
        return {"rect": _rect(payload.get("rect"), "rect"),
                "stream": _base64(payload.get("stream"), "stream")}
    if kind == "insert_font":
        name = _text(payload.get("fontname"), "fontname")
        if ("fontfile" in payload) == ("fontbuffer" in payload):
            raise OrderError("bad_order", "« fontfile » ou « fontbuffer », l'un des deux.")
        if "fontfile" in payload:
            return {"fontname": name, "fontfile": _absolute(payload["fontfile"], "fontfile")}
        return {"fontname": name, "fontbuffer": _base64(payload["fontbuffer"], "fontbuffer")}
    if kind == "insert_link":
        link = payload.get("link")
        if not isinstance(link, dict):
            raise OrderError("bad_order", "« link » attend le dictionnaire d'un lien.")
        return {"link": link}
    if kind == "add_markup_annot":
        maker = payload.get("maker")
        if maker not in MARKUP_MAKERS:
            raise OrderError("bad_order", f"« maker » attend l'un de {MARKUP_MAKERS}.")
        quads = payload.get("quads")
        if not isinstance(quads, list) or not quads or not all(
            isinstance(quad, list) and len(quad) == 4
            and all(isinstance(c, list) and len(c) == 2 and all(_number(v) for v in c)
                    for c in quad)
            for quad in quads
        ):
            raise OrderError("bad_order", "« quads » attend des quads de 4 points.")
        stroke = payload.get("stroke")
        if stroke is not None and (
            not isinstance(stroke, list) or not all(_number(v) for v in stroke)
        ):
            raise OrderError("bad_order", "« stroke » attend une couleur.")
        opacity = payload.get("opacity")
        if opacity is not None and not _number(opacity):
            raise OrderError("bad_order", "« opacity » attend un nombre.")
        return {"maker": maker, "quads": quads, "stroke": stroke,
                "has_stroke": "stroke" in payload, "opacity": opacity}
    if kind == "show_pdf_page":
        source_path, source_handle = _document(payload, "source")
        return {"rect": _rect(payload.get("rect"), "rect"), "source_path": source_path,
                "source_handle": source_handle,
                "source_page": _index(payload, "source_page")}
    raise OrderError("unknown_order", f"Ordre d'écriture inconnu : {kind}", 404)


def _write_options(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("options", {})
    if not isinstance(options, dict):
        raise OrderError("bad_order", "« options » attend un objet.")
    allowed = OPTIONS.get(kind, frozenset())
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise OrderError("bad_order", f"Options inconnues pour {kind} : {unknown}")
    for key, value in options.items():
        if key in ("color", "fill", "text_color"):
            if value is not None and (
                not isinstance(value, list) or not all(_number(v) for v in value)
            ):
                raise OrderError("bad_order", f"« {key} » attend une couleur ou null.")
        elif key == "clip":
            if value is not None and not (
                isinstance(value, list) and len(value) == 4 and all(_number(v) for v in value)
            ):
                raise OrderError("bad_order", "« clip » attend 4 nombres ou null.")
        elif key == "morph":
            if value is not None and not (
                isinstance(value, list) and len(value) == 2
                and isinstance(value[0], list) and len(value[0]) == 2
                and isinstance(value[1], list) and len(value[1]) == 6
                and all(_number(v) for v in [*value[0], *value[1]])
            ):
                raise OrderError("bad_order", "« morph » attend [[x, y], [a, b, c, d, e, f]].")
        elif value is not None and not isinstance(value, bool | int | float | str):
            raise OrderError("bad_order", f"« {key} » attend un nombre, un texte ou un booléen.")
    return dict(options)


#: The writing orders on one page, by name (``POST /v1/<name>``).
WRITE_KINDS = (
    "insert_textbox", "insert_text", "draw_rect", "insert_image", "insert_htmlbox",
    "add_redact_annot", "insert_font", "insert_link", "add_markup_annot", "show_pdf_page",
    "draw_line", "draw_circle", "draw_paths",
)

#: The text-markup annotations, by PyMuPDF's method name.
MARKUP_MAKERS = (
    "add_highlight_annot", "add_underline_annot", "add_squiggly_annot", "add_strikeout_annot",
)


def write_order(kind: str, payload: Any) -> WriteOrder:
    """``POST /v1/<kind>`` for a writing order on one page of a held document.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    return WriteOrder(
        kind, _held(payload), _index(payload, "page"),
        _write_args(kind, payload), _write_options(kind, payload),
    )


@dataclass(frozen=True)
class StoryOrder:
    """``POST /v1/story``: HTML flowed over as many sheets as it needs, into a file."""

    html: str
    path: str
    paper: list[float]
    frame: list[float]


def story_order(payload: Any) -> StoryOrder:
    """``POST /v1/story``: the HTML, the file to write, the sheet and the frame on it.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    return StoryOrder(
        _text(payload.get("html"), "html"),
        _absolute(payload.get("path"), "path"),
        _rect(payload.get("paper"), "paper"),
        _rect(payload.get("frame"), "frame"),
    )


@dataclass(frozen=True)
class DxfOrder:
    """``POST /v1/dxf_pdf``: which layouts of a DXF to draw, on what paper, into a file."""

    source: str
    path: str
    sheets: list[dict[str, Any]]
    background: str
    color: str


def _policy(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.isidentifier() or not value.isupper():
        raise OrderError("bad_order", f"« {field} » attend le nom d'une politique d'ezdxf.")
    return value


def dxf_order(payload: Any) -> DxfOrder:
    """``POST /v1/dxf_pdf``: the DXF, the PDF to write, the sheets and the policies.

    Each sheet is ``{"layout", "page", "render_box"}``: ``page`` null for the
    layout's own paper, or ``{"width", "height", "units", "margins"}``;
    ``render_box`` null to fit what is drawn, or four drawing coordinates.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    sheets = payload.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise OrderError("bad_order", "« sheets » attend une liste de présentations.")
    checked = []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise OrderError("bad_order", "Une présentation attend un objet.")
        paper = sheet.get("page")
        if paper is not None:
            if not isinstance(paper, dict) or not all(
                _number(paper.get(key)) and paper[key] > 0 for key in ("width", "height")
            ) or not _number(paper.get("margins")) or paper["margins"] < 0:
                raise OrderError("bad_order", "« page » attend width, height, units, margins.")
            paper = {"width": float(paper["width"]), "height": float(paper["height"]),
                     "units": _policy(paper.get("units", "mm").upper(), "units").lower(),
                     "margins": float(paper["margins"])}
        box = sheet.get("render_box")
        checked.append({
            "layout": _text(sheet.get("layout"), "layout"),
            "page": paper,
            "render_box": None if box is None else _rect(box, "render_box"),
        })
    return DxfOrder(
        _absolute(payload.get("source"), "source"),
        _absolute(payload.get("path"), "path"),
        checked,
        _policy(payload.get("background", "WHITE"), "background"),
        _policy(payload.get("color", "BLACK"), "color"),
    )


def new_page_order(payload: Any) -> tuple[str, float, float]:
    """``POST /v1/new_page``: the held document, the page's width and height.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    width, height = payload.get("width", 595), payload.get("height", 842)
    if not (_number(width) and _number(height)) or width <= 0 or height <= 0:
        raise OrderError("bad_order", "« width » et « height » attendent des nombres positifs.")
    return _held(payload), float(width), float(height)


def apply_order(payload: Any) -> tuple[str, int, dict[str, str]]:
    """``POST /v1/apply_redactions``: options by name, as ``redact`` takes them.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    options: dict[str, str] = {}
    for key, allowed in (("images", REDACT_IMAGES), ("graphics", REDACT_GRAPHICS),
                         ("text", REDACT_TEXT)):
        choice = _choice(payload, key, allowed)
        if choice is not None:
            options[key] = choice
    return _held(payload), _index(payload, "page"), options


def metadata_order(payload: Any) -> tuple[str, dict[str, str]]:
    """``POST /v1/set_metadata``: the held document and its new information.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise OrderError("bad_order", "« metadata » attend des textes par clé.")
    return _held(payload), metadata


@dataclass(frozen=True)
class EncodeOrder:
    """Samples to encode as an image file."""

    samples: bytes
    width: int
    height: int
    n: int
    output: str
    quality: int


def encode_order(payload: Any) -> EncodeOrder:
    """``POST /v1/encode_image``: samples, their shape, the format wanted.

    Raises:
        OrderError: ``bad_order``.
    """
    import base64

    payload = _object(payload)
    samples = base64.b64decode(_base64(payload.get("samples"), "samples"))
    shape = [payload.get(key) for key in ("width", "height", "n")]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in shape):
        raise OrderError("bad_order", "« width », « height », « n » attendent des entiers.")
    width, height, n = (int(v) for v in shape)
    if len(samples) != width * height * n:
        raise OrderError("bad_order", "« samples » n'a pas la taille width x height x n.")
    output = payload.get("output", "png")
    if output not in ("png", "jpeg", "jpg", "pnm", "pam", "psd", "ps"):
        raise OrderError("bad_order", "« output » attend un format d'image.")
    quality = payload.get("quality", 95)
    if not isinstance(quality, int) or isinstance(quality, bool) or not 0 < quality <= 100:
        raise OrderError("bad_order", "« quality » attend un entier de 1 à 100.")
    return EncodeOrder(samples, width, height, n, str(output), quality)


def delete_page_order(payload: Any) -> tuple[str, int]:
    """``POST /v1/delete_page``: the held document and the page to remove.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    return _held(payload), _index(payload, "page")


def labels_order(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    """``POST /v1/set_page_labels``: the held document and its label rules.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    labels = payload.get("labels")
    if not isinstance(labels, list) or not all(isinstance(rule, dict) for rule in labels):
        raise OrderError("bad_order", "« labels » attend une liste de règles.")
    return _held(payload), labels


@dataclass(frozen=True)
class LayoutLoadOrder:
    """``POST /v1/layout_load``: load the layout model, on the card if asked (1.9)."""

    gpu: str


def layout_load_order(payload: Any) -> LayoutLoadOrder:
    """``POST /v1/layout_load``: ``{"gpu": "auto" | "never"}``.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    gpu = payload.get("gpu", "auto")
    if gpu not in ("auto", "never"):
        raise OrderError("bad_order", "« gpu » attend « auto » ou « never ».")
    return LayoutLoadOrder(str(gpu))


@dataclass(frozen=True)
class LayoutRaster:
    """One page's pixels in a file: BGR bytes, ``height`` rows of ``width``."""

    path: str
    width: int
    height: int


@dataclass(frozen=True)
class LayoutDetectOrder:
    """``POST /v1/layout_detect``: the regions of each raster, in pixels (1.9)."""

    rasters: list[LayoutRaster]


def layout_detect_order(payload: Any) -> LayoutDetectOrder:
    """``POST /v1/layout_detect``: ``{"rasters": [{"path", "width", "height"}]}``.

    Raises:
        OrderError: ``bad_order``.
    """
    payload = _object(payload)
    rasters = payload.get("rasters")
    if not isinstance(rasters, list) or not rasters:
        raise OrderError("bad_order", "« rasters » attend une liste non vide.")
    read: list[LayoutRaster] = []
    for raster in rasters:
        raster = _object(raster)
        width, height = raster.get("width"), raster.get("height")
        if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1:
            raise OrderError("bad_order", "« width » et « height » attendent des entiers positifs.")
        read.append(LayoutRaster(_absolute(raster.get("path"), "path"), width, height))
    return LayoutDetectOrder(read)
