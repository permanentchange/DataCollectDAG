from data_collect_dag.models import NodeConfig, NodeResult
from data_collect_dag.nodes.common import PointCloudRoiCropNode
from data_collect_dag.sample_context import SampleContext


def test_pointcloud_roi_crop_node(dummy_session, pointcloud_frame):
    sample = SampleContext(sample_id="s")
    sample.put("top_lidar_base", pointcloud_frame, "tf")
    node = PointCloudRoiCropNode(
        NodeConfig("roi", "pointcloud_roi_crop", {"pointcloud": "top_lidar_base"}, {"pointcloud": "top_lidar_ready"}, {"front_m": 30, "rear_m": 20, "side_m": 15, "required": True}),
        dummy_session,
    )
    result = node.run(sample)
    assert result.status == NodeResult.OK
    assert sample.get("top_lidar_ready", producer="roi").points_xyz.shape[0] == 2

