import numpy as np

from data_collect_dag.models import NodeConfig, PointCloudFrame
from data_collect_dag.nodes.common import AggregatePointCloudNode
from data_collect_dag.sample_context import SampleContext
from tests.unit.conftest import make_meta


def test_aggregate_pointclouds_node(dummy_session):
    sample = SampleContext(sample_id="s")
    cloud_a = PointCloudFrame(make_meta("top_lidar", "pointcloud", 1000), np.array([[1.0, 0.0, 0.0]]))
    cloud_b = PointCloudFrame(make_meta("front_left_lidar", "pointcloud", 1000), np.array([[2.0, 0.0, 0.0]]))
    sample.put("top_lidar_ready", cloud_a, "a")
    sample.put("front_left_ready", cloud_b, "b")
    node = AggregatePointCloudNode(
        NodeConfig("agg", "aggregate_pointclouds", {}, {"pointcloud": "aggregated_pointcloud"}, {"required_inputs": ["top_lidar_ready"], "optional_inputs": ["front_left_ready"]}),
        dummy_session,
    )
    result = node.run(sample)
    assert result.status.value == "OK"
    assert sample.get("aggregated_pointcloud", producer="agg").points_xyz.shape[0] == 2

