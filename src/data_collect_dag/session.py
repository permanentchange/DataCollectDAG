from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from data_collect_dag.cache import SessionInputCache
from data_collect_dag.control import ControlCommand
from data_collect_dag.dag import DagExecutor
from data_collect_dag.io_utils import write_json
from data_collect_dag.logging_utils import BACKGROUND_LOG_FILENAME, attach_session_log_file, configure_logging, detach_handler
from data_collect_dag.metrics import MetricsRecorder
from data_collect_dag.models import (
    AppConfig,
    MainFrameEvent,
    NodeResult,
    PipelineDefinition,
    RecentSessionStatus,
    SessionSummary,
    make_session_id,
    utc_now_iso,
)
from data_collect_dag.node_registry import load_node_type
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.sample_context import SampleContext
from data_collect_dag.status import StatusManager
from data_collect_dag.storage import StorageError, StorageTracker, check_disk_free
from data_collect_dag.summary import SummaryWriter


STORAGE_RECONCILE_INTERVAL_SEC = 30.0
STORAGE_LOG_INTERVAL_SEC = 1.0


class SessionRuntime:
    def __init__(
        self,
        app_config: AppConfig,
        pipeline: PipelineDefinition,
        calibration: Dict[str, Any],
        ros_adapter: RosAdapter,
        status_manager: StatusManager,
        command_callback: Callable[[ControlCommand], None],
    ) -> None:
        self.app_config = app_config
        self.pipeline = pipeline
        self.calibration = calibration
        self.ros_adapter = ros_adapter
        self.status_manager = status_manager
        self._command_callback = command_callback
        self.session_id = make_session_id()
        self.session_root = app_config.output_root_dir / self.session_id
        self.cache = SessionInputCache(app_config.cache_policies)
        self.metrics = MetricsRecorder()
        self.summary_writer = SummaryWriter()
        self.cancel_event = threading.Event()
        self.main_frame_queue: "queue.Queue[MainFrameEvent]" = queue.Queue(maxsize=app_config.runtime.main_frame_queue_size)
        self._workers = []
        self._nodes = {}
        self._dag = None
        self._node_pool = None
        self._warnings = []
        self._warnings_lock = threading.Lock()
        self._fps_gate_lock = threading.Lock()
        self._fps_gate_last_timestamp_ns: Dict[str, int] = {}
        self._last_error = None
        self._sample_records = []
        self._sample_records_lock = threading.Lock()
        self._start_time = utc_now_iso()
        self._end_status = RecentSessionStatus.STOPPED
        self._end_reason = "external_stop"
        self._active = False
        self._paused = False
        self._accepting_frames = False
        self._completion_requested = False
        self._active_elapsed_sec = 0.0
        self._run_started_monotonic = None
        self._state_lock = threading.RLock()
        self._state_condition = threading.Condition(self._state_lock)
        self._duration_thread = None
        self._log_handler = None
        self._storage_stop = threading.Event()
        self._storage_thread = None
        self._inflight_samples = 0
        self._storage_failure: Optional[StorageError] = None
        self._storage_completion_sent = False
        self.storage_tracker = (
            StorageTracker(app_config.output_root_dir, app_config.disk_storage_limit_gb, self._check_storage)
            if app_config.disk_storage_limit_gb else None
        )
        self.logger = logging.getLogger("data_collect_dag.session")

    def start(self) -> None:
        configure_logging("DEBUG" if self.app_config.runtime.debug else None)
        try:
            self._publish_storage()
            if self.storage_tracker is not None:
                self.storage_tracker.reconcile()
                self.storage_tracker.check()
            check_disk_free(self.app_config.output_root_dir, self.app_config.disk_storage_need_gb)
            self._publish_storage()
            self.session_root.mkdir(parents=True, exist_ok=False)
        except Exception as exc:
            self._last_error = str(exc)
            self.logger.exception("session admission or directory creation failed session_id=%s", self.session_id)
            self.status_manager.set_last_error(self._last_error)
            self.status_manager.set_idle(RecentSessionStatus.FAILED)
            raise
        try:
            self._log_handler = attach_session_log_file(self.session_root / "debug.log")
            self._precheck_topics()
            self._build_nodes()
            self._active = True
            self._accepting_frames = True
            self._run_started_monotonic = time.monotonic()
            self.status_manager.set_running(self.session_id, self.pipeline.name, self._start_time)
            self.ros_adapter.bind_session(self)
            self._workers = []
            configured_workers = self.app_config.runtime.sample_workers
            effective_workers = max(1, configured_workers)
            if configured_workers != effective_workers:
                self.logger.warning(
                    "sample_workers=%s is coerced to %s; update the config to avoid misleading runtime behavior",
                    configured_workers,
                    effective_workers,
                )
            self._log_risky_runtime_configuration()
            self.logger.info(
                "session started session_id=%s pipeline=%s debug=%s sample_workers=%s node_workers=%s queue_size=%s delay_ms=%s",
                self.session_id,
                self.pipeline.name,
                self.app_config.runtime.debug,
                effective_workers,
                self.app_config.runtime.node_workers,
                self.app_config.runtime.main_frame_queue_size,
                self.app_config.runtime.main_frame_delay_ms,
            )
            for worker_idx in range(effective_workers):
                worker = threading.Thread(
                    target=self._sample_loop,
                    name=f"sample-worker-{self.session_id}-{worker_idx}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)
            if self.pipeline.stop_conditions and self.pipeline.stop_conditions.max_duration_sec is not None:
                self._duration_thread = threading.Thread(
                    target=self._duration_monitor_loop,
                    name=f"duration-monitor-{self.session_id}",
                    daemon=True,
                )
                self._duration_thread.start()
            if self.storage_tracker is not None:
                self._storage_thread = threading.Thread(
                    target=self._storage_monitor_loop, name=f"storage-monitor-{self.session_id}", daemon=True,
                )
                self._storage_thread.start()
        except Exception as exc:
            self.logger.exception("session start failed session_id=%s", self.session_id)
            cleanup_errors: List[str] = []
            with self._state_condition:
                self._active = False
                self._accepting_frames = False
                self._paused = False
                self._completion_requested = True
                self._run_started_monotonic = None
                self._state_condition.notify_all()
            self.cancel_event.set()
            self._run_cleanup_step(cleanup_errors, "storage_monitor_shutdown", self._stop_storage_monitor)
            self._run_cleanup_step(cleanup_errors, "unbind_session", lambda: self.ros_adapter.unbind_session(self))
            for _ in self._workers:
                try:
                    self.main_frame_queue.put_nowait(MainFrameEvent(main_frame=None))
                except queue.Full:
                    pass
            for index, worker in enumerate(self._workers):
                self._run_cleanup_step(
                    cleanup_errors,
                    f"worker_join[{index}]",
                    lambda worker=worker: worker.join(timeout=self.app_config.runtime.stop_timeout_sec),
                )
            self._workers = []
            if self._duration_thread is not None:
                duration_thread = self._duration_thread
                self._run_cleanup_step(
                    cleanup_errors,
                    "duration_thread_join",
                    lambda: duration_thread.join(timeout=self.app_config.runtime.stop_timeout_sec),
                )
                self._duration_thread = None
            for node_id, node in reversed(list(self._nodes.items())):
                self._run_cleanup_step(cleanup_errors, f"node_teardown[{node_id}]", node.teardown)
            if self._node_pool is not None:
                node_pool = self._node_pool
                self._run_cleanup_step(cleanup_errors, "node_pool_shutdown", lambda: node_pool.shutdown(wait=True))
                self._node_pool = None
            if self._log_handler is not None:
                log_handler = self._log_handler
                self._run_cleanup_step(cleanup_errors, "detach_log_handler", lambda: detach_handler(log_handler))
                self._log_handler = None
            self._last_error = "; ".join([str(exc)] + cleanup_errors)
            summary = SessionSummary(
                session_id=self.session_id,
                pipeline_name=self.pipeline.name,
                start_time=self._start_time,
                end_time=utc_now_iso(),
                end_status=RecentSessionStatus.FAILED.value,
                end_reason="setup_failed",
                config_path=str(self.app_config.config_path),
                session_root=str(self.session_root),
                pipeline_params={},
                metrics=self.metrics.metrics.to_dict(),
                save_outputs=list(self.metrics.metrics.save_outputs),
                last_error=self._last_error,
                warnings=list(self._warnings),
            )
            try:
                self.summary_writer.write(self.session_root / "session_summary.json", summary)
            except Exception as summary_exc:
                self.logger.exception("failed to write failed-session summary session_id=%s", self.session_id)
                cleanup_errors.append(f"write_session_summary: {summary_exc}")
                self._last_error = "; ".join([str(exc)] + cleanup_errors)
            self._refresh_storage_metadata()
            self.status_manager.set_last_error(self._last_error)
            self.status_manager.set_idle(RecentSessionStatus.FAILED)
            if cleanup_errors:
                raise RuntimeError(self._last_error) from exc
            raise

    def _precheck_topics(self) -> None:
        if not self.pipeline.topics:
            return
        missing_topics = self.ros_adapter.wait_for_topic_messages(
            self.pipeline.topics,
            self.pipeline.time_delay_ms,
        )
        if missing_topics:
            missing = ", ".join(sorted(missing_topics))
            raise RuntimeError(
                f"topic precheck timed out after {self.pipeline.time_delay_ms} ms; missing topics: {missing}"
            )

    def stop(self, *, reason: str = "external_stop", status: RecentSessionStatus = RecentSessionStatus.STOPPED) -> None:
        self.logger.info("stopping session session_id=%s reason=%s status=%s", self.session_id, reason, status.value)
        self._end_reason = reason
        self._end_status = status
        cleanup_errors: List[str] = []
        with self._state_condition:
            self._accepting_frames = False
            self._paused = False
            self._completion_requested = True
            self._active = False
            if self._run_started_monotonic is not None:
                self._active_elapsed_sec += max(0.0, time.monotonic() - self._run_started_monotonic)
                self._run_started_monotonic = None
            self._state_condition.notify_all()
        self.cancel_event.set()
        self._run_cleanup_step(cleanup_errors, "storage_monitor_shutdown", self._stop_storage_monitor)
        self._run_cleanup_step(cleanup_errors, "unbind_session", lambda: self.ros_adapter.unbind_session(self))
        for _ in self._workers:
            try:
                self.main_frame_queue.put_nowait(MainFrameEvent(main_frame=None))
            except queue.Full:
                pass
        for index, worker in enumerate(self._workers):
            self._run_cleanup_step(
                cleanup_errors,
                f"worker_join[{index}]",
                lambda worker=worker: worker.join(timeout=self.app_config.runtime.stop_timeout_sec),
            )
        self._workers = []
        if self._duration_thread is not None:
            duration_thread = self._duration_thread
            self._run_cleanup_step(
                cleanup_errors,
                "duration_thread_join",
                lambda: duration_thread.join(timeout=self.app_config.runtime.stop_timeout_sec),
            )
            self._duration_thread = None
        for node_id, node in reversed(list(self._nodes.items())):
            self._run_cleanup_step(cleanup_errors, f"node_teardown[{node_id}]", node.teardown)
        if self._node_pool is not None:
            node_pool = self._node_pool
            self._run_cleanup_step(cleanup_errors, "node_pool_shutdown", lambda: node_pool.shutdown(wait=True))
            self._node_pool = None
        self._run_cleanup_step(cleanup_errors, "write_saved_samples", self._write_sample_records)
        if self._log_handler is not None:
            log_handler = self._log_handler
            self._run_cleanup_step(cleanup_errors, "detach_log_handler", lambda: detach_handler(log_handler))
            self._log_handler = None
        if cleanup_errors:
            self._end_status = RecentSessionStatus.FAILED
            self._end_reason = f"{reason}_failed"
            self._last_error = "; ".join(cleanup_errors)
        summary = SessionSummary(
            session_id=self.session_id,
            pipeline_name=self.pipeline.name,
            start_time=self._start_time,
            end_time=utc_now_iso(),
            end_status=self._end_status.value,
            end_reason=self._end_reason,
            config_path=str(self.app_config.config_path),
            session_root=str(self.session_root),
            pipeline_params={},
            metrics=self.metrics.metrics.to_dict(),
            save_outputs=list(self.metrics.metrics.save_outputs),
            last_error=self._last_error,
            warnings=list(self._warnings),
        )
        try:
            self.summary_writer.write(self.session_root / "session_summary.json", summary)
        except Exception as exc:
            self.logger.exception("failed to write session summary session_id=%s", self.session_id)
            cleanup_errors.append(f"write_session_summary: {exc}")
            self._end_status = RecentSessionStatus.FAILED
            self._end_reason = f"{reason}_failed"
            self._last_error = "; ".join(cleanup_errors)
        self.status_manager.update_metrics(self.metrics.metrics)
        self._refresh_storage_metadata()
        if self._last_error is not None:
            self.status_manager.set_last_error(self._last_error)
        self.status_manager.set_idle(self._end_status)
        self.logger.info(
            "session stopped session_id=%s samples_started=%s samples_saved=%s samples_skipped=%s samples_failed=%s",
            self.session_id,
            self.metrics.metrics.samples_started,
            self.metrics.metrics.samples_saved,
            self.metrics.metrics.samples_skipped,
            self.metrics.metrics.samples_failed,
        )
        if cleanup_errors:
            raise RuntimeError(self._last_error)

    def _run_cleanup_step(self, errors: List[str], stage: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:
            self.logger.exception("session cleanup failed session_id=%s stage=%s", self.session_id, stage)
            errors.append(f"{stage}: {exc}")

    def accept_frame(self, topic_key: str, frame: Any) -> None:
        with self._state_lock:
            if not self._active or self.cancel_event.is_set() or not self._accepting_frames or self._paused or self._completion_requested:
                return
        self.metrics.received_message(topic_key)
        dropped = self.cache.append(topic_key, frame)
        for reason in dropped:
            self.metrics.cache_dropped(topic_key, reason)
        self.status_manager.update_metrics(self.metrics.metrics)
        if topic_key == self.pipeline.main_source:
            ready_at = time.monotonic() + max(0.0, self.app_config.runtime.main_frame_delay_ms / 1000.0)
            self._enqueue_main_frame_event(MainFrameEvent(main_frame=frame, ready_at_monotonic=ready_at))
            self.status_manager.update_metrics(self.metrics.metrics)

    def get_fps_gate_last_timestamp_ns(self, gate_id: str) -> Optional[int]:
        with self._fps_gate_lock:
            return self._fps_gate_last_timestamp_ns.get(gate_id)

    def mark_fps_gate_timestamp_ns(self, gate_id: str, timestamp_ns: int) -> None:
        with self._fps_gate_lock:
            previous = self._fps_gate_last_timestamp_ns.get(gate_id)
            if previous is None or timestamp_ns > previous:
                self._fps_gate_last_timestamp_ns[gate_id] = timestamp_ns

    def _sample_loop(self) -> None:
        while not self.cancel_event.is_set():
            event = self.main_frame_queue.get()
            if event.main_frame is None:
                break
            remaining = event.ready_at_monotonic - time.monotonic()
            if remaining > 0:
                self.cancel_event.wait(timeout=remaining)
                if self.cancel_event.is_set():
                    break
            if not self._wait_until_sample_can_run():
                break
            self._check_storage()
            with self._state_condition:
                if self._completion_requested or self.cancel_event.is_set():
                    break
                self._inflight_samples += 1
            try:
                self._process_sample(event)
            finally:
                self._check_storage()
                with self._state_condition:
                    self._inflight_samples -= 1
                    self._finish_storage_stop_locked()

    def _process_sample(self, event: MainFrameEvent) -> None:
        self.metrics.sample_started()
        sample = SampleContext(sample_id=str(event.main_frame.meta.source_timestamp_ns), cancel_event=threading.Event())
        sample.put("main_frame", event.main_frame, "main_source")
        self.logger.debug(
            "sample start session_id=%s sample_id=%s queue_size=%s",
            self.session_id, sample.sample_id, self.main_frame_queue.qsize(),
        )
        outcome = self._dag.run_sample(sample)
        self._record_sample_warnings(sample)
        if sample.metadata.get("save_result", {}).get("saved"):
            self._append_sample_record(sample)
            self.metrics.sample_saved()
            self._check_saved_sample_limit()
        self.metrics.sample_finished(outcome.status, outcome.reason)
        self.status_manager.update_metrics(self.metrics.metrics)
        self.logger.debug(
            "sample finished session_id=%s sample_id=%s status=%s reason=%s saved=%s",
            self.session_id, sample.sample_id, outcome.status.value, outcome.reason,
            bool(sample.metadata.get("save_result", {}).get("saved")),
        )

    def _publish_storage(self) -> None:
        self.status_manager.update_storage(
            self.storage_tracker.size_bytes if self.storage_tracker is not None else None,
            self.app_config.disk_storage_limit_gb, self.app_config.disk_storage_need_gb,
        )

    def _refresh_storage_metadata(self) -> None:
        if self.storage_tracker is not None:
            for name in ("debug.log", "saved_samples.json", "session_summary.json"):
                self.storage_tracker.refresh(self.session_root / name)
            self.storage_tracker.refresh(self.app_config.output_root_dir / BACKGROUND_LOG_FILENAME)
        self._publish_storage()

    def _check_storage(self) -> None:
        if self.storage_tracker is None or self._storage_stop.is_set():
            return
        self._publish_storage()
        try:
            self.storage_tracker.check()
        except StorageError as exc:
            with self._state_condition:
                if not self._active or self.cancel_event.is_set() or self._storage_failure is not None:
                    return
                self._storage_failure = exc
                self._last_error = str(exc)
                self.status_manager.set_last_error(self._last_error)
                self._completion_requested = True
                self._accepting_frames = False
                self._state_condition.notify_all()
                self._finish_storage_stop_locked()

    def _finish_storage_stop_locked(self) -> None:
        if self._storage_failure is None or self._inflight_samples or self._storage_completion_sent or self.cancel_event.is_set():
            return
        self._storage_completion_sent = True
        self._command_callback(ControlCommand(
            kind="complete", session_id=self.session_id, reason=self._storage_failure.reason,
            end_status=RecentSessionStatus.FAILED.value,
        ))

    def _storage_monitor_loop(self) -> None:
        next_scan = time.monotonic() + STORAGE_RECONCILE_INTERVAL_SEC
        while not self._storage_stop.is_set():
            self.storage_tracker.refresh(self.session_root / "debug.log")
            self.storage_tracker.refresh(self.app_config.output_root_dir / BACKGROUND_LOG_FILENAME)
            if time.monotonic() >= next_scan:
                self.storage_tracker.reconcile()
                next_scan = time.monotonic() + STORAGE_RECONCILE_INTERVAL_SEC
            if self._storage_stop.wait(STORAGE_LOG_INTERVAL_SEC):
                break

    def _stop_storage_monitor(self) -> None:
        self._storage_stop.set()
        if self._storage_thread is not None:
            if self._storage_thread.is_alive():
                self._storage_thread.join(timeout=self.app_config.runtime.stop_timeout_sec)
            self._storage_thread = None

    def pause(self) -> None:
        with self._state_condition:
            if not self._active or self.cancel_event.is_set() or self._paused or self._completion_requested:
                return
            self._paused = True
            self._accepting_frames = False
            if self._run_started_monotonic is not None:
                self._active_elapsed_sec += max(0.0, time.monotonic() - self._run_started_monotonic)
                self._run_started_monotonic = None
            self._state_condition.notify_all()
        self.status_manager.set_paused()
        self.logger.info("session paused session_id=%s", self.session_id)

    def resume(self) -> None:
        with self._state_condition:
            if not self._active or self.cancel_event.is_set() or not self._paused or self._completion_requested:
                return
            self._paused = False
            self._accepting_frames = True
            self._run_started_monotonic = time.monotonic()
            self._state_condition.notify_all()
        self.status_manager.set_resumed()
        self.logger.info("session resumed session_id=%s", self.session_id)

    def _build_nodes(self) -> None:
        self._node_pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.app_config.runtime.node_workers)
        for node_config in self.pipeline.nodes_by_id.values():
            node_cls = load_node_type(node_config.node_type)
            node = node_cls(node_config, self)
            self._nodes[node_config.node_id] = node
            node.setup()
        self._dag = DagExecutor(self.pipeline, self._nodes, self._node_pool)
        self.logger.debug("built DAG nodes=%s", list(self._nodes))

    def _record_sample_warnings(self, sample: SampleContext) -> None:
        warnings = list(sample.metadata.get("node_warnings") or [])
        if not warnings:
            return
        with self._warnings_lock:
            self._warnings.extend(warnings)
        for _warning in warnings:
            self.metrics.warning()

    def _append_sample_record(self, sample: SampleContext) -> None:
        save_result = dict(sample.metadata.get("save_result") or {})
        motion = dict(sample.metadata.get("motion_compensation") or {})
        pointcloud_processing = dict(sample.metadata.get("pointcloud_processing") or {})
        aggregated = dict(sample.metadata.get("aggregated_pointcloud") or {})
        time_offsets = dict(sample.metadata.get("time_offsets_ms") or {})
        record = {
            "sample_id": sample.sample_id,
            "main_source_timestamp_ns": int(sample.get("main_frame").meta.source_timestamp_ns),
            "top_lidar_time_offset_ms": time_offsets.get("top_lidar"),
            "saved_camera_topics": list(save_result.get("saved_camera_topics") or []),
            "motion_compensation_applied": bool(motion.get("applied", False)),
            "motion_compensation_reference_time_ns": motion.get("reference_time_ns"),
            "pointcloud_in_base": aggregated.get("frame_name") == "base",
            "sensor_radius_crop_applied": any(
                entry.get("stage") == "sensor_radius_crop" and entry.get("applied")
                for entry in pointcloud_processing.values()
            ),
            "base_roi_crop_applied": any(
                entry.get("stage") == "base_roi_crop" and entry.get("applied")
                for entry in pointcloud_processing.values()
            ),
            "xtreme1_paths": {
                "camera_config": save_result.get("camera_config"),
                "images": list(save_result.get("images") or []),
                "pointcloud": save_result.get("pointcloud"),
            },
            "node_timings_ms": dict(sample.metadata.get("node_timings_ms") or {}),
        }
        with self._sample_records_lock:
            self._sample_records.append(record)

    def _write_sample_records(self) -> None:
        with self._sample_records_lock:
            records = list(self._sample_records)
        write_json(self.session_root / "saved_samples.json", {"items": records})

    def _enqueue_main_frame_event(self, event: MainFrameEvent) -> None:
        sample_id = getattr(getattr(event.main_frame, "meta", None), "source_timestamp_ns", "sentinel")
        try:
            self.main_frame_queue.put_nowait(event)
            self.metrics.main_frame_event()
            self.logger.debug(
                "queued main frame session_id=%s sample_id=%s queue_size=%s ready_delay_ms=%s",
                self.session_id,
                sample_id,
                self.main_frame_queue.qsize(),
                self.app_config.runtime.main_frame_delay_ms,
            )
            return
        except queue.Full:
            pass
        try:
            dropped_event = self.main_frame_queue.get_nowait()
        except queue.Empty:
            dropped_event = None
        if dropped_event is not None:
            dropped_sample_id = getattr(getattr(dropped_event.main_frame, "meta", None), "source_timestamp_ns", "sentinel")
            self.metrics.main_frame_event_dropped("main_frame_queue_drop_oldest")
            self.logger.warning(
                "main frame evicted session_id=%s dropped_sample_id=%s new_sample_id=%s queue_size=%s",
                self.session_id,
                dropped_sample_id,
                sample_id,
                self.main_frame_queue.qsize(),
            )
            try:
                self.main_frame_queue.put_nowait(event)
                self.metrics.main_frame_event()
                self.logger.debug(
                    "queued main frame session_id=%s sample_id=%s queue_size=%s ready_delay_ms=%s",
                    self.session_id,
                    sample_id,
                    self.main_frame_queue.qsize(),
                    self.app_config.runtime.main_frame_delay_ms,
                )
                return
            except queue.Full:
                pass
        self.logger.warning(
            "main frame eviction retry failed session_id=%s sample_id=%s queue_size=%s",
            self.session_id,
            sample_id,
            self.main_frame_queue.qsize(),
        )

    def _log_risky_runtime_configuration(self) -> None:
        delay_sec = max(0.0, self.app_config.runtime.main_frame_delay_ms / 1000.0)
        image_ages = [
            self.app_config.cache_policies[topic_key].max_age_sec
            for topic_key, topic in self.app_config.topics.items()
            if topic.role == "image"
        ]
        if image_ages and delay_sec > min(image_ages):
            self.logger.warning(
                "main_frame_delay_ms=%s exceeds the smallest image cache max_age_sec=%s; delayed samples may miss synced images",
                self.app_config.runtime.main_frame_delay_ms,
                min(image_ages),
            )

    def _wait_until_sample_can_run(self) -> bool:
        with self._state_condition:
            while self._paused and not self.cancel_event.is_set() and not self._completion_requested:
                self._state_condition.wait(timeout=0.2)
            return not self.cancel_event.is_set() and not self._completion_requested

    def _current_active_elapsed_sec(self) -> float:
        with self._state_lock:
            return self._current_active_elapsed_sec_locked()

    def _current_active_elapsed_sec_locked(self) -> float:
        elapsed = self._active_elapsed_sec
        if self._run_started_monotonic is not None:
            elapsed += max(0.0, time.monotonic() - self._run_started_monotonic)
        return elapsed

    def _duration_monitor_loop(self) -> None:
        max_duration_sec = self.pipeline.stop_conditions.max_duration_sec if self.pipeline.stop_conditions else None
        if max_duration_sec is None:
            return
        while not self.cancel_event.is_set():
            with self._state_condition:
                if self._completion_requested:
                    return
                if self._paused:
                    self._state_condition.wait(timeout=0.2)
                    continue
                remaining = max_duration_sec - self._current_active_elapsed_sec_locked()
                if remaining <= 0:
                    break
                self._state_condition.wait(timeout=min(remaining, 0.2))
        self._request_completion("max_duration_reached")

    def _check_saved_sample_limit(self) -> None:
        max_saved_samples = self.pipeline.stop_conditions.max_saved_samples if self.pipeline.stop_conditions else None
        if max_saved_samples is None:
            return
        if self.metrics.metrics.samples_saved >= max_saved_samples:
            self._request_completion("max_saved_samples_reached")

    def _request_completion(self, reason: str) -> None:
        with self._state_condition:
            if self._completion_requested or self.cancel_event.is_set():
                return
            self._completion_requested = True
            self._accepting_frames = False
            if self._run_started_monotonic is not None:
                self._active_elapsed_sec += max(0.0, time.monotonic() - self._run_started_monotonic)
                self._run_started_monotonic = None
            self._state_condition.notify_all()
        self._command_callback(
            ControlCommand(
                kind="complete",
                session_id=self.session_id,
                reason=reason,
                end_status=RecentSessionStatus.COMPLETED.value,
            )
        )


class SessionManager:
    def __init__(
        self,
        app_config: AppConfig,
        calibration: Dict[str, Any],
        ros_adapter: RosAdapter,
        status_manager: StatusManager,
        command_callback: Callable[[ControlCommand], None],
    ) -> None:
        self.app_config = app_config
        self.calibration = calibration
        self.ros_adapter = ros_adapter
        self.status_manager = status_manager
        self.command_callback = command_callback
        self.active_session: SessionRuntime = None
        self.logger = logging.getLogger("data_collect_dag.session_manager")

    def start(self, pipeline_name: str) -> SessionRuntime:
        if pipeline_name not in self.app_config.pipelines:
            raise KeyError(f"unknown pipeline: {pipeline_name}")
        if self.active_session is not None:
            self.stop(reason="replaced_by_new_start", status=RecentSessionStatus.STOPPED)
        self.logger.info("starting session for pipeline=%s", pipeline_name)
        session = SessionRuntime(
            app_config=self.app_config,
            pipeline=self.app_config.pipelines[pipeline_name],
            calibration=self.calibration,
            ros_adapter=self.ros_adapter,
            status_manager=self.status_manager,
            command_callback=self.command_callback,
        )
        session.start()
        self.active_session = session
        return session

    def stop(self, *, reason: str = "external_stop", status: RecentSessionStatus = RecentSessionStatus.STOPPED) -> None:
        if self.active_session is None:
            return
        self.logger.info("stopping active session reason=%s status=%s", reason, status.value)
        try:
            self.active_session.stop(reason=reason, status=status)
        finally:
            self.active_session = None

    def pause(self) -> None:
        if self.active_session is None:
            return
        self.active_session.pause()

    def resume(self) -> None:
        if self.active_session is None:
            return
        self.active_session.resume()
