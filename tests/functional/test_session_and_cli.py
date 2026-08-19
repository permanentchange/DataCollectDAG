import json
import threading
import time
from pathlib import Path

from data_collect_dag.app import AppRuntime
from data_collect_dag.control import ControlCommand
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import SessionManager
from data_collect_dag.status import StatusManager

def test_session_manager_start_stop_replace(tmp_path, monkeypatch):
    runtime = AppRuntime(Path("demo/xtreme1_demo.yaml"), "lidar_frontcamera_1fps")
    runtime.app_config.output_root_dir = tmp_path
    runtime.session_manager = SessionManager(
        runtime.app_config,
        runtime.calibration,
        RosAdapter(runtime.app_config.topics, runtime.app_config.ros_node_name, runtime.app_config.control),
        StatusManager(),
        command_callback=lambda command: None,
    )
    session1 = runtime.session_manager.start("lidar_frontcamera_1fps")
    assert session1.session_root.exists()
    session2 = runtime.session_manager.start("lidar_frontcamera_1fps")
    assert session2.session_id != session1.session_id
    runtime.session_manager.stop()


def test_unknown_pipeline_start_stops_active_session_before_validation(tmp_path):
    runtime = AppRuntime(Path("demo/xtreme1_demo.yaml"), "lidar_frontcamera_1fps")
    runtime.app_config.output_root_dir = tmp_path
    status_manager = runtime.status_manager
    runtime.session_manager = SessionManager(
        runtime.app_config,
        runtime.calibration,
        RosAdapter(runtime.app_config.topics, runtime.app_config.ros_node_name, runtime.app_config.control),
        status_manager,
        command_callback=lambda command: None,
    )
    active_session = runtime.session_manager.start("lidar_frontcamera_1fps")
    runtime._control_thread = threading.Thread(target=runtime._control_loop, daemon=True)
    runtime._control_thread.start()

    try:
        runtime._submit_command(ControlCommand(kind="start", pipeline_name="missing"))
        deadline = time.monotonic() + 1.0
        while status_manager.snapshot().last_error is None and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        runtime._control_stop_event.set()
        runtime._control_thread.join(timeout=1.0)

    snapshot = status_manager.snapshot()
    assert snapshot.tool_state.value == "IDLE"
    assert snapshot.recent_session_status.value == "STOPPED"
    assert snapshot.current_session_id is None
    assert snapshot.current_pipeline_name is None
    assert snapshot.start_time is None
    assert "unknown pipeline: missing" in snapshot.last_error
    assert runtime.session_manager.active_session is None
    summary = json.loads((active_session.session_root / "session_summary.json").read_text(encoding="utf-8"))
    assert summary["end_status"] == "STOPPED"
    assert summary["end_reason"] == "replaced_by_new_start"
