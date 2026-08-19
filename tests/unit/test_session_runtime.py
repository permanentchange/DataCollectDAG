from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from data_collect_dag.control import ControlCommand
from data_collect_dag.models import AppConfig, CachePolicy, ControlConfig, MainFrameEvent, NodeConfig, PipelineDefinition, RecentSessionStatus, RuntimeConfig, StopConditions, TopicConfig
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import NODE_TYPES, SessionManager, SessionRuntime
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


def test_start_failure_aggregates_cleanup_errors_and_shuts_down_node_pool(tmp_path):
    session, _collector = _make_session(tmp_path)
    teardown_calls = []
    pool_shutdown_calls = []

    class _Node:
        def __init__(self, node_id, error=None):
            self.node_id = node_id
            self.error = error

        def teardown(self):
            teardown_calls.append(self.node_id)
            if self.error is not None:
                raise self.error

    class _NodePool:
        def shutdown(self, wait):
            pool_shutdown_calls.append(wait)

    def fail_build_nodes():
        session._nodes = {
            "first": _Node("first"),
            "second": _Node("second", RuntimeError("teardown boom")),
        }
        session._node_pool = _NodePool()
        raise RuntimeError("setup boom")

    session._build_nodes = fail_build_nodes

    with pytest.raises(RuntimeError, match="setup boom; node_teardown"):
        session.start()

    assert teardown_calls == ["second", "first"]
    assert pool_shutdown_calls == [True]
    snapshot = session.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    assert snapshot.last_error == "setup boom; node_teardown[second]: teardown boom"
    summary = json.loads((session.session_root / "session_summary.json").read_text(encoding="utf-8"))
    assert summary["end_status"] == "FAILED"
    assert summary["end_reason"] == "setup_failed"
    assert summary["last_error"] == snapshot.last_error


def test_node_that_raises_during_setup_is_included_in_start_rollback(tmp_path, monkeypatch):
    session, _collector = _make_session(tmp_path)
    teardown_calls = []

    class _PartialSetupNode:
        def __init__(self, node_config, _session):
            self.node_id = node_config.node_id

        def setup(self):
            raise RuntimeError("partial setup failed")

        def teardown(self):
            teardown_calls.append(self.node_id)

    monkeypatch.setitem(NODE_TYPES, "partial_setup", _PartialSetupNode)
    session.pipeline.nodes_by_id = {
        "partial": NodeConfig(
            node_id="partial",
            node_type="partial_setup",
            inputs={},
            outputs={},
            config={},
        )
    }

    with pytest.raises(RuntimeError, match="partial setup failed"):
        session.start()

    assert teardown_calls == ["partial"]


def test_start_failure_before_log_handler_attachment_reports_idle_failed(tmp_path, monkeypatch):
    session, _collector = _make_session(tmp_path)

    def fail_log_attachment(_path):
        raise OSError("cannot open debug log")

    monkeypatch.setattr("data_collect_dag.session.attach_session_log_file", fail_log_attachment)

    with pytest.raises(OSError, match="cannot open debug log"):
        session.start()

    snapshot = session.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    assert snapshot.last_error == "cannot open debug log"
    summary = json.loads((session.session_root / "session_summary.json").read_text(encoding="utf-8"))
    assert summary["end_status"] == "FAILED"
    assert summary["end_reason"] == "setup_failed"


def test_start_directory_collision_reports_failed_without_overwriting_existing_summary(tmp_path):
    session, _collector = _make_session(tmp_path)
    session.session_root.mkdir()
    existing_summary = session.session_root / "session_summary.json"
    existing_summary.write_text("existing session", encoding="utf-8")

    with pytest.raises(FileExistsError):
        session.start()

    snapshot = session.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    assert "File exists" in snapshot.last_error
    assert existing_summary.read_text(encoding="utf-8") == "existing session"


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


def test_session_manager_clears_active_session_when_stop_fails():
    manager = SessionManager(
        SimpleNamespace(pipelines={}),
        calibration={},
        ros_adapter=object(),
        status_manager=StatusManager(),
        command_callback=lambda command: None,
    )

    class _FailingSession:
        def stop(self, **_kwargs):
            raise RuntimeError("cleanup failed")

    manager.active_session = _FailingSession()

    with pytest.raises(RuntimeError, match="cleanup failed"):
        manager.stop()

    assert manager.active_session is None


def test_replacement_aborts_when_active_session_cleanup_fails(tmp_path):
    session, _collector = _make_session(tmp_path)
    session.app_config.pipelines[session.pipeline.name] = session.pipeline
    session._active = True
    session._accepting_frames = True
    session._run_started_monotonic = time.monotonic()
    session.status_manager.set_running(session.session_id, session.pipeline.name, session._start_time)

    class _FailingTeardownNode:
        def teardown(self):
            raise RuntimeError("replacement cleanup failed")

    session._nodes = {"bad": _FailingTeardownNode()}
    manager = SessionManager(
        session.app_config,
        calibration={},
        ros_adapter=session.ros_adapter,
        status_manager=session.status_manager,
        command_callback=lambda command: None,
    )
    manager.active_session = session

    with pytest.raises(RuntimeError, match="replacement cleanup failed"):
        manager.start(session.pipeline.name)

    assert manager.active_session is None
    snapshot = session.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    summary = json.loads((session.session_root / "session_summary.json").read_text(encoding="utf-8"))
    assert summary["end_status"] == "FAILED"
    assert summary["end_reason"] == "replaced_by_new_start_failed"
    assert [path for path in tmp_path.iterdir() if path.is_dir()] == [session.session_root]


def test_stop_continues_cleanup_and_reports_failed_status_after_teardown_error(tmp_path):
    session, _collector = _make_session(tmp_path)
    session._active = True
    session._accepting_frames = True
    session._run_started_monotonic = time.monotonic()
    session.status_manager.set_running(session.session_id, session.pipeline.name, session._start_time)
    teardown_calls = []

    class _Node:
        def __init__(self, node_id, error=None):
            self.node_id = node_id
            self.error = error

        def teardown(self):
            teardown_calls.append(self.node_id)
            if self.error is not None:
                raise self.error

    session._nodes = {
        "first": _Node("first"),
        "second": _Node("second", RuntimeError("teardown boom")),
    }

    with pytest.raises(RuntimeError, match=r"node_teardown\[second\]: teardown boom"):
        session.stop(reason="external_stop", status=RecentSessionStatus.STOPPED)

    assert teardown_calls == ["second", "first"]
    snapshot = session.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    assert snapshot.current_session_id is None
    assert snapshot.start_time is None
    assert snapshot.last_error == "node_teardown[second]: teardown boom"
    summary = json.loads((session.session_root / "session_summary.json").read_text(encoding="utf-8"))
    assert summary["end_status"] == "FAILED"
    assert summary["end_reason"] == "external_stop_failed"
    assert summary["last_error"] == "node_teardown[second]: teardown boom"


def test_stop_reports_idle_failed_when_summary_write_fails(tmp_path):
    session, _collector = _make_session(tmp_path)
    session._active = True
    session._accepting_frames = True
    session._run_started_monotonic = time.monotonic()
    session.status_manager.set_running(session.session_id, session.pipeline.name, session._start_time)

    def fail_summary_write(_path, _summary):
        raise OSError("summary disk error")

    session.summary_writer.write = fail_summary_write

    with pytest.raises(RuntimeError, match="write_session_summary: summary disk error"):
        session.stop(reason="external_stop", status=RecentSessionStatus.STOPPED)

    snapshot = session.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    assert snapshot.current_session_id is None
    assert snapshot.last_error == "write_session_summary: summary disk error"
