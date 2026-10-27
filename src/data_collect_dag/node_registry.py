"""Shared node names and lazy loaders; importing this module loads no runtime."""
from __future__ import annotations

from importlib import import_module
from typing import Dict, Tuple, Type


NODE_TYPES: Dict[str, Tuple[str, str]] = {
    "start": ("common", "StartNode"),
    "end": ("common", "EndNode"),
    "fps_guard": ("common", "FpsGuardNode"),
    "speed_gate": ("common", "SpeedGateNode"),
    "fps_marker": ("common", "FpsMarkerNode"),
    "image_undistort": ("image_undistort", "ImageUndistortNode"),
    "time_sync": ("common", "TimeSyncNode"),
    "multi_time_sync": ("common", "MultiTimeSyncNode"),
    "localization_range": ("common", "LocalizationRangeNode"),
    "pointcloud_radius_crop": ("common", "PointCloudRadiusCropNode"),
    "pointcloud_motion_compensation": ("common", "PointCloudMotionCompensationNode"),
    "pointcloud_transform": ("common", "PointCloudTransformNode"),
    "pointcloud_roi_crop": ("common", "PointCloudRoiCropNode"),
    "aggregate_pointclouds": ("common", "AggregatePointCloudNode"),
    "yolo_gate": ("vision", "YoloGateNode"),
    "xtreme1_structured_save": ("xtreme1", "Xtreme1SaveNode"),
}


def load_node_type(name: str) -> Type:
    module, class_name = NODE_TYPES[name]
    return getattr(import_module(f"data_collect_dag.nodes.{module}"), class_name)
