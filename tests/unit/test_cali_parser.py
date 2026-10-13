import importlib.util
import cv2
import numpy as np
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CALI_PARSER_PATH = ROOT / "tools" / "cali_parser.py"


def load_cali_parser():
    spec = importlib.util.spec_from_file_location("cali_parser", CALI_PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_output_path_defaults_to_vehicle_cali_json():
    parser = load_cali_parser()

    assert parser.resolve_output_path(Path("/tmp/vehicle"), None) == Path("/tmp/vehicle/cali.json")


def test_resolve_output_path_expands_explicit_output_path():
    parser = load_cali_parser()

    assert parser.resolve_output_path(Path("/tmp/vehicle"), Path("~/cali.json")) == Path.home() / "cali.json"


def test_camera_conversion_outputs_explicit_model_and_preserves_coefficients(tmp_path):
    parser = load_cali_parser()
    path = tmp_path / "camera.yaml"
    coeffs = np.array([0.1, -0.02, 0.003, -0.004])
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_WRITE)
    try:
        fs.write("topic", "/camera")
        fs.write("camera_matrix", np.eye(3))
        fs.write("dist_coeffs", coeffs.reshape(4, 1))
        fs.write("resolution", np.array([[1920], [1080]]))
        fs.write("base2camera", np.eye(4))
    finally:
        fs.release()
    result = parser.parse_camera_yaml(path)["/camera"]
    assert result["dist_model"] == "equidistant"
    assert result["dist_coeffs"] == coeffs.tolist()
    assert "is_fisheye" not in result and "distortion_model" not in result
