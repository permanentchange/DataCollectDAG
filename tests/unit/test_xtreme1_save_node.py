import json
from io import BytesIO
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from data_collect_dag.models import NodeConfig, NodeResult, PointCloudFrame, TopicConfig
from data_collect_dag.nodes.xtreme1 import Xtreme1SaveNode, _build_camera_config
from data_collect_dag.sample_context import SampleContext
from data_collect_dag.storage import StorageTracker, scan_files
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


@pytest.mark.parametrize("track_storage", [False, True])
def test_xtreme1_save_node_uses_ordered_generic_camera_bindings(dummy_session, image_frame, track_storage):
    if track_storage:
        dummy_session.storage_tracker = StorageTracker(dummy_session.app_config.output_root_dir, 1, lambda: None)
        dummy_session.storage_tracker.reconcile()
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
            "resolution": [6, 4],
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
    if track_storage:
        assert dummy_session.storage_tracker.size_bytes == sum(scan_files(dummy_session.app_config.output_root_dir).values())
        second_node = _node(dummy_session, inputs=inputs, cameras=cameras, dataset_name="second_dataset")
        second_node.setup()
        assert second_node.run(sample).status == NodeResult.OK
        assert dummy_session.storage_tracker.size_bytes == sum(scan_files(dummy_session.app_config.output_root_dir).values())


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


def test_build_camera_config_scales_intrinsics_to_image_resolution():
    base2camera = np.arange(16, dtype=np.float64).reshape(4, 4).tolist()
    camera_params = {
        "camera_matrix": [[1000.0, 0.0, 960.0], [0.0, 800.0, 540.0], [0.0, 0.0, 1.0]],
        "resolution": [1920, 1080],
        "base2camera": base2camera,
    }

    result = _build_camera_config(camera_params, SimpleNamespace(width=960, height=270))

    assert result["camera_internal"] == {
        "fx": 500.0,
        "fy": 200.0,
        "cx": 480.0,
        "cy": 135.0,
    }
    assert result["width"] == 960
    assert result["height"] == 270
    assert result["camera_external"] == np.asarray(base2camera).reshape(-1, order="F").tolist()


def test_build_camera_config_does_not_rescale_matching_resolution():
    camera_params = {
        "camera_matrix": [[1000.0, 0.0, 960.0], [0.0, 800.0, 540.0], [0.0, 0.0, 1.0]],
        "resolution": [1920, 1080],
        "base2camera": np.eye(4).tolist(),
    }

    result = _build_camera_config(camera_params, SimpleNamespace(width=1920, height=1080))

    assert result["camera_internal"] == {
        "fx": 1000.0,
        "fy": 800.0,
        "cx": 960.0,
        "cy": 540.0,
    }


def test_build_camera_config_preserves_intrinsics_without_calibration_resolution():
    camera_params = {
        "camera_matrix": [[1000.0, 0.0, 960.0], [0.0, 800.0, 540.0], [0.0, 0.0, 1.0]],
        "base2camera": np.eye(4).tolist(),
    }

    result = _build_camera_config(camera_params, SimpleNamespace(width=960, height=540))

    assert result["camera_internal"] == {
        "fx": 1000.0,
        "fy": 800.0,
        "cx": 960.0,
        "cy": 540.0,
    }
    assert result["width"] == 960
    assert result["height"] == 540


@pytest.mark.parametrize("resolution", ([0, 1080], [1920], ["bad", 1080]))
def test_build_camera_config_rejects_invalid_calibration_resolution(resolution):
    camera_params = {
        "camera_matrix": [[1000.0, 0.0, 960.0], [0.0, 800.0, 540.0], [0.0, 0.0, 1.0]],
        "resolution": resolution,
        "base2camera": np.eye(4).tolist(),
    }

    with pytest.raises(ValueError, match="camera calibration resolution"):
        _build_camera_config(camera_params, SimpleNamespace(width=960, height=540))


