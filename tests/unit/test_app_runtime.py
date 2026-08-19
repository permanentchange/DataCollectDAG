import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_collect_dag.app import AppRuntime
from data_collect_dag.control import ControlCommand, ControlCommandQueue
from data_collect_dag.status import StatusManager


class _FakeRosAdapter:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _FailOnceSessionManager:
    def __init__(self):
        self.start_attempts = []
        self.failed_start = threading.Event()
        self.successful_start = threading.Event()

    def start(self, pipeline_name):
        self.start_attempts.append(pipeline_name)
        if len(self.start_attempts) == 1:
            self.failed_start.set()
            raise ValueError("pipeline setup failed")
        self.successful_start.set()

    def stop(self, **_kwargs):
        return None


class _SuccessfulSessionManager:
    def start(self, _pipeline_name):
        return None


class _FailingStopSessionManager:
    def stop(self, **_kwargs):
        raise RuntimeError("stop cleanup failed")


class _FailureHandledEvent:
    def __init__(self):
        self._set_calls = 0
        self._event = threading.Event()

    def set(self):
        self._set_calls += 1
        if self._set_calls == 2:
            self._event.set()

    def wait(self):
        return self._event.wait(timeout=1.0)


def _make_runtime(session_manager):
    runtime = object.__new__(AppRuntime)
    runtime.app_config = SimpleNamespace(config_path=Path("config.yaml"))
    runtime.logger = logging.getLogger("test.app_runtime")
    runtime.pipeline_name = "broken"
    runtime.status_manager = StatusManager()
    runtime.ros_adapter = _FakeRosAdapter()
    runtime.command_queue = ControlCommandQueue()
    runtime.session_manager = session_manager
    runtime._stop_event = threading.Event()
    runtime._control_stop_event = threading.Event()
    runtime._control_thread = threading.Thread(target=runtime._control_loop, daemon=True)
    runtime._initial_start_event = _FailureHandledEvent()
    runtime._initial_start_error = None
    return runtime


def test_run_survives_failed_initial_start_and_accepts_later_start(monkeypatch):
    monkeypatch.setattr("data_collect_dag.app.signal.signal", lambda *_args: None)
    session_manager = _FailOnceSessionManager()
    runtime = _make_runtime(session_manager)

    def recover_then_shutdown():
        assert session_manager.failed_start.wait(timeout=1.0)
        runtime._submit_command(ControlCommand(kind="start", pipeline_name="valid"))
        assert session_manager.successful_start.wait(timeout=1.0)
        runtime._submit_command(ControlCommand(kind="shutdown", reason="test_complete"))

    driver = threading.Thread(target=recover_then_shutdown, daemon=True)
    driver.start()

    assert runtime.run() == 0
    driver.join(timeout=1.0)

    assert session_manager.start_attempts == ["broken", "valid"]
    assert runtime.ros_adapter.started is True
    assert runtime.ros_adapter.stopped is True


def test_successful_start_clears_previous_control_error():
    runtime = _make_runtime(_SuccessfulSessionManager())
    runtime.status_manager.set_last_error("previous failure")

    runtime._dispatch_command(ControlCommand(kind="start", pipeline_name="valid"))

    assert runtime.status_manager.snapshot().last_error is None


def test_shutdown_stops_control_loop_when_session_cleanup_fails():
    runtime = _make_runtime(_FailingStopSessionManager())

    with pytest.raises(RuntimeError, match="stop cleanup failed"):
        runtime._dispatch_command(ControlCommand(kind="shutdown", reason="test_shutdown"))

    assert runtime._stop_event.is_set()
    assert runtime._control_stop_event.is_set()
