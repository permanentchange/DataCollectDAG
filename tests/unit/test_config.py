from pathlib import Path

import pytest
import yaml

from data_collect_dag.config import ConfigError, load_app_config


def test_load_demo_config():
    config = load_app_config(Path("demo/xtreme1_demo.yaml"))
    assert "lidar_camera_1fps" in config.pipelines
    assert "localization" in config.topics
    assert config.runtime.debug is True
    pipeline = config.pipelines["lidar_camera_1fps"]
    assert pipeline.successors["start"] == ["fps_control_guard"]
    assert pipeline.successors["fps_control_guard"] == ["sync_top_lidar", "sync_corner_lidars", "sync_fisheye_images", "undistort_front_wide"]
    assert pipeline.successors["save_xtreme1"] == ["fps_control_marker"]
    assert pipeline.successors["fps_control_marker"] == ["end"]
    assert {
        "undistort_front_wide",
        "undistort_front_fisheye",
        "undistort_rear_fisheye",
        "undistort_left_fisheye",
        "undistort_right_fisheye",
    } <= set(pipeline.nodes_by_id)
    assert set(pipeline.predecessors["save_xtreme1"]) == {
        "aggregate_pointclouds",
        "undistort_front_wide",
        "undistort_front_fisheye",
        "undistort_rear_fisheye",
        "undistort_left_fisheye",
        "undistort_right_fisheye",
    }
    assert config.control.control_topic == "/data_collect/control"
    assert config.control.status_topic == "/data_collect/status"
    assert pipeline.stop_conditions.max_duration_sec == 3600.0
    assert pipeline.stop_conditions.max_saved_samples is None


