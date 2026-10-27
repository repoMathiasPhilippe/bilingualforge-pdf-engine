# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The documents the engine holds open, each for exactly as long as asked.

An order names its document in one of two ways:

* by **path** — the file is opened for that order alone and closed after it;
* by **handle** — returned by ``open``, kept until ``close``: several orders
  then share ONE open document.

The distinction is not a matter of speed. MuPDF keeps what it decoded for a
document — a scanned page's image, at the scale it was last drawn — and a
later render of the same document may start from it: the pixels differ, by
a little, from a render on a freshly opened copy. A client that opened a
file once for several renders gets what it got before by using a handle; a
client that opened it for one render, by using the path. Keeping documents
open on the engine's own initiative would change results.

A document is opened from its BYTES, never from its path: MuPDF keeps an open
path's file handle, and on Windows that handle forbids the client to delete
or rewrite its own file.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pymupdf

from pdf_engine.protocol import OrderError

#: Handles held at most; past it, the least recently used is closed.
CAPACITY = 64


def read(path: str) -> pymupdf.Document:
    """A fresh document, read from the bytes of the file at ``path``.

    Raises:
        OrderError: ``not_found`` for a missing file, ``forbidden`` for one
            the engine may not read, ``unreadable`` for a file MuPDF cannot
            open.
    """
    file = Path(path)
    try:
        data = file.read_bytes()
    except PermissionError as exc:
        raise OrderError("forbidden", f"Accès refusé : {path}", 403) from exc
    except OSError as exc:
        raise OrderError("not_found", f"Fichier introuvable : {path}", 404) from exc
    try:
        return pymupdf.open(stream=data, filetype=file.suffix.lstrip(".").lower() or None)
    except Exception as exc:  # noqa: BLE001 - named for the client
        raise OrderError("unreadable", f"Document illisible : {path} ({exc})", 422) from exc


class Documents:
    """The open handles, in least-recently-used order.

    Not thread-safe: the server calls it under its single lock.
    """

    def __init__(self, capacity: int = CAPACITY) -> None:
        self._capacity = capacity
        self._open: OrderedDict[str, pymupdf.Document] = OrderedDict()

    def open(self, path: str, in_place: bool = False) -> tuple[str, pymupdf.Document]:
        """Open ``path`` and keep it under a new handle.

        ``in_place`` opens the FILE itself rather than its bytes: the one
        way to save it incrementally, appending to it. The file stays open,
        and locked on Windows, until the handle is closed.

        Raises:
            OrderError: ``not_found``, ``unreadable``.
        """
        if not in_place:
            return self._hold(read(path))
        if not Path(path).is_file():
            raise OrderError("not_found", f"Fichier introuvable : {path}", 404)
        try:
            return self._hold(pymupdf.open(path))
        except Exception as exc:  # noqa: BLE001 - named for the client
            raise OrderError("unreadable", f"Document illisible : {exc}", 422) from exc

    def create(self) -> tuple[str, pymupdf.Document]:
        """A new, empty document, kept under a new handle."""
        return self._hold(pymupdf.open())

    def _hold(self, document: pymupdf.Document) -> tuple[str, pymupdf.Document]:
        handle = secrets.token_hex(8)
        self._open[handle] = document
        while len(self._open) > self._capacity:
            self.close(next(iter(self._open)))
        return handle, document

    def close(self, handle: str) -> bool:
        """Close ``handle``; whether it was open."""
        document = self._open.pop(handle, None)
        if document is None:
            return False
        document.close()
        return True

    @contextmanager
    def named(self, path: str | None, handle: str | None) -> Iterator[pymupdf.Document]:
        """The document an order names: its handle's, or a fresh one for its path.

        Raises:
            OrderError: ``unknown_handle`` for a handle closed or never given.
        """
        if handle is not None:
            document = self._open.get(handle)
            if document is None:
                raise OrderError("unknown_handle", f"Document inconnu : {handle}", 404)
            self._open.move_to_end(handle)
            yield document
            return
        document = read(str(path))
        try:
            yield document
        finally:
            document.close()

    def close_all(self) -> None:
        """Close every handle."""
        while self._open:
            self.close(next(iter(self._open)))
