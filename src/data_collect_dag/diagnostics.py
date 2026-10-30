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
    pixels, _indices, stats = project_camera_points(points_xyz, camera_matrix, base_to_camera, width, height)
    return pixels, {key: stats[key] for key in ("input_points", "points_in_front", "points_in_image")}


def project_camera_points(points, matrix, transform, width, height, model=None, coefficients=None):
    points = np.asarray(points, dtype=np.float64)
    matrix = np.asarray(matrix, dtype=np.float64)
    transform = np.asarray(transform, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or matrix.shape != (3, 3) or transform.shape != (4, 4):
        raise ValueError("expected Nx3 points, 3x3 intrinsics and 4x4 transform")
    if width <= 0 or height <= 0 or not np.isfinite(matrix).all() or not np.isfinite(transform).all():
        raise ValueError("invalid calibration or image dimensions")
    if model not in (None, 'pinhole', 'equidistant'):
        raise ValueError(f"unsupported distortion model: {model}")
    camera = np.c_[points, np.ones(len(points))] @ transform.T
    indices = np.flatnonzero(np.isfinite(camera).all(axis=1) & (camera[:, 2] > 0))
    visible = camera[indices, :3]
    if model == 'equidistant':
        import cv2
        distortion = np.asarray(coefficients, dtype=np.float64)
        if distortion.size != 4 or not np.isfinite(distortion).all():
            raise ValueError("equidistant requires four finite distortion coefficients")
        projected = cv2.fisheye.projectPoints(visible.reshape(1, -1, 3), np.zeros(3), np.zeros(3),
                                            matrix, distortion.reshape(4, 1))[0].reshape(-1, 2) if len(visible) else np.empty((0, 2))
    else:
        projected = visible @ matrix.T
        projected = projected[:, :2] / projected[:, 2:3]
    finite = np.isfinite(projected).all(axis=1)
    indices, projected = indices[finite], projected[finite]
    rounded = np.rint(projected)
    inside = (rounded[:, 0] >= 0) & (rounded[:, 0] < width) & (rounded[:, 1] >= 0) & (rounded[:, 1] < height)
    pixels, selected = rounded[inside].astype(np.int32), indices[inside]
    return pixels, selected, {'input_points': len(points), 'points_in_front': len(visible),
                              'points_in_image': len(pixels), 'points_rejected': len(points) - len(pixels)}
