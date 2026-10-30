from __future__ import annotations
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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

        if vertex_count < 0:
            raise ValueError("negative PLY vertex count")
        if vertex_count == 0:
            return PointCloudData(np.empty((0, 3)))
        property_names = [name for name, _ in vertex_properties]
        if format_name == "ascii":
            columns = np.loadtxt(handle, dtype=np.float64, max_rows=vertex_count)
            if columns.ndim == 1:
                columns = columns.reshape(1, -1)
            if columns.shape != (vertex_count, len(vertex_properties)):
                raise ValueError("PLY vertex row has fewer columns than declared properties")
            data = {name: columns[:, index] for index, name in enumerate(property_names)}
        elif format_name == "binary_little_endian":
            dtype = np.dtype([(name, PLY_TYPE_MAP[prop_type]) for name, prop_type in vertex_properties])
            structured = np.fromfile(handle, dtype=dtype, count=vertex_count)
            if len(structured) != vertex_count:
                raise ValueError("truncated PLY payload")
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

        if not fields or not sizes or not kinds or point_count < 0:
            raise ValueError("Invalid PCD header: missing FIELDS/SIZE/TYPE/POINTS")

        if not (len(fields) == len(sizes) == len(kinds) == len(counts)):
            raise ValueError("PCD header field lengths disagree")
        if point_count == 0:
            return PointCloudData(np.empty((0, 3)))
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
            if raw.shape != (point_count, expected_columns):
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
            if len(structured) != point_count:
                raise ValueError("truncated PCD payload")
            data = {name: structured[name] for name in expanded_names}
        elif data_type == "binary_compressed":
            import open3d
            points = np.asarray(open3d.io.read_point_cloud(str(path)).points)
            return PointCloudData(_filter_finite_rows(points))
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
        if any(value is not None and not np.isfinite(value) for value in (lower, upper)):
            raise ValueError("bounds must be finite")
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
