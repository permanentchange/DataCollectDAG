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
    from tools.lib.cloud_io import load_point_cloud
    return load_point_cloud(path).points


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
            reports.append({"camera_index": i, "missing_image": str(image_path)})
            continue

        image = np.asarray(Image.open(image_path).convert("RGB"))
        k = cam["camera_internal"]
        K = np.array([
            [k["fx"], 0, k["cx"]],
            [0, k["fy"], k["cy"]],
            [0, 0, 1]
        ], np.float64)

        T = np.asarray(cam["camera_external"], np.float64).reshape(4, 4, order="F")
        from tools.lib.geometry import project_camera_points
        if cam.get("rowMajor", False):
            T = np.asarray(cam["camera_external"], np.float64).reshape(4, 4)
        pixels, indices, stats = project_camera_points(
            points, K, T, image.shape[1], image.shape[0], cam.get("dist_model"), cam.get("dist_coeffs"))
        colors = height_to_rgb(values(points[indices], color_by), low, high)

        output = out_dir / f"camera_{i}_projected{suffix}.jpg"
        Image.fromarray(draw(image, pixels, colors)).save(output, quality=95)

        reports.append({
            "projection_model": cam.get("dist_model", "pinhole"),
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


def build_parser():
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

    return p


def main():
    p = build_parser()
    a = p.parse_args()

    xmin, xmax, ymin, ymax, zmin, zmax = a.pcd_range
    if not np.isfinite(a.pcd_range).all() or xmin >= xmax or ymin >= ymax or zmin >= zmax:
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