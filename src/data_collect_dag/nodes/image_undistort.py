from __future__ import annotations

import threading
from typing import Any, Dict, Tuple

import numpy as np

from data_collect_dag.io_utils import save_jpg, write_json
from data_collect_dag.models import ImageFrame, NodeOutcome, NodeResult
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.ros_messages import ensure_image_frame_decoded
from data_collect_dag.sample_context import MissingContextKeyError


def _load_cv2():
    import cv2

    return cv2


class ImageUndistortNode(BaseNode):
    """Undistort one fish-eye image using its configured camera calibration."""

    def setup(self) -> None:
        if "image" not in self.inputs:
            raise ValueError(f"image_undistort node {self.node_id} missing image input")
        if "image" not in self.outputs:
            raise ValueError(f"image_undistort node {self.node_id} missing image output")
        self._source = str(self.config.get("source", "")).strip()
        if not self._source:
            raise ValueError(f"image_undistort node {self.node_id} missing source")
        topic_config = self.session.app_config.topics.get(self._source)
        if topic_config is None:
            raise ValueError(f"image_undistort node {self.node_id} references unknown source: {self._source}")
        if topic_config.role != "image":
            raise ValueError(f"image_undistort node {self.node_id} source must have image role: {self._source}")

        camera_params = self.session.calibration.get("images", {}).get(topic_config.topic)
        if not isinstance(camera_params, dict):
            raise ValueError(f"missing camera calibration for {self._source} -> {topic_config.topic}")
        if camera_params.get("is_fisheye") is not True:
            raise ValueError(f"image_undistort node {self.node_id} calibration must set is_fisheye: true")
        self._camera_matrix = _read_camera_matrix(camera_params)
        self._dist_coeffs = _read_dist_coeffs(camera_params)
        self._resolution = _read_resolution(camera_params)
        self._write_debug_artifacts = bool(self.config.get("write_debug_artifacts", False))
        self._maps_by_resolution: Dict[Tuple[int, int], Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._map_lock = threading.Lock()

    def run(self, sample) -> NodeOutcome:
        try:
            image: ImageFrame = ensure_image_frame_decoded(sample.get(self.inputs["image"]))
        except MissingContextKeyError:
            return _skip(self.node_id, "missing_input")
        except ValueError:
            return _skip(self.node_id, "image_decode_failed")
        if image.image_bgr is None:
            return _skip(self.node_id, "image_decode_failed")
        if image.meta.topic_key != self._source:
            return _skip(self.node_id, "source_mismatch")
        image_size = (int(image.width), int(image.height))
        if image.image_bgr.shape[:2] != (image.height, image.width):
            return _skip(self.node_id, "resolution_mismatch")
        try:
            map1, map2, corrected_matrix = self._maps_for_resolution(image_size)
            cv2 = _load_cv2()
            undistorted = cv2.remap(
                image.image_bgr,
                map1,
                map2,
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
            )
        except Exception as exc:
            self.logger.exception("image undistort failed sample_id=%s node_id=%s", sample.sample_id, self.node_id)
            return NodeOutcome(
                status=NodeResult.SKIP_SAMPLE,
                reason=f"{self.node_id}_undistort_failed",
                metadata={"error": str(exc)},
            )
        if self._write_debug_artifacts:
            try:
                self._write_debug_images(sample.sample_id, image.image_bgr, undistorted, corrected_matrix, image_size)
            except Exception as exc:
                self.logger.exception("image undistort diagnostics failed sample_id=%s node_id=%s", sample.sample_id, self.node_id)
                return NodeOutcome(
                    status=NodeResult.SKIP_SAMPLE,
                    reason=f"{self.node_id}_diagnostics_failed",
                    metadata={"error": str(exc)},
                )
        sample.put(self.outputs["image"], image.replace(image_bgr=undistorted), self.node_id)
        sample.metadata.setdefault("undistort_camera_params", {})[self._source] = {
            "camera_matrix": corrected_matrix.tolist(),
            "resolution": list(image_size),
        }
        return self.ok(metadata={"source": self._source, "resolution": list(image_size)})

    def _maps_for_resolution(self, image_size: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        width, height = image_size
        calibration_width, calibration_height = self._resolution
        if not np.isclose(
            float(width) / float(height),
            float(calibration_width) / float(calibration_height),
            rtol=1e-4,
            atol=1e-6,
        ):
            raise ValueError("image resolution aspect ratio does not match calibration")
        with self._map_lock:
            cached = self._maps_by_resolution.get(image_size)
            if cached is not None:
                return cached
            corrected_matrix = self._camera_matrix.copy()
            corrected_matrix[0, :] *= float(width) / float(calibration_width)
            corrected_matrix[1, :] *= float(height) / float(calibration_height)
            corrected_matrix[2, :] = self._camera_matrix[2, :]
            cv2 = _load_cv2()
            map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                corrected_matrix,
                self._dist_coeffs,
                np.eye(3, dtype=np.float64),
                corrected_matrix,
                image_size,
                cv2.CV_16SC2,
            )
            result = (map1, map2, corrected_matrix)
            self._maps_by_resolution[image_size] = result
            return result

    def _write_debug_images(
        self,
        sample_id: str,
        raw_image: np.ndarray,
        undistorted: np.ndarray,
        corrected_matrix: np.ndarray,
        image_size: Tuple[int, int],
    ) -> None:
        root = self.session.session_root / "diagnostics" / "undistort" / sample_id / self._source
        storage_tracker = getattr(self.session, "storage_tracker", None)
        save_jpg(root / "raw.jpg", raw_image, storage_tracker=storage_tracker)
        save_jpg(root / "undistorted.jpg", undistorted, storage_tracker=storage_tracker)
        write_json(
            root / "calibration.json",
            {
                "camera_topic_key": self._source,
                "calibration_resolution": list(self._resolution),
                "resolution": list(image_size),
                "camera_matrix": self._camera_matrix.tolist(),
                "dist_coeffs": self._dist_coeffs.reshape(-1).tolist(),
                "undistorted_camera_matrix": corrected_matrix.tolist(),
            },
            storage_tracker=storage_tracker,
        )


def _read_camera_matrix(camera_params: Dict[str, Any]) -> np.ndarray:
    try:
        matrix = np.asarray(camera_params["camera_matrix"], dtype=np.float64)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("camera calibration missing valid camera_matrix") from exc
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValueError("camera calibration camera_matrix must be a finite 3x3 matrix")
    return matrix


def _read_dist_coeffs(camera_params: Dict[str, Any]) -> np.ndarray:
    try:
        coeffs = np.asarray(camera_params["dist_coeffs"], dtype=np.float64).reshape(-1, 1)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("camera calibration missing valid dist_coeffs") from exc
    if coeffs.shape != (4, 1) or not np.all(np.isfinite(coeffs)):
        raise ValueError("camera calibration dist_coeffs must contain four finite values")
    return coeffs


def _read_resolution(camera_params: Dict[str, Any]) -> Tuple[int, int]:
    try:
        width, height = camera_params["resolution"]
        size = (int(width), int(height))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("camera calibration missing valid resolution") from exc
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError("camera calibration resolution must be positive")
    return size


def _skip(node_id: str, suffix: str) -> NodeOutcome:
    return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{node_id}_{suffix}")
