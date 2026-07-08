from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from data_collect_dag.models import (
    FrameMeta,
    ImageFrame,
    ImuFrame,
    LocalizationFrame,
    OdometryFrame,
    PointCloudFrame,
    TextFrame,
)
from data_collect_dag.ros_env import ensure_ros_python_path, resolve_message_definition_path
from data_collect_dag.transforms import normalize_quaternion_xyzw


POINT_FIELD_DTYPES = {
    1: np.int8,
    2: np.uint8,
    3: np.int16,
    4: np.uint16,
    5: np.int32,
    6: np.uint32,
    7: np.float32,
    8: np.float64,
}

BUILTIN_TYPES = {
    "bool",
    "byte",
    "char",
    "int8",
    "uint8",
    "int16",
    "uint16",
    "int32",
    "uint32",
    "int64",
    "uint64",
    "float32",
    "float64",
    "string",
    "time",
    "duration",
}

_MESSAGE_CACHE: Dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def import_rospy():
    ensure_ros_python_path()
    import rospy  # type: ignore

    return rospy


def import_std_msgs_string():
    ensure_ros_python_path()
    from std_msgs.msg import String  # type: ignore

    return String


def import_std_srvs_trigger():
    ensure_ros_python_path()
    from std_srvs.srv import Trigger, TriggerResponse  # type: ignore

    return Trigger, TriggerResponse


def resolve_message_class(msg_type: str):
    with _CACHE_LOCK:
        if msg_type in _MESSAGE_CACHE:
            return _MESSAGE_CACHE[msg_type]
    ensure_ros_python_path()
    try:
        import roslib.message  # type: ignore

        msg_cls = roslib.message.get_message_class(msg_type)
    except Exception:
        msg_cls = None
    if msg_cls is None:
        msg_cls = _generate_dynamic_message_class(msg_type)
    with _CACHE_LOCK:
        _MESSAGE_CACHE[msg_type] = msg_cls
    return msg_cls


def deserialize_message(msg_type: str, serialized: bytes):
    message_class = resolve_message_class(msg_type)
    msg = message_class()
    msg.deserialize(serialized)
    return msg


def _generate_dynamic_message_class(msg_type: str):
    ensure_ros_python_path()
    import genpy.dynamic  # type: ignore

    msg_cat = _build_message_cat(msg_type)
    generated = genpy.dynamic.generate_dynamic(msg_type, msg_cat)
    return generated[msg_type]


def _build_message_cat(msg_type: str) -> str:
    seen: Set[str] = set()
    ordered: List[str] = []

    def visit(current_type: str) -> None:
        if current_type in seen:
            return
        seen.add(current_type)
        for dependency in _direct_dependencies(current_type):
            visit(dependency)
        ordered.append(current_type)

    visit(msg_type)
    core_text = _read_msg_text(msg_type)
    dep_sections = []
    for dep in ordered:
        if dep == msg_type:
            continue
        dep_sections.append("=" * 80)
        dep_sections.append(f"MSG: {dep}")
        dep_sections.append(_read_msg_text(dep).rstrip())
    if not dep_sections:
        return core_text
    return core_text.rstrip() + "\n" + "\n".join(dep_sections) + "\n"


def _direct_dependencies(msg_type: str) -> List[str]:
    dependencies: List[str] = []
    package, _name = msg_type.split("/", 1)
    text = _read_msg_text(msg_type)
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" in line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        field_type = _normalize_field_type(parts[0], package)
        if field_type is None:
            continue
        dependencies.append(field_type)
    return dependencies


def _normalize_field_type(field_type: str, current_package: str) -> Optional[str]:
    base = re.sub(r"\[[^\]]*\]$", "", field_type)
    if base in BUILTIN_TYPES:
        return None
    if "/" not in base:
        if base == "Header":
            return "std_msgs/Header"
        return f"{current_package}/{base}"
    return base


def _read_msg_text(msg_type: str) -> str:
    path = resolve_message_definition_path(msg_type)
    return path.read_text(encoding="utf-8")


def require_positive_stamp_ns(msg: Any) -> int:
    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    if stamp is None:
        raise ValueError("message missing header.stamp")
    value = int(stamp.to_nsec())
    if value <= 0:
        raise ValueError("message header.stamp must be positive")
    return value


