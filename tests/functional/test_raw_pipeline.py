"""Exercise raw-image DAGs with simulated sensors and real static lidar transforms."""

from dataclasses import replace
from io import BytesIO, StringIO
import json
from pathlib import Path
import time
import threading

import numpy as np
from PIL import Image
import pytest

from data_collect_dag.app import load_calibration
from data_collect_dag.config import load_app_config
from data_collect_dag.models import FrameMeta, ImageFrame, PointCloudFrame, VehicleStatusFrame
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.node_registry import load_node_type
from data_collect_dag.session import SessionRuntime
from data_collect_dag.status import StatusManager


@pytest.mark.parametrize("pipeline_name", ["fps_undist", "fps_undist_nomotion"])
def test_raw_pipeline_saves_synced_original_images_and_pointclouds(tmp_path, monkeypatch, pipeline_name):
    config = load_app_config(Path("demo/cfg.yaml"))
    config.output_root_dir = tmp_path
    config.disk_storage_limit_gb = None
    config.disk_storage_need_gb = None
    pipeline = config.pipelines[pipeline_name]
    calibration = load_calibration(config.calibration_path)
    class CompensatedLidarNode(BaseNode):
        def run(self, sample):
            sample.put(self.outputs["pointcloud"], sample.get(self.inputs["pointcloud"]), self.node_id)
            return self.ok()
    class ForbiddenMotionNode(BaseNode):
        def setup(self):
            raise AssertionError("nomotion pipeline must not initialize motion compensation")
    if pipeline_name == "fps_undist":
        # Physical motion compensation is covered by its own node tests.
        pipeline = replace(pipeline, topics=[], time_delay_ms=None)
        monkeypatch.setattr("data_collect_dag.session.load_node_type", lambda name: CompensatedLidarNode if name == "pointcloud_motion_compensation" else load_node_type(name))
    else:
        monkeypatch.setattr("data_collect_dag.session.load_node_type", lambda name: ForbiddenMotionNode if name == "pointcloud_motion_compensation" else load_node_type(name))
    def forbidden():
        raise AssertionError("raw pipeline must not invoke undistortion")
    monkeypatch.setattr("data_collect_dag.nodes.image_undistort._load_cv2", forbidden)
    status = StatusManager()
    adapter = RosAdapter(config.topics, config.ros_node_name, config.control)
    prechecked_topics = []
    def precheck(topic_keys, timeout_ms):
        assert timeout_ms == 500
        prechecked_topics.extend(topic_keys)
        return []
    monkeypatch.setattr(adapter, "wait_for_topic_messages", precheck)
    session = SessionRuntime(config, pipeline, calibration,
                             adapter, status,
                             command_callback=lambda command: None)
    timestamp = 1_000_000_000
    frames = {}
    session.start()
    try:
        downstream_calls = []
        for node_id, node in session._nodes.items():
            if node_id in {"start", "speed_gate"}:
                continue
            original = node.run
            def tracked(sample, original=original, node_id=node_id):
                downstream_calls.append(node_id)
                return original(sample)
            monkeypatch.setattr(node, "run", tracked)
        for count, (stamp, speed, reason) in enumerate([
            (100_000_000, None, "speed_not_matched"),
            (200_000_000, 0.0, "below_min_speed"),
            (300_000_000, float("nan"), "invalid_speed"),
            (900_000_000, None, "speed_not_matched"),
        ], 1):
            if speed is not None:
                topic = config.topics["iccp_status"]
                meta = FrameMeta("iccp_status", topic.topic, topic.sensor_name, topic.role, stamp, stamp)
                session.accept_frame("iccp_status", VehicleStatusFrame(meta, speed))
            topic = config.topics[pipeline.main_source]
            meta = FrameMeta(pipeline.main_source, topic.topic, topic.sensor_name, topic.role, stamp, stamp)
            session.accept_frame(pipeline.main_source, ImageFrame(meta, None, "bgr8", 6, 4))
            deadline = time.monotonic() + 3
            while status.snapshot().samples_skipped < count:
                assert time.monotonic() < deadline, status.snapshot().to_dict()
                time.sleep(0.01)
            assert status.snapshot().skip_reasons["speed_gate_" + reason] >= 1
            assert status.snapshot().samples_saved == 0
            assert status.snapshot().samples_failed == 0
            assert session.get_fps_gate_last_timestamp_ns("saved_sample_fps") is None
            assert not downstream_calls
            assert not any(path.is_file() for path in (session.session_root / "xtreme1").rglob("*"))
        for index, (key, topic) in enumerate(config.topics.items()):
            meta = FrameMeta(key, topic.topic, topic.sensor_name, topic.role, timestamp, timestamp)
            if topic.role == "image":
                pixels = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3) + index * 10
                frame = ImageFrame(meta, pixels, "bgr8", 6, 4)
            elif topic.role == "pointcloud":
                frame = PointCloudFrame(
                    meta, np.array([[1., 2., 3.], [-2., 0.5, 1.]]),
                    frame_name=key if pipeline_name == "fps_undist_nomotion" else "base",
                    intensities=np.array([index, index + 10], dtype=np.float64),
                    has_intensity=np.array([True, True]),
                )
            elif topic.role == "vehicle_status":
                frame = VehicleStatusFrame(meta, -1.0)
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
        assert "save_xtreme1" in downstream_calls
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
        np.testing.assert_allclose(
            [entry["camera_internal"]["fx"], entry["camera_internal"]["fy"]],
            [params["camera_matrix"][0][0] * 6 / params["resolution"][0],
             params["camera_matrix"][1][1] * 4 / params["resolution"][1]],
            rtol=1e-12, atol=0,
        )
        reference = BytesIO()
        Image.fromarray(frames[key].image_bgr[:, :, ::-1]).save(reference, format="JPEG", quality=95)
        assert (root / f"camera_image_{index}" / f"{timestamp}.jpg").read_bytes() == reference.getvalue()
    summary = json.loads((session.session_root / "session_summary.json").read_text())
    assert summary["pipeline_name"] == pipeline_name and summary["metrics"]["samples_saved"] == 1
    assert summary["metrics"]["samples_skipped"] == 4
    assert summary["metrics"]["skip_reasons"] == {
        "speed_gate_speed_not_matched": 2, "speed_gate_below_min_speed": 1, "speed_gate_invalid_speed": 1,
    }
    if pipeline_name == "fps_undist_nomotion":
        assert set(prechecked_topics) == set(frames)
        assert len(prechecked_topics) == 11
        expected_points = []
        expected_intensities = []
        inputs = pipeline.nodes_by_id["aggregate_pointclouds"].config["required_inputs"]
        for output in inputs:
            node = next(node for node in pipeline.nodes_by_id.values() if node.outputs.get("pointcloud") == output)
            topic = next(topic for topic in config.topics.values() if topic.topic == node.config["sensor_topic"])
            frame = frames[topic.topic_key]
            matrix = np.asarray(calibration["pointclouds"][topic.topic])
            expected_points.append(frame.points_xyz @ matrix[:3, :3].T + matrix[:3, 3])
            expected_intensities.append(frame.intensities)
        pcd = (root / "lidar_point_cloud_0" / f"{timestamp}.pcd").read_text()
        points = np.loadtxt(StringIO(pcd.split("DATA ascii\n", 1)[1]))
        np.testing.assert_allclose(points[:, :3], np.vstack(expected_points), rtol=0, atol=1e-6)
        np.testing.assert_array_equal(points[:, 3], np.concatenate(expected_intensities))
        np.testing.assert_array_equal(points[:, 4], np.ones(10))
        records = json.loads((session.session_root / "saved_samples.json").read_text())["items"]
        assert len(records) == 1
        assert records[0]["motion_compensation_applied"] is False
        assert records[0]["pointcloud_in_base"] is True


