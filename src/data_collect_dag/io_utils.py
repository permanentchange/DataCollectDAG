from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from PIL import Image
from data_collect_dag.storage import StorageTracker, tracked_write


def write_json(path: Path, payload: Dict[str, Any], storage_tracker: Optional[StorageTracker] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tracked_write(path, storage_tracker):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_jpg(path: Path, image_bgr: np.ndarray, quality: int = 95, storage_tracker: Optional[StorageTracker] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image must be HxWx3 BGR array")
    rgb = image_bgr[:, :, ::-1]
    with tracked_write(path, storage_tracker):
        Image.fromarray(rgb).save(str(path), format="JPEG", quality=int(quality))


def save_ascii_pcd(
    path: Path,
    points_xyz: np.ndarray,
    intensities: np.ndarray = None,
    has_intensity: np.ndarray = None,
    storage_tracker: Optional[StorageTracker] = None,
) -> None:
    points = np.asarray(points_xyz, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("points_xyz must have shape (N, 3)")
    if intensities is None:
        intensity_values = np.zeros(points.shape[0], dtype=np.float64)
        availability = np.zeros(points.shape[0], dtype=bool)
    else:
        intensity_values = np.asarray(intensities, dtype=np.float64).reshape(-1)
        if intensity_values.shape[0] != points.shape[0]:
            raise ValueError("intensities must align with points_xyz")
        if has_intensity is None:
            availability = np.ones(points.shape[0], dtype=bool)
        else:
            availability = np.asarray(has_intensity, dtype=bool).reshape(-1)
            if availability.shape[0] != points.shape[0]:
                raise ValueError("has_intensity must align with points_xyz")
    valid_values = intensity_values[availability]
    if (
        not np.isfinite(valid_values).all()
        or np.any(valid_values < 0)
        or np.any(valid_values > 255)
        or np.any(valid_values != np.rint(valid_values))
    ):
        raise ValueError("invalid intensity: available values must be integer uint8 values")
    output_intensities = np.where(availability, intensity_values, 0).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = [
        "# .PCD v0.7 - Point Cloud Data file format",
        "VERSION 0.7",
        "FIELDS x y z intensity has_intensity",
        "SIZE 4 4 4 1 1",
        "TYPE F F F U U",
        "COUNT 1 1 1 1 1",
        f"WIDTH {points.shape[0]}",
        "HEIGHT 1",
        "VIEWPOINT 0 0 0 1 0 0 0",
        f"POINTS {points.shape[0]}",
        "DATA ascii",
    ]
    for point, intensity, available in zip(points, output_intensities, availability):
        lines.append(f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f} {int(intensity)} {int(available)}")
    with tracked_write(path, storage_tracker):
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
