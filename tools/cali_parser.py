#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
	import yaml
except ImportError as exc:  # pragma: no cover
	raise ImportError("PyYAML is required. Please install it in the target env.") from exc


PRIMARY_LIDAR_TOPIC = "/rslidar_points"


def normalize_topic(topic: str) -> str:
	topic = topic.strip()
	if not topic:
		raise ValueError("topic is empty")
	if not topic.startswith("/"):
		topic = "/" + topic
	return topic


def read_matrix(storage: cv2.FileStorage, name: str, shape: Tuple[int, int]) -> List[Any]:
	node = storage.getNode(name)
	if node.empty():
		raise KeyError(f"missing field: {name}")
	mat = node.mat()
	if mat is None:
		raise ValueError(f"invalid matrix field: {name}")
	if mat.shape != shape:
		raise ValueError(f"field {name} shape mismatch, expect {shape}, got {mat.shape}")
	return [[float(v) for v in row] for row in mat.tolist()]


def read_vector(storage: cv2.FileStorage, name: str, length: int, as_int: bool = False) -> List[Any]:
	node = storage.getNode(name)
	if node.empty():
		raise KeyError(f"missing field: {name}")
	mat = node.mat()
	if mat is None:
		raise ValueError(f"invalid vector field: {name}")
	flat = mat.reshape(-1)
	if flat.size != length:
		raise ValueError(f"field {name} length mismatch, expect {length}, got {flat.size}")
	if as_int:
		return [int(v) for v in flat.tolist()]
	return [float(v) for v in flat.tolist()]


def parse_camera_yaml(yaml_path: Path) -> Dict[str, Dict[str, Any]]:
	fs = cv2.FileStorage(str(yaml_path), cv2.FILE_STORAGE_READ)
	if not fs.isOpened():
		raise FileNotFoundError(f"cannot open yaml: {yaml_path}")

	try:
		topic = fs.getNode("topic").string()
		if not topic:
			raise KeyError("missing field: topic")

		key = normalize_topic(topic)
		camera_data = {
			"camera_matrix": read_matrix(fs, "camera_matrix", (3, 3)),
			"dist_coeffs": read_vector(fs, "dist_coeffs", 4),
			"resolution": read_vector(fs, "resolution", 2, as_int=True),
			"scale_xy": [1.0, 1.0],
			"shift_xy": [0.0, 0.0],
			"base2camera": read_matrix(fs, "base2camera", (4, 4)),
			"is_fisheye": True,
		}
		return {key: camera_data}
	finally:
		fs.release()


def parse_camera_dir(camera_dir: Path) -> Dict[str, Dict[str, Any]]:
	if not camera_dir.is_dir():
		raise NotADirectoryError(f"camera dir not found: {camera_dir}")

	merged: Dict[str, Dict[str, Any]] = {}
	yaml_files = sorted(camera_dir.glob("*.yaml"))
	if not yaml_files:
		raise FileNotFoundError(f"no yaml found in dir: {camera_dir}")

	for yaml_file in yaml_files:
		parsed = parse_camera_yaml(yaml_file)
		for key, value in parsed.items():
			if key in merged:
				raise ValueError(f"duplicated camera key {key} from file: {yaml_file}")
			merged[key] = value

	return merged


def _normalize_quaternion(qw: float, qx: float, qy: float, qz: float) -> Tuple[float, float, float, float]:
	norm = float(np.sqrt(qw * qw + qx * qx + qy * qy + qz * qz))
	if norm == 0.0:
		raise ValueError("invalid quaternion: norm is zero")
	return qw / norm, qx / norm, qy / norm, qz / norm


