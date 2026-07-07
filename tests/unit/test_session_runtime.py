from __future__ import annotations

from pathlib import Path

from data_collect_dag.models import AppConfig, CachePolicy, ControlConfig, MainFrameEvent, PipelineDefinition, RuntimeConfig, TopicConfig
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import SessionRuntime
from data_collect_dag.status import StatusManager
from tests.unit.conftest import make_meta


class _Frame:
    def __init__(self, timestamp_ns: int) -> None:
        self.meta = make_meta("front_wide_camera", "image", timestamp_ns)


def _make_session(tmp_path: Path) -> SessionRuntime:
    topics = {
        "front_wide_camera": TopicConfig("front_wide_camera", "/front", "sensor_msgs/Image", "image", "front_wide_camera"),
    }
    app_config = AppConfig(
        config_path=tmp_path / "config.yaml",
        output_root_dir=tmp_path,
        ros_node_name="data_collect_dag",
        control=ControlConfig(),
        topics=topics,
        cache_policies={"front_wide_camera": CachePolicy(10, 1.0)},
        runtime=RuntimeConfig(main_frame_queue_size=2),
        pipelines={},
        calibration_path=None,
    )
    pipeline = PipelineDefinition(
        name="test",
        main_source="front_wide_camera",
        nodes_by_id={},
        predecessors={},
        successors={},
        start_node_id="start",
        end_node_id="end",
    )
    return SessionRuntime(app_config, pipeline, calibration={}, ros_adapter=RosAdapter({}, "data_collect_dag", ControlConfig()), status_manager=StatusManager())


def test_enqueue_main_frame_event_drops_oldest(tmp_path):
    session = _make_session(tmp_path)
    session._enqueue_main_frame_event(MainFrameEvent(main_frame=_Frame(1)))
    session._enqueue_main_frame_event(MainFrameEvent(main_frame=_Frame(2)))
    session._enqueue_main_frame_event(MainFrameEvent(main_frame=_Frame(3)))
    queued = [session.main_frame_queue.get_nowait().main_frame.meta.source_timestamp_ns for _ in range(session.main_frame_queue.qsize())]
    assert queued == [2, 3]
    assert session.metrics.metrics.main_frame_events == 3
    assert session.metrics.metrics.main_frame_events_dropped == 1
    assert session.metrics.metrics.drop_reasons["main_frame_queue_drop_oldest"] == 1
