from __future__ import annotations

from typing import Dict, Tuple

import numpy as np


def filter_points_by_base_height(points_xyz: np.ndarray, min_height: float, max_height: float) -> np.ndarray:
    """Return finite base-frame points whose inclusive Z height is in range."""
    points = np.asarray(points_xyz, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("points_xyz must have shape Nx3")
    if not np.isfinite(min_height) or not np.isfinite(max_height) or max_height < min_height:
        raise ValueError("height range must be finite and non-decreasing")
    finite = np.isfinite(points).all(axis=1)
    in_range = (points[:, 2] >= min_height) & (points[:, 2] <= max_height)
    return points[finite & in_range]


def height_to_rgb(heights: np.ndarray, min_height: float, max_height: float) -> np.ndarray:
    """Map base-frame Z heights to RGB, from blue (low) to red (high)."""
    values = np.asarray(heights, dtype=np.float64).reshape(-1)
    if not np.isfinite(min_height) or not np.isfinite(max_height) or max_height <= min_height:
        raise ValueError("height range must be finite and increasing")
    ratio = np.clip((values - min_height) / (max_height - min_height), 0.0, 1.0)
    red = np.rint(255.0 * ratio).astype(np.uint8)
    blue = np.rint(255.0 * (1.0 - ratio)).astype(np.uint8)
    return np.stack([red, np.zeros_like(red), blue], axis=1)


def project_base_points(
    points_xyz: np.ndarray,
    camera_matrix: np.ndarray,
    base_to_camera: np.ndarray,
    width: int,
    height: int,
) -> Tuple[np.ndarray, Dict[str, int]]:
    """Project base-frame points into an undistorted pinhole image."""
    points = np.asarray(points_xyz, dtype=np.float64)
    matrix = np.asarray(camera_matrix, dtype=np.float64)
    transform = np.asarray(base_to_camera, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("points_xyz must have shape Nx3")
    if matrix.shape != (3, 3):
        raise ValueError("camera_matrix must have shape 3x3")
    if transform.shape != (4, 4):
        raise ValueError("base_to_camera must have shape 4x4")
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")

    camera_points = np.concatenate(
        [points, np.ones((points.shape[0], 1), dtype=np.float64)], axis=1
    ) @ transform.T
    in_front = camera_points[:, 2] > 0.0
    visible = camera_points[in_front, :3]
    projected = visible @ matrix.T
    pixels_float = projected[:, :2] / projected[:, 2:3]
    pixels = np.rint(pixels_float).astype(np.int32)
    in_image = (
        (pixels[:, 0] >= 0)
        & (pixels[:, 0] < width)
        & (pixels[:, 1] >= 0)
        & (pixels[:, 1] < height)
    )
    return pixels[in_image], {
        "input_points": int(points.shape[0]),
        "points_in_front": int(in_front.sum()),
        "points_in_image": int(in_image.sum()),
    }