def resolve_localization_time_ns(msg: Any) -> int:
    raw_value = getattr(msg, "timestamp", None)
    if raw_value is None:
        return require_positive_stamp_ns(msg)
    value = int(raw_value)
    if value >= 10**17:
        return value
    if value >= 10**14:
        return value * 1_000
    if value >= 10**11:
        return value * 1_000_000
    if value >= 10**8:
        return value * 1_000_000_000
    return require_positive_stamp_ns(msg)


def frame_from_ros_message(topic_key: str, ros_topic: str, role: str, sensor_name: str, msg: Any) -> Any:
    receive_ns = time.time_ns()
    if role == "image":
        source_ns = require_positive_stamp_ns(msg)
        meta = FrameMeta(topic_key, ros_topic, sensor_name, role, source_ns, receive_ns, getattr(msg.header, "frame_id", ""))
        return ImageFrame(
            meta=meta,
            image_bgr=None,
            encoding=str(msg.encoding),
            width=int(msg.width),
            height=int(msg.height),
            raw_msg_ref=msg,
        )
    if role == "pointcloud":
        source_ns = require_positive_stamp_ns(msg)
        meta = FrameMeta(topic_key, ros_topic, sensor_name, role, source_ns, receive_ns, getattr(msg.header, "frame_id", ""))
        return PointCloudFrame(meta=meta, points_xyz=None, point_timestamps_ns=None, frame_name=topic_key, raw_msg_ref=msg)
    if role == "imu":
        source_ns = require_positive_stamp_ns(msg)
        meta = FrameMeta(topic_key, ros_topic, sensor_name, role, source_ns, receive_ns, getattr(msg.header, "frame_id", ""))
        return ImuFrame(
            meta=meta,
            linear_acceleration_xyz=np.array([msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z], dtype=np.float64),
            angular_velocity_xyz=np.array([msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z], dtype=np.float64),
            quaternion_xyzw=np.array([msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w], dtype=np.float64),
            raw_msg_ref=msg,
        )
    if role == "odometry":
        source_ns = require_positive_stamp_ns(msg)
        meta = FrameMeta(topic_key, ros_topic, sensor_name, role, source_ns, receive_ns, getattr(msg.header, "frame_id", ""))
        return OdometryFrame(
            meta=meta,
            position_xyz=np.array([msg.pose.pose.position.x, msg.pose.pose.position.y, msg.pose.pose.position.z], dtype=np.float64),
            quaternion_xyzw=np.array(
                [msg.pose.pose.orientation.x, msg.pose.pose.orientation.y, msg.pose.pose.orientation.z, msg.pose.pose.orientation.w],
                dtype=np.float64,
            ),
            linear_velocity_xyz=np.array([msg.twist.twist.linear.x, msg.twist.twist.linear.y, msg.twist.twist.linear.z], dtype=np.float64),
            angular_velocity_xyz=np.array([msg.twist.twist.angular.x, msg.twist.twist.angular.y, msg.twist.twist.angular.z], dtype=np.float64),
            raw_msg_ref=msg,
        )
    if role == "localization":
        source_ns = resolve_localization_time_ns(msg)
        header_frame = getattr(getattr(msg, "header", None), "frame_id", "")
        meta = FrameMeta(topic_key, ros_topic, sensor_name, role, source_ns, receive_ns, header_frame)
        return LocalizationFrame(
            meta=meta,
            position_xyz=np.array([msg.car_pose.position.x, msg.car_pose.position.y, msg.car_pose.position.z], dtype=np.float64),
            quaternion_xyzw=normalize_quaternion_xyzw(
                np.array([msg.car_pose.orientation.x, msg.car_pose.orientation.y, msg.car_pose.orientation.z, msg.car_pose.orientation.w], dtype=np.float64)
            ),
            linear_velocity_xyz=np.array([msg.vel.x, msg.vel.y, msg.vel.z], dtype=np.float64),
            angular_velocity_xyz=np.array([msg.gyr.x, msg.gyr.y, msg.gyr.z], dtype=np.float64),
            status=int(getattr(msg, "status", 0)),
            state=str(getattr(msg, "state", "")),
            raw_msg_ref=msg,
        )
    if role == "nmea":
        source_ns = require_positive_stamp_ns(msg)
        meta = FrameMeta(topic_key, ros_topic, sensor_name, role, source_ns, receive_ns, getattr(msg.header, "frame_id", ""))
        text = getattr(msg, "sentence", None)
        if text is None:
            text = getattr(msg, "data", "")
        return TextFrame(meta=meta, text=str(text), raw_msg_ref=msg)
    raise ValueError(f"unsupported topic role: {role}")


