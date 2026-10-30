from contextlib import contextmanager
import threading

import pytest

from data_collect_dag.app import AppRuntime
from data_collect_dag.control import ControlCommand
from tests.helpers import minimal_config, wait_until, write_config


class _FakeRosAdapter:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.shutdown = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def is_shutdown(self):
        return self.shutdown


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
    def __init__(self):
        self.started = threading.Event()
        self.start_attempts = []
        self.stop_calls = []

    def start(self, _pipeline_name):
        self.start_attempts.append(_pipeline_name)
        self.started.set()

    def stop(self, **kwargs):
        self.stop_calls.append(kwargs)


class _FailingStopSessionManager:
    def stop(self, **_kwargs):
        raise RuntimeError("stop cleanup failed")


@pytest.fixture
def make_runtime(tmp_path):
    def make(session_manager):
        payload = minimal_config(tmp_path)
        payload["pipelines"]["broken"] = payload["pipelines"].pop("init")
        runtime = AppRuntime(write_config(tmp_path, payload), "broken")
        runtime.ros_adapter = _FakeRosAdapter()
        runtime.session_manager = session_manager
        return runtime
    return make


@pytest.fixture
def running_runtime(monkeypatch):
    monkeypatch.setattr("data_collect_dag.app.signal.signal", lambda *_args: None)

    @contextmanager
    def run(runtime):
        errors = []

        def target():
            try:
                assert runtime.run() == 0
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        try:
            yield
        finally:
            runtime._submit_command(ControlCommand(kind="shutdown", reason="test_complete"))
            thread.join(timeout=3)
            assert not thread.is_alive(), "runtime did not exit"
            assert not runtime._control_thread.is_alive(), "control thread did not exit"
            if errors:
                raise errors[0]
    return run


def test_run_survives_failed_initial_start_and_accepts_later_start(make_runtime, running_runtime):
    session_manager = _FailOnceSessionManager()
    runtime = make_runtime(session_manager)

    with running_runtime(runtime):
        assert session_manager.failed_start.wait(timeout=3)
        runtime._submit_command(ControlCommand(kind="start", pipeline_name="valid"))
        assert session_manager.successful_start.wait(timeout=3)

    assert session_manager.start_attempts == ["broken", "valid"]
    assert runtime.ros_adapter.started is True
    assert runtime.ros_adapter.stopped is True


def test_successful_start_clears_previous_control_error(make_runtime):
    runtime = make_runtime(_SuccessfulSessionManager())
    runtime.status_manager.set_last_error("previous failure")

    runtime._dispatch_command(ControlCommand(kind="start", pipeline_name="valid"))

    assert runtime.status_manager.snapshot().last_error is None


def test_shutdown_stops_control_loop_when_session_cleanup_fails(make_runtime):
    runtime = make_runtime(_FailingStopSessionManager())

    with pytest.raises(RuntimeError, match="stop cleanup failed"):
        runtime._dispatch_command(ControlCommand(kind="shutdown", reason="test_shutdown"))

    assert runtime._stop_event.is_set()
    assert runtime._control_stop_event.is_set()


@pytest.mark.parametrize("handler,reason", [
    ("_handle_shutdown_command", "external_shutdown"),
    ("_handle_ros_shutdown", "ros_shutdown"),
])
def test_shutdown_callbacks_only_enqueue_shutdown(handler, reason, make_runtime):
    runtime = make_runtime(_SuccessfulSessionManager())
    getattr(runtime, handler)()
    assert not runtime._stop_event.is_set()
    assert runtime.session_manager.stop_calls == []
    command = runtime.command_queue.get(timeout=0.1)
    assert command.kind == "shutdown" and command.reason == reason


