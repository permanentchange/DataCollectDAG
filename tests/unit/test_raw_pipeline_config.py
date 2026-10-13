from pathlib import Path

import pytest
import yaml

from data_collect_dag.config import ConfigError, load_app_config


def test_fps_undist_preserves_fps_processing_and_saves_original_images():
    config = load_app_config(Path("demo/cfg.yaml"))
    reference = config.pipelines["fps"]
    raw = config.pipelines["fps_undist"]
    assert raw.main_source == reference.main_source
    assert raw.topics == reference.topics and raw.time_delay_ms == reference.time_delay_ms
    assert raw.stop_conditions == reference.stop_conditions
    removed = {key for key, node in reference.nodes_by_id.items() if node.node_type == "image_undistort"}
    assert len(removed) == 5
    assert set(raw.nodes_by_id) == set(reference.nodes_by_id) - removed
    assert not any(node.node_type in {"image_undistort", "yolo_gate"} for node in raw.nodes_by_id.values())
    for key, node in raw.nodes_by_id.items():
        if key != "save_xtreme1":
            assert node == reference.nodes_by_id[key]
    save = raw.nodes_by_id["save_xtreme1"]
    assert save.inputs == {
        "pointcloud": "aggregated_pointcloud", "camera_0": "main_frame", "camera_1": "front_camera",
        "camera_2": "rear_camera", "camera_3": "left_camera", "camera_4": "right_camera",
    }
    assert save.config == dict(reference.nodes_by_id["save_xtreme1"].config, include_distortion_params=True)
    assert set(raw.predecessors["save_xtreme1"]) == {"aggregate_pointclouds", "sync_images"}
    assert raw.successors["save_xtreme1"] == ["fps_marker"]
    assert raw.successors["fps_marker"] == ["end"]
    for key in raw.nodes_by_id:
        expected = [item for item in reference.successors.get(key, []) if item not in removed]
        if key == "sync_images":
            expected.append("save_xtreme1")
        assert raw.successors.get(key, []) == expected


@pytest.mark.parametrize("value", ["true", 1, None, []])
def test_include_distortion_params_requires_boolean(tmp_path, value):
    payload = yaml.safe_load(Path("demo/cfg.yaml").read_text())
    save = next(node for node in payload["pipelines"]["fps_undist"]["nodes"] if node["node_id"] == "save_xtreme1")
    save["config"]["include_distortion_params"] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(ConfigError, match="include_distortion_params must be a boolean"):
        load_app_config(path)
