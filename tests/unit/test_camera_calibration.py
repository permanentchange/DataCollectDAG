import json
from pathlib import Path

import numpy as np
import pytest

from data_collect_dag.app import load_calibration
from data_collect_dag.camera_calibration import read_distortion_params


@pytest.mark.parametrize(
    "params,error",
    [
        ({"is_fisheye": True}, "is_fisheye is no longer supported"),
        ({"is_fisheye": False}, "is_fisheye is no longer supported"),
        ({"is_fisheye": True, "dist_model": "equidistant"}, "is_fisheye is no longer supported"),
        ({}, "missing valid dist_model"),
        ({"dist_model": None}, "missing valid dist_model"),
        ({"dist_model": True}, "missing valid dist_model"),
        ({"dist_model": "plumb_bob"}, "unsupported camera dist_model"),
        ({"dist_model": "rational_polynomial"}, "unsupported camera dist_model"),
        ({"dist_model": "unknown"}, "unsupported camera dist_model"),
        ({"dist_model": "equidistant"}, "missing valid dist_coeffs"),
        ({"dist_model": "equidistant", "dist_coeffs": []}, "four finite values"),
        ({"dist_model": "equidistant", "dist_coeffs": [0, 0, 0]}, "four finite values"),
        ({"dist_model": "equidistant", "dist_coeffs": [0, 0, 0, 0, 0]}, "four finite values"),
        ({"dist_model": "equidistant", "dist_coeffs": [float("nan"), 0, 0, 0]}, "four finite values"),
        ({"dist_model": "equidistant", "dist_coeffs": [float("inf"), 0, 0, 0]}, "four finite values"),
        ({"dist_model": "equidistant", "dist_coeffs": ["0", "0", "0", "0"]}, "missing valid dist_coeffs"),
        ({"dist_model": "equidistant", "dist_coeffs": [True, True, True, True]}, "missing valid dist_coeffs"),
        ({"dist_model": "equidistant", "dist_coeffs": [True, 0.01, 0, 0]}, "missing valid dist_coeffs"),
    ],
)
def test_invalid_camera_model_and_coefficients(params, error):
    with pytest.raises(ValueError, match=error):
        read_distortion_params(params)


def test_model_coefficients_keep_order_and_values():
    values = [0.1, -0.2, 0.003, -0.004]
    model, coeffs = read_distortion_params({"dist_model": "equidistant", "dist_coeffs": values})
    assert model == "equidistant" and coeffs.shape == (4, 1)
    assert coeffs.reshape(-1).tolist() == values


def test_calibration_loader_validates_models_and_identifies_camera(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"images": {"/camera": {"is_fisheye": True}}}))
    with pytest.raises(ValueError, match="invalid camera calibration for /camera.*dist_model"):
        load_calibration(path)


def test_calibration_loader_preserves_pointcloud_normalization(tmp_path):
    path = tmp_path / "calibration.json"
    image = {"dist_model": "equidistant", "dist_coeffs": [0.1, 0.2, 0.3, 0.4]}
    transform = np.eye(4).tolist()
    path.write_text(json.dumps({
        "images": {"/camera": image},
        "pointclouds": {"/lidar": {"sensor2base": transform}, "/unused": {}},
    }))
    assert load_calibration(path) == {"images": {"/camera": image}, "pointclouds": {"/lidar": transform}}


def test_all_committed_demo_calibrations_use_new_model():
    directory = Path("demo/calibration")
    assert not list(directory.glob("*.json"))
    paths = list(directory.glob("*/cali.json"))
    assert len(paths) == 13
    assert (directory / "sa4/cali.json") in paths
    for path in paths:
        assert not path.is_symlink()
        images = load_calibration(path)["images"]
        assert images
        assert all(entry["dist_model"] == "equidistant" and "is_fisheye" not in entry for entry in images.values())