def _parse_ros_image_message(msg: Any) -> np.ndarray:
    import cv2

    raw = np.frombuffer(msg.data, dtype=np.uint8)
    if msg.encoding == "bgr8":
        return raw.reshape(msg.height, msg.width, 3).copy()
    if msg.encoding == "rgb8":
        return cv2.cvtColor(raw.reshape(msg.height, msg.width, 3), cv2.COLOR_RGB2BGR)
    if msg.encoding == "bgra8":
        return cv2.cvtColor(raw.reshape(msg.height, msg.width, 4), cv2.COLOR_BGRA2BGR)
    if msg.encoding == "rgba8":
        return cv2.cvtColor(raw.reshape(msg.height, msg.width, 4), cv2.COLOR_RGBA2BGR)
    if msg.encoding == "mono8":
        return cv2.cvtColor(raw.reshape(msg.height, msg.width), cv2.COLOR_GRAY2BGR)
    if msg.encoding == "bayer_rggb8":
        return cv2.cvtColor(raw.reshape(msg.height, msg.width), cv2.COLOR_BayerBG2BGR)
    raise ValueError(f"unsupported image encoding: {msg.encoding}")


def _parse_pointcloud_message(msg: Any) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    dtype = _build_structured_dtype(msg.fields, int(msg.point_step))
    count = int(msg.width) * int(msg.height)
    cloud = np.ndarray(shape=(count,), dtype=dtype, buffer=memoryview(msg.data))
    points_xyz = np.stack(
        [
            np.asarray(cloud["x"], dtype=np.float64),
            np.asarray(cloud["y"], dtype=np.float64),
            np.asarray(cloud["z"], dtype=np.float64),
        ],
        axis=1,
    )
    timestamp_ns = None
    if "timestamp" in (cloud.dtype.names or ()):
        raw_ts = np.asarray(cloud["timestamp"]).reshape(-1).astype(np.float64, copy=False)
        if raw_ts.size > 0 and np.isfinite(raw_ts).any():
            median = float(np.nanmedian(np.abs(raw_ts[np.isfinite(raw_ts)])))
            converted = np.zeros(raw_ts.shape[0], dtype=np.int64)
            finite = np.isfinite(raw_ts)
            if 1e15 <= median <= 1e20:
                converted[finite] = np.rint(raw_ts[finite]).astype(np.int64)
                timestamp_ns = converted
            elif 1e8 <= median <= 1e11:
                converted[finite] = np.rint(raw_ts[finite] * 1_000_000_000.0).astype(np.int64)
                timestamp_ns = converted
    return points_xyz, timestamp_ns


def ensure_image_frame_decoded(frame: ImageFrame) -> ImageFrame:
    if frame.image_bgr is not None:
        return frame
    if frame.raw_msg_ref is None:
        raise ValueError("image frame missing raw ROS message")
    return frame.replace(image_bgr=_parse_ros_image_message(frame.raw_msg_ref))


def ensure_pointcloud_frame_parsed(frame: PointCloudFrame) -> PointCloudFrame:
    if frame.points_xyz is not None:
        return frame
    if frame.raw_msg_ref is None:
        raise ValueError("pointcloud frame missing raw ROS message")
    points_xyz, point_timestamps_ns = _parse_pointcloud_message(frame.raw_msg_ref)
    return frame.replace(points_xyz=points_xyz, point_timestamps_ns=point_timestamps_ns)


def _build_structured_dtype(fields: Iterable[Any], point_step: int) -> np.dtype:
    names = []
    formats = []
    offsets = []
    for field in fields:
        names.append(field.name)
        field_dtype = POINT_FIELD_DTYPES[int(field.datatype)]
        if int(field.count) == 1:
            formats.append(field_dtype)
        else:
            formats.append((field_dtype, (int(field.count),)))
        offsets.append(int(field.offset))
    return np.dtype({"names": names, "formats": formats, "offsets": offsets, "itemsize": int(point_step)})
