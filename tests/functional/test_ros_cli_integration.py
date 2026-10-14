"""Opt-in ROS1 Noetic test using an isolated local master and real TCPROS."""

import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from xmlrpc.client import ServerProxy

import pytest
import yaml

from data_collect_dag.ros_env import ROS_PYTHON_PATH

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("DCD_TEST_REAL_ROS") != "1", reason="set DCD_TEST_REAL_ROS=1 to run against ROS1 Noetic",
)


@pytest.fixture
def ros_env(tmp_path):
    roscore = shutil.which("roscore")
    assert roscore, "source /opt/ros/noetic/setup.bash before running real ROS tests"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, ROS_MASTER_URI=f"http://127.0.0.1:{port}", ROS_HOSTNAME="localhost",
               ROS_HOME=str(tmp_path / "ros_home"), ROS_LOG_DIR=str(tmp_path / "ros_logs"),
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROS_PYTHON_PATH), os.environ.get("PYTHONPATH", "")]))
    env.pop("ROS_NAMESPACE", None)
    with (tmp_path / "master.log").open("w") as log:
        master = subprocess.Popen([roscore, "-p", str(port)], env=env, stdout=log, stderr=log, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    with ServerProxy(env["ROS_MASTER_URI"]) as proxy:
                        assert proxy.getPid("/integration_test")[0] == 1
                    break
                except (OSError, AssertionError):
                    assert master.poll() is None, (tmp_path / "master.log").read_text()
                    assert time.monotonic() < deadline, "ROS master did not start"
                    time.sleep(0.05)
            yield env
        finally:
            if master.poll() is None:
                os.killpg(master.pid, signal.SIGTERM)
            try:
                master.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(master.pid, signal.SIGKILL)
                master.wait(timeout=5)


def cli(env, *args):
    return subprocess.run([sys.executable, "-m", "data_collect_dag", *args], env=env,
                          capture_output=True, text=True, timeout=10)


def wait_status(env, state, pipeline=None, error=None):
    deadline = time.monotonic() + 10
    while True:
        result = cli(env, "status", "--timeout", "2")
        if result.returncode == 0 and f"State:       {state}" in result.stdout:
            if (pipeline is None or f"Pipeline:    {pipeline}" in result.stdout) and (error is None or error in result.stdout):
                return result.stdout
        assert time.monotonic() < deadline, result.stdout + result.stderr
        time.sleep(0.05)


@pytest.mark.parametrize("background", [False, True])
def test_real_ros_cli_lifecycle_locking_and_timeouts(tmp_path, ros_env, background):
    name = "/vehicle/ros_cli_test_" + uuid.uuid4().hex
    calibration = tmp_path / "calibration.json"
    calibration.write_text('{"images": {}, "pointclouds": {}}')
    pipeline = {"main_source": "camera", "nodes": [{"node_id": "start", "type": "start"},
                {"node_id": "end", "type": "end"}], "edges": [["start", "end"]]}
    config = tmp_path / "cfg.yaml"
    config.write_text(yaml.safe_dump({
        "ros": {"node_name": name, "control": {"control_topic": "/data_collect/control",
                 "status_topic": "/data_collect/status"}, "topics": {
                 "camera": {"topic": "/test_camera", "msg_type": "sensor_msgs/Image", "role": "image"}}},
        "calibration": {"path": str(calibration)}, "output": {"root_dir": str(tmp_path / "output")},
        "cache": {"defaults_by_role": {"image": {"max_frames": 2, "max_age_sec": 1}}},
        "pipelines": {"init": pipeline, "fps": pipeline},
    }))
    args = ["--config", str(config)] + (["--background"] if background else [])
    with (tmp_path / "collector.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "data_collect_dag", *args], env=ros_env,
                                   stdout=log, stderr=log, start_new_session=True)
        pid = process.pid
        try:
            if background:
                assert process.wait(timeout=10) == 0
                pid = int(re.search(r"PID=(\d+)", (tmp_path / "collector.log").read_text()).group(1))
            wait_status(ros_env, "RUNNING", "init")
            # Check that the guarded full YAML name is the actual ROS node.
            with ServerProxy(ros_env["ROS_MASTER_URI"]) as proxy:
                assert proxy.lookupNode("/integration_test", name)[0] == 1
            assert cli(ros_env, "start", "fps").returncode == 0
            active = wait_status(ros_env, "RUNNING", "fps")
            session_id = re.search(r"Session:\s+(\S+)", active).group(1)
            assert cli(ros_env, "pause").returncode == 0
            wait_status(ros_env, "PAUSED", "fps")
            assert cli(ros_env, "resume").returncode == 0
            wait_status(ros_env, "RUNNING", "fps")
            # Sending a valid control message is distinct from successful execution.
            assert cli(ros_env, "start", "missing").returncode == 0
            active = wait_status(ros_env, "RUNNING", "fps", "unknown pipeline: missing")
            assert session_id in active
            assert cli(ros_env, "stop").returncode == 0
            wait_status(ros_env, "IDLE")
            for duplicate_background in (False, True):
                duplicate = cli(ros_env, "--config", str(config), *(["--background"] if duplicate_background else []))
                assert duplicate.returncode == 1 and "is already running" in duplicate.stderr
            missing = cli(ros_env, "pause", "--control-topic", "/absent/control", "--timeout", "0.2")
            assert missing.returncode == 1 and "no collection control subscriber" in missing.stderr
            assert cli(ros_env, "shutdown").returncode == 0
            wait_status_gone = cli(ros_env, "status", "--timeout", "1")
            # Allow a last latched snapshot during shutdown; the next query must fail.
            if wait_status_gone.returncode == 0:
                time.sleep(0.3)
                wait_status_gone = cli(ros_env, "status", "--timeout", "1")
            assert wait_status_gone.returncode == 1
            if not background:
                assert process.wait(timeout=5) == 0
            from data_collect_dag.instance_guard import InstanceGuard
            with InstanceGuard(name):
                pass
        finally:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def test_real_unavailable_master_has_nonzero_bounded_exit(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, ROS_MASTER_URI=f"http://127.0.0.1:{port}")
    begin = time.monotonic()
    result = cli(env, "status", "--timeout", "0.2")
    assert result.returncode == 1 and "ROS Master unavailable" in result.stderr
    assert time.monotonic() - begin < 5
