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
    # Import only when a pipeline actually contains a YOLO node.
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(
            "YOLO inference requires the optional ultralytics dependencies. "
            "Install data-collect-dag[yolo] or supply compatible inference "
            "packages for your platform."
        ) from exc

    return YOLO


def _load_torch_module():
    # Never import torch for non-YOLO pipelines.
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "A CUDA YOLO device requires a compatible PyTorch installation. "
            "On Jetson, use the vendor-matched torch build, or select device: cpu."
        ) from exc

    return torch


class YoloGateNode(BaseNode):
    def __init__(self, node_config, session: Any) -> None:
        super().__init__(node_config, session)
        self._model = None
        self._model_spec = ""
        self._model_suffix = ""
        self._class_names: List[str] = []
        self._confidence_thresholds: List[float] = []
        self._min_area_ratios: List[float] = []
        self._target_thresholds: Dict[str, Dict[str, float]] = {}
        self._predict_conf = 0.25
        self._device: Optional[str] = None
        self._auto_device = False
        self._imgsz = None
        self._verbose = False
        self._predict_lock = threading.Lock()

    def setup(self) -> None:
        self._model_spec = str(self.config["model_path"])
        model_path = Path(self._model_spec).expanduser()
        if not model_path.exists():
            raise ValueError(f"missing yolo model_path: {self._model_spec}")
        self._model_spec = str(model_path)
        self._model_suffix = model_path.suffix.lower()
        self._class_names = [str(value).strip() for value in self.config["class_names"]]
        self._confidence_thresholds = [float(value) for value in self.config["confidence_thresholds"]]
        self._min_area_ratios = [float(value) for value in self.config["min_area_ratios"]]
        self._target_thresholds = {
            class_name: {
                "confidence_threshold": self._confidence_thresholds[index],
                "min_area_ratio": self._min_area_ratios[index],
            }
            for index, class_name in enumerate(self._class_names)
        }
        self._predict_conf = float(self.config["predict_conf"])
        requested_device = _normalize_device(self.config.get("device", "cpu"))
        self._auto_device = requested_device == "auto" and self._model_suffix != ".engine"
        if self._auto_device:
            # Prefer CUDA when available; do not require it for auto mode.
            try:
                _validate_torch_cuda_available("0")
            except (RuntimeError, ValueError, ImportError) as exc:
                self.logger.info("CUDA is unavailable for YOLO auto device, using CPU: %s", exc)
                self._device = "cpu"
            else:
                self._device = "0"
        elif requested_device == "auto":
            # TensorRT .engine files cannot be automatically rerouted to CPU.
            self._device = None
        else:
            self._device = requested_device
        self._imgsz = self.config.get("imgsz")
        self._verbose = bool(self.config.get("verbose", False))
        if not self._auto_device and self._model_suffix != ".engine" and _device_requires_cuda(self._device):
            _validate_torch_cuda_available(self._device)
        yolo_cls = _load_yolo_class()
        self._model = yolo_cls(self._model_spec)

    def run(self, sample) -> NodeOutcome:
        try:
            image: ImageFrame = ensure_image_frame_decoded(sample.get(self.inputs["image"]))
        except MissingContextKeyError:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_input")
        except ValueError:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_image_decode_failed")
        if image.image_bgr is None:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_image_decode_failed")
        try:
            predict_kwargs = {
                "source": image.image_bgr,
                "conf": self._predict_conf,
                "verbose": self._verbose,
            }
            if self._device is not None and (self._model_suffix != ".engine" or self._device != "cpu"):
                predict_kwargs["device"] = self._device
            if self._imgsz is not None:
                predict_kwargs["imgsz"] = self._imgsz
            with self._predict_lock:
                try:
                    results = self._model.predict(**predict_kwargs)
                except Exception:
                    if not self._auto_device or not _device_requires_cuda(self._device):
                        raise
                    # Only auto-selected CUDA retries on CPU. Explicit CUDA
                    # requests preserve the fail-fast behavior.
                    self.logger.warning(
                        "YOLO CUDA inference failed; retrying this and subsequent samples on CPU",
                        exc_info=True,
                    )
                    self._device = "cpu"
                    predict_kwargs["device"] = "cpu"
                    results = self._model.predict(**predict_kwargs)
        except Exception as exc:
            self.logger.exception("yolo gate inference failed sample_id=%s node_id=%s", sample.sample_id, self.node_id)
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
            reason=f"{self.node_id}_target_not_detected",
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
            target_class = _resolve_target_class(class_name, class_id, self._class_names, names)
            if target_class is None:
                continue
            thresholds = self._target_thresholds[target_class]
            x1, y1, x2, y2 = [float(value) for value in xyxy_values[index]]
            width = max(0.0, x2 - x1)
            height = max(0.0, y2 - y1)
            area_ratio = (width * height) / image_area
            confidence = float(conf_values[index])
            confidence_threshold = thresholds["confidence_threshold"]
            min_area_ratio = thresholds["min_area_ratio"]
            passed = confidence >= confidence_threshold and area_ratio >= min_area_ratio
            if passed:
                passed_count += 1
            max_confidence = max(max_confidence, confidence)
            max_area_ratio = max(max_area_ratio, area_ratio)
            detections.append(
                {
                    "class_id": class_id,
                    "class_name": class_name,
                    "target_class": target_class,
                    "confidence": round(confidence, 6),
                    "confidence_threshold": confidence_threshold,
                    "area_ratio": round(area_ratio, 6),
                    "min_area_ratio": min_area_ratio,
                    "bbox_xyxy": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                    "passed": passed,
                }
            )
        return {
            "passed": passed_count > 0,
            "matched_count": passed_count,
            "target_candidates": detections,
            "max_confidence": round(max_confidence, 6),
            "max_area_ratio": round(max_area_ratio, 6),
            "image_size": [int(image.width), int(image.height)],
            "model_path": self._model_spec,
            "class_names": list(self._class_names),
            "confidence_thresholds": list(self._confidence_thresholds),
            "min_area_ratios": list(self._min_area_ratios),
            "predict_conf": self._predict_conf,
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


def _resolve_target_class(class_name: str, class_id: int, target_classes: List[str], names: Dict[int, str]) -> Optional[str]:
    if class_name in target_classes:
        return class_name
    if not names and "person" in target_classes and class_id == 0:
        return "person"
    return None


def _to_numpy(value: Any) -> np.ndarray:
    if value is None:
        return np.empty((0,), dtype=np.float64)
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _normalize_device(value: Any) -> str:
    device = str(value or "cpu").strip().lower()
    if not device:
        return "cpu"
    if device == "gpu":
        return "0"
    if device.startswith("gpu:") and device[4:].isdigit():
        return device[4:]
    if device == "cuda":
        return "0"
    return device


def _device_requires_cuda(device: Optional[str]) -> bool:
    if device is None:
        return False
    normalized = str(device).strip().lower()
    return normalized not in {"", "none", "cpu", "mps"}


def _validate_torch_cuda_available(device: str) -> None:
    torch = _load_torch_module()
    if not hasattr(torch, "cuda") or not torch.cuda.is_available():
        raise ValueError(
            f"requested YOLO device '{device}' but torch.cuda.is_available() is False; "
            "install a CUDA-enabled torch build or switch the node config device to 'cpu'"
        )
