from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from data_collect_dag.io_utils import save_ascii_pcd, save_jpg, write_json
from data_collect_dag.models import NodeOutcome, NodeResult
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.ros_messages import ensure_image_frame_decoded, ensure_pointcloud_frame_parsed
from data_collect_dag.sample_context import MissingContextKeyError


class Xtreme1SaveNode(BaseNode):
    def setup(self) -> None:
        self._dataset_name = str(self.config.get("dataset_name", "collect_demo"))
        if "pointcloud" not in self.inputs:
            raise ValueError("pointcloud input is required")
        self._cameras = _resolve_camera_bindings(
            self.config.get("cameras"),
            self.inputs,
            self.session.app_config.topics,
            self.session.calibration["images"],
        )
        self._save_root = self.session.session_root / "xtreme1" / self._dataset_name
        (self._save_root / "camera_config").mkdir(parents=True, exist_ok=True)
        (self._save_root / "lidar_point_cloud_0").mkdir(parents=True, exist_ok=True)
        for index in range(len(self._cameras)):
            (self._save_root / f"camera_image_{index}").mkdir(parents=True, exist_ok=True)

    def run(self, sample):
        if not sample.has(self.inputs["pointcloud"]):
            self.logger.debug("save skip sample_id=%s node_id=%s missing_required=pointcloud", sample.sample_id, self.node_id)
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_pointcloud_missing")
        for input_name, _, _ in self._cameras:
            if not sample.has(self.inputs[input_name]):
                self.logger.debug(
                    "save skip sample_id=%s node_id=%s missing_required=%s",
                    sample.sample_id,
                    self.node_id,
                    input_name,
                )
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_{input_name}_missing")

        camera_images = [
            ensure_image_frame_decoded(sample.get(self.inputs[input_name]))
            for input_name, _, _ in self._cameras
        ]
        pointcloud = ensure_pointcloud_frame_parsed(sample.get(self.inputs["pointcloud"]))
        if pointcloud.frame_name != "base":
            self.logger.debug(
                "save fail sample_id=%s node_id=%s pointcloud_frame=%s",
                sample.sample_id,
                self.node_id,
                pointcloud.frame_name,
            )
            return NodeOutcome(status=NodeResult.FAIL_SAMPLE, reason=f"{self.node_id}_pointcloud_not_in_base")
        stem = str(int(camera_images[0].meta.source_timestamp_ns))
        storage_tracker = getattr(self.session, "storage_tracker", None)
        saved_images: List[str] = []
        saved_camera_topics: List[str] = []
        camera_configs: List[Dict[str, Any]] = []
        for index, ((_, topic_key, ros_topic), image) in enumerate(zip(self._cameras, camera_images)):
            image_path = self._save_root / f"camera_image_{index}" / f"{stem}.jpg"
            assert image.image_bgr is not None
            save_jpg(image_path, image.image_bgr, storage_tracker=storage_tracker)
            saved_images.append(str(image_path))
            saved_camera_topics.append(topic_key)
            camera_params = dict(self.session.calibration["images"][ros_topic])
            corrected_params = sample.metadata.get("undistort_camera_params", {}).get(topic_key)
            if corrected_params is not None:
                camera_params.update(corrected_params)
            camera_configs.append(_build_camera_config(camera_params, image))
        pcd_path = self._save_root / "lidar_point_cloud_0" / f"{stem}.pcd"
        assert pointcloud.points_xyz is not None
        try:
            save_ascii_pcd(
                pcd_path,
                pointcloud.points_xyz,
                intensities=pointcloud.intensities,
                has_intensity=pointcloud.has_intensity,
                storage_tracker=storage_tracker,
            )
        except ValueError as exc:
            self.logger.debug("save fail sample_id=%s node_id=%s invalid_intensity=%s", sample.sample_id, self.node_id, exc)
            return NodeOutcome(status=NodeResult.FAIL_SAMPLE, reason=f"{self.node_id}_invalid_intensity")
        config_path = self._save_root / "camera_config" / f"{stem}.json"
        write_json(config_path, {"items": camera_configs}, storage_tracker=storage_tracker)
        sample.metadata["save_result"] = {
            "saved": True,
            "dataset_name": self._dataset_name,
            "camera_config": str(config_path),
            "images": saved_images,
            "saved_camera_topics": saved_camera_topics,
            "pointcloud": str(pcd_path),
        }
        self.session.metrics.save_output(str(config_path))
        self.session.metrics.save_output(str(pcd_path))
        self.logger.debug(
            "sample saved sample_id=%s node_id=%s cameras=%s pointcloud=%s config=%s",
            sample.sample_id,
            self.node_id,
            saved_camera_topics,
            pcd_path,
            config_path,
        )
        return self.ok()