@pytest.mark.parametrize("notification", ["hook", "poll"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_ros_shutdown_exits_runtime_and_cleans_up_once(monkeypatch, notification, cleanup_fails, make_runtime, running_runtime):
    session_manager = _SuccessfulSessionManager()
    runtime = make_runtime(session_manager)
    if cleanup_fails:
        stop = session_manager.stop

        def failing_stop(**kwargs):
            stop(**kwargs)
            raise RuntimeError("cleanup failed")

        session_manager.stop = failing_stop

    with running_runtime(runtime):
        assert session_manager.started.wait(timeout=3)
        runtime.ros_adapter.shutdown = True
        if notification == "hook":
            runtime._handle_ros_shutdown()
            runtime._handle_ros_shutdown()
        wait_until(runtime._stop_event.is_set)

    assert session_manager.start_attempts == ["broken"]
    assert len(session_manager.stop_calls) == 1
    assert session_manager.stop_calls[0]["reason"] == "ros_shutdown"
    assert runtime.ros_adapter.stopped
    assert not runtime._control_thread.is_alive()
    if cleanup_fails:
        assert runtime.status_manager.snapshot().last_error == "cleanup failed"


@pytest.mark.parametrize("notify", [False, True])
def test_ros_shutdown_during_adapter_start_skips_initial_pipeline(monkeypatch, notify, make_runtime, running_runtime):
    runtime = make_runtime(_SuccessfulSessionManager())

    def start_then_close():
        runtime.ros_adapter.shutdown = True
        if notify:
            runtime._handle_ros_shutdown()

    monkeypatch.setattr(runtime.ros_adapter, "start", start_then_close)

    with running_runtime(runtime):
        wait_until(runtime._stop_event.is_set)
    assert runtime.session_manager.start_attempts == []
    assert len(runtime.session_manager.stop_calls) == 1
    assert runtime.session_manager.stop_calls[0]["reason"] == "ros_shutdown"
    assert runtime.ros_adapter.stopped


def test_start_queued_before_ros_shutdown_does_not_start_a_session(make_runtime, running_runtime):
    runtime = make_runtime(_SuccessfulSessionManager())
    runtime._submit_command(ControlCommand(kind="start", pipeline_name="broken"))
    runtime._handle_ros_shutdown()

    with running_runtime(runtime):
        wait_until(runtime._stop_event.is_set)

    assert runtime.session_manager.start_attempts == []
    assert len(runtime.session_manager.stop_calls) == 1


def test_start_after_ros_shutdown_without_hook_requests_cleanup(make_runtime):
    runtime = make_runtime(_SuccessfulSessionManager())
    runtime.ros_adapter.shutdown = True

    runtime._dispatch_command(ControlCommand(kind="start", pipeline_name="broken"))

    assert runtime.session_manager.start_attempts == []
    command = runtime.command_queue.get(timeout=0.1)
    assert command.kind == "shutdown" and command.reason == "ros_shutdown"


def test_adapter_start_failure_still_cleans_up_partial_resources(monkeypatch, make_runtime):
    monkeypatch.setattr("data_collect_dag.app.signal.signal", lambda *_args: None)
    runtime = make_runtime(_SuccessfulSessionManager())

    def fail():
        raise RuntimeError("adapter setup failed")

    monkeypatch.setattr(runtime.ros_adapter, "start", fail)
    with pytest.raises(RuntimeError, match="adapter setup failed"):
        runtime.run()

    assert runtime.ros_adapter.stopped
    assert runtime._control_thread.ident is None
    assert runtime.session_manager.start_attempts == []


def test_duplicate_shutdown_dispatch_does_not_repeat_cleanup(make_runtime):
    runtime = make_runtime(_SuccessfulSessionManager())
    for _ in range(2):
        runtime._dispatch_command(ControlCommand(kind="shutdown", reason="ros_shutdown"))
    assert len(runtime.session_manager.stop_calls) == 1


def test_runtime_error_waits_for_session_cleanup_before_stopping_adapter(monkeypatch, make_runtime):
    monkeypatch.setattr("data_collect_dag.app.signal.signal", lambda *_args: None)
    runtime = make_runtime(_SuccessfulSessionManager())

    def fail_after_start(_timeout):
        assert runtime.session_manager.started.wait(timeout=1.0)
        raise RuntimeError("monitor failed")

    def stop_adapter():
        assert len(runtime.session_manager.stop_calls) == 1
        assert not runtime._control_thread.is_alive()
        runtime.ros_adapter.stopped = True

    monkeypatch.setattr(runtime._stop_event, "wait", fail_after_start)
    monkeypatch.setattr(runtime.ros_adapter, "stop", stop_adapter)

    with pytest.raises(RuntimeError, match="monitor failed"):
        runtime.run()

    assert runtime.session_manager.stop_calls[0]["reason"] == "runtime_exit"
    assert runtime.ros_adapter.stopped


def test_main_thread_failure_still_stops_runtime(make_runtime, running_runtime):
    manager = _SuccessfulSessionManager()
    runtime = make_runtime(manager)
    with pytest.raises(ValueError, match="assertion driver failed"):
        with running_runtime(runtime):
            assert manager.started.wait(timeout=3)
            raise ValueError("assertion driver failed")
    assert runtime.ros_adapter.stopped
    assert len(manager.stop_calls) == 1


def test_background_runtime_exception_reaches_main_thread(make_runtime, running_runtime, monkeypatch):
    runtime = make_runtime(_SuccessfulSessionManager())

    def fail():
        raise ValueError("background startup failed")

    monkeypatch.setattr(runtime.ros_adapter, "start", fail)
    with pytest.raises(ValueError, match="background startup failed"):
        with running_runtime(runtime):
            pass
    assert runtime.ros_adapter.stopped
