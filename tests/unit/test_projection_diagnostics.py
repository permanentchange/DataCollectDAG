import numpy as np
import importlib.util
from pathlib import Path

import pytest

from data_collect_dag.diagnostics import filter_points_by_base_height, height_to_rgb, project_base_points


def _load_projection_tool():
    module_path = Path(__file__).parents[2] / "tools" / "visualize_pointcloud_projection.py"
    spec = importlib.util.spec_from_file_location("visualize_pointcloud_projection", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projection_tool_defaults_to_base_z_minus_two_to_three_meters():
    module = _load_projection_tool()

    assert module.MIN_BASE_HEIGHT_M == -2.0
    assert module.MAX_BASE_HEIGHT_M == 3.0


def test_projection_tool_supports_xyz_and_distance_color_modes_with_distance_default():
    module = _load_projection_tool()
    points = np.array([[3.0, 4.0, 12.0], [-1.0, 2.0, -2.0]], dtype=np.float64)

    assert module.build_parser().parse_args(["session", "stem"]).color_by == "d"
    assert module.build_parser().parse_args(["session", "stem", "--color-by", "x"]).color_by == "x"
    np.testing.assert_array_equal(module.color_values(points, "x"), [3.0, -1.0])
    np.testing.assert_array_equal(module.color_values(points, "y"), [4.0, 2.0])
    np.testing.assert_array_equal(module.color_values(points, "z"), [12.0, -2.0])
    np.testing.assert_array_equal(module.color_values(points, "d"), [13.0, 3.0])


def test_projection_tool_color_range_defaults_and_clamps_values_at_boundaries():
    module = _load_projection_tool()
    parser = module.build_parser()

    assert parser.parse_args(["session", "stem"]).color_range == (0.2, 20.0)
    assert parser.parse_args(["session", "stem", "--color-range=-1,1"]).color_range == (-1.0, 1.0)
    colors = height_to_rgb(np.array([0.1, 0.2, 10.0, 20.0, 21.0]), min_height=0.2, max_height=20.0)
    np.testing.assert_array_equal(colors[0], [0, 0, 255])
    np.testing.assert_array_equal(colors[-1], [255, 0, 0])
    with pytest.raises(SystemExit):
        parser.parse_args(["session", "stem", "--color-range", "20,0.2"])


def test_project_base_points_uses_camera_intrinsics_and_base_to_camera_transform():
    points = np.array(
        [
            [1.0, 0.0, 2.0],
            [0.0, 1.0, 2.0],
            [0.0, 0.0, -1.0],
            [10.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    camera_matrix = np.array([[10.0, 0.0, 10.0], [0.0, 10.0, 10.0], [0.0, 0.0, 1.0]])

    pixels, stats = project_base_points(points, camera_matrix, np.eye(4), width=20, height=20)

    np.testing.assert_array_equal(pixels, np.array([[15, 10], [10, 15]], dtype=np.int32))
    assert stats == {"input_points": 4, "points_in_front": 3, "points_in_image": 2}


def test_height_to_rgb_maps_low_heights_to_blue_and_high_heights_to_red():
    colors = height_to_rgb(np.array([0.0, 5.0, 10.0]), min_height=0.0, max_height=10.0)

    np.testing.assert_array_equal(colors[0], [0, 0, 255])
    np.testing.assert_array_equal(colors[-1], [255, 0, 0])
    np.testing.assert_array_equal(colors[1], [128, 0, 128])


def test_filter_points_by_base_height_keeps_inclusive_requested_range():
    points = np.array([[0.0, 0.0, -0.1], [0.0, 0.0, 0.0], [0.0, 0.0, 3.0], [0.0, 0.0, 3.1]])

    filtered = filter_points_by_base_height(points, min_height=0.0, max_height=3.0)

    np.testing.assert_array_equal(filtered, np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 3.0]]))
