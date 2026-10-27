"""Le modèle de mise en page, dans le moteur (protocole 1.9, ADR-408).

BabelDOC est AGPL-3.0 : il vit dans le moteur, et le code fermé ne lui
parle plus que par ordres. Ce que ces bancs gardent est ce qu'ADR-198 et
ADR-199 avaient payé, désormais du bon côté de la frontière :

* la carte ne se croit jamais sur parole — la session CONSTRUITE fait foi,
  puis une inférence réelle à la forme de production ;
* plusieurs pages partent en UN lancement, et rendent exactement ce que la
  page par page rendait ; si BabelDOC change de forme, on repasse page par page.

Aucun banc n'a besoin d'une carte : c'est la RÈGLE qu'ils vérifient.
"""

from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pdf_engine import layout
from pdf_engine.protocol import (
    LayoutDetectOrder,
    LayoutRaster,
    OrderError,
    layout_detect_order,
    layout_load_order,
)
from pdf_engine.server import Engine, respond

BOTH = ("CUDAExecutionProvider", "CPUExecutionProvider")


class _Session:
    def __init__(self, providers: tuple[str, ...], explode: bool = False) -> None:
        self._providers = providers
        self._explode = explode
        self.shapes: list[tuple[int, ...]] = []

    def get_providers(self) -> list[str]:
        return list(self._providers)

    def get_inputs(self) -> list[types.SimpleNamespace]:
        return [types.SimpleNamespace(name="images")]

    def run(self, _outputs: object, feed: dict[str, Any]) -> list[Any]:
        batch = next(iter(feed.values()))
        self.shapes.append(tuple(batch.shape))
        if self._explode:
            raise RuntimeError("cudnn64_9.dll introuvable")
        rows = np.zeros((batch.shape[0], 2, 6), dtype=np.float32)
        for index in range(batch.shape[0]):
            # Deux boîtes, la moins sûre d'abord : YoloResult doit les trier.
            rows[index, 0] = [10.0 + index, 20.0, 200.0, 60.0, 0.5, 0.0]
            rows[index, 1] = [30.0 + index, 70.0, 220.0, 90.0, 0.9, 1.0]
        return [rows]


class _Model:
    """Ce que BabelDOC expose et que le lot utilise."""

    def __init__(self, batched: bool = True) -> None:
        self.model_path = "fake.onnx"
        self.lock = threading.Lock()
        self.model: Any = _Session(("CPUExecutionProvider",))
        self.single: list[int] = []
        self._batched = batched

    def resize_and_pad_image(self, image: np.ndarray, new_shape: int) -> np.ndarray:
        return np.zeros((new_shape, new_shape, 3), dtype=np.uint8)

    def scale_boxes(self, _shape: Any, boxes: np.ndarray, _original: Any) -> np.ndarray:
        return boxes

    @property
    def _names(self) -> dict[int, str]:
        if not self._batched:
            raise AttributeError("plus de _names dans cette version")
        return {0: "text", 1: "title"}

    def predict(self, image: np.ndarray, imgsz: int = 800) -> list[Any]:
        from babeldoc.docvision.doclayout import YoloResult

        self.single.append(image.shape[0])
        boxes = np.array([[1.0, 2.0, 3.0, 4.0, 0.8, 0.0]], dtype=np.float32)
        return [YoloResult(boxes_data=boxes, names={0: "text"})]


def _onnxruntime(
    monkeypatch: pytest.MonkeyPatch, announced: tuple[str, ...], session: object
) -> None:
    module = types.ModuleType("onnxruntime")
    module.get_available_providers = lambda: list(announced)  # type: ignore[attr-defined]

    def _build(_path: str, providers: list[str]) -> object:
        if isinstance(session, Exception):
            raise session
        return session

    module.InferenceSession = _build  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", module)


def _rasters(folder: Path, count: int, width: int = 40, height: int = 30) -> LayoutDetectOrder:
    rasters = []
    for index in range(count):
        path = folder / f"{index}.bgr"
        np.full((height, width, 3), index, dtype=np.uint8).tofile(path)
        rasters.append(LayoutRaster(str(path), width, height))
    return LayoutDetectOrder(rasters)


# --- la carte, sur preuve ----------------------------------------------------


def test_a_silent_cpu_fallback_keeps_the_cpu_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le banc qui rougit si quelqu'un fait confiance à la liste annoncée."""
    model = _Model()
    cpu = model.model
    _onnxruntime(monkeypatch, BOTH, _Session(("CPUExecutionProvider",)))

    about = layout.Detector(lambda: model).load("auto")

    assert about["device"] == "cpu"
    assert model.model is cpu
    assert "retombée sur le CPU" in about["detail"]


def test_a_session_that_constructs_but_cannot_run_is_declined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La leçon cuDNN : construite n'est pas la même chose que fonctionnelle."""
    model = _Model()
    cpu = model.model
    _onnxruntime(monkeypatch, BOTH, _Session(BOTH, explode=True))

    assert layout.Detector(lambda: model).load("auto")["device"] == "cpu"
    assert model.model is cpu


