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


PLY_TYPE_MAP = {
    "char": np.int8,
    "uchar": np.uint8,
    "int8": np.int8,
    "uint8": np.uint8,
    "short": np.int16,
    "ushort": np.uint16,
    "int16": np.int16,
    "uint16": np.uint16,
    "int": np.int32,
    "uint": np.uint32,
    "int32": np.int32,
    "uint32": np.uint32,
    "float": np.float32,
    "float32": np.float32,
    "double": np.float64,
    "float64": np.float64,
}

PCD_TYPE_MAP = {
    ("F", "4"): np.float32,
    ("F", "8"): np.float64,
    ("I", "1"): np.int8,
    ("I", "2"): np.int16,
    ("I", "4"): np.int32,
    ("U", "1"): np.uint8,
    ("U", "2"): np.uint16,
    ("U", "4"): np.uint32,
}

DEFAULT_COLORSCALE = "Turbo"
SUPPORTED_COLOR_MODES = ("x", "y", "z", "d")


@dataclass
class PointCloudData:
    points: np.ndarray


@dataclass
class FilterBounds:
    xmin: Optional[float]
    xmax: Optional[float]
    ymin: Optional[float]
    ymax: Optional[float]
    zmin: Optional[float]
    zmax: Optional[float]
    dmin: Optional[float]
    dmax: Optional[float]


def _filter_finite_rows(points: np.ndarray) -> np.ndarray:
    finite_mask = np.isfinite(points).all(axis=1)
    return points[finite_mask]


def load_point_cloud(path: Path) -> PointCloudData:
    suffix = path.suffix.lower()
    if suffix == ".ply":
        return load_ply(path)
    if suffix == ".pcd":
        return load_pcd(path)
    raise ValueError("Unsupported file type: %s. Expected .pcd or .ply" % path.suffix)


def load_ply(path: Path) -> PointCloudData:
    with path.open("rb") as handle:
        header_lines: List[str] = []
        while True:
            line = handle.readline()
            if not line:
                raise ValueError("Unexpected EOF while reading PLY header")
            decoded = line.decode("utf-8", errors="ignore").strip()
            header_lines.append(decoded)
            if decoded == "end_header":
                break

        format_name = None
        vertex_count = None
        vertex_properties: List[Tuple[str, str]] = []
        current_element = None
        for line in header_lines:
            if not line or line.startswith("comment"):
                continue
            parts = line.split()
            keyword = parts[0]
            if keyword == "format":
                format_name = parts[1]
            elif keyword == "element":
                current_element = parts[1]
                if current_element == "vertex":
                    vertex_count = int(parts[2])
            elif keyword == "property" and current_element == "vertex":
                if parts[1] == "list":
                    raise ValueError("PLY list vertex properties are not supported")
                vertex_properties.append((parts[2], parts[1]))

        if format_name is None or vertex_count is None or not vertex_properties:
            raise ValueError("Invalid PLY header: missing format, vertex count, or properties")

        property_names = [name for name, _ in vertex_properties]
        if format_name == "ascii":
            columns = np.loadtxt(handle, dtype=np.float64, max_rows=vertex_count)
            if columns.ndim == 1:
                columns = columns.reshape(1, -1)
            if columns.shape[1] < len(vertex_properties):
                raise ValueError("PLY vertex row has fewer columns than declared properties")
            data = {name: columns[:, index] for index, name in enumerate(property_names)}
        elif format_name == "binary_little_endian":
            dtype = np.dtype([(name, PLY_TYPE_MAP[prop_type]) for name, prop_type in vertex_properties])
            structured = np.fromfile(handle, dtype=dtype, count=vertex_count)
            data = {name: structured[name] for name in property_names}
        else:
            raise ValueError("Unsupported PLY format: %s" % format_name)

    if not {"x", "y", "z"}.issubset(data):
        raise ValueError("PLY file does not contain x/y/z fields")

    points = np.column_stack((data["x"], data["y"], data["z"])).astype(np.float32)
    points = _filter_finite_rows(points)
    return PointCloudData(points=points)