def _resolve_camera_bindings(
    configured_cameras: Any,
    inputs: Dict[str, str],
    available_topics: Dict[str, Any],
    image_calibrations: Dict[str, Any],
) -> List[Tuple[str, str, str]]:
    if not isinstance(configured_cameras, list) or not configured_cameras:
        raise ValueError("cameras must be a non-empty list")
    bindings: List[Tuple[str, str, str]] = []
    input_names = set()
    topic_keys = set()
    for index, item in enumerate(configured_cameras):
        if not isinstance(item, dict):
            raise ValueError(f"camera binding at index {index} must be a mapping")
        input_name = str(item.get("input_name", "")).strip()
        topic_key = str(item.get("topic_key", "")).strip()
        if not input_name:
            raise ValueError(f"camera binding at index {index} missing input_name")
        if not topic_key:
            raise ValueError(f"camera binding at index {index} missing topic_key")
        if input_name not in inputs:
            raise ValueError(f"camera input_name not found in node inputs: {input_name}")
        if input_name in input_names:
            raise ValueError(f"duplicate camera input_name: {input_name}")
        if topic_key in topic_keys:
            raise ValueError(f"duplicate camera topic_key: {topic_key}")
        topic = available_topics.get(topic_key)
        if topic is None:
            raise ValueError(f"camera topic_key not found in ros.topics: {topic_key}")
        if topic.role != "image":
            raise ValueError(f"camera topic_key must have image role: {topic_key}")
        if topic.topic not in image_calibrations:
            raise ValueError(f"missing camera calibration for {topic_key} -> {topic.topic}")
        input_names.add(input_name)
        topic_keys.add(topic_key)
        bindings.append((input_name, topic_key, topic.topic))
    return bindings


def _build_camera_config(camera_params: Dict[str, Any], image) -> Dict[str, Any]:
    matrix = np.asarray(camera_params["camera_matrix"], dtype=np.float64)
    calibration_resolution = camera_params.get("resolution")
    calibration_width = 0
    calibration_height = 0
    if calibration_resolution is not None:
        try:
            calibration_width, calibration_height = (int(value) for value in calibration_resolution)
        except (TypeError, ValueError) as exc:
            raise ValueError("camera calibration resolution must contain two positive integers") from exc
        if calibration_width <= 0 or calibration_height <= 0:
            raise ValueError("camera calibration resolution must contain two positive integers")

    width = int(image.width) if image is not None else calibration_width
    height = int(image.height) if image is not None else calibration_height
    scale_x = float(width) / float(calibration_width) if image is not None and calibration_width else 1.0
    scale_y = float(height) / float(calibration_height) if image is not None and calibration_height else 1.0
    return {
        "camera_internal": {
            "fx": float(matrix[0][0]) * scale_x,
            "fy": float(matrix[1][1]) * scale_y,
            "cx": float(matrix[0][2]) * scale_x,
            "cy": float(matrix[1][2]) * scale_y,
        },
        "width": width,
        "height": height,
        "camera_external": np.asarray(camera_params["base2camera"], dtype=np.float64).reshape(-1, order="F").tolist(),
        "rowMajor": False,
    }
