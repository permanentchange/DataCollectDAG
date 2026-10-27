from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from data_collect_dag.cache import SessionInputCache
from data_collect_dag.config import ConfigError, load_app_config
from data_collect_dag.models import CachePolicy, NodeConfig, NodeResult, VehicleStatusFrame
from data_collect_dag.nodes.common import SpeedGateNode
from data_collect_dag.sample_context import SampleContext
from tests.unit.conftest import make_meta


@pytest.fixture
def gate():
    session = SimpleNamespace(cache=SessionInputCache({"status": CachePolicy(100, 2.0)}))
    node = SpeedGateNode(NodeConfig("speed_gate", "speed_gate", {"reference_frame": "camera"}, {},
                                    {"source": "status"}), session)
    node.setup()
    return node


def _sample(timestamp_ns=2_000_000_000):
    sample = SampleContext(str(timestamp_ns))
    sample.put("camera", SimpleNamespace(meta=make_meta("camera", "image", timestamp_ns)), "main_source")
    return sample


def _append(gate, speed, timestamp_ns=2_000_000_000):
    frame = VehicleStatusFrame(make_meta("status", "vehicle_status", timestamp_ns), float(speed))
    gate.session.cache.append("status", frame)


@pytest.mark.parametrize("speed,expected", [
    (0.0, NodeResult.SKIP_SAMPLE), (-0.0, NodeResult.SKIP_SAMPLE),
    (0.009, NodeResult.SKIP_SAMPLE), (-0.009, NodeResult.SKIP_SAMPLE),
    (0.01, NodeResult.OK), (-0.01, NodeResult.OK),
    (np.float32(0.01), NodeResult.OK), (np.float32(-0.01), NodeResult.OK),
    (np.nextafter(np.float32(0.01), np.float32(0)), NodeResult.SKIP_SAMPLE),
    (1.0, NodeResult.OK), (-1.0, NodeResult.OK),
])
def test_speed_gate_absolute_threshold_and_float32_boundary(gate, speed, expected):
    _append(gate, speed)
    outcome = gate.run(_sample())
    assert outcome.status == expected
    if expected == NodeResult.SKIP_SAMPLE:
        assert outcome.reason == "speed_gate_below_min_speed"


@pytest.mark.parametrize("timestamp_ns,expected", [
    (1_499_999_999, NodeResult.SKIP_SAMPLE), (1_500_000_000, NodeResult.OK),
    (1_900_000_000, NodeResult.OK), (2_000_000_000, NodeResult.OK),
    (2_000_000_001, NodeResult.SKIP_SAMPLE),
])
def test_speed_gate_rejects_stale_and_future_status(gate, timestamp_ns, expected):
    _append(gate, 1.0, timestamp_ns)
    outcome = gate.run(_sample())
    assert outcome.status == expected
    if expected == NodeResult.SKIP_SAMPLE:
        assert outcome.reason == "speed_gate_speed_not_matched"


def test_speed_gate_missing_status(gate):
    assert gate.run(_sample()).reason == "speed_gate_speed_not_matched"


@pytest.mark.parametrize("speed", [float("nan"), float("inf"), -float("inf")])
def test_speed_gate_does_not_fall_back_from_invalid_latest_status(gate, speed):
    _append(gate, 1.0, 1_900_000_000)
    _append(gate, speed)
    outcome = gate.run(_sample())
    assert outcome.status == NodeResult.SKIP_SAMPLE
    assert outcome.reason == "speed_gate_invalid_speed"


def test_speed_gate_selects_latest_source_timestamp_and_reports_age(gate):
    _append(gate, -1.0, 1_900_000_000)
    _append(gate, 10.0, 2_100_000_000)
    _append(gate, 0.0, 1_800_000_000)
    outcome = gate.run(_sample())
    assert outcome.status == NodeResult.OK
    assert outcome.metadata["speed_mps"] == -1.0
    assert outcome.metadata["age_ms"] == 100.0
    assert outcome.metadata["source_timestamp_ns"] == 1_900_000_000


