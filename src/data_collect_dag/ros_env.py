from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict


ROS_PYTHON_PATH = Path("/opt/ros/noetic/lib/python3/dist-packages")
STANDARD_MSG_ROOT = Path("/opt/ros/noetic/share")
CUSTOM_MSG_ROOTS: Dict[str, Path] = {
    "igv_msgs": Path("/home/xfzhou/workspace/intelligentvehicle/igv_msgs/msg"),
    "bdstar": Path("/home/xfzhou/workspace/intelligentvehicle/embedded/driver/BDStar_NC504/bdstar_ros/msg"),
    "chcnav": Path("/home/xfzhou/workspace/intelligentvehicle/embedded/driver/CHCNAV_CGI-430/chcnav_ros/msg"),
}


def ensure_ros_python_path() -> None:
    ros_python = str(ROS_PYTHON_PATH)
    if ros_python not in sys.path and ROS_PYTHON_PATH.exists():
        sys.path.append(ros_python)

