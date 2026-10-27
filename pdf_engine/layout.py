# Copyright (C) 2026 The BilingualForge PDF engine authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Page layout analysis: DocLayout-YOLO, through BabelDOC (1.9).

BabelDOC is AGPL-3.0, like PyMuPDF, and lives here for the same reason. The
model only ever sees PIXELS: rasters the client had rendered and wrote as BGR
bytes. It parses no client file, so the engine that answers these orders
need not be the sandboxed one, and can keep the graphics card.

What stays here is the mechanics: loading the model, proving a CUDA session
before using it, and running several pages in one launch while showing the
model exactly what BabelDOC's own ``predict`` would. Which detections count,
how they become paragraphs, and how long a device may stay silent are the
client's decisions.
"""

from __future__ import annotations

import glob
import os
import site
from collections.abc import Callable
from typing import Any

import numpy as np

from pdf_engine.protocol import LayoutDetectOrder, LayoutRaster, OrderError

#: BabelDOC letterboxes every page to this side whatever ``imgsz`` says, and
#: keeps every candidate above this confidence: the batched path reproduces
#: both, read off its source.
MODEL_SIDE = 1024
MODEL_CONFIDENCE = 0.25


def _load_babeldoc() -> Any:
    from babeldoc.docvision.doclayout import DocLayoutModel  # noqa: PLC0415 - optional

    return DocLayoutModel.load_onnx()


def _register_cuda_libraries() -> None:
    """Put the ``nvidia-*`` DLL folders where the Windows loader looks, once.

    The CUDA provider pulls ``cublasLt`` as a transitive dependency, which
    the loader resolves through ``PATH``, not through ``add_dll_directory``.
    Only the folders not already there are added: ``PATH`` is capped.
    """
    folders: list[str] = []
    for base in site.getsitepackages():
        folders.extend(glob.glob(os.path.join(base, "nvidia", "*", "bin")))
    present = os.environ.get("PATH", "").split(os.pathsep)
    missing = [folder for folder in folders if folder not in present]
    for folder in missing:
        try:
            os.add_dll_directory(folder)
        except (OSError, AttributeError):
            continue
    if missing:
        os.environ["PATH"] = os.pathsep.join([*missing, os.environ.get("PATH", "")])


class Detector:
    """The layout model of one engine process, loaded on the first order."""

    def __init__(self, loader: Callable[[], Any] = _load_babeldoc) -> None:
        self._loader = loader
        self.model: Any = None
        self.device = "cpu"
        self.announced: list[str] = []
        self.actual: list[str] = []
        self.detail = ""

    def load(self, gpu: str) -> dict[str, Any]:
        """Load the model once; with ``gpu="auto"``, move it to a PROVEN card.

        Raises:
            OrderError: ``unavailable`` when BabelDOC or its weights cannot load.
        """
        if self.model is None:
            try:
                self.model = self._loader()
            except Exception as exc:  # noqa: BLE001 - every cause means the same
                raise OrderError(
                    "unavailable",
                    f"Modèle de mise en page indisponible : {exc}",
                    503,
                ) from exc
            if gpu == "auto":
                self._try_cuda()
        return self.about()

    def about(self) -> dict[str, Any]:
        """Where the model runs, and on what evidence."""
        return {
            "device": self.device,
            "announced": self.announced,
            "actual": self.actual,
            "detail": self.detail,
        }

    def _try_cuda(self) -> None:
        """Replace BabelDOC's CPU session by a CUDA one, when it is PROVEN.

        BabelDOC keeps only providers whose name is ``cpu``. The announced
        list is no evidence — ``get_available_providers()`` names CUDA on a
        wheel that cannot load it — so the providers are read off the BUILT
        session, and one inference at the production shape must then run: a
        warm-up at another shape leaves the convolutions tuned for nothing.
        Any failure keeps the CPU session, already loaded and correct.
        """
        try:
            import onnxruntime  # noqa: PLC0415 - optional
        except ImportError:
            self.detail = "onnxruntime absent"
            return
        self.announced = list(onnxruntime.get_available_providers())
        path = getattr(self.model, "model_path", None)
        if "CUDAExecutionProvider" not in self.announced or not path:
            self.detail = "aucun fournisseur CUDA annoncé"
            return
        _register_cuda_libraries()
        try:
            session = onnxruntime.InferenceSession(
                str(path), providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
            self.actual = list(session.get_providers())
            if "CUDAExecutionProvider" not in self.actual:
                self.detail = "la session est retombée sur le CPU"
                return
            name = session.get_inputs()[0].name
            session.run(None, {name: np.zeros((1, 3, MODEL_SIDE, MODEL_SIDE), np.float32)})
            self.model.model = session
        except Exception as exc:  # noqa: BLE001 - the CPU session stays
            self.detail = f"session CUDA refusée : {str(exc)[:200]}"
            return
        self.device = "gpu"
        self.detail = ""

    def detect(self, order: LayoutDetectOrder) -> dict[str, Any]:
        """The regions of each raster: ``[x0, y0, x1, y1, confidence, label]``.

        Coordinates are in the raster's pixels, every candidate above
        :data:`MODEL_CONFIDENCE`, highest confidence first, as BabelDOC's
        ``YoloResult`` orders them.

        Raises:
            OrderError: ``unavailable`` before ``layout_load``, ``not_found``
                or ``bad_order`` for a raster that is not what it says.
        """
        if self.model is None:
            raise OrderError("unavailable", "Charger d'abord le modèle (layout_load).", 409)
        images = [_raster(raster) for raster in order.rasters]
        results = self._predict_batch(images)
        if results is None:
            # BabelDOC's internals are not what the batched path expects:
            # back to one page at a time, which always works.
            results = [self.model.predict(image, imgsz=800)[0] for image in images]
        return {"pages": [_boxes(result) for result in results], "device": self.device}

    def _predict_batch(self, images: list[np.ndarray]) -> list[Any] | None:
        """Several pages in ONE launch, or None when BabelDOC changed shape.

        BabelDOC's ``predict`` takes a ``batch_size`` and overwrites it with
        1. This repeats its preprocessing by calling its own
        ``resize_and_pad_image`` and ``scale_boxes``: only the batching may
        differ, never what the model sees.
        """
        try:
            from babeldoc.docvision.doclayout import YoloResult  # noqa: PLC0415

            resize = self.model.resize_and_pad_image
            scale_boxes = self.model.scale_boxes
            names = self.model._names
            session = self.model.model
        except (AttributeError, ImportError):
            return None

        prepared, shapes = [], []
        for image in images:
            shapes.append(image.shape[:2])
            padded = resize(image, new_shape=MODEL_SIDE)
            prepared.append(np.transpose(padded, (2, 0, 1)).astype(np.float32) / 255.0)
        stacked = np.stack(prepared, axis=0)
        height, width = stacked.shape[2:]
        with self.model.lock:
            predictions = session.run(None, {"images": stacked})[0]

        results = []
        for index in range(len(images)):
            boxes = predictions[index]
            boxes = boxes[boxes[..., 4] > MODEL_CONFIDENCE]
            if len(boxes) > 0:
                boxes[..., :4] = scale_boxes((height, width), boxes[..., :4], shapes[index])
            results.append(YoloResult(boxes_data=boxes, names=names))
        return results


def _raster(raster: LayoutRaster) -> np.ndarray:
    try:
        data = np.fromfile(raster.path, dtype=np.uint8)
    except FileNotFoundError as exc:
        raise OrderError("not_found", f"Raster absent : {raster.path}", 404) from exc
    if data.size != raster.width * raster.height * 3:
        raise OrderError(
            "bad_order",
            f"{raster.path} : {data.size} octets, {raster.width}x{raster.height}x3 attendus.",
        )
    return data.reshape(raster.height, raster.width, 3)


def _boxes(result: Any) -> list[list[Any]]:
    names = getattr(result, "names", {}) or {}
    boxes: list[list[Any]] = []
    for box in getattr(result, "boxes", []):
        coordinates = [float(value) for value in getattr(box, "xyxy", [])]
        if len(coordinates) != 4:
            continue
        label = str(names.get(int(getattr(box, "cls", -1)), "text"))
        boxes.append([*coordinates, float(getattr(box, "conf", 0.0)), label])
    return boxes
