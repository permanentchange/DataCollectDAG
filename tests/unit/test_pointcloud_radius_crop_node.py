from data_collect_dag.models import NodeConfig, NodeResult
from data_collect_dag.nodes.common import PointCloudRadiusCropNode
from data_collect_dag.sample_context import SampleContext


def test_pointcloud_radius_crop_node(dummy_session, pointcloud_frame):
    sample = SampleContext(sample_id="s")
    sample.put("top_lidar_synced", pointcloud_frame, "sync")
    node = PointCloudRadiusCropNode(
        NodeConfig("crop", "pointcloud_radius_crop", {"pointcloud": "top_lidar_synced"}, {"pointcloud": "top_lidar_cropped"}, {"max_radius_m": 30, "required": True}),
        dummy_session,
    )
    result = node.run(sample)
    assert result.status == NodeResult.OK
    assert sample.get("top_lidar_cropped", producer="crop").points_xyz.shape[0] == 2

