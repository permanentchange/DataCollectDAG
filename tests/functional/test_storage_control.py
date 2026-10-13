import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from data_collect_dag.app import AppRuntime
from data_collect_dag.cli import main
from data_collect_dag.control import ControlCommand
from data_collect_dag.models import FrameMeta
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.session import NODE_TYPES
from data_collect_dag.storage import tracked_write


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for storage control"
        time.sleep(0.005)


@pytest.fixture
def runtime(tmp_path):
    calibration = tmp_path / "calibration.json"
    calibration.write_text('{"images": {}, "pointclouds": {}}')
    payload = {
        "output": {"root_dir": str(tmp_path / "output"), "disk_storage_limit_GB": 0.001},
        "calibration": {"path": str(calibration)},
        "ros": {"topics": {"camera": {"topic": "/camera", "msg_type": "sensor_msgs/Image", "role": "image"}}},
        "cache": {"defaults_by_role": {"image": {"max_frames": 10, "max_age_sec": 1}}},
        "runtime": {"sample_workers": 2, "node_workers": 2},
        "pipelines": {"test": {
            "main_source": "camera",
            "nodes": [{"node_id": "start", "type": "start"}, {"node_id": "end", "type": "end"}],
            "edges": [["start", "end"]],
        }},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    app = AppRuntime(path, "test")
    app._control_thread.start()
    try:
        yield app
    finally:
        app._submit_command(ControlCommand(kind="shutdown", reason="test_shutdown"))
        app._control_thread.join(3)
        assert not app._control_thread.is_alive()


def _start(runtime):
    runtime._handle_start_command("test")
    _wait(lambda: runtime.session_manager.active_session is not None)
    return runtime.session_manager.active_session


def test_clean_cli_without_ros_or_pipeline(tmp_path, monkeypatch, capsys):
    root = tmp_path / "output"
    session = root / "20250101_120000_abcdef12"
    session.mkdir(parents=True)
    (session / "data").write_text("test data")
    config = tmp_path / "clean.yaml"
    config.write_text(f"output:\n  root_dir: {root}\n")
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--config", str(config), "--pipeline", "missing", "--clean", "0"])
    def forbidden(*args, **kwargs):
        raise AssertionError("cleanup must not construct a ROS runtime")
    monkeypatch.setattr("data_collect_dag.cli.AppRuntime", forbidden)
    assert main() == 0
    assert not session.exists() and root.exists()
    assert "deleted 1 session directories" in capsys.readouterr().out


def test_start_refused_before_creating_session_and_can_recover(runtime):
    root = runtime.app_config.output_root_dir
    root.mkdir()
    old = root / "unrelated.dat"
    old.write_bytes(b"x" * 1_000_000)
    runtime._handle_start_command("test")
    _wait(lambda: runtime.status_manager.snapshot().last_error is not None)
    snapshot = runtime.status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "FAILED"
    assert "storage_limit_reached" in snapshot.last_error
    assert runtime.session_manager.active_session is None
    assert list(root.iterdir()) == [old]
    assert runtime._control_thread.is_alive()
    old.unlink()
    session = _start(runtime)
    assert session.session_root.exists()
    assert runtime.status_manager.snapshot().last_error is None


def test_free_space_checked_once_per_start_and_not_resume(runtime, monkeypatch):
    calls = []
    available = [999_999_999]
    def disk_usage(path):
        calls.append(path)
        return SimpleNamespace(free=available[0])
    # Disable snapshot's independent live disk-free query to count admission checks.
    runtime.status_manager._disk_usage_path = None
    monkeypatch.setattr("data_collect_dag.storage.shutil.disk_usage", disk_usage)
    runtime.app_config.disk_storage_need_gb = 1
    runtime._handle_start_command("test")
    _wait(lambda: runtime.status_manager.snapshot().last_error is not None)
    assert "insufficient_disk_space" in runtime.status_manager.snapshot().last_error
    assert not runtime.app_config.output_root_dir.exists()
    available[0] = 1_000_000_000
    session = _start(runtime)
    session.pause()
    available[0] = 0
    session.resume()
    assert len(calls) == 2
    assert runtime.status_manager.snapshot().tool_state.value == "RUNNING"


def test_limit_drains_two_inflight_samples_and_stops_queued_sample(runtime, monkeypatch):
    entered = [threading.Event(), threading.Event()]
    begin_writes = threading.Event()
    written = [threading.Event(), threading.Event()]
    finish = threading.Event()
    complete_commands = []
    original_put = runtime.command_queue.put
    def record(command):
        if command.kind == "complete":
            complete_commands.append(command)
        original_put(command)
    monkeypatch.setattr(runtime.command_queue, "put", record)

    class SavingNode(BaseNode):
        def run(self, sample):
            index = int(sample.sample_id) - 1
            assert index < 2, "queued sample must not begin"
            entered[index].set()
            assert begin_writes.wait(3)
            path = self.session.session_root / f"frame_{index}.bin"
            with tracked_write(path, self.session.storage_tracker):
                path.write_bytes(b"x" * 600_000)
            written[index].set()
            assert finish.wait(3)
            sample.metadata["save_result"] = {"saved": True}
            return self.ok()

    monkeypatch.setitem(NODE_TYPES, "end", SavingNode)
    session = _start(runtime)
    def frame(timestamp):
        return SimpleNamespace(meta=FrameMeta("camera", "/camera", "camera", "image", timestamp, timestamp))
    try:
        session.accept_frame("camera", frame(1))
        session.accept_frame("camera", frame(2))
        assert all(event.wait(3) for event in entered)
        session.accept_frame("camera", frame(3))
        begin_writes.set()
        assert all(event.wait(3) for event in written)
        assert session._completion_requested and not session._accepting_frames
        assert complete_commands == []
        session.accept_frame("camera", frame(4))
        assert session.metrics.metrics.samples_started == 2
        finish.set()
        _wait(lambda: runtime.session_manager.active_session is None)
        assert len(complete_commands) == 1
        assert complete_commands[0].session_id == session.session_id
        summary = json.loads((session.session_root / "session_summary.json").read_text())
        assert summary["end_status"] == "FAILED"
        assert summary["end_reason"] == "storage_limit_reached"
        assert summary["metrics"]["samples_saved"] == 2
        assert summary["metrics"]["samples_started"] == 2
        assert all((session.session_root / f"frame_{index}.bin").stat().st_size == 600_000 for index in range(2))
        status = json.loads(runtime.ros_adapter.publish_status())
        assert status["tool_state"] == "IDLE"
        assert status["recent_session_status"] == "FAILED"
        assert "storage_limit_reached" in status["last_error"]
        assert status["disk_storage_limit_gb"] == 0.001
        assert status["output_size_gb"] is not None
        assert runtime._control_thread.is_alive()
    finally:
        begin_writes.set()
        finish.set()


@pytest.mark.parametrize("source", ["external", "log", "background_log", "scan_failure"])
def test_background_monitor_stops_without_incoming_frames(runtime, monkeypatch, source):
    monkeypatch.setattr("data_collect_dag.session.STORAGE_RECONCILE_INTERVAL_SEC", 0.01)
    monkeypatch.setattr("data_collect_dag.session.STORAGE_LOG_INTERVAL_SEC", 0.01)
    session = _start(runtime)
    if source == "scan_failure":
        def fail(root):
            raise PermissionError("scan denied")
        monkeypatch.setattr("data_collect_dag.storage.scan_files", fail)
        expected_reason = "storage_check_failed"
    else:
        if source == "log":
            path = session.session_root / "debug.log"
        else:
            name = "background.log" if source == "background_log" else "external.bin"
            path = runtime.app_config.output_root_dir / name
        path.write_bytes(b"x" * 1_000_000)
        expected_reason = "storage_limit_reached"
    _wait(lambda: runtime.session_manager.active_session is None)
    snapshot = runtime.status_manager.snapshot()
    assert snapshot.recent_session_status.value == "FAILED"
    assert expected_reason in snapshot.last_error
    summary = json.loads((session.session_root / "session_summary.json").read_text())
    assert summary["end_reason"] == expected_reason


def test_background_log_is_monitored_without_directory_scan(runtime, monkeypatch):
    monkeypatch.setattr("data_collect_dag.session.STORAGE_LOG_INTERVAL_SEC", 0.01)
    session = _start(runtime)
    def no_scan(root):
        raise AssertionError("log limit must be detected without a directory scan")
    monkeypatch.setattr("data_collect_dag.storage.scan_files", no_scan)
    (runtime.app_config.output_root_dir / "background.log").write_bytes(b"x" * 1_000_000)
    _wait(lambda: runtime.session_manager.active_session is None)
    assert "storage_limit_reached" in runtime.status_manager.snapshot().last_error


@pytest.mark.parametrize("state", ["idle", "running", "paused"])
def test_ros_shutdown_stops_entire_runtime_and_writes_summary(runtime, state):
    session = None
    if state != "idle":
        session = _start(runtime)
        if state == "paused":
            session.pause()
    runtime.ros_adapter._on_control_topic(SimpleNamespace(data='{"command":"shutdown"}'))
    runtime._control_thread.join(3)
    assert not runtime._control_thread.is_alive()
    assert runtime._stop_event.is_set() and runtime._control_stop_event.is_set()
    assert runtime.session_manager.active_session is None
    if session is not None:
        summary = json.loads((session.session_root / "session_summary.json").read_text())
        assert summary["end_status"] == "STOPPED" and summary["end_reason"] == "external_shutdown"


def test_ros_stop_keeps_runtime_available_for_restart(runtime):
    first = _start(runtime)
    runtime.ros_adapter._on_control_topic(SimpleNamespace(data='{"command":"stop"}'))
    _wait(lambda: runtime.session_manager.active_session is None)
    assert runtime._control_thread.is_alive() and not runtime._stop_event.is_set()
    assert _start(runtime).session_id != first.session_id
