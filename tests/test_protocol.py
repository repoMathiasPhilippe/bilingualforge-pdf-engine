"""The engine's orders are checked before any document is touched (ADR-388)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pdf_engine.protocol import OrderError, close_order, open_order, render_order

PDF = str(Path(__file__).resolve())


def _order(**changes: Any) -> dict[str, Any]:
    return {"document": {"path": PDF}, "page": 0, **changes}


def test_a_minimal_order_takes_the_engine_defaults() -> None:
    order = render_order(_order())

    assert (order.page, order.dpi, order.matrix, order.clip) == (0, None, None, None)
    assert (order.colorspace, order.alpha, order.annots) == ("rgb", False, True)
    assert order.output.kind == "samples"


def test_every_field_of_a_full_order_is_read() -> None:
    order = render_order(
        _order(
            page=3,
            matrix=[2, 0, 0, 2, 0, 0],
            clip=[1, 2, 3.5, 4],
            colorspace="gray",
            alpha=True,
            annots=False,
            output={"kind": "shared", "path": PDF},
        )
    )

    assert order.matrix == (2, 0, 0, 2, 0, 0)
    assert order.clip == (1, 2, 3.5, 4)
    assert (order.colorspace, order.alpha, order.annots) == ("gray", True, False)
    assert (order.output.kind, order.output.path) == ("shared", PDF)


@pytest.mark.parametrize(
    "changes",
    [
        {"document": {"path": "relative.pdf"}},
        {"document": {}},
        {"document": {"path": PDF, "handle": "h"}},
        {"document": {"handle": ""}},
        {"page": -1},
        {"page": True},
        {"page": 1.0},
        {"dpi": 0},
        {"dpi": float("nan")},
        {"dpi": 150, "matrix": [1, 0, 0, 1, 0, 0]},
        {"matrix": [1, 0, 0, 1]},
        {"clip": [0, 0, "1", 1]},
        {"colorspace": "cmyk"},
        {"alpha": "yes"},
        {"output": {"kind": "jpeg"}},
        {"output": {"kind": "file"}},
        {"output": {"kind": "shared", "path": "relative.bin"}},
    ],
)
def test_a_malformed_order_is_refused_naming_its_fault(changes: dict[str, Any]) -> None:
    with pytest.raises(OrderError) as refused:
        render_order(_order(**changes))

    assert refused.value.code == "bad_order"
    assert refused.value.status == 400
    assert refused.value.message


def test_a_body_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(OrderError):
        render_order([1, 2])


def test_an_order_may_name_an_open_document_by_its_handle() -> None:
    order = render_order({"document": {"handle": "ab12"}, "page": 0})

    assert (order.path, order.handle) == (None, "ab12")


def test_open_and_close_orders_are_checked() -> None:
    assert open_order({"document": {"path": PDF}}) == (PDF, False)
    assert close_order({"handle": "ab12"}) == "ab12"
    for bad in ({"document": {"path": "relative.pdf"}}, {}, [1]):
        with pytest.raises(OrderError):
            open_order(bad)
    for bad in ({"handle": 3}, {}, None):
        with pytest.raises(OrderError):
            close_order(bad)
