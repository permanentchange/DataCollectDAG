from __future__ import annotations

import enum
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()) + f".{int(time.time_ns() % 1_000_000_000):09d}"


def make_session_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime()) + "_" + uuid.uuid4().hex[:8]


class ToolState(str, enum.Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class RecentSessionStatus(str, enum.Enum):
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NodeResult(str, enum.Enum):
    OK = "OK"
    SKIP_SAMPLE = "SKIP_SAMPLE"
    FAIL_SAMPLE = "FAIL_SAMPLE"
    FAIL_SESSION = "FAIL_SESSION"
    CANCEL_SESSION = "CANCEL_SESSION"


@dataclass(frozen=True)
class FrameMeta:
    topic_key: str
    ros_topic: str
    sensor_name: str
    role: str
    source_timestamp_ns: int
    receive_timestamp_ns: int
    frame_id: str = ""


@dataclass(frozen=True)
class ImageFrame:
    meta: FrameMeta
    image_bgr: Optional[np.ndarray]
    encoding: str
    width: int
    height: int
    raw_msg_ref: Any = None

    def replace(self, *, image_bgr: Optional[np.ndarray] = None) -> "ImageFrame":
        return ImageFrame(
            meta=self.meta,
            image_bgr=self.image_bgr if image_bgr is None else image_bgr,
            encoding=self.encoding,
            width=self.width,
            height=self.height,
            raw_msg_ref=self.raw_msg_ref,
        )


@dataclass(frozen=True)
class PointCloudFrame:
    meta: FrameMeta
    points_xyz: Optional[np.ndarray]
    point_timestamps_ns: Optional[np.ndarray] = None
    intensities: Optional[np.ndarray] = None
    ring: Optional[np.ndarray] = None
    frame_name: str = ""
    raw_msg_ref: Any = None

    def replace(
        self,
        *,
        points_xyz: Optional[np.ndarray] = None,
        point_timestamps_ns: Optional[np.ndarray] = None,
        frame_name: Optional[str] = None,
    ) -> "PointCloudFrame":
        return PointCloudFrame(
            meta=self.meta,
            points_xyz=self.points_xyz if points_xyz is None else points_xyz,
            point_timestamps_ns=self.point_timestamps_ns if point_timestamps_ns is None else point_timestamps_ns,
            intensities=self.intensities,
            ring=self.ring,
            frame_name=self.frame_name if frame_name is None else frame_name,
            raw_msg_ref=self.raw_msg_ref,
        )


@dataclass(frozen=True)
class LocalizationFrame:
    meta: FrameMeta
    position_xyz: np.ndarray
    quaternion_xyzw: np.ndarray
    linear_velocity_xyz: np.ndarray
    angular_velocity_xyz: np.ndarray
    status: int = 0
    state: str = ""
    raw_msg_ref: Any = None


@dataclass(frozen=True)
class ImuFrame:
    meta: FrameMeta
    linear_acceleration_xyz: np.ndarray
    angular_velocity_xyz: np.ndarray
    quaternion_xyzw: np.ndarray
    raw_msg_ref: Any = None


@dataclass(frozen=True)
class OdometryFrame:
    meta: FrameMeta
    position_xyz: np.ndarray
    quaternion_xyzw: np.ndarray
    linear_velocity_xyz: np.ndarray
    angular_velocity_xyz: np.ndarray
    raw_msg_ref: Any = None


@dataclass(frozen=True)
class TextFrame:
    meta: FrameMeta
    text: str
    raw_msg_ref: Any = None


FrameLike = Any


@dataclass
class MainFrameEvent:
    main_frame: FrameLike
    ready_at_monotonic: float = 0.0


@dataclass
class NodeOutcome:
    status: NodeResult
    reason: str = ""
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TopicConfig:
    topic_key: str
    topic: str
    msg_type: str
    role: str
    sensor_name: str


@dataclass
class CachePolicy:
    max_frames: int
    max_age_sec: float


@dataclass
class NodeConfig:
    node_id: str
    node_type: str
    inputs: Dict[str, str]
    outputs: Dict[str, str]
    config: Dict[str, Any]


@dataclass
class PipelineDefinition:
    name: str
    main_source: str
    nodes_by_id: Dict[str, NodeConfig]
    predecessors: Dict[str, List[str]]
    successors: Dict[str, List[str]]
    start_node_id: str
    end_node_id: str
    stop_conditions: "StopConditions" = None


@dataclass
class StopConditions:
    max_duration_sec: Optional[float] = None
    max_saved_samples: Optional[int] = None


@dataclass
class RuntimeConfig:
    debug: bool = False
    sample_workers: int = 1
    node_workers: int = 4
    main_frame_queue_size: int = 20
    main_frame_delay_ms: int = 0
    stop_timeout_sec: float = 5.0


@dataclass
class ControlConfig:
    start_topic: Optional[str] = None
    stop_topic: Optional[str] = None
    pause_topic: Optional[str] = None
    resume_topic: Optional[str] = None
    status_topic: Optional[str] = None
    start_service: Optional[str] = None
    stop_service: Optional[str] = None
    status_service: Optional[str] = None


@dataclass
class AppConfig:
    config_path: Path
    output_root_dir: Path
    ros_node_name: str
    control: ControlConfig
    topics: Dict[str, TopicConfig]
    cache_policies: Dict[str, CachePolicy]
    runtime: RuntimeConfig
    pipelines: Dict[str, PipelineDefinition]
    calibration_path: Optional[Path] = None


@dataclass
class SessionMetrics:
    received_messages: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cache_dropped_messages: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    main_frame_events: int = 0
    main_frame_events_dropped: int = 0
    samples_started: int = 0
    samples_saved: int = 0
    samples_skipped: int = 0
    samples_failed: int = 0
    samples_canceled: int = 0
    warnings: int = 0
    errors: int = 0
    drop_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skip_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    fail_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    save_outputs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "received_messages": dict(self.received_messages),
            "cache_dropped_messages": dict(self.cache_dropped_messages),
            "main_frame_events": self.main_frame_events,
            "main_frame_events_dropped": self.main_frame_events_dropped,
            "samples_started": self.samples_started,
            "samples_saved": self.samples_saved,
            "samples_skipped": self.samples_skipped,
            "samples_failed": self.samples_failed,
            "samples_canceled": self.samples_canceled,
            "warnings": self.warnings,
            "errors": self.errors,
            "drop_reasons": dict(self.drop_reasons),
            "skip_reasons": dict(self.skip_reasons),
            "fail_reasons": dict(self.fail_reasons),
        }


