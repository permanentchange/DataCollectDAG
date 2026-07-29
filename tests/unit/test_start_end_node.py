from types import SimpleNamespace

from data_collect_dag.models import NodeConfig, NodeResult
from data_collect_dag.nodes.common import EndNode, FpsGuardNode, FpsMarkerNode, StartNode
from data_collect_dag.sample_context import SampleContext


class _FpsSession:
    def __init__(self) -> None:
        self.timestamps = {}

    def get_fps_gate_last_timestamp_ns(self, gate_id):
        return self.timestamps.get(gate_id)

    def mark_fps_gate_timestamp_ns(self, gate_id, timestamp_ns):
        previous = self.timestamps.get(gate_id)
        if previous is None or timestamp_ns > previous:
            self.timestamps[gate_id] = timestamp_ns


def _sample(timestamp_ns: int) -> SampleContext:
    sample = SampleContext(sample_id=str(timestamp_ns))
    sample.put("main_frame", SimpleNamespace(meta=SimpleNamespace(source_timestamp_ns=timestamp_ns)), "main_source")
    return sample


def _fps_guard(session, *, gate_id: str = "saved", max_fps: float = 1.0) -> FpsGuardNode:
    guard = FpsGuardNode(
        NodeConfig("fps_control_guard", "fps_guard", {}, {}, {"gate_id": gate_id, "max_fps": max_fps}),
        session,
    )
    guard.setup()
    return guard


def _fps_marker(session, *, node_id: str = "fps_control_marker", gate_id: str = "saved") -> FpsMarkerNode:
    marker = FpsMarkerNode(NodeConfig(node_id, "fps_marker", {}, {}, {"gate_id": gate_id}), session)
    marker.setup()
    return marker


def test_start_end_nodes_return_ok(dummy_session):
    sample = SampleContext(sample_id="s")
    start = StartNode(NodeConfig("start", "start", {}, {}, {}), dummy_session)
    end = EndNode(NodeConfig("end", "end", {}, {}, {}), dummy_session)
    assert start.run(sample).status == NodeResult.OK
    assert end.run(sample).status == NodeResult.OK


def test_fps_guard_allows_samples_before_marker():
    session = _FpsSession()
    guard = _fps_guard(session)

    assert guard.run(_sample(1_000_000_000)).status == NodeResult.OK


def test_fps_guard_skips_inside_marker_window():
    session = _FpsSession()
    guard = _fps_guard(session)
    marker = _fps_marker(session)

    assert marker.run(_sample(1_000_000_000)).status == NodeResult.OK
    outcome = guard.run(_sample(1_500_000_000))

    assert outcome.status == NodeResult.SKIP_SAMPLE
    assert outcome.reason == "fps_control_guard_fps_limited"


def test_fps_guard_allows_samples_after_marker_window():
    session = _FpsSession()
    guard = _fps_guard(session)
    marker = _fps_marker(session)

    marker.run(_sample(1_000_000_000))

    assert guard.run(_sample(2_000_000_000)).status == NodeResult.OK


def test_fps_marker_keeps_latest_timestamp():
    session = _FpsSession()
    marker = _fps_marker(session)

    marker.run(_sample(2_000_000_000))
    marker.run(_sample(1_000_000_000))

    assert session.timestamps["saved"] == 2_000_000_000


def test_fps_gate_ids_are_isolated():
    session = _FpsSession()
    guard = _fps_guard(session)
    other_marker = _fps_marker(session, node_id="other_marker", gate_id="other")

    other_marker.run(_sample(1_000_000_000))

    assert guard.run(_sample(1_500_000_000)).status == NodeResult.OK