def test_load_pipeline_topic_precheck_config(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    pipeline = payload["pipelines"]["init"]
    pipeline["topics"] = ["front_wide_camera", "top_lidar"]
    pipeline["time_delay_ms"] = 1000
    path = tmp_path / "topic_precheck.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_app_config(path)

    assert config.pipelines["init"].topics == ["front_wide_camera", "top_lidar"]
    assert config.pipelines["init"].time_delay_ms == 1000


@pytest.mark.parametrize(
    "topics,error",
    [
        ([], "topics must be a non-empty list"),
        (["top_lidar", "top_lidar"], "topics must not contain duplicates"),
        (["unknown_topic"], "unknown topic_key"),
    ],
)
def test_pipeline_topic_precheck_rejects_invalid_topics(tmp_path, topics, error):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    pipeline = payload["pipelines"]["init"]
    pipeline["topics"] = topics
    pipeline["time_delay_ms"] = 1000
    path = tmp_path / "invalid_topic_precheck.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match=error):
        load_app_config(path)


def test_pipeline_topic_precheck_requires_time_delay(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["pipelines"]["init"]["topics"] = ["top_lidar"]
    path = tmp_path / "missing_topic_precheck_delay.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="time_delay_ms"):
        load_app_config(path)


@pytest.mark.parametrize("time_delay_ms", [0, -1, 1.5, "1000", True])
def test_pipeline_topic_precheck_requires_positive_integer_time_delay(tmp_path, time_delay_ms):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    pipeline = payload["pipelines"]["init"]
    pipeline["topics"] = ["top_lidar"]
    pipeline["time_delay_ms"] = time_delay_ms
    path = tmp_path / "invalid_topic_precheck_delay.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="time_delay_ms"):
        load_app_config(path)


def test_pipeline_rejects_time_delay_without_topics(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["pipelines"]["init"]["time_delay_ms"] = 1000
    path = tmp_path / "topic_precheck_delay_without_topics.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="topics"):
        load_app_config(path)


def test_frontcamera_pipeline_only_processes_front_wide_camera():
    config = load_app_config(Path("demo/xtreme1_demo.yaml"))
    pipeline = config.pipelines["lidar_frontcamera_1fps"]

    assert "sync_fisheye_images" not in pipeline.nodes_by_id
    assert {node_id for node_id in pipeline.nodes_by_id if node_id.startswith("undistort_")} == {"undistort_front_wide"}
    assert pipeline.nodes_by_id["save_xtreme1"].inputs == {
        "camera_0": "front_wide_camera_undistorted",
        "pointcloud": "aggregated_pointcloud",
    }
    assert pipeline.nodes_by_id["save_xtreme1"].config["cameras"] == [
        {"input_name": "camera_0", "topic_key": "front_wide_camera"}
    ]
    assert set(pipeline.predecessors["save_xtreme1"]) == {"aggregate_pointclouds", "undistort_front_wide"}


def test_all_demo_xtreme1_nodes_use_generic_camera_bindings():
    config = load_app_config(Path("demo/xtreme1_demo.yaml"))

    for pipeline in config.pipelines.values():
        save_node = next((node for node in pipeline.nodes_by_id.values() if node.node_type == "xtreme1_structured_save"), None)
        if save_node is None:
            continue
        assert "pointcloud" in save_node.inputs
        assert "required_inputs" not in save_node.config
        assert "camera_topics" not in save_node.config
        assert save_node.config["cameras"]
        for camera in save_node.config["cameras"]:
            assert camera["input_name"] in save_node.inputs
            assert config.topics[camera["topic_key"]].role == "image"


def test_load_sa4_fps_uses_ordered_generic_camera_bindings():
    config = load_app_config(Path("demo/cfg_sa4.yaml"))
    pipeline = config.pipelines["fps"]
    save_node = pipeline.nodes_by_id["save_xtreme1"]

    assert save_node.inputs == {
        "pointcloud": "aggregated_pointcloud",
        "camera_0": "front_ext_camera_undistorted",
        "camera_1": "front_camera_undistorted",
        "camera_2": "rear_camera_undistorted",
        "camera_3": "left_camera_undistorted",
        "camera_4": "right_camera_undistorted",
    }
    assert save_node.config["cameras"] == [
        {"input_name": "camera_0", "topic_key": "front_ext_camera"},
        {"input_name": "camera_1", "topic_key": "front_camera"},
        {"input_name": "camera_2", "topic_key": "rear_camera"},
        {"input_name": "camera_3", "topic_key": "left_camera"},
        {"input_name": "camera_4", "topic_key": "right_camera"},
    ]
    undistort_specs = {
        "undistort_front_ext": ("main_frame", "front_ext_camera_undistorted", "front_ext_camera", "fps_guard"),
        "undistort_front": ("front_camera", "front_camera_undistorted", "front_camera", "sync_images"),
        "undistort_rear": ("rear_camera", "rear_camera_undistorted", "rear_camera", "sync_images"),
        "undistort_left": ("left_camera", "left_camera_undistorted", "left_camera", "sync_images"),
        "undistort_right": ("right_camera", "right_camera_undistorted", "right_camera", "sync_images"),
    }
    for node_id, (input_key, output_key, source, predecessor) in undistort_specs.items():
        node = pipeline.nodes_by_id[node_id]
        assert node.node_type == "image_undistort"
        assert node.inputs == {"image": input_key}
        assert node.outputs == {"image": output_key}
        assert node.config == {"source": source, "write_debug_artifacts": False}
        assert pipeline.predecessors[node_id] == [predecessor]
    assert set(pipeline.predecessors["save_xtreme1"]) == {
        "aggregate_pointclouds",
        "undistort_front_ext",
        "undistort_front",
        "undistort_rear",
        "undistort_left",
        "undistort_right",
    }


def test_demo_collection_pipelines_disable_undistort_debug_artifacts():
    config = load_app_config(Path("demo/xtreme1_demo.yaml"))

    for pipeline_name in ("lidar_camera_1fps", "lidar_frontcamera_1fps"):
        pipeline = config.pipelines[pipeline_name]
        undistort_nodes = [node for node in pipeline.nodes_by_id.values() if node.node_type == "image_undistort"]
        assert undistort_nodes
        assert all(node.config.get("write_debug_artifacts") is False for node in undistort_nodes)


def test_control_config_rejects_legacy_control_topics(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["ros"]["control"]["start_topic"] = "/data_collect/start"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="control_topic"):
        load_app_config(path)


def test_control_config_rejects_unknown_keys(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["ros"]["control"]["unexpected_topic"] = "/data_collect/unexpected"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown ros.control keys"):
        load_app_config(path)


def test_invalid_sample_workers_fails(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["runtime"]["sample_workers"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="sample_workers"):
        load_app_config(path)


def test_yolo_gate_requires_model_path(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"].pop("model_path")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="model_path"):
        load_app_config(path)


def test_yolo_gate_requires_class_names(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"].pop("class_names")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="class_names"):
        load_app_config(path)


def test_yolo_gate_requires_threshold_lists(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"].pop("confidence_thresholds")
    gate["config"].pop("min_area_ratios")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="confidence_thresholds"):
        load_app_config(path)


def test_yolo_gate_rejects_mismatched_threshold_lengths(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["class_names"] = ["person", "car"]
    gate["config"]["confidence_thresholds"] = [0.25]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="confidence_thresholds length"):
        load_app_config(path)


def test_yolo_gate_rejects_duplicate_class_names(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["class_names"] = ["person", "person"]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="class_names"):
        load_app_config(path)


def test_yolo_gate_rejects_invalid_confidence_threshold(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["confidence_thresholds"] = [0.25, -0.1]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="confidence_thresholds"):
        load_app_config(path)


def test_yolo_gate_rejects_invalid_area_ratio(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["min_area_ratios"] = [1.5, 0.01]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="min_area_ratios"):
        load_app_config(path)


def test_yolo_gate_rejects_deprecated_single_class_keys(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["class_name"] = "person"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported yolo_gate config keys"):
        load_app_config(path)


def test_yolo_gate_rejects_invalid_predict_conf(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["predict_conf"] = 1.5
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="predict_conf"):
        load_app_config(path)


def test_yolo_gate_rejects_predict_conf_above_class_thresholds(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["predict_conf"] = 0.3
    gate["config"]["class_names"] = ["person", "car"]
    gate["config"]["confidence_thresholds"] = [0.25, 0.4]
    gate["config"]["min_area_ratios"] = [0.01, 0.01]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="predict_conf"):
        load_app_config(path)


def test_fps_guard_requires_positive_max_fps(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    guard = next(node for node in nodes if node["node_id"] == "fps_control_guard")
    guard["config"]["max_fps"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_fps"):
        load_app_config(path)


def test_fps_guard_requires_gate_id(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    guard = next(node for node in nodes if node["node_id"] == "fps_control_guard")
    guard["config"]["gate_id"] = ""
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="gate_id"):
        load_app_config(path)


def test_fps_marker_requires_gate_id(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["person_1fps"]["nodes"]
    marker = next(node for node in nodes if node["node_id"] == "fps_control_marker")
    marker["config"]["gate_id"] = ""
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="gate_id"):
        load_app_config(path)


def test_invalid_max_duration_sec_fails(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["pipelines"]["person_1fps"]["stop_conditions"]["max_duration_sec"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_duration_sec"):
        load_app_config(path)


def test_invalid_max_saved_samples_fails(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["pipelines"]["person_1fps"]["stop_conditions"]["max_saved_samples"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_saved_samples"):
        load_app_config(path)
