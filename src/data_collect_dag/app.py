from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict

from data_collect_dag.camera_calibration import read_distortion_params
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
    for topic, camera_params in images.items():
        try:
            read_distortion_params(camera_params)
        except ValueError as exc:
            raise ValueError(f"invalid camera calibration for {topic}: {exc}") from exc
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
        self.status_manager = StatusManager(disk_usage_path=self.app_config.output_root_dir)
        self.status_manager.update_storage(None, self.app_config.disk_storage_limit_gb, self.app_config.disk_storage_need_gb)
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
        self.ros_adapter.bind_app(
            status_callback=self.status_manager.snapshot,
            start_callback=self._handle_start_command,
            stop_callback=self._handle_stop_command,
            pause_callback=self._handle_pause_command,
            resume_callback=self._handle_resume_command,
            shutdown_callback=self._handle_shutdown_command,
        )

    def run(self) -> int:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        self.logger.info("starting app runtime config=%s pipeline=%s", self.app_config.config_path, self.pipeline_name)
        self.ros_adapter.start()
        self._control_thread.start()
        self._submit_command(ControlCommand(kind="start", pipeline_name=self.pipeline_name))
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

    def _handle_shutdown_command(self) -> None:
        self.logger.info("received shutdown command")
        self._submit_command(ControlCommand(kind="shutdown", reason="external_shutdown"))

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

    def _dispatch_command(self, command: ControlCommand) -> None:
        if command.kind == "start":
            self.status_manager.set_last_error(None)
            self.session_manager.start(command.pipeline_name or self.pipeline_name)
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
            try:
                self.session_manager.stop(reason=command.reason or "external_stop", status=RecentSessionStatus.STOPPED)
            finally:
                self._stop_event.set()
                self._control_stop_event.set()
            return
        raise ValueError(f"unknown control command: {command.kind}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Data collection DAG demo runtime")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--init-demo", type=Path, metavar="DIR", help="export bundled demo templates and exit (no ROS needed)")
    parser.add_argument("--force", action="store_true", help="overwrite template files; only valid with --init-demo")
    parser.add_argument("--pipeline", default='init')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--clean", type=_nonnegative_days, metavar="DAY", help="delete expired session directories and exit (0: all, 1: keep today)")
    mode.add_argument("--background", action="store_true", help="run in the background; print PID and log path, then exit")
    return parser


def _nonnegative_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--clean must be a non-negative integer")
    if days < 0:
        raise argparse.ArgumentTypeError("--clean must be a non-negative integer")
    return days
