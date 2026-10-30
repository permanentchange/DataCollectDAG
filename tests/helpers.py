"""Small shared fixtures and Linux process helpers; no runtime imports."""

import os
from pathlib import Path
import signal
import time
from typing import Any, Callable, Dict

import yaml


def minimal_config(root: Path) -> Dict[str, Any]:
    calibration = root / "calibration.json"
    calibration.write_text('{"images": {}, "pointclouds": {}}')
    return {
        "calibration": {"path": str(calibration)},
        "output": {"root_dir": str(root / "output")},
        "ros": {"topics": {"camera": {
            "topic": "/camera", "msg_type": "sensor_msgs/Image", "role": "image",
        }}},
        "cache": {"defaults_by_role": {"image": {"max_frames": 2, "max_age_sec": 1}}},
        "pipelines": {"init": {
            "main_source": "camera",
            "nodes": [{"node_id": "start", "type": "start"}, {"node_id": "end", "type": "end"}],
            "edges": [["start", "end"]],
        }},
    }


def write_config(root: Path, payload: Dict[str, Any]) -> Path:
    path = root / "cfg.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0,
               message: str = "condition did not become true") -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        time.sleep(0.01)


def process_alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def terminate_process(pid: int, timeout: float = 5.0) -> None:
    try:
        if process_alive(pid):
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + timeout
            while process_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.02)
        if process_alive(pid):
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    wait_until(lambda: not process_alive(pid), timeout, f"process {pid} did not exit")