@dataclass
class SessionSummary:
    session_id: str
    pipeline_name: str
    start_time: str
    end_time: str
    end_status: str
    end_reason: str
    config_path: str
    session_root: str
    pipeline_params: Dict[str, Any]
    metrics: Dict[str, Any]
    save_outputs: List[str]
    last_error: Optional[str]
    warnings: List[str]


@dataclass
class StatusSnapshot:
    tool_state: ToolState = ToolState.IDLE
    recent_session_status: Optional[RecentSessionStatus] = None
    current_session_id: Optional[str] = None
    current_pipeline_name: Optional[str] = None
    start_time: Optional[str] = None
    last_error: Optional[str] = None
    warnings: int = 0
    received_messages: Dict[str, int] = field(default_factory=dict)
    main_frame_events: int = 0
    samples_saved: int = 0
    samples_skipped: int = 0
    samples_failed: int = 0
    samples_canceled: int = 0
    drop_reasons: Dict[str, int] = field(default_factory=dict)
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    fail_reasons: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_state": self.tool_state.value,
            "recent_session_status": self.recent_session_status.value if self.recent_session_status else None,
            "current_session_id": self.current_session_id,
            "current_pipeline_name": self.current_pipeline_name,
            "start_time": self.start_time,
            "last_error": self.last_error,
            "warnings": self.warnings,
            "received_messages": self.received_messages,
            "main_frame_events": self.main_frame_events,
            "samples_saved": self.samples_saved,
            "samples_skipped": self.samples_skipped,
            "samples_failed": self.samples_failed,
            "samples_canceled": self.samples_canceled,
            "drop_reasons": self.drop_reasons,
            "skip_reasons": self.skip_reasons,
            "fail_reasons": self.fail_reasons,
        }


class WarningCollector:
    def __init__(self) -> None:
        self._items: List[str] = []
        self._lock = threading.Lock()

    def add(self, message: str) -> None:
        with self._lock:
            self._items.append(message)

    def snapshot(self) -> List[str]:
        with self._lock:
            return list(self._items)
