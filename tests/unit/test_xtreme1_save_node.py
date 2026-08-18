import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from data_collect_dag.models import NodeConfig, NodeResult, PointCloudFrame, TopicConfig
from data_collect_dag.nodes.xtreme1 import Xtreme1SaveNode
from data_collect_dag.sample_context import SampleContext
from tests.unit.conftest import make_meta


def _base_pointcloud():
    return PointCloudFrame(
        make_meta("top_lidar", "pointcloud", 1_000_000_000),
        np.array([[1.0, 2.0, 3.0]]),
        frame_name="base",
    )


def _node(dummy_session, *, inputs, cameras, dataset_name="collect_demo"):
    return Xtreme1SaveNode(
        NodeConfig(
            "save",
            "xtreme1_structured_save",
            inputs,
            {},
            {"dataset_name": dataset_name, "cameras": cameras},
        ),
        dummy_session,
    )


def test_xtreme1_save_node_uses_ordered_generic_camera_bindings(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    corrected = image_frame.replace(image_bgr=np.full_like(image_frame.image_bgr, [200, 0, 0]))
    later_image = replace(image_frame, meta=make_meta("front_fisheye_camera", "image", 2_000_000_000))
    sample.put("wide_corrected", corrected, "undistort")
    sample.put("fisheye_front", later_image, "sync")
    sample.put("fisheye_rear", image_frame, "sync")
    sample.put("fisheye_left", image_frame, "sync")
    sample.put("fisheye_right", image_frame, "sync")
    sample.put("aggregated_pointcloud", _base_pointcloud(), "agg")
    sample.metadata["undistort_camera_params"] = {
        "front_wide_camera": {
            "camera_matrix": [[500.0, 0.0, 480.0], [0.0, 500.0, 270.0], [0.0, 0.0, 1.0]],
            "resolution": [960, 540],
        }
    }
    inputs = {
        "pointcloud": "aggregated_pointcloud",
        "arbitrary_wide": "wide_corrected",
        "arbitrary_front": "fisheye_front",
        "arbitrary_rear": "fisheye_rear",
        "arbitrary_left": "fisheye_left",
        "arbitrary_right": "fisheye_right",
    }
    cameras = [
        {"input_name": "arbitrary_wide", "topic_key": "front_wide_camera"},
        {"input_name": "arbitrary_front", "topic_key": "front_fisheye_camera"},
        {"input_name": "arbitrary_rear", "topic_key": "rear_fisheye_camera"},
        {"input_name": "arbitrary_left", "topic_key": "left_fisheye_camera"},
        {"input_name": "arbitrary_right", "topic_key": "right_fisheye_camera"},
    ]
    node = _node(dummy_session, inputs=inputs, cameras=cameras)

    node.setup()
    result = node.run(sample)

    assert result.status == NodeResult.OK
    save_result = sample.metadata["save_result"]
    assert save_result["saved_camera_topics"] == [item["topic_key"] for item in cameras]
    assert [Path(path).parent.name for path in save_result["images"]] == [f"camera_image_{index}" for index in range(5)]
    assert later_image.meta.source_timestamp_ns != image_frame.meta.source_timestamp_ns
    assert Path(save_result["camera_config"]).stem == str(image_frame.meta.source_timestamp_ns)
    assert Path(save_result["pointcloud"]).exists()
    saved_front = np.asarray(Image.open(Path(save_result["images"][0])))
    assert saved_front[0, 0, 2] > 150
    payload = json.loads(Path(save_result["camera_config"]).read_text(encoding="utf-8"))
    assert len(payload["items"]) == 5
    assert payload["items"][0]["camera_internal"]["fx"] == 500.0


def test_xtreme1_save_node_supports_topic_keys_not_known_by_node(dummy_session, image_frame):
    source_topic = dummy_session.app_config.topics["front_wide_camera"]
    dummy_session.app_config.topics["sa4_front_ext"] = TopicConfig(
        "sa4_front_ext",
        source_topic.topic,
        source_topic.msg_type,
        source_topic.role,
        "sa4_front_ext",
    )
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main")
    sample.put("aggregated_pointcloud", _base_pointcloud(), "agg")
    node = _node(
        dummy_session,
        inputs={"pointcloud": "aggregated_pointcloud", "camera_any_name": "main_frame"},
        cameras=[{"input_name": "camera_any_name", "topic_key": "sa4_front_ext"}],
    )

    node.setup()
    result = node.run(sample)

    assert result.status == NodeResult.OK
    assert sample.metadata["save_result"]["saved_camera_topics"] == ["sa4_front_ext"]


def test_xtreme1_save_node_skips_when_any_configured_camera_is_missing(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main")
    sample.put("aggregated_pointcloud", _base_pointcloud(), "agg")
    node = _node(
        dummy_session,
        inputs={"pointcloud": "aggregated_pointcloud", "camera_0": "main_frame", "camera_1": "missing_frame"},
        cameras=[
            {"input_name": "camera_0", "topic_key": "front_wide_camera"},
            {"input_name": "camera_1", "topic_key": "front_fisheye_camera"},
        ],
    )
    node.setup()

    result = node.run(sample)

    assert result.status == NodeResult.SKIP_SAMPLE
    assert result.reason == "save_camera_1_missing"


def test_xtreme1_save_node_skips_when_pointcloud_is_missing(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main")
    node = _node(
        dummy_session,
        inputs={"pointcloud": "missing_cloud", "camera_0": "main_frame"},
        cameras=[{"input_name": "camera_0", "topic_key": "front_wide_camera"}],
    )
    node.setup()

    result = node.run(sample)

    assert result.status == NodeResult.SKIP_SAMPLE
    assert result.reason == "save_pointcloud_missing"


@pytest.mark.parametrize(
    "inputs,cameras,error",
    [
        ({"pointcloud": "cloud"}, [], "cameras must be a non-empty list"),
        (
            {"pointcloud": "cloud", "camera_0": "image"},
            [{"input_name": "unknown_input", "topic_key": "front_wide_camera"}],
            "camera input_name not found in node inputs",
        ),
        (
            {"pointcloud": "cloud", "camera_0": "image"},
            [{"input_name": "camera_0", "topic_key": "unknown_camera"}],
            "camera topic_key not found in ros.topics",
        ),
        (
            {"pointcloud": "cloud", "camera_0": "image"},
            [{"input_name": "camera_0", "topic_key": "top_lidar"}],
            "camera topic_key must have image role",
        ),
    ],
)
def test_xtreme1_save_node_rejects_invalid_camera_bindings(dummy_session, inputs, cameras, error):
    node = _node(dummy_session, inputs=inputs, cameras=cameras)

    with pytest.raises(ValueError, match=error):
        node.setup()


def test_xtreme1_save_node_rejects_duplicate_camera_bindings(dummy_session):
    node = _node(
        dummy_session,
        inputs={"pointcloud": "cloud", "camera_0": "image_0", "camera_1": "image_1"},
        cameras=[
            {"input_name": "camera_0", "topic_key": "front_wide_camera"},
            {"input_name": "camera_0", "topic_key": "front_fisheye_camera"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate camera input_name"):
        node.setup()


def test_xtreme1_save_node_rejects_duplicate_camera_topics(dummy_session):
    node = _node(
        dummy_session,
        inputs={"pointcloud": "cloud", "camera_0": "image_0", "camera_1": "image_1"},
        cameras=[
            {"input_name": "camera_0", "topic_key": "front_wide_camera"},
            {"input_name": "camera_1", "topic_key": "front_wide_camera"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate camera topic_key"):
        node.setup()


def test_xtreme1_save_node_rejects_missing_camera_calibration(dummy_session):
    topic = dummy_session.app_config.topics["front_wide_camera"].topic
    dummy_session.calibration["images"].pop(topic)
    node = _node(
        dummy_session,
        inputs={"pointcloud": "cloud", "camera_0": "image"},
        cameras=[{"input_name": "camera_0", "topic_key": "front_wide_camera"}],
    )

    with pytest.raises(ValueError, match="missing camera calibration"):
        node.setup()


def test_xtreme1_save_node_requires_base_frame_pointcloud(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main")
    sample.put(
        "aggregated_pointcloud",
        PointCloudFrame(make_meta("top_lidar", "pointcloud", 1_000_000_000), np.array([[1.0, 2.0, 3.0]]), frame_name="top_lidar"),
        "agg",
    )
    node = _node(
        dummy_session,
        inputs={"pointcloud": "aggregated_pointcloud", "camera_0": "main_frame"},
        cameras=[{"input_name": "camera_0", "topic_key": "front_wide_camera"}],
    )
    node.setup()

    result = node.run(sample)

    assert result.status == NodeResult.FAIL_SAMPLE
    assert result.reason == "save_pointcloud_not_in_base"
