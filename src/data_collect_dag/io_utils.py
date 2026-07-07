from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
from PIL import Image


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_jpg(path: Path, image_bgr: np.ndarray, quality: int = 95) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image must be HxWx3 BGR array")
    rgb = image_bgr[:, :, ::-1]
    Image.fromarray(rgb).save(str(path), format="JPEG", quality=int(quality))


def save_ascii_pcd(path: Path, points_xyz: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    points = np.asarray(points_xyz, dtype=np.float64)
    lines: List[str] = [
        "# .PCD v0.7 - Point Cloud Data file format",
        "VERSION 0.7",
        "FIELDS x y z",
        "SIZE 4 4 4",
        "TYPE F F F",
        "COUNT 1 1 1",
        f"WIDTH {points.shape[0]}",
        "HEIGHT 1",
        "VIEWPOINT 0 0 0 1 0 0 0",
        f"POINTS {points.shape[0]}",
        "DATA ascii",
    ]
    for point in points:
        lines.append(f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

