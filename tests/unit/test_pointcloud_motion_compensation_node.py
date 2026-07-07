import numpy as np

from data_collect_dag.models import LocalizationFrame, NodeConfig, NodeResult
from data_collect_dag.nodes.common import PointCloudMotionCompensationNode
from data_collect_dag.sample_context import SampleContext
from tests.unit.conftest import make_meta


def test_motion_compensation_node_passes_with_pose_coverage(dummy_session, pointcloud_frame, image_frame):
    loc0 = LocalizationFrame(make_meta("localization", "localization", 999_999_000), np.zeros(3), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(3), np.zeros(3))
    loc1 = LocalizationFrame(make_meta("localization", "localization", 1_000_100_000), np.zeros(3), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(3), np.zeros(3))
    dummy_session.cache.append("localization", loc0)
    dummy_session.cache.append("localization", loc1)
    sample = SampleContext(sample_id="s")
    sample.put("top_lidar_radius", pointcloud_frame, "crop")
    sample.put("main_frame", image_frame, "main_source")
    node = PointCloudMotionCompensationNode(
        NodeConfig("mc", "pointcloud_motion_compensation", {"pointcloud": "top_lidar_radius", "reference_frame": "main_frame"}, {"pointcloud": "top_lidar_comp"}, {"sensor_topic": "/rslidar_points", "localization_source": "localization", "required": True}),
        dummy_session,
    )
    node.setup()
    result = node.run(sample)
    assert result.status == NodeResult.OK
    np.testing.assert_allclose(sample.get("top_lidar_comp", producer="mc").points_xyz, pointcloud_frame.points_xyz)


def test_motion_compensation_node_missing_timestamps_skips(dummy_session, pointcloud_frame, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("top_lidar_radius", pointcloud_frame.replace(point_timestamps_ns=None), "crop")
    sample.put("main_frame", image_frame, "main_source")
    node = PointCloudMotionCompensationNode(
        NodeConfig("mc", "pointcloud_motion_compensation", {"pointcloud": "top_lidar_radius", "reference_frame": "main_frame"}, {"pointcloud": "top_lidar_comp"}, {"sensor_topic": "/rslidar_points", "localization_source": "localization", "required": True}),
        dummy_session,
    )
    node.setup()
    assert node.run(sample).status == NodeResult.SKIP_SAMPLE

