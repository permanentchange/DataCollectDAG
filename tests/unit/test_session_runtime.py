from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

from data_collect_dag.control import ControlCommand
from data_collect_dag.models import AppConfig, CachePolicy, ControlConfig, MainFrameEvent, PipelineDefinition, RuntimeConfig, StopConditions, TopicConfig
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import SessionRuntime
from data_collect_dag.status import StatusManager
from tests.unit.conftest import make_meta


class _Frame:
    def __init__(self, timestamp_ns: int) -> None:
        self.meta = make_meta("front_wide_camera", "image", timestamp_ns)


class _CommandCollector:
    def __init__(self) -> None:
        self.items = []
        self._lock = threading.Lock()

    def __call__(self, command: ControlCommand) -> None:
        with self._lock:
            self.items.append(command)


def _make_session(
    tmp_path: Path,
    stop_conditions: Optional[StopConditions] = None,
    precheck_topics=None,
    time_delay_ms: Optional[int] = None,
):
    topics = {
        "front_wide_camera": TopicConfig("front_wide_camera", "/front", "sensor_msgs/Image", "image", "front_wide_camera"),
        "top_lidar": TopicConfig("top_lidar", "/lidar", "sensor_msgs/PointCloud2", "pointcloud", "top_lidar"),
    }
    app_config = AppConfig(
        config_path=tmp_path / "config.yaml",
        output_root_dir=tmp_path,
        ros_node_name="data_collect_dag",
        control=ControlConfig(),
        topics=topics,
        cache_policies={topic_key: CachePolicy(10, 1.0) for topic_key in topics},
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
        stop_conditions=stop_conditions or StopConditions(),
        topics=list(precheck_topics or []),
        time_delay_ms=time_delay_ms,
    )
    collector = _CommandCollector()
    session = SessionRuntime(
        app_config,
        pipeline,
        calibration={},
        ros_adapter=RosAdapter(topics, "data_collect_dag", ControlConfig()),
        status_manager=StatusManager(),
        command_callback=collector,
    )
    return session, collector


def test_topic_precheck_failure_stops_session_setup_before_building_nodes(tmp_path):
    session, _collector = _make_session(
        tmp_path,
        precheck_topics=["top_lidar", "front_wide_camera"],
        time_delay_ms=25,
    )

    def wait_for_topic_messages(topic_keys, timeout_ms):
        assert topic_keys == ["top_lidar", "front_wide_camera"]
        assert timeout_ms == 25
        return ["top_lidar", "front_wide_camera"]

    session.ros_adapter.wait_for_topic_messages = wait_for_topic_messages
    build_called = []
    session._build_nodes = lambda: build_called.append(True)
    expected_error = "topic precheck timed out after 25 ms; missing topics: front_wide_camera, top_lidar"

    with pytest.raises(RuntimeError, match="topic precheck timed out") as exc_info:
        session.start()

    assert str(exc_info.value) == expected_error
    assert build_called == []
    snapshot = session.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    assert snapshot.last_error == expected_error
    summary = json.loads((session.session_root / "session_summary.json").read_text(encoding="utf-8"))
    assert summary["end_status"] == "FAILED"
    assert summary["end_reason"] == "setup_failed"
    assert summary["last_error"] == expected_error


def test_topic_precheck_success_continues_normal_session_start(tmp_path):
    session, _collector = _make_session(
        tmp_path,
        precheck_topics=["front_wide_camera", "top_lidar"],
        time_delay_ms=100,
    )
    wait_calls = []

    def wait_for_topic_messages(topic_keys, timeout_ms):
        wait_calls.append((list(topic_keys), timeout_ms))
        return []

    session.ros_adapter.wait_for_topic_messages = wait_for_topic_messages

    session.start()
    try:
        assert wait_calls == [(["front_wide_camera", "top_lidar"], 100)]
        assert session.status_manager.snapshot().tool_state.value == "RUNNING"
    finally:
        session.stop()


def test_enqueue_main_frame_event_drops_oldest(tmp_path):
    session, _collector = _make_session(tmp_path)
    session._enqueue_main_frame_event(MainFrameEvent(main_frame=_Frame(1)))
    session._enqueue_main_frame_event(MainFrameEvent(main_frame=_Frame(2)))
    session._enqueue_main_frame_event(MainFrameEvent(main_frame=_Frame(3)))
    queued = [session.main_frame_queue.get_nowait().main_frame.meta.source_timestamp_ns for _ in range(session.main_frame_queue.qsize())]
    assert queued == [2, 3]
    assert session.metrics.metrics.main_frame_events == 3
    assert session.metrics.metrics.main_frame_events_dropped == 1
    assert session.metrics.metrics.drop_reasons["main_frame_queue_drop_oldest"] == 1


def test_pause_resume_controls_frame_acceptance(tmp_path):
    session, _collector = _make_session(tmp_path)
    session._active = True
    session._accepting_frames = True
    session._run_started_monotonic = time.monotonic()
    session.pause()
    session.accept_frame("front_wide_camera", _Frame(1))
    assert session.metrics.metrics.received_messages["front_wide_camera"] == 0
    assert session.status_manager.snapshot().tool_state.value == "PAUSED"
    session.resume()
    session.accept_frame("front_wide_camera", _Frame(2))
    assert session.metrics.metrics.received_messages["front_wide_camera"] == 1
    assert session.status_manager.snapshot().tool_state.value == "RUNNING"


def test_max_saved_samples_requests_completion(tmp_path):
    session, collector = _make_session(tmp_path, StopConditions(max_saved_samples=2))
    session.metrics.metrics.samples_saved = 1
    session._check_saved_sample_limit()
    assert collector.items == []
    session.metrics.metrics.samples_saved = 2
    session._check_saved_sample_limit()
    assert len(collector.items) == 1
    command = collector.items[0]
    assert command.kind == "complete"
    assert command.session_id == session.session_id
    assert command.reason == "max_saved_samples_reached"
    assert command.end_status == "COMPLETED"


def test_max_duration_requests_completion(tmp_path):
    session, collector = _make_session(tmp_path, StopConditions(max_duration_sec=0.01))
    session._active = True
    session._accepting_frames = True
    session._run_started_monotonic = time.monotonic() - 0.02
    thread = threading.Thread(target=session._duration_monitor_loop, daemon=True)
    thread.start()
    thread.join(timeout=0.5)
    assert len(collector.items) == 1
    assert collector.items[0].reason == "max_duration_reached"


def test_pause_excludes_time_from_duration_counter(tmp_path):
    session, _collector = _make_session(tmp_path, StopConditions(max_duration_sec=10.0))
    session._active = True
    session._accepting_frames = True
    session._run_started_monotonic = time.monotonic() - 0.02
    session.pause()
    elapsed_when_paused = session._current_active_elapsed_sec()
    time.sleep(0.02)
    elapsed_still_paused = session._current_active_elapsed_sec()
    assert elapsed_still_paused - elapsed_when_paused < 0.01