def test_a_proven_session_replaces_babeldocs_and_was_proven_at_the_production_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une chauffe à la mauvaise forme est PIRE que pas de chauffe : sonde
    640 -> 8,344 s/page en production, sonde 1024 -> 0,607 (ADR-198)."""
    model = _Model()
    good = _Session(BOTH)
    _onnxruntime(monkeypatch, BOTH, good)

    about = layout.Detector(lambda: model).load("auto")

    assert about["device"] == "gpu"
    assert model.model is good
    assert good.shapes == [(1, 3, 1024, 1024)]


def test_a_construction_failure_keeps_the_cpu_path(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _Model()
    _onnxruntime(monkeypatch, BOTH, RuntimeError("cublasLt64_13.dll manquante"))

    about = layout.Detector(lambda: model).load("auto")

    assert about["device"] == "cpu"
    assert "cublasLt64_13" in about["detail"]


def test_never_does_not_even_build_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def _loud(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("une session a été construite malgré gpu=never")

    module = types.ModuleType("onnxruntime")
    module.get_available_providers = lambda: list(BOTH)  # type: ignore[attr-defined]
    module.InferenceSession = _loud  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", module)

    assert layout.Detector(_Model).load("never")["device"] == "cpu"


def test_a_model_that_cannot_load_is_unavailable() -> None:
    def _missing() -> Any:
        raise ImportError("No module named 'babeldoc'")

    with pytest.raises(OrderError) as refused:
        layout.Detector(_missing).load("never")

    assert refused.value.code == "unavailable"


# --- le lot ------------------------------------------------------------------


def test_several_pages_travel_in_one_launch(tmp_path: Path) -> None:
    """Le banc qui rougit si le lot repasse à une page par lancement (ADR-199)."""
    pytest.importorskip("babeldoc")
    model = _Model()
    detector = layout.Detector(lambda: model)
    detector.load("never")

    answer = detector.detect(_rasters(tmp_path, 4))

    assert model.model.shapes == [(4, 3, 1024, 1024)]
    assert len(answer["pages"]) == 4


def test_every_page_keeps_its_own_boxes_highest_confidence_first(tmp_path: Path) -> None:
    """Un lot qui mélange ses pages est pire qu'un lot lent ; et l'ordre est
    celui de YoloResult, que le code fermé lisait avant la migration."""
    pytest.importorskip("babeldoc")
    model = _Model()
    detector = layout.Detector(lambda: model)
    detector.load("never")

    pages = detector.detect(_rasters(tmp_path, 3))["pages"]

    for index, boxes in enumerate(pages):
        assert boxes[0] == [30.0 + index, 70.0, 220.0, 90.0, pytest.approx(0.9), "title"]
        assert boxes[1][0] == 10.0 + index
        assert boxes[1][5] == "text"


def test_a_changed_babeldoc_falls_back_to_one_page_at_a_time(tmp_path: Path) -> None:
    """Le repli existe parce que le chemin en lot lit des attributs privés."""
    pytest.importorskip("babeldoc")
    model = _Model(batched=False)
    detector = layout.Detector(lambda: model)
    detector.load("never")

    pages = detector.detect(_rasters(tmp_path, 3))["pages"]

    assert model.single == [30, 30, 30]
    assert len(pages) == 3


def test_a_raster_that_lies_about_its_size_is_refused(tmp_path: Path) -> None:
    detector = layout.Detector(_Model)
    detector.load("never")
    (tmp_path / "0.bgr").write_bytes(b"\x00" * 10)

    with pytest.raises(OrderError) as refused:
        detector.detect(LayoutDetectOrder([LayoutRaster(str(tmp_path / "0.bgr"), 40, 30)]))

    assert refused.value.code == "bad_order"


def test_detecting_before_loading_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OrderError) as refused:
        layout.Detector(_Model).detect(_rasters(tmp_path, 1))

    assert refused.value.code == "unavailable"


# --- les ordres ----------------------------------------------------------------


def test_the_orders_are_validated() -> None:
    assert layout_load_order({"gpu": "never"}).gpu == "never"
    for bad in ({"gpu": "cuda"}, []):
        with pytest.raises(OrderError):
            layout_load_order(bad)
    for bad in ({}, {"rasters": []}, {"rasters": [{"path": "relatif.bgr", "width": 1, "height": 1}]},
                {"rasters": [{"path": "C:/a.bgr", "width": 0, "height": 1}]}):
        with pytest.raises(OrderError):
            layout_detect_order(bad)


def test_the_server_answers_both_orders(tmp_path: Path) -> None:
    pytest.importorskip("babeldoc")
    engine = Engine()
    engine.detector = layout.Detector(_Model)

    loaded = respond(engine, "POST", "/v1/layout_load", json.dumps({"gpu": "never"}).encode())
    order = _rasters(tmp_path, 2)
    body = {"rasters": [vars(raster) for raster in order.rasters]}
    detected = respond(engine, "POST", "/v1/layout_detect", json.dumps(body).encode())

    assert loaded.status == 200
    assert json.loads(loaded.body)["device"] == "cpu"
    assert detected.status == 200
    assert len(json.loads(detected.body)["pages"]) == 2
