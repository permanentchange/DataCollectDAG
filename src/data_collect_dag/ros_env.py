from __future__ import annotations

import sys
from pathlib import Path


ROS_PYTHON_PATH = Path("/opt/ros/noetic/lib/python3/dist-packages")
STANDARD_MSG_ROOT = Path("/opt/ros/noetic/share")


class MessageDefinitionNotFoundError(FileNotFoundError):
    pass


def ensure_ros_python_path() -> None:
    ros_python = str(ROS_PYTHON_PATH)
    if ros_python not in sys.path and ROS_PYTHON_PATH.exists():
        sys.path.append(ros_python)


def resolve_message_definition_path(msg_type: str) -> Path:
    package, name = msg_type.split("/", 1)
    default_path = STANDARD_MSG_ROOT / package / "msg" / f"{name}.msg"
    if default_path.exists():
        return default_path
    package_root = _resolve_ros_package_path(package, msg_type)
    msg_path = package_root / "msg" / f"{name}.msg"
    if msg_path.exists():
        return msg_path
    raise MessageDefinitionNotFoundError(
        f"message definition not found for {msg_type}: ROS package '{package}' was found at {package_root}, "
        f"but message file was missing: {msg_path}. Rebuild/install the package and source its setup.bash before running data_collect_dag."
    )


def _resolve_ros_package_path(package: str, msg_type: str) -> Path:
    try:
        import rospkg
    except Exception as exc:
        raise MessageDefinitionNotFoundError(
            f"message definition not found for {msg_type}: rospkg is unavailable ({exc}). "
            "Install runtime dependencies and source the ROS environment before running data_collect_dag."
        ) from exc
    try:
        return Path(rospkg.RosPack().get_path(package))
    except rospkg.ResourceNotFound as exc:
        raise MessageDefinitionNotFoundError(
            f"message definition not found for {msg_type}: ROS package '{package}' was not found. "
            "Install/build the package and source its setup.bash before running data_collect_dag."
        ) from exc