def quaternion_to_rotation_matrix(qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
	qw, qx, qy, qz = _normalize_quaternion(qw, qx, qy, qz)
	return np.array(
		[
			[1.0 - 2.0 * (qy * qy + qz * qz), 2.0 * (qx * qy - qz * qw), 2.0 * (qx * qz + qy * qw)],
			[2.0 * (qx * qy + qz * qw), 1.0 - 2.0 * (qx * qx + qz * qz), 2.0 * (qy * qz - qx * qw)],
			[2.0 * (qx * qz - qy * qw), 2.0 * (qy * qz + qx * qw), 1.0 - 2.0 * (qx * qx + qy * qy)],
		],
		dtype=np.float64,
	)


def pose_to_matrix(tx: float, ty: float, tz: float, qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
	transform = np.eye(4, dtype=np.float64)
	transform[:3, :3] = quaternion_to_rotation_matrix(qw, qx, qy, qz)
	transform[:3, 3] = np.array([tx, ty, tz], dtype=np.float64)
	return transform


def resolve_aux_lidar_ref_path(config_path: Path, configured_path: str) -> Path:
	candidate = Path(configured_path).expanduser()
	if candidate.exists():
		return candidate

	fallback = config_path.parent / candidate.name
	if fallback.exists():
		return fallback

	raise FileNotFoundError(
		f"aux_lidar_calib_file not found: {candidate}; fallback also missing: {fallback}"
	)


def parse_lidar_ref_file(ref_path: Path) -> List[Dict[str, float]]:
	poses: List[Dict[str, float]] = []
	lines = ref_path.read_text(encoding="utf-8").splitlines()
	for line_idx, line in enumerate(lines, start=1):
		stripped = line.strip()
		if not stripped:
			continue
		parts = stripped.split()
		if len(parts) != 7:
			raise ValueError(f"invalid lidar_ref line {line_idx}: expected 7 values, got {len(parts)}")
		tx, ty, tz, qw, qx, qy, qz = [float(x) for x in parts]
		poses.append({"tx": tx, "ty": ty, "tz": tz, "qw": qw, "qx": qx, "qy": qy, "qz": qz})
	return poses


def load_freespace_config(config_path: Path) -> Dict[str, Any]:
	data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
	if not isinstance(data, dict):
		raise ValueError("freespace yaml root must be a mapping")
	return data


def build_pointclouds_subitem(freespace_config: Dict[str, Any], lidar_ref_path: Path) -> Dict[str, Any]:
	lidar_cfg = freespace_config.get("lidar")
	if not isinstance(lidar_cfg, dict):
		raise ValueError("missing 'lidar' section")

	rslidar2base = np.asarray(lidar_cfg.get("rslidar2base"), dtype=np.float64)
	if rslidar2base.shape != (4, 4):
		raise ValueError(f"lidar.rslidar2base must be 4x4, got {rslidar2base.shape}")

	aux_topics = lidar_cfg.get("auxiliary_lidar_topic")
	if not isinstance(aux_topics, list) or not aux_topics:
		raise ValueError("lidar.auxiliary_lidar_topic must be a non-empty list")

	ref_poses = parse_lidar_ref_file(lidar_ref_path)
	if len(ref_poses) != len(aux_topics):
		raise ValueError(
			f"aux lidar count mismatch: {len(aux_topics)} topics vs {len(ref_poses)} ref lines"
		)

	pointclouds: Dict[str, Any] = {
		normalize_topic(PRIMARY_LIDAR_TOPIC): {
			"sensor2base": rslidar2base.tolist(),
		}
	}

	for topic, pose in zip(aux_topics, ref_poses):
		key = normalize_topic(str(topic))
		sensor2rslidar = {
			"tx": pose["tx"],
			"ty": pose["ty"],
			"tz": pose["tz"],
			"qw": pose["qw"],
			"qx": pose["qx"],
			"qy": pose["qy"],
			"qz": pose["qz"],
		}
		transform_sensor2rslidar = pose_to_matrix(**sensor2rslidar)
		transform_sensor2base = rslidar2base @ transform_sensor2rslidar
		pointclouds[key] = {
			"sensor2base": transform_sensor2base.tolist(),
			"sensor2rslidar": sensor2rslidar,
		}

	return pointclouds


def parse_vehicle_dir(vehicle_dir: Path) -> Dict[str, Any]:
	camera_dir = vehicle_dir / "camera"
	freespace_path = vehicle_dir / "freespace" / "freespace.yaml"

	if not vehicle_dir.is_dir():
		raise NotADirectoryError(f"vehicle dir not found: {vehicle_dir}")
	if not freespace_path.exists():
		raise FileNotFoundError(f"freespace yaml not found: {freespace_path}")

	images = parse_camera_dir(camera_dir)

	config = load_freespace_config(freespace_path)
	lidar_cfg = config.get("lidar")
	if not isinstance(lidar_cfg, dict):
		raise ValueError("missing 'lidar' section")

	configured_ref = lidar_cfg.get("aux_lidar_calib_file")
	if not isinstance(configured_ref, str) or not configured_ref.strip():
		raise ValueError("lidar.aux_lidar_calib_file must be a non-empty string")

	lidar_ref_path = resolve_aux_lidar_ref_path(freespace_path, configured_ref)
	pointclouds = build_pointclouds_subitem(config, lidar_ref_path)

	return {
		"images": images,
		"pointclouds": pointclouds,
	}


def resolve_output_path(vehicle_dir: Path, output_path: Optional[Path]) -> Path:
	if output_path is not None:
		return output_path.expanduser()
	return vehicle_dir / "cali.json"


def build_arg_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Generate calibration json from a vehicle config directory."
	)
	parser.add_argument("vehicle_dir", type=Path, help="vehicle config directory, e.g. SA1")
	parser.add_argument("-o", "--output", type=Path, default=None, help="output json path")
	parser.add_argument("--indent", type=int, default=2, help="json indent")
	return parser


def main() -> None:
	args = build_arg_parser().parse_args()
	vehicle_dir = args.vehicle_dir.expanduser()
	output_path = resolve_output_path(vehicle_dir, args.output)

	result = parse_vehicle_dir(vehicle_dir)

	output_path.parent.mkdir(parents=True, exist_ok=True)
	with output_path.open("w", encoding="utf-8") as f:
		json.dump(result, f, ensure_ascii=False, indent=args.indent)
		f.write("\n")

	print(f"saved json: {output_path}")
	print(f"vehicle dir: {vehicle_dir}")
	print(f"camera count: {len(result['images'])}")
	print(f"pointcloud count: {len(result['pointclouds'])}")


if __name__ == "__main__":
	main()
