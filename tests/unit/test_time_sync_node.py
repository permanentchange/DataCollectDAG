import numpy as np

from data_collect_dag.models import ImageFrame, NodeConfig, NodeResult
from data_collect_dag.nodes.common import TimeSyncNode
from data_collect_dag.sample_context import SampleContext
from tests.unit.conftest import make_meta


def test_time_sync_node_matches_nearest(dummy_session):
    ref = ImageFrame(meta=make_meta("front_wide_camera", "image", 1_000_000_000), image_bgr=np.zeros((2, 2, 3), dtype=np.uint8), encoding="bgr8", width=2, height=2)
    cloud = ImageFrame(meta=make_meta("top_lidar", "pointcloud", 1_000_010_000), image_bgr=np.zeros((2, 2, 3), dtype=np.uint8), encoding="bgr8", width=2, height=2)
    dummy_session.cache.append("top_lidar", cloud)
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", ref, "main_source")
    node = TimeSyncNode(
        NodeConfig("sync", "time_sync", {"reference_frame": "main_frame"}, {"matched_frame": "matched"}, {"source": "top_lidar", "strategy": "nearest", "required": True, "max_time_diff_ms": 50, "wait_timeout_ms": 0}),
        dummy_session,
    )
    result = node.run(sample)
    assert result.status == NodeResult.OK
    assert sample.get("matched", producer="sync") is cloud


def test_time_sync_node_required_missing_skips(dummy_session, image_frame):
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main_source")
    node = TimeSyncNode(
        NodeConfig("sync", "time_sync", {"reference_frame": "main_frame"}, {"matched_frame": "matched"}, {"source": "top_lidar", "strategy": "nearest", "required": True, "max_time_diff_ms": 1, "wait_timeout_ms": 0}),
        dummy_session,
    )
    assert node.run(sample).status == NodeResult.SKIP_SAMPLE

