"""The engine is a program of its own: it imports nothing of any client."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "pdf_engine"

#: What the engine may import besides the standard library and itself.
THIRD_PARTY = {"pymupdf", "ezdxf", "babeldoc", "onnxruntime", "numpy"}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_the_engine_imports_only_the_standard_library_and_its_backends() -> None:
    """A client's package (``src``…) or any library not declared here would
    make the engine depend on what it serves."""
    import sys

    foreign = {
        f"{path.name}: {name}"
        for path in sorted(PACKAGE.glob("*.py"))
        for name in _top_level_imports(path)
        if name != "pdf_engine" and name not in THIRD_PARTY and name not in sys.stdlib_module_names
    }
    assert foreign == set()
