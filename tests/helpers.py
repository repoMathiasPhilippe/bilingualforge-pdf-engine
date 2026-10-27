"""Test documents, generated on the fly rather than checked in as binaries."""

from __future__ import annotations

from pathlib import Path

import pymupdf

FRENCH_PARAGRAPHS = (
    "Le chiffre d'affaires du groupe atteint 1 234,56 millions d'euros.",
    "Publié le 12/03/2024 par la Direction de la Communication.",
    "Cette organisation a analysé le comportement de ses clients.",
)


def make_pdf(path: Path, pages: int = 2, text: tuple[str, ...] = FRENCH_PARAGRAPHS) -> Path:
    """Write a small text-layer PDF.

    Args:
        path: Where to write the file.
        pages: How many pages to generate.
        text: Paragraphs to lay out on each page.

    Returns:
        The written path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        for page_index in range(pages):
            page = document.new_page(width=595, height=842)  # A4
            for line_index, paragraph in enumerate(text):
                page.insert_textbox(
                    pymupdf.Rect(56, 80 + line_index * 90, 539, 160 + line_index * 90),
                    f"{paragraph} (page {page_index + 1})",
                    fontname="helv",
                    fontsize=11,
                )
        document.save(path)
    return path
