from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_collect_dag.cache import SessionInputCache
from data_collect_dag.metrics import MetricsRecorder
from data_collect_dag.models import AppConfig, CachePolicy, ControlConfig, FrameMeta, ImageFrame, PointCloudFrame, RuntimeConfig, TopicConfig


def make_meta(topic_key: str, role: str, source_ns: int) -> FrameMeta:
    return FrameMeta(
        topic_key=topic_key,
        ros_topic=f"/{topic_key}",
        sensor_name=topic_key,
        role=role,
        source_timestamp_ns=source_ns,
        receive_timestamp_ns=source_ns,
    )


@pytest.fixture
def image_frame():
    image = np.zeros((4, 6, 3), dtype=np.uint8)
    image[:, :, 0] = 10
    return ImageFrame(meta=make_meta("front_wide_camera", "image", 1_000_000_000), image_bgr=image, encoding="bgr8", width=6, height=4)


@pytest.fixture
def pointcloud_frame():
    points = np.array([[1.0, 0.0, 0.0], [5.0, 0.0, 0.0], [40.0, 0.0, 0.0]], dtype=np.float64)
    timestamps = np.array([1_000_000_000, 1_000_010_000, 1_000_020_000], dtype=np.int64)
    return PointCloudFrame(meta=make_meta("top_lidar", "pointcloud", 1_000_000_000), points_xyz=points, point_timestamps_ns=timestamps, frame_name="top_lidar")


@pytest.fixture
def dummy_session(tmp_path):
    topics = {
        "front_wide_camera": TopicConfig("front_wide_camera", "/miivii_gmsl_camera_node/miivii_gmsl/image2", "sensor_msgs/Image", "image", "front_wide_camera"),
        "front_fisheye_camera": TopicConfig("front_fisheye_camera", "/miivii_gmsl_camera_node/miivii_gmsl/image4", "sensor_msgs/Image", "image", "front_fisheye_camera"),
        "rear_fisheye_camera": TopicConfig("rear_fisheye_camera", "/miivii_gmsl_camera_node/miivii_gmsl/image5", "sensor_msgs/Image", "image", "rear_fisheye_camera"),
        "left_fisheye_camera": TopicConfig("left_fisheye_camera", "/miivii_gmsl_camera_node/miivii_gmsl/image6", "sensor_msgs/Image", "image", "left_fisheye_camera"),
        "right_fisheye_camera": TopicConfig("right_fisheye_camera", "/miivii_gmsl_camera_node/miivii_gmsl/image7", "sensor_msgs/Image", "image", "right_fisheye_camera"),
        "top_lidar": TopicConfig("top_lidar", "/rslidar_points", "sensor_msgs/PointCloud2", "pointcloud", "top_lidar"),
        "front_left_lidar": TopicConfig("front_left_lidar", "/livox/lidar_lf", "sensor_msgs/PointCloud2", "pointcloud", "front_left_lidar"),
        "front_right_lidar": TopicConfig("front_right_lidar", "/livox/lidar_rf", "sensor_msgs/PointCloud2", "pointcloud", "front_right_lidar"),
        "rear_left_lidar": TopicConfig("rear_left_lidar", "/livox/lidar_lb", "sensor_msgs/PointCloud2", "pointcloud", "rear_left_lidar"),
        "rear_right_lidar": TopicConfig("rear_right_lidar", "/livox/lidar_rb", "sensor_msgs/PointCloud2", "pointcloud", "rear_right_lidar"),
        "localization": TopicConfig("localization", "/localization", "igv_msgs/location", "localization", "localization"),
    }
    session = SimpleNamespace()
    session.cache = SessionInputCache({key: CachePolicy(20, 1.0) for key in topics})
    session.metrics = MetricsRecorder()
    session.app_config = AppConfig(
        config_path=ROOT / "demo" / "xtreme1_demo.yaml",
        output_root_dir=tmp_path,
        ros_node_name="data_collect_dag",
        control=ControlConfig(status_topic="/data_collect/status"),
        topics=topics,
        cache_policies={key: CachePolicy(20, 1.0) for key in topics},
        runtime=RuntimeConfig(),
        pipelines={},
        calibration_path=ROOT / "demo" / "calibration" / "sa1_cali.json",
    )
    session.session_root = tmp_path / "session"
    session.session_root.mkdir()
    session.calibration = {
        "images": {
            "/miivii_gmsl_camera_node/miivii_gmsl/image2": {
                "camera_matrix": [[1000.0, 0.0, 960.0], [0.0, 1000.0, 540.0], [0.0, 0.0, 1.0]],
                "resolution": [1920, 1536],
                "base2camera": np.eye(4, dtype=np.float64).tolist(),
            },
            "/miivii_gmsl_camera_node/miivii_gmsl/image4": {
                "camera_matrix": [[500.0, 0.0, 960.0], [0.0, 500.0, 540.0], [0.0, 0.0, 1.0]],
                "resolution": [1920, 1080],
                "base2camera": np.eye(4, dtype=np.float64).tolist(),
            },
            "/miivii_gmsl_camera_node/miivii_gmsl/image5": {
                "camera_matrix": [[500.0, 0.0, 960.0], [0.0, 500.0, 540.0], [0.0, 0.0, 1.0]],
                "resolution": [1920, 1080],
                "base2camera": np.eye(4, dtype=np.float64).tolist(),
            },
            "/miivii_gmsl_camera_node/miivii_gmsl/image6": {
                "camera_matrix": [[500.0, 0.0, 960.0], [0.0, 500.0, 540.0], [0.0, 0.0, 1.0]],
                "resolution": [1920, 1080],
                "base2camera": np.eye(4, dtype=np.float64).tolist(),
            },
            "/miivii_gmsl_camera_node/miivii_gmsl/image7": {
                "camera_matrix": [[500.0, 0.0, 960.0], [0.0, 500.0, 540.0], [0.0, 0.0, 1.0]],
                "resolution": [1920, 1080],
                "base2camera": np.eye(4, dtype=np.float64).tolist(),
            },
        },
        "pointclouds": {
            "/rslidar_points": np.eye(4, dtype=np.float64).tolist(),
            "/livox/lidar_lf": np.eye(4, dtype=np.float64).tolist(),
            "/livox/lidar_rf": np.eye(4, dtype=np.float64).tolist(),
            "/livox/lidar_lb": np.eye(4, dtype=np.float64).tolist(),
            "/livox/lidar_rb": np.eye(4, dtype=np.float64).tolist(),
        },
    }
    return session
