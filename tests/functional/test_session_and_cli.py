from pathlib import Path

import pytest

from data_collect_dag.app import AppRuntime
from data_collect_dag.config import load_app_config
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import SessionManager
from data_collect_dag.status import StatusManager


def test_missing_extrinsics_session_start_fails():
    config = load_app_config(Path("demo/xtreme1_demo_missing_extrinsics.yaml"))
    runtime = AppRuntime(Path("demo/xtreme1_demo_missing_extrinsics.yaml"), "xtreme1_collect")
    with pytest.raises(Exception):
        runtime.session_manager.start("xtreme1_collect")


def test_session_manager_start_stop_replace(tmp_path):
    runtime = AppRuntime(Path("demo/xtreme1_demo.yaml"), "xtreme1_collect")
    runtime.app_config.output_root_dir = tmp_path
    runtime.session_manager = SessionManager(
        runtime.app_config,
        runtime.calibration,
        RosAdapter(runtime.app_config.topics, runtime.app_config.ros_node_name, runtime.app_config.control),
        StatusManager(),
    )
    session1 = runtime.session_manager.start("xtreme1_collect")
    assert session1.session_root.exists()
    session2 = runtime.session_manager.start("xtreme1_collect")
    assert session2.session_id != session1.session_id
    runtime.session_manager.stop()
