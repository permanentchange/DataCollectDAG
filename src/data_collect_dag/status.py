from __future__ import annotations

import threading
from typing import Optional

from data_collect_dag.models import RecentSessionStatus, SessionMetrics, StatusSnapshot, ToolState


class StatusManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = StatusSnapshot()

    def set_running(self, session_id: str, pipeline_name: str, start_time: str) -> None:
        with self._lock:
            self._snapshot.tool_state = ToolState.RUNNING
            self._snapshot.current_session_id = session_id
            self._snapshot.current_pipeline_name = pipeline_name
            self._snapshot.start_time = start_time

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

    def snapshot(self) -> StatusSnapshot:
        with self._lock:
            cloned = StatusSnapshot()
            cloned.__dict__.update(self._snapshot.__dict__)
            return cloned