def load_pcd(path: Path) -> PointCloudData:
    header: Dict[str, List[str]] = {}
    data_type = None
    with path.open("rb") as handle:
        while True:
            line = handle.readline()
            if not line:
                raise ValueError("Unexpected EOF while reading PCD header")
            decoded = line.decode("utf-8", errors="ignore").strip()
            if not decoded or decoded.startswith("#"):
                continue
            parts = decoded.split()
            key = parts[0].upper()
            header[key] = parts[1:]
            if key == "DATA":
                data_type = parts[1].lower()
                break

        fields = header.get("FIELDS")
        sizes = header.get("SIZE")
        kinds = header.get("TYPE")
        counts = header.get("COUNT", ["1"] * len(fields or []))
        point_count = int(header.get("POINTS", ["0"])[0])

        if not fields or not sizes or not kinds or point_count <= 0:
            raise ValueError("Invalid PCD header: missing FIELDS/SIZE/TYPE/POINTS")

        dtype_fields = []
        expanded_names: List[str] = []
        for field, size, kind, count in zip(fields, sizes, kinds, counts):
            np_type = PCD_TYPE_MAP.get((kind.upper(), size))
            if np_type is None:
                raise ValueError("Unsupported PCD field type: kind=%s size=%s" % (kind, size))
            repeat = int(count)
            if repeat == 1:
                dtype_fields.append((field, np_type))
                expanded_names.append(field)
            else:
                dtype_fields.append((field, np_type, (repeat,)))
                expanded_names.append(field)

        dtype = np.dtype(dtype_fields)
        if data_type == "ascii":
            raw = np.loadtxt(handle, dtype=np.float64, max_rows=point_count)
            if raw.ndim == 1:
                raw = raw.reshape(1, -1)
            expected_columns = sum(int(count) for count in counts)
            if raw.shape[1] < expected_columns:
                raise ValueError("PCD ascii payload has fewer columns than declared")
            data: Dict[str, np.ndarray] = {}
            column_index = 0
            for field, count in zip(fields, counts):
                repeat = int(count)
                values = raw[:, column_index:column_index + repeat]
                data[field] = values[:, 0] if repeat == 1 else values
                column_index += repeat
        elif data_type == "binary":
            structured = np.fromfile(handle, dtype=dtype, count=point_count)
            data = {name: structured[name] for name in expanded_names}
        else:
            raise ValueError("Unsupported PCD DATA mode: %s" % data_type)

    if not {"x", "y", "z"}.issubset(data):
        raise ValueError("PCD file does not contain x/y/z fields")

    points = np.column_stack((data["x"], data["y"], data["z"])).astype(np.float32)
    points = _filter_finite_rows(points)
    return PointCloudData(points=points)


def apply_filters(points: np.ndarray, bounds: FilterBounds) -> np.ndarray:
    mask = np.ones(points.shape[0], dtype=bool)
    if bounds.xmin is not None:
        mask &= points[:, 0] >= bounds.xmin
    if bounds.xmax is not None:
        mask &= points[:, 0] <= bounds.xmax
    if bounds.ymin is not None:
        mask &= points[:, 1] >= bounds.ymin
    if bounds.ymax is not None:
        mask &= points[:, 1] <= bounds.ymax
    if bounds.zmin is not None:
        mask &= points[:, 2] >= bounds.zmin
    if bounds.zmax is not None:
        mask &= points[:, 2] <= bounds.zmax
    if bounds.dmin is not None or bounds.dmax is not None:
        distances = np.linalg.norm(points, axis=1)
        if bounds.dmin is not None:
            mask &= distances >= bounds.dmin
        if bounds.dmax is not None:
            mask &= distances <= bounds.dmax
    return points[mask]


