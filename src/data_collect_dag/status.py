from __future__ import annotations

from pathlib import Path
import shutil
import threading
from typing import Optional

from data_collect_dag.models import RecentSessionStatus, SessionMetrics, StatusSnapshot, ToolState


class StatusManager:
    def __init__(self, disk_usage_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._snapshot = StatusSnapshot()
        self._disk_usage_path = disk_usage_path

    def set_running(self, session_id: str, pipeline_name: str, start_time: str) -> None:
        with self._lock:
            self._snapshot.tool_state = ToolState.RUNNING
            self._snapshot.current_session_id = session_id
            self._snapshot.current_pipeline_name = pipeline_name
            self._snapshot.start_time = start_time
            self._snapshot.last_error = None
            self._snapshot.warnings = 0
            self._snapshot.received_messages = {}
            self._snapshot.main_frame_events = 0
            self._snapshot.samples_saved = 0
            self._snapshot.samples_skipped = 0
            self._snapshot.samples_failed = 0
            self._snapshot.samples_canceled = 0
            self._snapshot.drop_reasons = {}
            self._snapshot.skip_reasons = {}
            self._snapshot.fail_reasons = {}

    def set_paused(self) -> None:
        with self._lock:
            self._snapshot.tool_state = ToolState.PAUSED

    def set_resumed(self) -> None:
        with self._lock:
            self._snapshot.tool_state = ToolState.RUNNING

    def set_idle(self, recent_status: RecentSessionStatus) -> None:
        with self._lock:
            self._snapshot.tool_state = ToolState.IDLE
            self._snapshot.recent_session_status = recent_status
            self._snapshot.current_session_id = None
            self._snapshot.current_pipeline_name = None
            self._snapshot.start_time = None

    def update_metrics(self, metrics: SessionMetrics) -> None:
        with self._lock:
            self._snapshot.received_messages = dict(metrics.received_messages)
            self._snapshot.main_frame_events = metrics.main_frame_events
            self._snapshot.samples_saved = metrics.samples_saved
            self._snapshot.samples_skipped = metrics.samples_skipped
            self._snapshot.samples_failed = metrics.samples_failed
            self._snapshot.samples_canceled = metrics.samples_canceled
            self._snapshot.drop_reasons = dict(metrics.drop_reasons)
            self._snapshot.skip_reasons = dict(metrics.skip_reasons)
            self._snapshot.fail_reasons = dict(metrics.fail_reasons)
            self._snapshot.warnings = metrics.warnings

    def set_last_error(self, message: Optional[str]) -> None:
        with self._lock:
            self._snapshot.last_error = message

    def update_storage(self, size_bytes: Optional[int], limit_gb: Optional[float], need_gb: Optional[float]) -> None:
        with self._lock:
            self._snapshot.output_size_gb = round(size_bytes / 1_000_000_000, 2) if size_bytes is not None else None
            self._snapshot.disk_storage_limit_gb = limit_gb
            self._snapshot.disk_storage_need_gb = need_gb

    def snapshot(self) -> StatusSnapshot:
        with self._lock:
            cloned = StatusSnapshot()
            cloned.__dict__.update(self._snapshot.__dict__)
        cloned.disk_free_gb = self._get_disk_free_gb()
        return cloned

    def _get_disk_free_gb(self) -> Optional[float]:
        if self._disk_usage_path is None:
            return None
        try:
            usage_path = self._nearest_existing_path(self._disk_usage_path)
            usage = shutil.disk_usage(usage_path)
        except OSError:
            return None
        return round(usage.free / 1_000_000_000, 2)

    def _nearest_existing_path(self, path: Path) -> Path:
        candidate = path
        while not candidate.exists():
            parent = candidate.parent
            if parent == candidate:
                return candidate
            candidate = parent
        return candidate
