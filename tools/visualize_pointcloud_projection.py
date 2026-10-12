#!/usr/bin/env python3

import argparse, json, re
from pathlib import Path
import numpy as np
from PIL import Image

from data_collect_dag.diagnostics import (
    height_to_rgb, project_base_points
)

DEFAULT_RANGE = (0.2, 20.0)
DEFAULT_PCD_RANGE = (-10.0, 10.0, -10.0, 10.0, 0.2, 2.0)

LABELS = {"x": "base_x_m", "y": "base_y_m", "z": "base_z_m", "d": "base_distance_m"}
NEG_RANGE = re.compile(r"^-[\d.]+(?:[eE][+-]?\d+)?,-?[\d.]+(?:[eE][+-]?\d+)?$")


class Parser(argparse.ArgumentParser):
    def _parse_optional(self, s):
        return None if NEG_RANGE.match(s) else super()._parse_optional(s)


def load_pcd(path):
    lines = path.read_text().splitlines()
    start = next(i for i, x in enumerate(lines) if x.strip().lower() == "data ascii") + 1
    data = [x.split()[:3] for x in lines[start:] if x.strip()]
    return np.asarray(data, np.float64).reshape(-1, 3) if data else np.empty((0, 3))


def parse_range(s):
    try:
        a, b = map(float, s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("must be MIN,MAX")
    if not np.isfinite(a) or not np.isfinite(b) or a >= b:
        raise argparse.ArgumentTypeError("must satisfy MIN < MAX")
    return a, b


def filter_pcd_range(points, pcd_range):
    xmin, xmax, ymin, ymax, zmin, zmax = pcd_range
    return points[
        (points[:, 0] >= xmin) & (points[:, 0] <= xmax) &
        (points[:, 1] >= ymin) & (points[:, 1] <= ymax) &
        (points[:, 2] >= zmin) & (points[:, 2] <= zmax)
    ]


def values(points, mode):
    return {
        "x": lambda: points[:, 0],
        "y": lambda: points[:, 1],
        "z": lambda: points[:, 2],
        "d": lambda: np.linalg.norm(points, axis=1),
    }[mode]()


def draw(image, pixels, colors):
    out = image.copy()
    h, w = out.shape[:2]
    for (x, y), c in zip(pixels, colors):
        out[max(0, y - 1):min(h, y + 2), max(0, x - 1):min(w, x + 2)] = c
    return out


def render(root, stem, color_by="d", color_range=DEFAULT_RANGE,
           pcd_range=DEFAULT_PCD_RANGE, suffix=""):
    config = root / "camera_config" / f"{stem}.json"
    pcd = root / "lidar_point_cloud_0" / f"{stem}.pcd"
    cameras = json.loads(config.read_text())["items"]

    points = filter_pcd_range(load_pcd(pcd), pcd_range)
    low, high = color_range

    out_dir = root / "diagnostics" / "projection" / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = []

    for i, cam in enumerate(cameras):
        image_path = root / f"camera_image_{i}" / f"{stem}.jpg"
        if not image_path.exists():
            continue

        image = np.asarray(Image.open(image_path).convert("RGB"))
        k = cam["camera_internal"]
        K = np.array([
            [k["fx"], 0, k["cx"]],
            [0, k["fy"], k["cy"]],
            [0, 0, 1]
        ], np.float64)

        T = np.asarray(cam["camera_external"], np.float64).reshape(4, 4, order="F")
        pixels, stats = project_base_points(
            points, K, T, image.shape[1], image.shape[0]
        )

        cp = np.c_[points, np.ones(len(points))] @ T.T
        front = cp[:, 2] > 0
        proj = cp[front, :3] @ K.T
        px = np.rint(proj[:, :2] / proj[:, 2:3]).astype(np.int32)

        inside = (
            (px[:, 0] >= 0) & (px[:, 0] < image.shape[1]) &
            (px[:, 1] >= 0) & (px[:, 1] < image.shape[0])
        )

        colors = height_to_rgb(
            values(points[front][inside], color_by), low, high
        )

        output = out_dir / f"camera_{i}_projected{suffix}.jpg"
        Image.fromarray(draw(image, pixels, colors)).save(output, quality=95)

        reports.append({
            "camera_index": i,
            "source_image": str(image_path),
            "projection_image": str(output),
            "camera_matrix": K.tolist(),
            "base_to_camera": T.tolist(),
            "pcd_range": {
                "xmin": pcd_range[0],
                "xmax": pcd_range[1],
                "ymin": pcd_range[2],
                "ymax": pcd_range[3],
                "zmin": pcd_range[4],
                "zmax": pcd_range[5],
            },
            "color": {
                "mode": color_by,
                "axis": LABELS[color_by],
                "range": [low, high]
            },
            **stats
        })

    report = out_dir / f"projection_report{suffix}.json"
    report.write_text(json.dumps({
        "pointcloud": str(pcd),
        "cameras": reports
    }, indent=2))

    return report


def main():
    p = Parser()
    p.add_argument("session_root", type=Path)
    p.add_argument("stem")
    p.add_argument("--color-by", choices=LABELS, default="d")
    p.add_argument("--color-range", type=parse_range, default=DEFAULT_RANGE)
    p.add_argument(
        "--pcd-range",
        nargs=6,
        type=float,
        metavar=("XMIN", "XMAX", "YMIN", "YMAX", "ZMIN", "ZMAX"),
        default=DEFAULT_PCD_RANGE
    )
    p.add_argument("--suffix", default="")

    a = p.parse_args()

    xmin, xmax, ymin, ymax, zmin, zmax = a.pcd_range
    if xmin >= xmax or ymin >= ymax or zmin >= zmax:
        p.error("--pcd-range must satisfy xmin<xmax, ymin<ymax, zmin<zmax")

    print(render(
        a.session_root,
        a.stem,
        a.color_by,
        a.color_range,
        a.pcd_range,
        a.suffix
    ))


if __name__ == "__main__":
    main()