def test_save_raw_images_and_per_camera_distortion_params(dummy_session, image_frame):
    dummy_session.storage_tracker = StorageTracker(dummy_session.app_config.output_root_dir, 1, lambda: None)
    dummy_session.storage_tracker.reconcile()
    topic_keys = [key for key, topic in dummy_session.app_config.topics.items() if topic.role == "image"]
    sample = SampleContext(sample_id="raw")
    sample.put("pointcloud", _base_pointcloud(), "test")
    inputs = {"pointcloud": "pointcloud"}
    cameras = []
    frames = []
    for index, topic_key in enumerate(topic_keys):
        binding = f"camera_{index}"
        inputs[binding] = topic_key
        cameras.append({"input_name": binding, "topic_key": topic_key})
        pixels = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3) + index * 10
        frame = replace(image_frame, meta=make_meta(topic_key, "image", 1_000_000_000), image_bgr=pixels)
        frames.append(frame)
        sample.put(topic_key, frame, "test")
        params = dummy_session.calibration["images"][dummy_session.app_config.topics[topic_key].topic]
        params.update({
            "dist_model": "equidistant", "dist_coeffs": [0.1 + index, -0.02, 0.003, -0.004],
            "resolution": [12, 8], "camera_matrix": [[100, 0, 6], [0, 120, 4], [0, 0, 1]],
        })
    node = _node(dummy_session, inputs=inputs, cameras=cameras)
    node.config["include_distortion_params"] = True
    node.setup()
    assert node.run(sample).status == NodeResult.OK
    saved = sample.metadata["save_result"]
    config = json.loads(Path(saved["camera_config"]).read_text())
    for index, (entry, frame) in enumerate(zip(config["items"], frames)):
        assert entry["dist_model"] == "equidistant"
        assert entry["dist_coeffs"] == [0.1 + index, -0.02, 0.003, -0.004]
        assert "is_fisheye" not in entry and "distortion_model" not in entry
        assert entry["camera_internal"] == {"fx": 50, "fy": 60, "cx": 3, "cy": 2}
        reference = BytesIO()
        Image.fromarray(frame.image_bgr[:, :, ::-1]).save(reference, format="JPEG", quality=95)
        assert Path(saved["images"][index]).read_bytes() == reference.getvalue()
    assert dummy_session.storage_tracker.size_bytes == sum(scan_files(dummy_session.app_config.output_root_dir).values())


def test_distortion_export_rejects_undistorted_sample_before_writing(dummy_session, image_frame):
    sample = SampleContext(sample_id="corrected")
    sample.put("image", image_frame, "test")
    sample.put("pointcloud", _base_pointcloud(), "test")
    sample.metadata["undistort_camera_params"] = {"front_wide_camera": {}}
    node = _node(dummy_session,
                 inputs={"pointcloud": "pointcloud", "camera": "image"},
                 cameras=[{"input_name": "camera", "topic_key": "front_wide_camera"}])
    node.config["include_distortion_params"] = True
    node.setup()
    outcome = node.run(sample)
    assert outcome.status == NodeResult.FAIL_SAMPLE and "require_raw_images" in outcome.reason
    assert not list(node._save_root.rglob("*.jpg"))
    assert not list(node._save_root.rglob("*.pcd"))
    assert not list(node._save_root.rglob("*.json"))
    assert "save_result" not in sample.metadata


@pytest.mark.parametrize("field", ["dist_model", "dist_coeffs"])
def test_distortion_export_requires_source_calibration_at_setup(dummy_session, field):
    params = dummy_session.calibration["images"][dummy_session.app_config.topics["front_wide_camera"].topic]
    params.pop(field)
    node = _node(dummy_session,
                 inputs={"pointcloud": "pointcloud", "camera": "image"},
                 cameras=[{"input_name": "camera", "topic_key": "front_wide_camera"}])
    node.config["include_distortion_params"] = True
    with pytest.raises(ValueError, match=f"front_wide_camera.*{field}"):
        node.setup()
    assert not (dummy_session.session_root / "xtreme1").exists()


def test_default_camera_json_has_no_distortion_extension(dummy_session):
    params = next(iter(dummy_session.calibration["images"].values()))
    result = _build_camera_config(params, SimpleNamespace(width=6, height=4))
    assert set(result) == {"camera_internal", "width", "height", "camera_external", "rowMajor"}
