"""Use real OS processes to verify CLI locking, detachment and crash recovery."""

import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_HOOK = '''
import os
from pathlib import Path
import signal
import time
from types import SimpleNamespace
from data_collect_dag import app
from data_collect_dag.instance_guard import load_node_name

class Runtime:
    def __init__(self, **kwargs):
        self.app_config = SimpleNamespace(ros_node_name=load_node_name(kwargs["config_path"]))
        if os.environ.get("DCD_TEST_FAIL"):
            raise ValueError("test initialization failure")
    def run(self):
        signal.signal(signal.SIGTERM, lambda *_args: exit(0))
        Path(os.environ["DCD_TEST_READY"]).write_text(str(os.getpid()))
        while True:
            time.sleep(0.01)
app.AppRuntime = Runtime
'''


@pytest.fixture
def instance_env(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(RUNTIME_HOOK)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(hooks), str(ROOT / "src")])
    env["DCD_TEST_READY"] = str(tmp_path / "ready")
    name = "process_test_" + uuid.uuid4().hex
    config = tmp_path / "config.yaml"
    config.write_text(f"ros:\n  node_name: {name}\noutput:\n  root_dir: {tmp_path / 'output'}\n")
    return env, config, Path(env["DCD_TEST_READY"])


def launch(config, env, background=False):
    command = [sys.executable, "-m", "data_collect_dag", "run", "--config", str(config)]
    if background:
        command.append("--background")
    return subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_ready(path, pid=None):
    deadline = time.monotonic() + 8
    while not path.exists() or not path.read_text() or (pid is not None and path.read_text() != str(pid)):
        assert time.monotonic() < deadline, "collector did not become ready"
        time.sleep(0.02)
    return int(path.read_text())


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def stop(pid):
    if alive(pid):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while alive(pid):
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.mark.parametrize("first_background", [False, True])
@pytest.mark.parametrize("second_background", [False, True])
def test_foreground_background_duplicates_and_release(instance_env, first_background, second_background):
    env, config, ready = instance_env
    first = launch(config, env, first_background)
    pid = None
    try:
        if first_background:
            stdout, stderr = first.communicate(timeout=8)
            assert first.returncode == 0, stderr
            pid = int(re.search(r"PID=(\d+)", stdout).group(1))
        else:
            pid = first.pid
        wait_ready(ready, pid)
        # A different config and output directory cannot bypass the lock.
        second_config = config.with_name("other.yaml")
        second_config.write_text(config.read_text().replace("root_dir:", "unused:") + "\n")
        second = launch(second_config, env, second_background)
        stdout, stderr = second.communicate(timeout=8)
        assert second.returncode == 1 and "is already running" in stderr
        assert not stdout and alive(pid)
        # SIGKILL cannot run any explicit unlock code.
        stop(pid)
        if not first_background:
            first.wait(timeout=5)
        restarted = launch(config, env)
        try:
            wait_ready(ready, restarted.pid)
        finally:
            stop(restarted.pid)
            restarted.wait(timeout=5)
    finally:
        if pid is not None:
            stop(pid)
        if first.poll() is None:
            first.kill()
        first.wait(timeout=5)


def test_concurrent_cli_start_allows_exactly_one_instance(instance_env):
    env, config, ready = instance_env
    processes = [launch(config, env, True) for _ in range(4)]
    winners = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            if process.returncode == 0:
                winners.append(int(re.search(r"PID=(\d+)", stdout).group(1)))
            else:
                assert process.returncode == 1 and "is already running" in stderr
        assert len(winners) == 1
        wait_ready(ready, winners[0])
    finally:
        for pid in winners:
            stop(pid)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_different_nodes_can_run_together(instance_env):
    env, config, ready = instance_env
    other = config.with_name("other.yaml")
    other.write_text(config.read_text().replace("process_test_", "other_test_"))
    other_env = dict(env, DCD_TEST_READY=str(ready.with_name("other_ready")))
    processes = [launch(config, env), launch(other, other_env)]
    try:
        wait_ready(ready, processes[0].pid)
        wait_ready(Path(other_env["DCD_TEST_READY"]), processes[1].pid)
        assert all(process.poll() is None for process in processes)
    finally:
        for process in processes:
            stop(process.pid)
            process.wait(timeout=5)


def test_failed_initialization_releases_lock(instance_env):
    env, config, ready = instance_env
    failed = launch(config, dict(env, DCD_TEST_FAIL="1"))
    _stdout, stderr = failed.communicate(timeout=8)
    assert failed.returncode == 1 and "initialization failure" in stderr
    restarted = launch(config, env)
    try:
        wait_ready(ready, restarted.pid)
    finally:
        stop(restarted.pid)
        restarted.wait(timeout=5)


def test_control_client_imports_no_collection_or_inference_modules(tmp_path):
    code = '''
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("data_collect_dag.app", "data_collect_dag.config",
                                "data_collect_dag.session", "data_collect_dag.dag",
                                "data_collect_dag.nodes", "data_collect_dag.models",
                                "numpy", "cv2", "torch", "ultralytics", "onnx")):
            raise AssertionError("unexpected runtime import: " + fullname)
sys.meta_path.insert(0, Block())
from data_collect_dag import cli, ros_control_client
ros_control_client.run_control_command = lambda *args, **kwargs: None
sys.argv = ["data_collect_dag", "start", "fps"]
assert cli.main() == 0
'''
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
