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


def test_projection_tool_defaults_and_overrides():
    module = _load_projection_tool()
    parser = module.build_parser()
    args = parser.parse_args(["session", "stem"])
    assert args.pcd_range == (-10., 10., -10., 10., .2, 2.)
    assert args.color_by == "d" and args.color_range == (.2, 20.)
    assert parser.parse_args(["session", "stem", "--color-range", "-0.1,0.1"]).color_range == (-.1, .1)
    with pytest.raises(SystemExit):
        parser.parse_args(["session", "stem", "--color-range", "20,0.2"])


@pytest.mark.parametrize("mode,expected", [("x", [3, -1]), ("y", [4, 2]), ("z", [12, -2]), ("d", [13, 3])])
def test_projection_color_values(mode, expected):
    np.testing.assert_array_equal(_load_projection_tool().values(np.array([[3., 4., 12.], [-1., 2., -2.]]), mode), expected)


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
