from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from data_collect_dag.io_utils import save_ascii_pcd, save_jpg, write_json
from data_collect_dag.models import NodeOutcome, NodeResult
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.ros_messages import ensure_image_frame_decoded, ensure_pointcloud_frame_parsed
from data_collect_dag.sample_context import MissingContextKeyError


CAMERA_TOPIC_ORDER = [
    "front_wide_camera",
    "front_fisheye_camera",
    "rear_fisheye_camera",
    "left_fisheye_camera",
    "right_fisheye_camera",
]

CAMERA_INPUT_NAME_BY_TOPIC = {
    "front_wide_camera": "image_front",
    "front_fisheye_camera": "image_fisheye_front",
    "rear_fisheye_camera": "image_fisheye_rear",
    "left_fisheye_camera": "image_fisheye_left",
    "right_fisheye_camera": "image_fisheye_right",
}


class Xtreme1SaveNode(BaseNode):
    def setup(self) -> None:
        self._dataset_name = str(self.config.get("dataset_name", "collect_demo"))
        self._save_root = self.session.session_root / "xtreme1" / self._dataset_name
        (self._save_root / "camera_config").mkdir(parents=True, exist_ok=True)
        (self._save_root / "lidar_point_cloud_0").mkdir(parents=True, exist_ok=True)
        for index in range(len(CAMERA_TOPIC_ORDER)):
            (self._save_root / f"camera_image_{index}").mkdir(parents=True, exist_ok=True)
        for topic_key in CAMERA_TOPIC_ORDER:
            if topic_key not in self.session.app_config.topics:
                continue
            ros_topic = self.session.app_config.topics[topic_key].topic
            if ros_topic not in self.session.calibration["images"]:
                raise ValueError(f"missing camera calibration for {topic_key} -> {ros_topic}")

    def run(self, sample):
        required_inputs = list(self.config.get("required_inputs") or [])
        for key in required_inputs:
            if not sample.has(self.inputs[key]):
                self.logger.debug("save skip sample_id=%s node_id=%s missing_required=%s", sample.sample_id, self.node_id, key)
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_{key}_missing")
        main_image = ensure_image_frame_decoded(sample.get(self.inputs["image_front"]))
        pointcloud = ensure_pointcloud_frame_parsed(sample.get(self.inputs["pointcloud"]))
        if pointcloud.frame_name != "base":
            self.logger.debug(
                "save fail sample_id=%s node_id=%s pointcloud_frame=%s",
                sample.sample_id,
                self.node_id,
                pointcloud.frame_name,
            )
            return NodeOutcome(status=NodeResult.FAIL_SAMPLE, reason=f"{self.node_id}_pointcloud_not_in_base")
        stem = str(int(main_image.meta.source_timestamp_ns))
        saved_images: List[str] = []
        saved_camera_topics: List[str] = []
        camera_configs: List[Dict[str, Any]] = []
        for index, topic_key in enumerate(CAMERA_TOPIC_ORDER):
            if topic_key not in self.session.app_config.topics:
                continue
            input_name = CAMERA_INPUT_NAME_BY_TOPIC[topic_key]
            ros_topic = self.session.app_config.topics[topic_key].topic
            image = None
            if input_name in self.inputs:
                input_key = self.inputs[input_name]
                if sample.has(input_key):
                    image = ensure_image_frame_decoded(sample.get(input_key))
                    image_path = self._save_root / f"camera_image_{index}" / f"{stem}.jpg"
                    assert image.image_bgr is not None
                    save_jpg(image_path, image.image_bgr)
                    saved_images.append(str(image_path))
                    saved_camera_topics.append(topic_key)
            camera_configs.append(_build_camera_config(self.session.calibration["images"][ros_topic], image))
        pcd_path = self._save_root / "lidar_point_cloud_0" / f"{stem}.pcd"
        assert pointcloud.points_xyz is not None
        save_ascii_pcd(pcd_path, pointcloud.points_xyz)
        config_path = self._save_root / "camera_config" / f"{stem}.json"
        write_json(config_path, {"items": camera_configs})
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


def _build_camera_config(camera_params: Dict[str, Any], image) -> Dict[str, Any]:
    matrix = np.asarray(camera_params["camera_matrix"], dtype=np.float64)
    resolution = list(camera_params.get("resolution") or [0, 0])
    width = int(image.width) if image is not None else int(resolution[0])
    height = int(image.height) if image is not None else int(resolution[1])
    return {
        "camera_internal": {
            "fx": float(matrix[0][0]),
            "fy": float(matrix[1][1]),
            "cx": float(matrix[0][2]),
            "cy": float(matrix[1][2]),
        },
        "width": width,
        "height": height,
        "camera_external": np.asarray(camera_params["base2camera"], dtype=np.float64).reshape(-1, order="F").tolist(),
        "rowMajor": False,
    }