def test_speed_gate_custom_threshold_age_and_recovery(gate):
    gate.config.update(min_abs_speed_mps=0.5, max_age_ms=100)
    gate.setup()
    _append(gate, 1.0, 1_800_000_000)
    assert gate.run(_sample()).reason == "speed_gate_speed_not_matched"
    _append(gate, 0.4, 1_950_000_000)
    assert gate.run(_sample()).reason == "speed_gate_below_min_speed"
    _append(gate, -0.5)
    assert gate.run(_sample()).status == NodeResult.OK
    gate.config["min_abs_speed_mps"] = 0
    gate.setup()
    _append(gate, 0.0)
    assert gate.run(_sample()).status == NodeResult.OK


@pytest.fixture
def gate_config():
    return {
        "output": {"root_dir": "/tmp/unused-speed-gate"},
        "ros": {"topics": {
            "camera": {"topic": "/camera", "msg_type": "sensor_msgs/Image", "role": "image"},
            "status": {"topic": "/iccp_status", "msg_type": "vcu_msgs/ICCPStatus", "role": "vehicle_status"},
        }},
        "cache": {"defaults_by_role": {role: {"max_frames": 100, "max_age_sec": 2.0}
                                       for role in ("image", "vehicle_status")}},
        "pipelines": {"test": {
            "main_source": "camera",
            "nodes": [{"node_id": "start", "type": "start"},
                      {"node_id": "speed_gate", "type": "speed_gate", "inputs": {"reference_frame": "main_frame"},
                       "config": {"source": "status"}},
                      {"node_id": "end", "type": "end"}],
            "edges": [["start", "speed_gate"], ["speed_gate", "end"]],
        }},
    }


@pytest.mark.parametrize("key,value,error", [
    ("source", None, "source"), ("source", "", "source"), ("source", "missing", "unknown source"),
    ("source", "camera", "role vehicle_status"),
    ("min_abs_speed_mps", -1, "min_abs_speed_mps"),
    ("min_abs_speed_mps", float("nan"), "min_abs_speed_mps"),
    ("min_abs_speed_mps", float("inf"), "min_abs_speed_mps"),
    ("min_abs_speed_mps", 1e39, "float32"),
    ("min_abs_speed_mps", True, "min_abs_speed_mps"),
    ("min_abs_speed_mps", "0.01", "min_abs_speed_mps"),
    ("max_age_ms", 0, "max_age_ms"), ("max_age_ms", -1, "max_age_ms"),
    ("max_age_ms", float("nan"), "max_age_ms"), ("max_age_ms", float("inf"), "max_age_ms"),
    ("max_age_ms", False, "max_age_ms"), ("max_age_ms", None, "max_age_ms"),
])
def test_speed_gate_static_validation(gate_config, tmp_path, key, value, error):
    gate_config["pipelines"]["test"]["nodes"][1]["config"][key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(gate_config))
    with pytest.raises(ConfigError, match=error):
        load_app_config(path)


def test_speed_gate_static_defaults_and_required_reference(gate_config, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(gate_config))
    assert load_app_config(path).pipelines["test"].nodes_by_id["speed_gate"].config == {"source": "status"}
    gate_config["pipelines"]["test"]["nodes"][1]["inputs"] = {}
    path.write_text(yaml.safe_dump(gate_config))
    with pytest.raises(ConfigError, match="reference_frame"):
        load_app_config(path)


def test_demo_speed_gates_precede_all_processing_except_init():
    config = load_app_config(Path("demo/cfg.yaml"))
    assert config.topics["iccp_status"].msg_type == "vcu_msgs/ICCPStatus"
    assert config.cache_policies["iccp_status"] == CachePolicy(100, 2.0)
    for name, pipeline in config.pipelines.items():
        assert "iccp_status" in pipeline.topics
        if name == "init":
            assert not any(node.node_type == "speed_gate" for node in pipeline.nodes_by_id.values())
        else:
            assert pipeline.successors["start"] == ["speed_gate"]
            assert pipeline.successors["speed_gate"] == ["fps_guard"]
            gate = pipeline.nodes_by_id["speed_gate"]
            assert gate.node_type == "speed_gate"
            assert gate.config == {"source": "iccp_status", "min_abs_speed_mps": 0.01, "max_age_ms": 500}
