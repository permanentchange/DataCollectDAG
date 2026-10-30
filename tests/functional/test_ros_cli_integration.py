"""Opt-in ROS1 Noetic test using an isolated local master and real TCPROS."""

from contextlib import contextmanager
import json
import os
from http.client import RemoteDisconnected
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
from tests.helpers import minimal_config, write_config as write_payload, process_alive, wait_until, terminate_process

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
    config = write_config(tmp_path, name)
    with (tmp_path / "collector.log").open("w") as log, collector(ros_env, config, background, log) as (process, pid):
        wait_status(ros_env, "RUNNING", "init")
        # Check that the guarded full YAML name is the actual ROS node.
        with ServerProxy(ros_env["ROS_MASTER_URI"]) as proxy:
            assert proxy.lookupNode("/integration_test", name)[0] == 1
        assert cli(ros_env, "run", "start", "fps").returncode == 0
        active = wait_status(ros_env, "RUNNING", "fps")
        session_id = re.search(r"Session:\s+(\S+)", active).group(1)
        assert cli(ros_env, "run", "pause").returncode == 0
        wait_status(ros_env, "PAUSED", "fps")
        assert cli(ros_env, "run", "resume").returncode == 0
        wait_status(ros_env, "RUNNING", "fps")
        # Sending a valid control message is distinct from successful execution.
        assert cli(ros_env, "run", "start", "missing").returncode == 0
        active = wait_status(ros_env, "RUNNING", "fps", "unknown pipeline: missing")
        assert session_id in active
        assert cli(ros_env, "run", "stop").returncode == 0
        wait_status(ros_env, "IDLE")
        for duplicate_background in (False, True):
            duplicate = cli(ros_env, "run", "launch", "--config", str(config), *(["--background"] if duplicate_background else []))
            assert duplicate.returncode == 1 and "is already running" in duplicate.stderr
        missing = cli(ros_env, "run", "pause", "--control-topic", "/absent/control", "--timeout", "0.2")
        assert missing.returncode == 1 and "no collection control subscriber" in missing.stderr
        assert cli(ros_env, "run", "start", "fps").returncode == 0
        restarted = wait_status(ros_env, "RUNNING", "fps")
        assert session_id not in restarted
        assert cli(ros_env, "shutdown").returncode == 0
        wait_status_gone = cli(ros_env, "status", "--timeout", "1")
        # Allow a last latched snapshot during shutdown; the next query must fail.
        if wait_status_gone.returncode == 0:
            time.sleep(0.3)
            wait_status_gone = cli(ros_env, "status", "--timeout", "1")
        assert wait_status_gone.returncode == 1
        missing = cli(ros_env, "run", "start", "fps", "--timeout", "0.2")
        assert missing.returncode == 1 and "no collection control subscriber" in missing.stderr
        if not background:
            assert process.wait(timeout=5) == 0
        from data_collect_dag.instance_guard import InstanceGuard
        with InstanceGuard(name):
            pass


def write_config(tmp_path, name):
    raw = minimal_config(tmp_path)
    raw["ros"].update({"node_name": name, "control": {
        "control_topic": "/data_collect/control", "status_topic": "/data_collect/status",
    }})
    raw["pipelines"]["fps"] = raw["pipelines"]["init"]
    return write_payload(tmp_path, raw)


def wait_process_exit(process, pid):
    wait_until(lambda: not process_alive(pid), 10, f"collector PID {pid} did not exit")
    assert process.wait(timeout=5) == 0


def terminate_collector(process, pid):
    try:
        terminate_process(pid)
    finally:
        process.wait(timeout=5)


@contextmanager
def collector(env, config, background, log):
    args = ["run", "launch", "--config", str(config)] + (["--background"] if background else [])
    process = subprocess.Popen([sys.executable, "-m", "data_collect_dag", *args], env=env,
                               stdout=log, stderr=log, start_new_session=True)
    pid = process.pid
    try:
        if background:
            assert process.wait(timeout=10) == 0
            pid = int(re.search(r"PID=(\d+)", Path(log.name).read_text()).group(1))
        yield process, pid
    finally:
        terminate_collector(process, pid)