def test_init_synchronizes_at_zero_speed(tmp_path, monkeypatch):
    config = load_app_config(Path("demo/cfg.yaml"))
    config.output_root_dir = tmp_path
    config.disk_storage_limit_gb = config.disk_storage_need_gb = None
    pipeline = config.pipelines["init"]
    adapter = RosAdapter(config.topics, config.ros_node_name, config.control)
    prechecked = []
    def precheck(topics, timeout_ms):
        prechecked.extend(topics)
        return []
    monkeypatch.setattr(adapter, "wait_for_topic_messages", precheck)
    status = StatusManager()
    session = SessionRuntime(config, pipeline, load_calibration(config.calibration_path), adapter, status,
                             command_callback=lambda command: None)
    finished = threading.Event()
    offsets = {}
    session.start()
    try:
        assert "iccp_status" in prechecked
        original = session._nodes["end"].run
        def end(sample):
            offsets.update(sample.metadata.get("time_offsets_ms", {}))
            result = original(sample)
            finished.set()
            return result
        monkeypatch.setattr(session._nodes["end"], "run", end)
        main = None
        for key, topic in config.topics.items():
            meta = FrameMeta(key, topic.topic, topic.sensor_name, topic.role, 1_000_000_000, 1_000_000_000)
            if topic.role == "image":
                frame = ImageFrame(meta, None, "bgr8", 6, 4)
            elif topic.role == "pointcloud":
                frame = PointCloudFrame(meta, None, frame_name=key)
            elif topic.role == "vehicle_status":
                frame = VehicleStatusFrame(meta, 0.0)
            else:
                continue
            if key == pipeline.main_source:
                main = frame
            else:
                session.accept_frame(key, frame)
        session.accept_frame(pipeline.main_source, main)
        assert finished.wait(3), status.snapshot().to_dict()
    finally:
        session.stop(reason="test_finished")
    expected = {key for key, topic in config.topics.items() if topic.role in {"image", "pointcloud"}}
    assert set(offsets) == expected - {pipeline.main_source}
    assert status.snapshot().samples_skipped == status.snapshot().samples_failed == 0
