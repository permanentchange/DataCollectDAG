from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict

from data_collect_dag.config import load_app_config
from data_collect_dag.logging_utils import configure_logging
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import SessionManager
from data_collect_dag.status import StatusManager


def load_calibration(calibration_path: Path) -> Dict[str, Any]:
    payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    images = payload.get("images") or {}
    pointclouds = payload.get("pointclouds") or {}
    normalized_pointclouds = {
        topic: entry["sensor2base"]
        for topic, entry in pointclouds.items()
        if "sensor2base" in entry
    }
    return {"images": images, "pointclouds": normalized_pointclouds}


class AppRuntime:
    def __init__(self, config_path: Path, pipeline_name: str) -> None:
        self.app_config = load_app_config(config_path)
        configure_logging("DEBUG" if self.app_config.runtime.debug else None)
        self.logger = logging.getLogger("data_collect_dag.app")
        self.pipeline_name = pipeline_name
        if self.app_config.calibration_path is None:
            raise ValueError("calibration.path is required")
        self.status_manager = StatusManager()
        self.ros_adapter = RosAdapter(self.app_config.topics, self.app_config.ros_node_name, self.app_config.control)
        self.calibration = load_calibration(self.app_config.calibration_path)
        self.session_manager = SessionManager(self.app_config, self.calibration, self.ros_adapter, self.status_manager)
        self._stop_event = threading.Event()
        self.ros_adapter.bind_app(
            status_callback=self.status_manager.snapshot,
            start_callback=self._handle_start_command,
            stop_callback=self._handle_stop_command,
        )

    def run(self) -> int:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        self.logger.info("starting app runtime config=%s pipeline=%s", self.app_config.config_path, self.pipeline_name)
        self.ros_adapter.start()
        self.session_manager.start(self.pipeline_name)
        while not self._stop_event.is_set():
            time.sleep(0.2)
        self.session_manager.stop()
        self.ros_adapter.stop()
        self.logger.info("app runtime stopped")
        return 0

    def _handle_signal(self, signum, frame) -> None:
        self.logger.info("received signal=%s", signum)
        self._stop_event.set()

    def _handle_start_command(self, pipeline_name: str) -> None:
        target = pipeline_name or self.pipeline_name
        self.logger.info("received start command target=%s", target)
        self.session_manager.start(target)

    def _handle_stop_command(self) -> None:
        self.logger.info("received stop command")
        self.session_manager.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Data collection DAG demo runtime")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--pipeline", required=True)
    return parser
