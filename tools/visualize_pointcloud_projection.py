#!/usr/bin/env python3
"""Render base-frame point clouds on saved undistorted Xtreme1 camera images."""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image

from data_collect_dag.diagnostics import filter_points_by_base_height, height_to_rgb, project_base_points


MIN_BASE_HEIGHT_M = -2.0
MAX_BASE_HEIGHT_M = 3.0
DEFAULT_COLOR_RANGE = (0.2, 20.0)
COLOR_LABELS = {
    "x": "base_x_m",
    "y": "base_y_m",
    "z": "base_z_m",
    "d": "base_distance_m",
}


def load_ascii_pcd(path: Path) -> np.ndarray:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip().lower() == "data ascii") + 1
    except StopIteration as exc:
        raise ValueError(f"unsupported PCD format: {path}") from exc
    values = [line.split()[:3] for line in lines[start:] if line.strip()]
    return np.asarray(values, dtype=np.float64).reshape((-1, 3)) if values else np.empty((0, 3), dtype=np.float64)


def draw_points(image_rgb: np.ndarray, pixels: np.ndarray, colors_rgb: np.ndarray) -> np.ndarray:
    result = image_rgb.copy()
    height, width = result.shape[:2]
    for (x, y), color in zip(pixels, colors_rgb):
        x0, x1 = max(0, x - 1), min(width, x + 2)
        y0, y1 = max(0, y - 1), min(height, y + 2)
        result[y0:y1, x0:x1] = color
    return result


def color_values(points_xyz: np.ndarray, color_by: str) -> np.ndarray:
    points = np.asarray(points_xyz, dtype=np.float64)
    if color_by == "x":
        return points[:, 0]
    if color_by == "y":
        return points[:, 1]
    if color_by == "z":
        return points[:, 2]
    if color_by == "d":
        return np.linalg.norm(points, axis=1)
    raise ValueError(f"unsupported color mode: {color_by}")


def parse_color_range(value: str) -> Tuple[float, float]:
    try:
        low_text, high_text = value.split(",")
        low, high = float(low_text), float(high_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("color range must be MIN,MAX") from exc
    if not np.isfinite(low) or not np.isfinite(high) or low >= high:
        raise argparse.ArgumentTypeError("color range must be finite and have MIN < MAX")
    return low, high


def render_sample(
    session_root: Path,
    dataset_name: str,
    stem: str,
    color_by: str = "d",
    color_range: Tuple[float, float] = DEFAULT_COLOR_RANGE,
) -> Path:
    dataset_root = session_root / "xtreme1" / dataset_name
    config_path = dataset_root / "camera_config" / f"{stem}.json"
    pointcloud_path = dataset_root / "lidar_point_cloud_0" / f"{stem}.pcd"
    camera_items = json.loads(config_path.read_text(encoding="utf-8"))["items"]
    points = filter_points_by_base_height(load_ascii_pcd(pointcloud_path), MIN_BASE_HEIGHT_M, MAX_BASE_HEIGHT_M)
    low_value, high_value = color_range
    output_root = session_root / "diagnostics" / "projection" / stem
    output_root.mkdir(parents=True, exist_ok=True)
    reports: List[Dict[str, object]] = []
    for index, item in enumerate(camera_items):
        image_path = dataset_root / f"camera_image_{index}" / f"{stem}.jpg"
        if not image_path.exists():
            continue
        image = np.asarray(Image.open(image_path).convert("RGB"))
        internal = item["camera_internal"]
        matrix = np.array(
            [[internal["fx"], 0.0, internal["cx"]], [0.0, internal["fy"], internal["cy"]], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        transform = np.asarray(item["camera_external"], dtype=np.float64).reshape((4, 4), order="F")
        pixels, stats = project_base_points(points, matrix, transform, image.shape[1], image.shape[0])
        camera_points = np.concatenate([points, np.ones((points.shape[0], 1))], axis=1) @ transform.T
        in_front = camera_points[:, 2] > 0.0
        projected = camera_points[in_front, :3] @ matrix.T
        candidate_pixels = np.rint(projected[:, :2] / projected[:, 2:3]).astype(np.int32)
        in_image = (
            (candidate_pixels[:, 0] >= 0)
            & (candidate_pixels[:, 0] < image.shape[1])
            & (candidate_pixels[:, 1] >= 0)
            & (candidate_pixels[:, 1] < image.shape[0])
        )
        visible_values = color_values(points[in_front][in_image], color_by)
        colors = height_to_rgb(visible_values, low_value, high_value)
        output_path = output_root / f"camera_{index}_projected.jpg"
        Image.fromarray(draw_points(image, pixels, colors)).save(str(output_path), format="JPEG", quality=95)
        reports.append(
            {
                "camera_index": index,
                "source_image": str(image_path),
                "projection_image": str(output_path),
                "camera_matrix": matrix.tolist(),
                "base_to_camera": transform.tolist(),
                "height_filter": {
                    "axis": "base_z_m",
                    "range_m": [MIN_BASE_HEIGHT_M, MAX_BASE_HEIGHT_M],
                },
                "color": {
                    "mode": color_by,
                    "axis": COLOR_LABELS[color_by],
                    "range": [low_value, high_value],
                    "low_rgb": [0, 0, 255],
                    "high_rgb": [255, 0, 0],
                },
                **stats,
            }
        )
    report_path = output_root / "projection_report.json"
    report_path.write_text(json.dumps({"pointcloud": str(pointcloud_path), "cameras": reports}, indent=2), encoding="utf-8")
    return report_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_root", type=Path)
    parser.add_argument("stem", help="saved timestamp stem, without extension")
    parser.add_argument("--dataset-name", default="collect_demo")
    parser.add_argument("--color-by", choices=sorted(COLOR_LABELS), default="d", help="point color source; default: d")
    parser.add_argument(
        "--color-range",
        type=parse_color_range,
        default=DEFAULT_COLOR_RANGE,
        metavar="MIN,MAX",
        help="blue-to-red gradient range; values outside use the boundary color; default: 0.2,20",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    print(render_sample(args.session_root, args.dataset_name, args.stem, color_by=args.color_by, color_range=args.color_range))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
