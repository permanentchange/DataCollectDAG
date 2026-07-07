from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


class ContextError(KeyError):
    pass


class MissingContextKeyError(ContextError):
    pass


class AmbiguousContextKeyError(ContextError):
    pass


@dataclass
class SampleContext:
    sample_id: str
    data: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    cancel_event: Any = None

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def put(self, key: str, value: Any, producer: str) -> None:
        with self._lock:
            self.data.setdefault(key, {})[producer] = value

    def get(self, key: str, producer: Optional[str] = None) -> Any:
        with self._lock:
            if key not in self.data:
                raise MissingContextKeyError(key)
            producers = self.data[key]
            if producer is None:
                if len(producers) != 1:
                    raise AmbiguousContextKeyError(key)
                return next(iter(producers.values()))
            if producer not in producers:
                raise MissingContextKeyError(f"{key}:{producer}")
            return producers[producer]

    def has(self, key: str, producer: Optional[str] = None) -> bool:
        try:
            self.get(key, producer=producer)
            return True
        except ContextError:
            return False

    def remove(self, key: str, producer: Optional[str] = None) -> None:
        with self._lock:
            if key not in self.data:
                return
            if producer is None:
                self.data.pop(key, None)
                return
            self.data[key].pop(producer, None)
            if not self.data[key]:
                self.data.pop(key, None)

    def list_producers(self, key: str) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self.data.get(key, {}).keys())

