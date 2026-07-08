from pathlib import Path

import pytest
import yaml

from data_collect_dag.config import ConfigError, load_app_config


def test_load_demo_config():
    config = load_app_config(Path("demo/xtreme1_demo.yaml"))
    assert "xtreme1_collect" in config.pipelines
    assert "localization" in config.topics
    assert config.runtime.debug is True
    assert config.pipelines["xtreme1_collect"].successors["start"] == ["front_person_gate"]
    assert "front_person_gate" in config.pipelines["xtreme1_collect"].nodes_by_id
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


def test_yolo_person_gate_requires_model_path(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_person_gate")
    gate["config"].pop("model_path")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="model_path"):
        load_app_config(path)


def test_yolo_person_gate_rejects_invalid_area_ratio(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    nodes = payload["pipelines"]["xtreme1_collect"]["nodes"]
    gate = next(node for node in nodes if node["node_id"] == "front_person_gate")
    gate["config"]["min_area_ratio"] = 1.5
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="min_area_ratio"):
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
