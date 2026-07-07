from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

from data_collect_dag.models import CachePolicy, FrameLike


@dataclass
class CachedFrame:
    frame: FrameLike

    @property
    def source_timestamp_ns(self) -> int:
        return int(self.frame.meta.source_timestamp_ns)

    @property
    def receive_timestamp_ns(self) -> int:
        return int(self.frame.meta.receive_timestamp_ns)


class SessionInputCache:
    def __init__(self, policies: Dict[str, CachePolicy]) -> None:
        self._policies = dict(policies)
        self._buffers: Dict[str, Deque[CachedFrame]] = defaultdict(deque)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    def append(self, topic_key: str, frame: FrameLike) -> List[str]:
        dropped_reasons: List[str] = []
        with self._condition:
            policy = self._policies[topic_key]
            buffer = self._buffers[topic_key]
            cutoff_ns = int(frame.meta.receive_timestamp_ns - policy.max_age_sec * 1_000_000_000)
            while buffer and buffer[0].receive_timestamp_ns < cutoff_ns:
                buffer.popleft()
                dropped_reasons.append("cache_age_expired")
            buffer.append(CachedFrame(frame))
            while len(buffer) > policy.max_frames:
                buffer.popleft()
                dropped_reasons.append("cache_max_frames")
            self._condition.notify_all()
        return dropped_reasons

    def query_nearest(self, topic_key: str, timestamp_ns: int, max_time_diff_ms: float) -> Optional[FrameLike]:
        with self._lock:
            best = None
            best_abs = None
            limit_ns = int(max_time_diff_ms * 1_000_000)
            for item in self._buffers.get(topic_key, ()):
                delta = abs(item.source_timestamp_ns - int(timestamp_ns))
                if delta > limit_ns:
                    continue
                if best is None or delta < best_abs:
                    best = item.frame
                    best_abs = delta
            return best

    def query_latest_before(self, topic_key: str, timestamp_ns: int, max_age_sec: float) -> Optional[FrameLike]:
        with self._lock:
            min_ts = int(timestamp_ns - max_age_sec * 1_000_000_000)
            best = None
            for item in self._buffers.get(topic_key, ()):
                if min_ts <= item.source_timestamp_ns <= int(timestamp_ns):
                    best = item.frame
            return best

    def query_range(self, topic_key: str, start_time_ns: int, end_time_ns: int) -> List[FrameLike]:
        with self._lock:
            return [
                item.frame
                for item in self._buffers.get(topic_key, ())
                if int(start_time_ns) <= item.source_timestamp_ns <= int(end_time_ns)
            ]

    def wait_nearest(self, topic_key: str, timestamp_ns: int, max_time_diff_ms: float, timeout_ms: int) -> Optional[FrameLike]:
        deadline = time.monotonic() + float(timeout_ms) / 1000.0
        with self._condition:
            while True:
                matched = self.query_nearest(topic_key, timestamp_ns, max_time_diff_ms)
                if matched is not None:
                    return matched
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)

