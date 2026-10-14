import json
import threading
import time

import pytest
import yaml

from data_collect_dag.app import AppRuntime
from data_collect_dag.control import ControlCommand


@pytest.fixture
def runtime(tmp_path):
    calibration = tmp_path / "calibration.json"
    calibration.write_text('{"images": {}, "pointclouds": {}}')
    pipeline = {
        "main_source": "camera",
        "nodes": [{"node_id": "start", "type": "start"}, {"node_id": "end", "type": "end"}],
        "edges": [["start", "end"]],
    }
    config = tmp_path / "cfg.yaml"
    config.write_text(yaml.safe_dump({
        "calibration": {"path": str(calibration)}, "output": {"root_dir": str(tmp_path / "output")},
        "ros": {"topics": {"camera": {"topic": "/camera", "msg_type": "sensor_msgs/Image", "role": "image"}}},
        "cache": {"defaults_by_role": {"image": {"max_frames": 2, "max_age_sec": 1}}},
        "pipelines": {"fps": pipeline, "other": pipeline},
    }))
    app = AppRuntime(config, "fps")
    try:
        yield app
    finally:
        app._control_stop_event.set()
        if app._control_thread.is_alive():
            app._control_thread.join(timeout=2)
        app.session_manager.stop()


def test_session_manager_start_stop_replace(runtime):
    manager = runtime.session_manager
    session1 = manager.start("fps")
    assert session1.session_root.exists()
    session2 = manager.start("other")
    assert session2.session_id != session1.session_id
    summary = json.loads((session1.session_root / "session_summary.json").read_text())
    assert summary["end_reason"] == "replaced_by_new_start"
    assert summary["end_status"] == "STOPPED"
    assert manager.active_session is session2
    manager.stop()
    assert manager.active_session is None
    assert runtime.status_manager.snapshot().tool_state.value == "IDLE"


def test_unknown_pipeline_start_preserves_active_session(runtime):
    active_session = runtime.session_manager.start("fps")
    before = runtime.status_manager.snapshot()
    runtime._control_thread.start()
    runtime._submit_command(ControlCommand(kind="start", pipeline_name="missing"))
    deadline = time.monotonic() + 1.0
    while runtime.status_manager.snapshot().last_error is None and time.monotonic() < deadline:
        time.sleep(0.01)

    snapshot = runtime.status_manager.snapshot()
    assert snapshot.tool_state.value == "RUNNING"
    assert snapshot.recent_session_status is None
    assert snapshot.current_session_id == active_session.session_id
    assert snapshot.current_pipeline_name == "fps"
    assert snapshot.start_time == before.start_time
    assert snapshot.samples_saved == before.samples_saved
    assert "unknown pipeline: missing" in snapshot.last_error
    assert runtime.session_manager.active_session is active_session
    assert not (active_session.session_root / "session_summary.json").exists()


def test_unknown_pipeline_preserves_paused_session(runtime):
    active = runtime.session_manager.start("fps")
    runtime.session_manager.pause()
    with pytest.raises(KeyError, match="unknown pipeline"):
        runtime.session_manager.start("missing")
    assert runtime.session_manager.active_session is active
    assert runtime.status_manager.snapshot().tool_state.value == "PAUSED"
    runtime.session_manager.resume()
    assert runtime.status_manager.snapshot().tool_state.value == "RUNNING"
