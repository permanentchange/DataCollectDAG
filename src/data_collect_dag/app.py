from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from data_collect_dag.config import load_app_config
from data_collect_dag.control import ControlCommand, ControlCommandQueue
from data_collect_dag.logging_utils import configure_logging
from data_collect_dag.models import RecentSessionStatus
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
        self.command_queue = ControlCommandQueue()
        self.session_manager = SessionManager(
            self.app_config,
            self.calibration,
            self.ros_adapter,
            self.status_manager,
            command_callback=self._submit_command,
        )
        self._stop_event = threading.Event()
        self._control_stop_event = threading.Event()
        self._control_thread = threading.Thread(target=self._control_loop, name="control-thread", daemon=True)
        self._initial_start_event = threading.Event()
        self._initial_start_error: Optional[Exception] = None
        self.ros_adapter.bind_app(
            status_callback=self.status_manager.snapshot,
            start_callback=self._handle_start_command,
            stop_callback=self._handle_stop_command,
            pause_callback=self._handle_pause_command,
            resume_callback=self._handle_resume_command,
        )

    def run(self) -> int:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        self.logger.info("starting app runtime config=%s pipeline=%s", self.app_config.config_path, self.pipeline_name)
        self.ros_adapter.start()
        self._control_thread.start()
        self._submit_command(ControlCommand(kind="start", pipeline_name=self.pipeline_name, reason="initial_start"))
        self._initial_start_event.wait()
        if self._initial_start_error is not None:
            self._control_stop_event.set()
            self._control_thread.join(timeout=1.0)
            self.ros_adapter.stop()
            raise self._initial_start_error
        while not self._stop_event.is_set():
            time.sleep(0.2)
        self._control_stop_event.set()
        self._control_thread.join(timeout=1.0)
        self.ros_adapter.stop()
        self.logger.info("app runtime stopped")
        return 0

    def _handle_signal(self, signum, frame) -> None:
        self.logger.info("received signal=%s", signum)
        self._submit_command(ControlCommand(kind="shutdown", reason=f"signal_{signum}"))

    def _handle_start_command(self, pipeline_name: str) -> None:
        target = pipeline_name or self.pipeline_name
        self.logger.info("received start command target=%s", target)
        self._submit_command(ControlCommand(kind="start", pipeline_name=target))

    def _handle_stop_command(self) -> None:
        self.logger.info("received stop command")
        self._submit_command(ControlCommand(kind="stop", reason="external_stop", end_status=RecentSessionStatus.STOPPED.value))

    def _handle_pause_command(self) -> None:
        self.logger.info("received pause command")
        self._submit_command(ControlCommand(kind="pause"))

    def _handle_resume_command(self) -> None:
        self.logger.info("received resume command")
        self._submit_command(ControlCommand(kind="resume"))

    def _submit_command(self, command: ControlCommand) -> None:
        self.command_queue.put(command)

    def _control_loop(self) -> None:
        while not self._control_stop_event.is_set():
            try:
                command = self.command_queue.get(timeout=0.2)
            except Exception:
                continue
            try:
                self._dispatch_command(command)
            except Exception as exc:
                self.logger.exception("control command failed kind=%s", command.kind)
                self.status_manager.set_last_error(str(exc))
                if command.reason == "initial_start":
                    self._initial_start_error = exc
                    self._initial_start_event.set()

    def _dispatch_command(self, command: ControlCommand) -> None:
        if command.kind == "start":
            try:
                self.session_manager.start(command.pipeline_name or self.pipeline_name)
            finally:
                if command.reason == "initial_start":
                    self._initial_start_event.set()
            return
        if command.kind == "stop":
            self.session_manager.stop(
                reason=command.reason or "external_stop",
                status=RecentSessionStatus(command.end_status or RecentSessionStatus.STOPPED.value),
            )
            return
        if command.kind == "pause":
            self.session_manager.pause()
            return
        if command.kind == "resume":
            self.session_manager.resume()
            return
        if command.kind == "complete":
            active_session = self.session_manager.active_session
            if active_session is None or active_session.session_id != command.session_id:
                self.logger.info("ignoring stale complete command session_id=%s", command.session_id)
                return
            self.session_manager.stop(
                reason=command.reason or "completed",
                status=RecentSessionStatus(command.end_status or RecentSessionStatus.COMPLETED.value),
            )
            return
        if command.kind == "shutdown":
            self.session_manager.stop(reason=command.reason or "external_stop", status=RecentSessionStatus.STOPPED)
            self._stop_event.set()
            self._control_stop_event.set()
            return
        raise ValueError(f"unknown control command: {command.kind}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Data collection DAG demo runtime")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--pipeline", required=True)
    return parser