def validate_bounds(bounds: FilterBounds) -> None:
    checks = (
        ("xmin", bounds.xmin, "xmax", bounds.xmax),
        ("ymin", bounds.ymin, "ymax", bounds.ymax),
        ("zmin", bounds.zmin, "zmax", bounds.zmax),
        ("dmin", bounds.dmin, "dmax", bounds.dmax),
    )
    for lower_name, lower, upper_name, upper in checks:
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("%s cannot be greater than %s" % (lower_name, upper_name))


def build_color_values(points: np.ndarray, color_mode: str) -> np.ndarray:
    if color_mode == "x":
        return points[:, 0]
    if color_mode == "y":
        return points[:, 1]
    if color_mode == "z":
        return points[:, 2]
    if color_mode == "d":
        return np.linalg.norm(points, axis=1)
    raise ValueError("Unsupported color mode: %s" % color_mode)


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

    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Point Cloud Viewer</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f2f4f7;
      --panel: rgba(255, 255, 255, 0.92);
      --text: #16202a;
      --muted: #5f6b7a;
      --border: rgba(22, 32, 42, 0.12);
      --shadow: 0 12px 32px rgba(19, 31, 45, 0.14);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(80, 154, 255, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(60, 196, 170, 0.18), transparent 24%),
        var(--bg);
      color: var(--text);
    }}
    .page {{
      display: grid;
      grid-template-columns: minmax(280px, 360px) 1fr;
      gap: 18px;
      min-height: 100vh;
      padding: 18px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    .sidebar {{
      padding: 18px;
    }}
    .title {{
      margin: 0 0 6px;
      font-size: 24px;
      line-height: 1.2;
    }}
    .subtitle {{
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      word-break: break-all;
    }}
    .section {{
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
    }}
    .section:first-of-type {{
      margin-top: 0;
      padding-top: 0;
      border-top: none;
    }}
    .section h2 {{
      margin: 0 0 12px;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .stats {{
      display: grid;
      gap: 10px;
    }}
    .stat {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(244, 247, 250, 0.88);
    }}
    .stat .label {{
      color: var(--muted);
      font-size: 13px;
    }}
    .stat .value {{
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    .picked {{
      display: grid;
      gap: 10px;
    }}
    .hint {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .viewer {{
      overflow: hidden;
      min-height: calc(100vh - 36px);
    }}
    #plot {{
      width: 100%;
      height: calc(100vh - 36px);
    }}
    @media (max-width: 960px) {{
      .page {{
        grid-template-columns: 1fr;
      }}
      .viewer, #plot {{
        min-height: 70vh;
        height: 70vh;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <aside class="panel sidebar">
      <h1 class="title">Point Cloud Viewer</h1>
      <p class="subtitle">{input_name}</p>

      <section class="section">
        <h2>Overview</h2>
        <div class="stats">
          <div class="stat"><span class="label">Points</span><span class="value" id="point-count"></span></div>
          <div class="stat"><span class="label">Color Mode</span><span class="value" id="color-mode"></span></div>
          <div class="stat"><span class="label">Color Scale</span><span class="value" id="color-scale"></span></div>
          <div class="stat"><span class="label">Point Size</span><span class="value" id="point-size"></span></div>
        </div>
      </section>

      <section class="section">
        <h2>Selection</h2>
        <div class="picked">
          <p class="hint" id="selection-hint">左键点击一个点以查看真实坐标。左键拖拽旋转，滚轮缩放，右键拖拽平移。</p>
          <div class="stat"><span class="label">Index</span><span class="value" id="picked-index">-</span></div>
          <div class="stat"><span class="label">X</span><span class="value" id="picked-x">-</span></div>
          <div class="stat"><span class="label">Y</span><span class="value" id="picked-y">-</span></div>
          <div class="stat"><span class="label">Z</span><span class="value" id="picked-z">-</span></div>
          <div class="stat"><span class="label">Distance</span><span class="value" id="picked-distance">-</span></div>
        </div>
      </section>

      <section class="section">
        <h2>Bounds</h2>
        <div class="stats">
          <div class="stat"><span class="label">X Range</span><span class="value" id="x-range"></span></div>
          <div class="stat"><span class="label">Y Range</span><span class="value" id="y-range"></span></div>
          <div class="stat"><span class="label">Z Range</span><span class="value" id="z-range"></span></div>
        </div>
      </section>
    </aside>
    <main class="panel viewer">
      <div id="plot"></div>
    </main>
  </div>

  <script>
    const cloud = {cloud_json};
    const meta = {meta_json};

    document.getElementById("point-count").textContent = String(meta.point_count);
    document.getElementById("color-mode").textContent = meta.color_mode;
    document.getElementById("color-scale").textContent = meta.colorscale;
    document.getElementById("point-size").textContent = String(meta.size);
    document.getElementById("x-range").textContent = `${{meta.xmin.toFixed(6)}} to ${{meta.xmax.toFixed(6)}}`;
    document.getElementById("y-range").textContent = `${{meta.ymin.toFixed(6)}} to ${{meta.ymax.toFixed(6)}}`;
    document.getElementById("z-range").textContent = `${{meta.zmin.toFixed(6)}} to ${{meta.zmax.toFixed(6)}}`;

    const trace = {{
      type: "scatter3d",
      mode: "markers",
      x: cloud.x,
      y: cloud.y,
      z: cloud.z,
      customdata: cloud.distance.map((distance, index) => [index, distance]),
      hovertemplate:
        "x=%{{x:.6f}}<br>" +
        "y=%{{y:.6f}}<br>" +
        "z=%{{z:.6f}}<br>" +
        "distance=%{{customdata[1]:.6f}}<extra></extra>",
      marker: {{
        size: meta.size,
        color: cloud.color,
        colorscale: meta.colorscale,
        opacity: 0.95,
        colorbar: {{
          title: meta.color_mode
        }}
      }}
    }};

    const layout = {{
      margin: {{ l: 0, r: 0, t: 0, b: 0 }},
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      showlegend: false,
      dragmode: "orbit",
      scene: {{
        aspectmode: "data",
        xaxis: {{ title: "X" }},
        yaxis: {{ title: "Y" }},
        zaxis: {{ title: "Z" }},
        camera: {{
          center: {{ x: 0, y: 0, z: 0 }},
          eye: {{
            x: 1.6,
            y: 1.6,
            z: 0.9
          }}
        }}
      }}
    }};

    const config = {{
      responsive: true,
      scrollZoom: true,
      displaylogo: false,
      modeBarButtonsToAdd: ["orbitRotation", "tableRotation", "resetCameraDefault3d", "resetCameraLastSave3d"],
      toImageButtonOptions: {{
        filename: meta.output_name.replace(/\\.html$/i, "")
      }}
    }};

    const plotDiv = document.getElementById("plot");
    Plotly.newPlot(plotDiv, [trace], layout, config);

    plotDiv.on("plotly_click", (eventData) => {{
      if (!eventData || !eventData.points || !eventData.points.length) {{
        return;
      }}
      const point = eventData.points[0];
      document.getElementById("selection-hint").textContent = "已选中一个点。继续左键点击其他点可更新坐标。";
      document.getElementById("picked-index").textContent = String(point.customdata[0]);
      document.getElementById("picked-x").textContent = Number(point.x).toFixed(6);
      document.getElementById("picked-y").textContent = Number(point.y).toFixed(6);
      document.getElementById("picked-z").textContent = Number(point.z).toFixed(6);
      document.getElementById("picked-distance").textContent = Number(point.customdata[1]).toFixed(6);
    }});
  </script>
</body>
</html>
""".format(
        input_name=input_path.name,
        cloud_json=json.dumps(point_records, separators=(",", ":")),
        meta_json=json.dumps(metadata, separators=(",", ":")),
    )


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
