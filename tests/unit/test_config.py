from pathlib import Path

import pytest
import yaml

from data_collect_dag.config import ConfigError, load_app_config


def test_load_demo_config():
    config = load_app_config(Path("demo/xtreme1_demo.yaml"))
    assert "xtreme1_collect" in config.pipelines
    assert "localization" in config.topics
    assert config.runtime.debug is True
    assert config.pipelines["xtreme1_collect"].successors["start"] == ["fps_control_guard"]
    assert config.pipelines["xtreme1_collect"].successors["fps_control_guard"] == ["front_yolo_gate"]
    assert config.pipelines["xtreme1_collect"].successors["save_xtreme1"] == ["fps_control_marker"]
    assert config.pipelines["xtreme1_collect"].successors["fps_control_marker"] == ["end"]
    assert "front_yolo_gate" in config.pipelines["xtreme1_collect"].nodes_by_id
    assert config.control.pause_topic == "/data_collect/pause"
    assert config.control.resume_topic == "/data_collect/resume"
    assert config.pipelines["xtreme1_collect"].stop_conditions.max_duration_sec == 600.0
    assert config.pipelines["xtreme1_collect"].stop_conditions.max_saved_samples == 1000


def test_invalid_sample_workers_fails(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["runtime"]["sample_workers"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="sample_workers"):
        load_app_config(path)


def test_yolo_gate_requires_model_path(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"].pop("model_path")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="model_path"):
        load_app_config(path)


def test_yolo_gate_requires_class_names(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"].pop("class_names")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="class_names"):
        load_app_config(path)


def test_yolo_gate_requires_threshold_lists(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"].pop("confidence_thresholds")
    gate["config"].pop("min_area_ratios")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="confidence_thresholds"):
        load_app_config(path)


def test_yolo_gate_rejects_mismatched_threshold_lengths(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["confidence_thresholds"] = [0.25]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="confidence_thresholds length"):
        load_app_config(path)


def test_yolo_gate_rejects_duplicate_class_names(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["class_names"] = ["person", "person"]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="class_names"):
        load_app_config(path)


def test_yolo_gate_rejects_invalid_confidence_threshold(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["confidence_thresholds"] = [0.25, -0.1]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="confidence_thresholds"):
        load_app_config(path)


def test_yolo_gate_rejects_invalid_area_ratio(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["min_area_ratios"] = [1.5, 0.01]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="min_area_ratios"):
        load_app_config(path)


def test_yolo_gate_rejects_deprecated_single_class_keys(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["class_name"] = "person"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported yolo_gate config keys"):
        load_app_config(path)


def test_yolo_gate_rejects_invalid_predict_conf(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["predict_conf"] = 1.5
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="predict_conf"):
        load_app_config(path)


def test_yolo_gate_rejects_predict_conf_above_class_thresholds(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_yolo_gate")
    gate["config"]["predict_conf"] = 0.3
    gate["config"]["confidence_thresholds"] = [0.25, 0.4]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="predict_conf"):
        load_app_config(path)


def test_fps_guard_requires_positive_max_fps(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    guard = next(node for node in nodes if node["node_id"] == "fps_control_guard")
    guard["config"]["max_fps"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_fps"):
        load_app_config(path)


def test_fps_guard_requires_gate_id(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    guard = next(node for node in nodes if node["node_id"] == "fps_control_guard")
    guard["config"]["gate_id"] = ""
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="gate_id"):
        load_app_config(path)


def test_fps_marker_requires_gate_id(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    marker = next(node for node in nodes if node["node_id"] == "fps_control_marker")
    marker["config"]["gate_id"] = ""
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="gate_id"):
        load_app_config(path)


def test_invalid_max_duration_sec_fails(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["pipelines"]["xtreme1_collect"]["stop_conditions"]["max_duration_sec"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_duration_sec"):
        load_app_config(path)


def test_invalid_max_saved_samples_fails(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["pipelines"]["xtreme1_collect"]["stop_conditions"]["max_saved_samples"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_saved_samples"):
        load_app_config(path)
