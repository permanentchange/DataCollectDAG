from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from data_collect_dag.models import ImageFrame, NodeOutcome, NodeResult
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.ros_messages import ensure_image_frame_decoded
from data_collect_dag.sample_context import MissingContextKeyError


def _load_yolo_class():
    from ultralytics import YOLO

    return YOLO


class YoloPersonGateNode(BaseNode):
    def __init__(self, node_config, session: Any) -> None:
        super().__init__(node_config, session)
        self._model = None
        self._model_spec = ""
        self._class_name = "person"
        self._confidence_threshold = 0.25
        self._min_area_ratio = 0.0
        self._device: Optional[str] = None
        self._imgsz = None
        self._verbose = False
        self._predict_lock = threading.Lock()

    def setup(self) -> None:
        self._model_spec = str(self.config["model_path"])
        model_path = Path(self._model_spec).expanduser()
        if not model_path.exists():
            raise ValueError(f"missing yolo model_path: {self._model_spec}")
        self._model_spec = str(model_path)
        self._class_name = str(self.config.get("class_name", "person"))
        self._confidence_threshold = float(self.config.get("confidence_threshold", 0.25))
        self._min_area_ratio = float(self.config["min_area_ratio"])
        self._device = str(self.config.get("device", "cpu"))
        self._imgsz = self.config.get("imgsz")
        self._verbose = bool(self.config.get("verbose", False))
        yolo_cls = _load_yolo_class()
        self._model = yolo_cls(self._model_spec)

    def run(self, sample) -> NodeOutcome:
        try:
            image: ImageFrame = ensure_image_frame_decoded(sample.get(self.inputs["image"]))
        except MissingContextKeyError:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_input")
        if image.image_bgr is None:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_image_decode_failed")
        try:
            predict_kwargs = {
                "source": image.image_bgr,
                "conf": self._confidence_threshold,
                "device": self._device,
                "verbose": self._verbose,
            }
            if self._imgsz is not None:
                predict_kwargs["imgsz"] = self._imgsz
            with self._predict_lock:
                results = self._model.predict(**predict_kwargs)
        except Exception as exc:
            self.logger.exception("person gate inference failed sample_id=%s node_id=%s", sample.sample_id, self.node_id)
            return NodeOutcome(
                status=NodeResult.SKIP_SAMPLE,
                reason=f"{self.node_id}_inference_failed",
                metadata={
                    "passed": False,
                    "error": str(exc),
                    "model_path": self._model_spec,
                },
            )
        summary = self._summarize_results(results, image)
        if summary["passed"]:
            return self.ok(metadata=summary)
        return NodeOutcome(
            status=NodeResult.SKIP_SAMPLE,
            reason=f"{self.node_id}_person_not_detected",
            metadata=summary,
        )

    def _summarize_results(self, results: Any, image: ImageFrame) -> Dict[str, Any]:
        names = _resolve_names(results, self._model)
        boxes_obj = results[0].boxes if results else None
        cls_values = _to_numpy(getattr(boxes_obj, "cls", None)).reshape(-1)
        conf_values = _to_numpy(getattr(boxes_obj, "conf", None)).reshape(-1)
        xyxy_values = _to_numpy(getattr(boxes_obj, "xyxy", None))
        if xyxy_values.ndim == 1 and xyxy_values.size == 4:
            xyxy_values = xyxy_values.reshape(1, 4)
        if xyxy_values.ndim != 2 or xyxy_values.shape[1] != 4:
            xyxy_values = np.empty((0, 4), dtype=np.float64)
        count = min(len(cls_values), len(conf_values), xyxy_values.shape[0])
        image_area = max(1.0, float(image.width * image.height))
        detections: List[Dict[str, Any]] = []
        passed_count = 0
        max_confidence = 0.0
        max_area_ratio = 0.0
        for index in range(count):
            class_id = int(cls_values[index])
            class_name = _resolve_class_name(names, class_id)
            if not _matches_target_class(class_name, class_id, self._class_name, names):
                continue
            x1, y1, x2, y2 = [float(value) for value in xyxy_values[index]]
            width = max(0.0, x2 - x1)
            height = max(0.0, y2 - y1)
            area_ratio = (width * height) / image_area
            confidence = float(conf_values[index])
            passed = confidence >= self._confidence_threshold and area_ratio >= self._min_area_ratio
            if passed:
                passed_count += 1
            max_confidence = max(max_confidence, confidence)
            max_area_ratio = max(max_area_ratio, area_ratio)
            detections.append(
                {
                    "class_id": class_id,
                    "class_name": class_name,
                    "confidence": round(confidence, 6),
                    "area_ratio": round(area_ratio, 6),
                    "bbox_xyxy": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                    "passed": passed,
                }
            )
        return {
            "passed": passed_count > 0,
            "person_count": passed_count,
            "person_candidates": detections,
            "max_confidence": round(max_confidence, 6),
            "max_area_ratio": round(max_area_ratio, 6),
            "image_size": [int(image.width), int(image.height)],
            "model_path": self._model_spec,
            "class_name": self._class_name,
            "confidence_threshold": self._confidence_threshold,
            "min_area_ratio": self._min_area_ratio,
        }


def _resolve_names(results: Any, model: Any) -> Dict[int, str]:
    for source in (results[0] if results else None, model):
        names = getattr(source, "names", None)
        if isinstance(names, dict):
            return {int(key): str(value) for key, value in names.items()}
        if isinstance(names, (list, tuple)):
            return {index: str(value) for index, value in enumerate(names)}
    return {}


def _resolve_class_name(names: Dict[int, str], class_id: int) -> str:
    return names.get(class_id, str(class_id))


def _matches_target_class(class_name: str, class_id: int, target_class: str, names: Dict[int, str]) -> bool:
    if class_name == target_class:
        return True
    return not names and target_class == "person" and class_id == 0


def _to_numpy(value: Any) -> np.ndarray:
    if value is None:
        return np.empty((0,), dtype=np.float64)
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)
