import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from data_collect_dag.models import NodeConfig, NodeResult, PointCloudFrame
from data_collect_dag.nodes.xtreme1 import Xtreme1SaveNode
from data_collect_dag.sample_context import SampleContext
from tests.unit.conftest import make_meta


def test_xtreme1_save_node_outputs_expected_files(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main_source")
    sample.put(
        "front_wide_camera_undistorted",
        image_frame.replace(image_bgr=np.full_like(image_frame.image_bgr, [200, 0, 0])),
        "undistort_front_wide",
    )
    sample.metadata["undistort_camera_params"] = {
        "front_wide_camera": {
            "camera_matrix": [[500.0, 0.0, 480.0], [0.0, 500.0, 270.0], [0.0, 0.0, 1.0]],
            "resolution": [960, 540],
        }
    }
    sample.put("front_fisheye_camera", image_frame, "sync")
    sample.put("rear_fisheye_camera", image_frame, "sync")
    sample.put("left_fisheye_camera", image_frame, "sync")
    sample.put("right_fisheye_camera", image_frame, "sync")
    sample.put("aggregated_pointcloud", PointCloudFrame(make_meta("top_lidar", "pointcloud", 1_000_000_000), np.array([[1.0, 2.0, 3.0]]), frame_name="base"), "agg")
    node = Xtreme1SaveNode(
        NodeConfig(
            "save",
            "xtreme1_structured_save",
            {
                "image_front": "main_frame",
                "image_fisheye_front": "front_fisheye_camera",
                "image_fisheye_rear": "rear_fisheye_camera",
                "image_fisheye_left": "left_fisheye_camera",
                "image_fisheye_right": "right_fisheye_camera",
                "pointcloud": "aggregated_pointcloud",
            },
            {},
            {"dataset_name": "collect_demo", "required_inputs": ["image_front", "pointcloud"]},
        ),
        dummy_session,
    )
    node.setup()
    result = node.run(sample)
    assert result.status.value == "OK"
    save_result = sample.metadata["save_result"]
    assert save_result["saved"] is True
    assert save_result["saved_camera_topics"] == [
        "front_wide_camera",
        "front_fisheye_camera",
        "rear_fisheye_camera",
        "left_fisheye_camera",
        "right_fisheye_camera",
    ]
    assert Path(save_result["camera_config"]).exists()
    assert Path(save_result["pointcloud"]).exists()
    saved_front = np.asarray(Image.open(Path(save_result["images"][0])))
    assert saved_front[0, 0, 2] > 150
    payload = json.loads(Path(save_result["camera_config"]).read_text(encoding="utf-8"))
    assert len(payload["items"]) == 5
    assert payload["items"][0]["camera_internal"]["fx"] == 500.0


def test_xtreme1_save_node_writes_all_camera_configs_even_when_optional_images_missing(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main_source")
    sample.put("aggregated_pointcloud", PointCloudFrame(make_meta("top_lidar", "pointcloud", 1_000_000_000), np.array([[1.0, 2.0, 3.0]]), frame_name="base"), "agg")
    node = Xtreme1SaveNode(
        NodeConfig(
            "save",
            "xtreme1_structured_save",
            {
                "image_front": "main_frame",
                "image_fisheye_front": "front_fisheye_camera",
                "image_fisheye_rear": "rear_fisheye_camera",
                "image_fisheye_left": "left_fisheye_camera",
                "image_fisheye_right": "right_fisheye_camera",
                "pointcloud": "aggregated_pointcloud",
            },
            {},
            {"dataset_name": "collect_demo", "required_inputs": ["image_front", "pointcloud"]},
        ),
        dummy_session,
    )
    node.setup()
    result = node.run(sample)
    assert result.status == NodeResult.OK
    payload = json.loads(Path(sample.metadata["save_result"]["camera_config"]).read_text(encoding="utf-8"))
    assert len(payload["items"]) == 5
    assert sample.metadata["save_result"]["saved_camera_topics"] == ["front_wide_camera"]
    assert payload["items"][1]["width"] == 1920
    assert payload["items"][1]["height"] == 1080


def test_xtreme1_save_node_limits_output_to_configured_camera_topics(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("front_wide_camera_undistorted", image_frame, "undistort_front_wide")
    sample.put(
        "aggregated_pointcloud",
        PointCloudFrame(make_meta("top_lidar", "pointcloud", 1_000_000_000), np.array([[1.0, 2.0, 3.0]]), frame_name="base"),
        "agg",
    )
    node = Xtreme1SaveNode(
        NodeConfig(
            "save",
            "xtreme1_structured_save",
            {
                "image_front": "front_wide_camera_undistorted",
                "pointcloud": "aggregated_pointcloud",
            },
            {},
            {
                "dataset_name": "collect_frontcamera",
                "required_inputs": ["image_front", "pointcloud"],
                "camera_topics": ["front_wide_camera"],
            },
        ),
        dummy_session,
    )
    node.setup()
    result = node.run(sample)

    assert result.status == NodeResult.OK
    save_result = sample.metadata["save_result"]
    assert save_result["saved_camera_topics"] == ["front_wide_camera"]
    assert Path(save_result["images"][0]).parent.name == "camera_image_0"
    assert not (dummy_session.session_root / "xtreme1" / "collect_frontcamera" / "camera_image_1").exists()
    payload = json.loads(Path(save_result["camera_config"]).read_text(encoding="utf-8"))
    assert len(payload["items"]) == 1


def test_xtreme1_save_node_rejects_unknown_configured_camera_topic(dummy_session):
    node = Xtreme1SaveNode(
        NodeConfig(
            "save",
            "xtreme1_structured_save",
            {"image_front": "main_frame", "pointcloud": "aggregated_pointcloud"},
            {},
            {"camera_topics": ["unknown_camera"]},
        ),
        dummy_session,
    )

    with pytest.raises(ValueError, match="unsupported camera topic"):
        node.setup()


def test_xtreme1_save_node_requires_base_frame_pointcloud(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main_source")
    sample.put(
        "aggregated_pointcloud",
        PointCloudFrame(make_meta("top_lidar", "pointcloud", 1_000_000_000), np.array([[1.0, 2.0, 3.0]]), frame_name="top_lidar"),
        "agg",
    )
    node = Xtreme1SaveNode(
        NodeConfig(
            "save",
            "xtreme1_structured_save",
            {
                "image_front": "main_frame",
                "pointcloud": "aggregated_pointcloud",
            },
            {},
            {"dataset_name": "collect_demo", "required_inputs": ["image_front", "pointcloud"]},
        ),
        dummy_session,
    )
    node.setup()
    result = node.run(sample)
    assert result.status == NodeResult.FAIL_SAMPLE
    assert result.reason == "save_pointcloud_not_in_base"
