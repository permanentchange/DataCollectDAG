#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


from tools.lib.cloud_io import *

def default_output_path(input_path: Path) -> Path:
    return input_path.with_suffix(".html")


def format_value(value: float) -> str:
    if math.isfinite(value):
        return "%.6f" % value
    return str(value)


def build_html(
    input_path: Path,
    output_path: Path,
    points: np.ndarray,
    color_values: np.ndarray,
    color_mode: str,
    colorscale: str,
    size: float,
    input_count: Optional[int] = None,
    filtered_count: Optional[int] = None,
) -> str:
    distances = np.linalg.norm(points, axis=1)
    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    center = points.mean(axis=0)
    scene_radius = float(np.max(maxs - mins)) or 1.0

    point_records = {
        "x": points[:, 0].astype(float).tolist(),
        "y": points[:, 1].astype(float).tolist(),
        "z": points[:, 2].astype(float).tolist(),
        "distance": distances.astype(float).tolist(),
        "color": color_values.astype(float).tolist(),
    }
    metadata = {
        "input_name": input_path.name,
        "output_name": output_path.name,
        "point_count": int(points.shape[0]),
        "input_count": input_count if input_count is not None else len(points),
        "filtered_count": filtered_count if filtered_count is not None else len(points),
        "color_mode": color_mode,
        "colorscale": colorscale,
        "size": float(size),
        "xmin": float(mins[0]),
        "xmax": float(maxs[0]),
        "ymin": float(mins[1]),
        "ymax": float(maxs[1]),
        "zmin": float(mins[2]),
        "zmax": float(maxs[2]),
        "center_x": float(center[0]),
        "center_y": float(center[1]),
        "center_z": float(center[2]),
        "scene_radius": scene_radius,
    }

    from plotly.offline import get_plotlyjs
    # Format the template before embedding JS: JavaScript contains braces.
    template = Path(__file__).with_name("viewer.html").read_text().format(
        input_name=__import__("html").escape(input_path.name),
        cloud_json=json.dumps(point_records, separators=(",", ":")),
        meta_json=json.dumps(metadata, separators=(",", ":")),
    )
    return template.replace('<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>',
                            '<script>' + get_plotlyjs() + '</script>')



def write_html(output_path: Path, content: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def open_in_browser(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize PCD or PLY point clouds in an interactive HTML viewer.")
    parser.add_argument("input", type=Path, help="Path to a .pcd or .ply file")
    parser.add_argument("--output-html", type=Path, help="Path to the generated HTML file")
    parser.add_argument("--size", type=float, default=2.0, help="Point size in the HTML viewer")
    parser.add_argument("--color", type=str, default=DEFAULT_COLORSCALE, help="Plotly colorscale name")
    parser.add_argument(
        "--color-mode",
        type=str,
        default="z",
        choices=SUPPORTED_COLOR_MODES,
        help="Dimension used for point coloring: x, y, z, or d(distance)",
    )
    parser.add_argument("--xmin", type=float, default=-50.0, help="Minimum x filter (default: -50)")
    parser.add_argument("--xmax", type=float, default=50.0, help="Maximum x filter (default: 50)")
    parser.add_argument("--ymin", type=float, default=-50.0, help="Minimum y filter (default: -50)")
    parser.add_argument("--ymax", type=float, default=50.0, help="Maximum y filter (default: 50)")
    parser.add_argument("--zmin", type=float, help="Minimum z filter (default: no limit)")
    parser.add_argument("--zmax", type=float, help="Maximum z filter (default: no limit)")
    parser.add_argument("--dmin", type=float, help="Minimum distance filter")
    parser.add_argument("--dmax", type=float, help="Maximum distance filter")
    parser.add_argument("--max-points", type=int, default=200000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--open", action="store_true", help="Open the generated HTML in the default browser")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.size <= 0:
        raise SystemExit("--size must be positive")
    if not args.input.is_file():
        raise SystemExit("Input file does not exist: %s" % args.input)

    bounds = FilterBounds(
        xmin=args.xmin,
        xmax=args.xmax,
        ymin=args.ymin,
        ymax=args.ymax,
        zmin=args.zmin,
        zmax=args.zmax,
        dmin=args.dmin,
        dmax=args.dmax,
    )
    validate_bounds(bounds)

    cloud = load_point_cloud(args.input)
    points = apply_filters(cloud.points, bounds)
    if points.size == 0:
        raise SystemExit("No valid points remain after filtering")

    if args.max_points < 0:
        raise ValueError("--max-points must be nonnegative")
    filtered_count = len(points)
    if args.max_points and len(points) > args.max_points:
        indices = np.random.default_rng(args.seed).choice(len(points), args.max_points, replace=False)
        points = points[np.sort(indices)]
    print(f"input={len(cloud.points)} filtered={filtered_count} displayed={len(points)}")
    color_values = build_color_values(points, args.color_mode)
    output_path = args.output_html or default_output_path(args.input)
    html = build_html(
        input_path=args.input,
        output_path=output_path,
        points=points,
        color_values=color_values,
        color_mode=args.color_mode,
        colorscale=args.color,
        size=args.size,
        input_count=len(cloud.points),
        filtered_count=filtered_count,
    )
    write_html(output_path, html)
    print("Wrote HTML viewer to %s" % output_path)

    if args.open:
        try:
            open_in_browser(output_path)
            print("Opened %s in the default browser" % output_path)
        except Exception as exc:
            print("Failed to open browser automatically: %s" % exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
