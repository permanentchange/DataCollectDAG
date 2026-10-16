"""Exercise real process detachment with a local fake ROS transport."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
FAKE_ROS = r'''
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from data_collect_dag.ros_adapter import RosAdapter

original_stop = RosAdapter.stop

def start(self):
    self._test_stop = threading.Event()
    print("fake ROS stdout", flush=True)
    print("fake ROS stderr", file=sys.stderr, flush=True)
    def pump():
        ready = Path(os.environ["DCD_TEST_READY"])
        control = Path(os.environ["DCD_TEST_CONTROL"])
        while not self._test_stop.wait(0.01):
            snapshot = self._status_callback()
            if snapshot.tool_state.value == "RUNNING" and not ready.exists():
                ready.write_text(json.dumps({
                    "pid": os.getpid(), "sid": os.getsid(0), "cwd": os.getcwd(),
                    "env": os.environ["DCD_TEST_ENV"],
                    "session_id": snapshot.current_session_id,
                }))
            if control.exists():
                self._on_control_topic(SimpleNamespace(data=control.read_text()))
                return
    self._test_thread = threading.Thread(target=pump, daemon=True)
    self._test_thread.start()

def stop(self):
    self._test_stop.set()
    self._test_thread.join(2)
    original_stop(self)

RosAdapter.start = start
RosAdapter.stop = stop
'''


def _is_live(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


def _wait(predicate, log):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail(f"background process timed out; log:\n{log.read_text() if log.exists() else 'missing'}")
        time.sleep(0.01)


def _launch(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(FAKE_ROS)
    calibration = tmp_path / "calibration.json"
    calibration.write_text('{"images": {}, "pointclouds": {}}')
    payload = {
        "output": {"root_dir": "output"},
        "calibration": {"path": "calibration.json"},
        "ros": {"control": {"control_topic": "/data_collect/control"}, "topics": {
            "camera": {"topic": "/camera", "msg_type": "sensor_msgs/Image", "role": "image"},
        }},
        "cache": {"defaults_by_role": {"image": {"max_frames": 10, "max_age_sec": 1}}},
        "pipelines": {"init": {
            "main_source": "camera",
            "nodes": [{"node_id": "start", "type": "start"}, {"node_id": "end", "type": "end"}],
            "edges": [["start", "end"]],
        }},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(payload))
    root = tmp_path / "output"
    root.mkdir()
    log = root / "background.log"
    log.write_text("previous launch\n")
    ready = tmp_path / "ready.json"
    control = tmp_path / "control.json"
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": os.pathsep.join([str(hooks), str(ROOT / "src")]),
        "DCD_TEST_READY": str(ready), "DCD_TEST_CONTROL": str(control), "DCD_TEST_ENV": "inherited",
    })
    launcher = subprocess.run(
        [sys.executable, "-m", "data_collect_dag", "run", "launch", "--config", "config.yaml", "--background"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=5,
    )
    assert launcher.returncode == 0, launcher.stderr
    pid = int(re.search(r"PID=(\d+)", launcher.stdout).group(1))
    return pid, root, log, ready, control


@pytest.mark.skipif(sys.platform != "linux", reason="ROS Noetic target; checks Linux process state")
@pytest.mark.parametrize("shutdown_mode", ["topic", "sigterm"])
def test_background_survives_launcher_and_exits_cleanly(tmp_path, shutdown_mode):
    pid, root, log, ready, control = _launch(tmp_path)
    try:
        _wait(lambda: ready.exists() and ready.stat().st_size > 0, log)
        info = json.loads(ready.read_text())
        assert info["pid"] == pid and info["sid"] == pid
        assert info["cwd"] == str(tmp_path) and info["env"] == "inherited"
        assert _is_live(pid)
        assert log.read_text().startswith("previous launch\n")
        assert "fake ROS stdout" in log.read_text() and "fake ROS stderr" in log.read_text()
        if shutdown_mode == "topic":
            control.write_text('{"command":"shutdown"}')
            reason = "external_shutdown"
        else:
            os.kill(pid, signal.SIGTERM)
            reason = f"signal_{signal.SIGTERM}"
        _wait(lambda: not _is_live(pid), log)
        summary = json.loads((root / info["session_id"] / "session_summary.json").read_text())
        assert summary["end_status"] == "STOPPED" and summary["end_reason"] == reason
        assert (root / info["session_id"] / "saved_samples.json").exists()
        assert "app runtime stopped" in log.read_text()
    finally:
        if _is_live(pid):
            os.kill(pid, signal.SIGTERM)
            try:
                _wait(lambda: not _is_live(pid), log)
            finally:
                if _is_live(pid):
                    os.kill(pid, signal.SIGKILL)
