from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ControlCommand:
    kind: str
    pipeline_name: Optional[str] = None
    session_id: Optional[str] = None
    reason: Optional[str] = None
    end_status: Optional[str] = None
    params: Dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.params is None:
            self.params = {}
