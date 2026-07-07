from types import SimpleNamespace

import numpy as np

from data_collect_dag.models import ImageFrame, NodeConfig, NodeResult
from data_collect_dag.nodes import common as common_nodes
from data_collect_dag.nodes.common import MultiTimeSyncNode
from data_collect_dag.sample_context import SampleContext
from tests.unit.conftest import make_meta


def test_multi_time_sync_collects_optional_sources(dummy_session, image_frame):
    matched = ImageFrame(meta=make_meta("front_fisheye_camera", "image", 1_000_000_010), image_bgr=np.zeros((2, 2, 3), dtype=np.uint8), encoding="bgr8", width=2, height=2)
    dummy_session.cache.append("front_fisheye_camera", matched)
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main_source")
    node = MultiTimeSyncNode(
        NodeConfig(
            "sync_multi",
            "multi_time_sync",
            {"reference_frame": "main_frame"},
            {"front_fisheye_camera": "front_fisheye_camera", "rear_fisheye_camera": "rear_fisheye_camera"},
            {"sources": ["front_fisheye_camera", "rear_fisheye_camera"], "strategy": "nearest", "required": False, "max_time_diff_ms": 50, "wait_timeout_ms": 0},
        ),
        dummy_session,
    )
    assert node.run(sample).status == NodeResult.OK
    assert sample.get("front_fisheye_camera", producer="sync_multi") is matched


def test_multi_time_sync_shares_one_wait_budget(monkeypatch, image_frame):
    timeout_calls = []

    class FakeCache:
        def wait_nearest(self, source, timestamp_ns, max_time_diff_ms, timeout_ms):
            timeout_calls.append((source, timeout_ms))
            return None

    fake_session = SimpleNamespace(cache=FakeCache())
    sample = SampleContext(sample_id="s")
    sample.put("main_frame", image_frame, "main_source")
    node = MultiTimeSyncNode(
        NodeConfig(
            "sync_multi",
            "multi_time_sync",
            {"reference_frame": "main_frame"},
            {"front_fisheye_camera": "front_fisheye_camera", "rear_fisheye_camera": "rear_fisheye_camera"},
            {"sources": ["front_fisheye_camera", "rear_fisheye_camera"], "strategy": "nearest", "required": False, "max_time_diff_ms": 50, "wait_timeout_ms": 400},
        ),
        fake_session,
    )
    monotonic_values = iter([10.0, 10.0, 10.15, 10.3])
    monkeypatch.setattr(common_nodes.time, "monotonic", lambda: next(monotonic_values))
    result = node.run(sample)
    assert result.status == NodeResult.OK
    assert timeout_calls == [("front_fisheye_camera", 400), ("rear_fisheye_camera", 250)]
