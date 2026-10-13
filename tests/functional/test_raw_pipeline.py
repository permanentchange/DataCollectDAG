"""Exercise the real raw-image DAG with simulated compensated lidar inputs."""

from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from data_collect_dag.app import load_calibration
from data_collect_dag.config import load_app_config
from data_collect_dag.models import FrameMeta, ImageFrame, PointCloudFrame
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import NODE_TYPES, SessionRuntime
from data_collect_dag.status import StatusManager


def test_fps_undist_saves_synced_original_images_and_models(tmp_path, monkeypatch):
    config = load_app_config(Path("demo/cfg.yaml"))
    config.output_root_dir = tmp_path
    config.disk_storage_limit_gb = None
    config.disk_storage_need_gb = None
    # Transport/topic prechecks and physical lidar motion are covered separately.
    pipeline = replace(config.pipelines["fps_undist"], topics=[], time_delay_ms=None)
    calibration = load_calibration(config.calibration_path)
    class CompensatedLidarNode(BaseNode):
        def run(self, sample):
            sample.put(self.outputs["pointcloud"], sample.get(self.inputs["pointcloud"]), self.node_id)
            return self.ok()
    monkeypatch.setitem(NODE_TYPES, "pointcloud_motion_compensation", CompensatedLidarNode)
    def forbidden():
        raise AssertionError("raw pipeline must not invoke undistortion")
    monkeypatch.setattr("data_collect_dag.nodes.image_undistort._load_cv2", forbidden)
    status = StatusManager()
    session = SessionRuntime(config, pipeline, calibration,
                             RosAdapter(config.topics, config.ros_node_name, config.control), status,
                             command_callback=lambda command: None)
    timestamp = 1_000_000_000
    frames = {}
    session.start()
    try:
        for index, (key, topic) in enumerate(config.topics.items()):
            meta = FrameMeta(key, topic.topic, topic.sensor_name, topic.role, timestamp, timestamp)
            if topic.role == "image":
                pixels = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3) + index * 10
                frame = ImageFrame(meta, pixels, "bgr8", 6, 4)
            elif topic.role == "pointcloud":
                frame = PointCloudFrame(meta, np.array([[1., 2., 3.]]), frame_name="base")
            else:
                continue
            frames[key] = frame
            if key != pipeline.main_source:
                session.accept_frame(key, frame)
        session.accept_frame(pipeline.main_source, frames[pipeline.main_source])
        deadline = time.monotonic() + 3
        while status.snapshot().samples_saved < 1:
            assert time.monotonic() < deadline, status.snapshot().to_dict()
            time.sleep(0.01)
    finally:
        session.stop(reason="test_finished")
    root = session.session_root / "xtreme1" / "collect_demo"
    payload = json.loads((root / "camera_config" / f"{timestamp}.json").read_text())
    for index, camera in enumerate(pipeline.nodes_by_id["save_xtreme1"].config["cameras"]):
        key = camera["topic_key"]
        params = calibration["images"][config.topics[key].topic]
        entry = payload["items"][index]
        assert entry["dist_model"] == params["dist_model"]
        assert entry["dist_coeffs"] == params["dist_coeffs"]
        reference = BytesIO()
        Image.fromarray(frames[key].image_bgr[:, :, ::-1]).save(reference, format="JPEG", quality=95)
        assert (root / f"camera_image_{index}" / f"{timestamp}.jpg").read_bytes() == reference.getvalue()
    summary = json.loads((session.session_root / "session_summary.json").read_text())
    assert summary["pipeline_name"] == "fps_undist" and summary["metrics"]["samples_saved"] == 1
