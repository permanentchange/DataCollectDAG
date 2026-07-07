from __future__ import annotations

import threading
from typing import Any

from data_collect_dag.models import NodeResult, SessionMetrics


class MetricsRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.metrics = SessionMetrics()

    def received_message(self, topic_key: str) -> None:
        with self._lock:
            self.metrics.received_messages[topic_key] += 1

    def cache_dropped(self, topic_key: str, reason: str) -> None:
        with self._lock:
            self.metrics.cache_dropped_messages[topic_key] += 1
            self.metrics.drop_reasons[reason] += 1

    def main_frame_event(self) -> None:
        with self._lock:
            self.metrics.main_frame_events += 1

    def main_frame_event_dropped(self, reason: str) -> None:
        with self._lock:
            self.metrics.main_frame_events_dropped += 1
            self.metrics.drop_reasons[reason] += 1

    def sample_started(self) -> None:
        with self._lock:
            self.metrics.samples_started += 1

    def sample_finished(self, result: NodeResult, reason: str = "") -> None:
        with self._lock:
            if result == NodeResult.OK:
                return
            if result == NodeResult.SKIP_SAMPLE:
                self.metrics.samples_skipped += 1
                if reason:
                    self.metrics.skip_reasons[reason] += 1
            elif result == NodeResult.FAIL_SAMPLE:
                self.metrics.samples_failed += 1
                if reason:
                    self.metrics.fail_reasons[reason] += 1
            elif result == NodeResult.CANCEL_SESSION:
                self.metrics.samples_canceled += 1
            elif result == NodeResult.FAIL_SESSION:
                self.metrics.samples_failed += 1
                if reason:
                    self.metrics.fail_reasons[reason] += 1

    def sample_saved(self) -> None:
        with self._lock:
            self.metrics.samples_saved += 1

    def warning(self) -> None:
        with self._lock:
            self.metrics.warnings += 1

    def error(self) -> None:
        with self._lock:
            self.metrics.errors += 1

    def save_output(self, path: str) -> None:
        with self._lock:
            self.metrics.save_outputs.append(path)

