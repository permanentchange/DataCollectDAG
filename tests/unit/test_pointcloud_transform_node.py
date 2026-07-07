import numpy as np

from data_collect_dag.models import NodeConfig, NodeResult
from data_collect_dag.nodes.common import PointCloudTransformNode
from data_collect_dag.sample_context import SampleContext


def test_pointcloud_transform_node(dummy_session, pointcloud_frame):
    session = dummy_session
    session.calibration["pointclouds"]["/rslidar_points"] = [[1, 0, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    sample = SampleContext(sample_id="s")
    sample.put("top_lidar_compensated", pointcloud_frame, "mc")
    node = PointCloudTransformNode(
        NodeConfig("tf", "pointcloud_transform", {"pointcloud": "top_lidar_compensated"}, {"pointcloud": "top_lidar_base"}, {"sensor_topic": "/rslidar_points", "required": True}),
        session,
    )
    node.setup()
    result = node.run(sample)
    assert result.status == NodeResult.OK
    transformed = sample.get("top_lidar_base", producer="tf")
    np.testing.assert_allclose(transformed.points_xyz[0], np.array([2.0, 0.0, 0.0]))