@pytest.mark.parametrize("trigger,idle,background,restart", [
    pytest.param("node_shutdown", False, False, True, id="ros-running-foreground"),
    pytest.param("node_shutdown", True, True, False, id="ros-idle-background"),
    pytest.param("same_name", False, True, True, id="replacement-running-background"),
    pytest.param("same_name", True, False, False, id="replacement-idle-foreground"),
    pytest.param("sigint", False, False, False, id="sigint-running-foreground"),
    pytest.param("sigterm", False, True, False, id="sigterm-running-background"),
])
def test_real_ros_external_shutdown_releases_process_and_lock(tmp_path, ros_env, trigger, idle, background, restart):
    from data_collect_dag.instance_guard import InstanceGuard

    name = "/vehicle/shutdown_test_" + uuid.uuid4().hex
    config = write_config(tmp_path, name)
    replacement = None
    with (tmp_path / "collector.log").open("w") as log, collector(ros_env, config, background, log) as (process, pid):
        try:
            active = wait_status(ros_env, "RUNNING", "init")
            session_id = re.search(r"Session:\s+(\S+)", active).group(1)
            summary_path = tmp_path / "output" / session_id / "session_summary.json"
            if idle:
                assert cli(ros_env, "run", "stop").returncode == 0
                wait_status(ros_env, "IDLE")
                previous_summary = summary_path.read_bytes()

            if trigger == "node_shutdown":
                with ServerProxy(ros_env["ROS_MASTER_URI"]) as master:
                    code, message, uri = master.lookupNode("/integration_test", name)
                    assert code == 1, message
                with ServerProxy(uri) as node:
                    try:
                        assert node.shutdown("/integration_test", "external shutdown test")[0] == 1
                    except (ConnectionRefusedError, RemoteDisconnected):
                        # The process can finish cleanup before XML-RPC replies
                        # (or before ServerProxy retries a dropped response).
                        # Exit, lock release and summary below prove delivery.
                        pass
            elif trigger == "same_name":
                # A plain rospy process bypasses the host-local instance guard,
                # just as a node started on another host would.
                code = ("import rospy, sys; "
                        "rospy.init_node('replacement', argv=['replacement', '__name:=' + sys.argv[1]]); "
                        "rospy.spin()")
                replacement = subprocess.Popen([sys.executable, "-c", code, name], env=ros_env,
                                               stdout=log, stderr=log, start_new_session=True)
            else:
                os.kill(pid, signal.SIGINT if trigger == "sigint" else signal.SIGTERM)

            wait_process_exit(process, pid)
            with InstanceGuard(name):
                pass
            if idle:
                assert summary_path.read_bytes() == previous_summary
            else:
                summary = json.loads(summary_path.read_text())
                assert summary["end_status"] == "STOPPED"
                expected = {"sigint": "signal_2", "sigterm": "signal_15"}.get(trigger, "ros_shutdown")
                assert summary["end_reason"] == expected

            if replacement is not None:
                assert replacement.poll() is None
                terminate_collector(replacement, replacement.pid)
                replacement = None
            if restart:
                restarted = subprocess.Popen([sys.executable, "-m", "data_collect_dag", "run", "launch",
                                              "--config", str(config)], env=ros_env, stdout=log, stderr=log)
                try:
                    current = wait_status(ros_env, "RUNNING", "init")
                    assert session_id not in current
                    assert cli(ros_env, "shutdown").returncode == 0
                    assert restarted.wait(timeout=10) == 0
                finally:
                    terminate_collector(restarted, restarted.pid)
        finally:
            if replacement is not None:
                terminate_collector(replacement, replacement.pid)


def test_real_unavailable_master_has_nonzero_bounded_exit(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, ROS_MASTER_URI=f"http://127.0.0.1:{port}")
    begin = time.monotonic()
    result = cli(env, "status", "--timeout", "0.2")
    assert result.returncode == 1 and "ROS Master unavailable" in result.stderr
    assert time.monotonic() - begin < 5
