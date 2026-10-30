"""Static validation and offline CLI behavior use the actual configuration parser."""
import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tests.unit.config_cases import CLI_ERRORS

from data_collect_dag import __version__
from data_collect_dag.cli import main
from data_collect_dag.config import ConfigError, load_app_config
from data_collect_dag.node_registry import NODE_TYPES, load_node_type


@pytest.fixture
def config_file(tmp_path):
    payload = {
        "output": {"root_dir": str(tmp_path / "never-created")},
        "calibration": {"path": str(tmp_path / "missing-calibration.json")},
        "ros": {"node_name": "custom_node", "topics": {
            "camera": {"topic": "/custom/image", "msg_type": "missing_msgs/Image", "role": "image"}}},
        "cache": {"defaults_by_role": {"image": {"max_frames": 10, "max_age_sec": 1}}},
        "pipelines": {"custom": {
            "description": "User-defined pipeline", "main_source": "camera", "topics": ["camera"],
            "time_delay_ms": 750, "stop_conditions": {"max_saved_samples": 3},
            "nodes": [{"node_id": "begin", "type": "start"}, {"node_id": "finish", "type": "end"}],
            "edges": [["begin", "finish"]],
        }},
    }
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path, payload


def inspect(path, monkeypatch, pipeline=None):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "inspect", "--config", str(path)] +
                        (["--pipeline", pipeline] if pipeline else []))
    return main()


def test_summary_and_pipeline_details(config_file, monkeypatch, capsys):
    path, _ = config_file
    assert load_app_config(path).pipelines["custom"].description == "User-defined pipeline"
    assert inspect(path, monkeypatch) == 0
    output = capsys.readouterr().out
    for expected in (str(path), __version__, "PASS (static configuration only)", "custom_node", "not configured",
                     "Topics:       1", "custom: User-defined pipeline", "Main source: camera", "Runtime environment: not checked"):
        assert expected in output
    assert inspect(path, monkeypatch, "custom") == 0
    output = capsys.readouterr().out
    for expected in ("camera: /custom/image", "750 ms", "Max saved samples: 3", "begin: start", "begin -> finish"):
        assert expected in output
    assert not (path.parent / "never-created").exists()


def test_unknown_pipeline_and_bad_unselected_pipeline(config_file, monkeypatch, capsys):
    path, payload = config_file
    assert inspect(path, monkeypatch, "absent") == 1
    output = capsys.readouterr()
    assert not output.out and "available pipelines: custom" in output.err
    payload["pipelines"]["broken"] = copy.deepcopy(payload["pipelines"]["custom"])
    payload["pipelines"]["broken"]["nodes"][0]["type"] = "typo"
    path.write_text(yaml.safe_dump(payload))
    assert inspect(path, monkeypatch, "custom") == 1
    output = capsys.readouterr()
    assert not output.out and "pipeline broken node begin unknown node type: typo" in output.err


def test_inspect_demo_displays_speed_gate(monkeypatch, capsys):
    assert inspect(Path("demo/cfg.yaml"), monkeypatch, "fps") == 0
    output = capsys.readouterr().out
    for expected in ("Topics:       15", "iccp_status: /iccp_status", "speed_gate: speed_gate",
                     "start -> speed_gate", "speed_gate -> fps_guard"):
        assert expected in output


@pytest.mark.parametrize("overrides,error", CLI_ERRORS)
def test_inspect_reports_validation_errors(config_file, overrides, error, monkeypatch, capsys):
    path, raw = config_file
    raw.update(copy.deepcopy(overrides))
    path.write_text(yaml.safe_dump(raw))
    assert inspect(path, monkeypatch) == 1
    output = capsys.readouterr()
    assert not output.out and error in output.err


def test_yaml_error_has_file_and_line(config_file, monkeypatch, capsys):
    path, _ = config_file
    path.write_text("ros: [\n")
    assert inspect(path, monkeypatch) == 1
    assert "line" in capsys.readouterr().err


def test_inspect_missing_file(tmp_path, monkeypatch, capsys):
    assert inspect(tmp_path / "missing.yaml", monkeypatch) == 1
    assert "missing.yaml" in capsys.readouterr().err


def test_inspect_import_boundary_and_read_only(config_file):
    path, raw = config_file
    pipeline = raw["pipelines"]["custom"]
    pipeline["nodes"].insert(1, {"node_id": "yolo", "type": "yolo_gate", "inputs": {"image": "main_frame"},
        "config": {"model_path": "missing.pt", "class_names": ["car"], "confidence_thresholds": [0.5],
                   "min_area_ratios": [0.01], "predict_conf": 0.1}})
    raw["ros"]["topics"]["speed"] = {"topic": "/iccp_status", "msg_type": "missing_msgs/ICCPStatus", "role": "vehicle_status"}
    raw["cache"]["defaults_by_role"]["vehicle_status"] = {"max_frames": 100, "max_age_sec": 2}
    pipeline["nodes"].insert(1, {"node_id": "speed_gate", "type": "speed_gate",
                                "inputs": {"reference_frame": "main_frame"}, "config": {"source": "speed"}})
    pipeline["edges"] = [["begin", "speed_gate"], ["speed_gate", "yolo"], ["yolo", "finish"]]
    path.write_text(yaml.safe_dump(raw))
    before = {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    script = r'''
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("rospy", "roslib", "cv2", "torch", "torchvision", "ultralytics", "onnx", "onnxruntime",
                   "data_collect_dag.app", "data_collect_dag.session", "data_collect_dag.nodes",
                   "data_collect_dag.ros_adapter", "data_collect_dag.ros_control_client")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("unexpected import: " + fullname)
sys.meta_path.insert(0, Block())
from data_collect_dag import cli
def no_lock(*args, **kwargs):
    raise AssertionError("inspection must not acquire a lock")
cli.InstanceGuard = no_lock
config_path = sys.argv[1]
for selection in ([], ["--pipeline", "custom"]):
    sys.argv = ["data_collect_dag", "inspect", "--config", config_path] + selection
    assert cli.main() == 0
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", script, str(path)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()} == before
    assert not (path.parent / "never-created").exists()


def test_registry_loaders_match_node_implementations():
    for name in NODE_TYPES:
        assert isinstance(load_node_type(name), type)
