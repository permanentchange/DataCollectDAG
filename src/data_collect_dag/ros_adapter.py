from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from http.client import HTTPConnection
from typing import Any, Callable, Dict, List, Optional, Sequence
from xmlrpc.client import ServerProxy, Transport

from data_collect_dag.models import FrameLike, StatusSnapshot, TopicConfig
from data_collect_dag.ros_messages import (
    frame_from_ros_message,
    import_rospy,
    resolve_message_class,
    import_std_msgs_string,
    import_std_srvs_trigger,
)


ROS_MASTER_WAIT_TIMEOUT_SEC = 5.0
ROS_MASTER_PROBE_TIMEOUT_SEC = 0.5
ROS_MASTER_RETRY_INTERVAL_SEC = 0.2
RUNTIME_SUMMARY_INTERVAL_SEC = 1.0


class _TimeoutTransport(Transport):
    def __init__(self, timeout_sec: float) -> None:
        super().__init__()
        self._timeout_sec = timeout_sec

    def make_connection(self, host: str) -> HTTPConnection:
        return HTTPConnection(host, timeout=self._timeout_sec)


class RosAdapter:
    def __init__(self, topics: Dict[str, TopicConfig], ros_node_name: str, control: Any) -> None:
        self._topics = dict(topics)
        self._ros_node_name = ros_node_name
        self._control = control
        self._session = None
        self._status_callback: Optional[Callable[[], StatusSnapshot]] = None
        self._start_callback: Optional[Callable[[str], None]] = None
        self._stop_callback: Optional[Callable[[], None]] = None
        self._pause_callback: Optional[Callable[[], None]] = None
        self._resume_callback: Optional[Callable[[], None]] = None
        self._shutdown_callback: Optional[Callable[[], None]] = None
        self._status_thread: Optional[threading.Thread] = None
        self._status_stop = threading.Event()
        self._subscribers = []
        self._publishers = {}
        self._services = []
        self._rospy = None
        self._last_runtime_summary_log_monotonic: Optional[float] = None
        self._topic_message_condition = threading.Condition()
        self._topic_message_counts: Dict[str, int] = {topic_key: 0 for topic_key in topics}
        self._logger = logging.getLogger("data_collect_dag.ros")

    def bind_app(
        self,
        *,
        status_callback: Callable[[], StatusSnapshot],
        start_callback: Callable[[str], None],
        stop_callback: Callable[[], None],
        pause_callback: Callable[[], None],
        resume_callback: Callable[[], None],
        shutdown_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self._status_callback = status_callback
        self._start_callback = start_callback
        self._stop_callback = stop_callback
        self._pause_callback = pause_callback
        self._resume_callback = resume_callback
        self._shutdown_callback = shutdown_callback

    def bind_session(self, session: Any) -> None:
        self._session = session

    def unbind_session(self, session: Any) -> None:
        if self._session is session:
            self._session = None

    def wait_for_topic_messages(self, topic_keys: Sequence[str], timeout_ms: int) -> List[str]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        with self._topic_message_condition:
            initial_counts = {topic_key: self._topic_message_counts.get(topic_key, 0) for topic_key in topic_keys}
            while True:
                missing = [
                    topic_key
                    for topic_key in topic_keys
                    if self._topic_message_counts.get(topic_key, 0) <= initial_counts[topic_key]
                ]
                if not missing:
                    return []
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return missing
                self._topic_message_condition.wait(timeout=remaining)

    def start(self) -> None:
        rospy = sys.modules.get("rospy")
        if rospy is None:
            self._wait_for_ros_master()
            rospy = import_rospy()
        self._rospy = rospy
        if not rospy.core.is_initialized():
            self._wait_for_ros_master()
            rospy.init_node(self._ros_node_name, anonymous=False, disable_signals=True)
        self._logger.info("starting ROS adapter node=%s subscribed_topics=%s", self._ros_node_name, sorted(self._topics))
        resolved_message_classes = self._resolve_topic_message_classes()
        self._subscribers = []
        for topic_key, topic_config in self._topics.items():
            msg_class = resolved_message_classes[topic_key]
            subscriber = rospy.Subscriber(topic_config.topic, msg_class, self._make_topic_callback(topic_key, topic_config), queue_size=50)
            self._subscribers.append(subscriber)
        StringMsg = import_std_msgs_string()
        if self._control.control_topic:
            self._subscribers.append(rospy.Subscriber(self._control.control_topic, StringMsg, self._on_control_topic, queue_size=10))
        if self._control.status_topic:
            self._publishers["status_topic"] = rospy.Publisher(self._control.status_topic, StringMsg, queue_size=10, latch=True)
            self._status_stop.clear()
            self._status_thread = threading.Thread(target=self._status_loop, name="ros-status-publisher", daemon=True)
            self._status_thread.start()
        if self._control.status_service:
            Trigger, TriggerResponse = import_std_srvs_trigger()
            self._services.append(rospy.Service(self._control.status_service, Trigger, self._handle_status_service))
        if self._control.stop_service:
            Trigger, TriggerResponse = import_std_srvs_trigger()
            self._services.append(rospy.Service(self._control.stop_service, Trigger, self._handle_stop_service))
        if self._control.start_service:
            Trigger, TriggerResponse = import_std_srvs_trigger()
            self._services.append(rospy.Service(self._control.start_service, Trigger, self._handle_start_service))

    def stop(self) -> None:
        self._logger.info("stopping ROS adapter")
        self._status_stop.set()
        if self._status_thread is not None:
            self._status_thread.join(timeout=1.0)
            self._status_thread = None
        for subscriber in self._subscribers:
            try:
                subscriber.unregister()
            except Exception:
                pass
        self._subscribers = []
        for service in self._services:
            try:
                service.shutdown()
            except Exception:
                pass
        self._services = []
        for publisher in self._publishers.values():
            try:
                publisher.unregister()
            except Exception:
                pass
        self._publishers = {}

    def publish_status(self) -> str:
        snapshot = self._get_status_snapshot()
        if snapshot is None:
            return "{}"
        return json.dumps(snapshot.to_dict(), ensure_ascii=False)

    def _wait_for_ros_master(self) -> None:
        master_uri = os.environ.get("ROS_MASTER_URI") or "http://localhost:11311"
        deadline = time.monotonic() + ROS_MASTER_WAIT_TIMEOUT_SEC
        last_error: Optional[Exception] = None
        self._logger.info(
            "waiting for ROS master ROS_MASTER_URI=%s timeout_sec=%s",
            master_uri,
            ROS_MASTER_WAIT_TIMEOUT_SEC,
        )
        while True:
            remaining = deadline - time.monotonic()
            probe_timeout_sec = max(0.001, min(ROS_MASTER_PROBE_TIMEOUT_SEC, remaining))
            try:
                self._probe_ros_master(master_uri, probe_timeout_sec)
                self._logger.info("ROS master is available ROS_MASTER_URI=%s", master_uri)
                return
            except Exception as exc:
                last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(ROS_MASTER_RETRY_INTERVAL_SEC, remaining))
        raise RuntimeError(
            "unable to contact ROS master within "
            f"{ROS_MASTER_WAIT_TIMEOUT_SEC:g} seconds (ROS_MASTER_URI={master_uri}); "
            "start it with 'roscore' and retry. "
            f"Last error: {last_error}"
        )

    def _probe_ros_master(self, master_uri: str, timeout_sec: float) -> None:
        proxy = ServerProxy(master_uri, transport=_TimeoutTransport(timeout_sec), allow_none=True)
        code, message, _pid = proxy.getPid(self._ros_node_name)
        if code != 1:
            raise RuntimeError(f"master getPid failed: {message}")

    def _get_status_snapshot(self) -> Optional[StatusSnapshot]:
        if self._status_callback is None:
            return None
        return self._status_callback()

    def _maybe_log_runtime_summary(self, snapshot: StatusSnapshot) -> None:
        now = time.monotonic()
        previous = self._last_runtime_summary_log_monotonic
        if previous is not None and now - previous < RUNTIME_SUMMARY_INTERVAL_SEC:
            return
        self._last_runtime_summary_log_monotonic = now
        session = self._session
        pipeline = getattr(session, "pipeline", None)
        main_source = getattr(pipeline, "main_source", None)
        received = snapshot.received_messages.get(main_source, 0) if main_source else 0
        self._logger.info(
            "runtime state=%s pipeline=%s received=%s saved=%s skipped=%s failed=%s",
            snapshot.tool_state.value,
            snapshot.current_pipeline_name,
            received,
            snapshot.samples_saved,
            snapshot.samples_skipped,
            snapshot.samples_failed,
        )

    def _resolve_topic_message_classes(self) -> Dict[str, Any]:
        resolved: Dict[str, Any] = {}
        failures = []
        for topic_key, topic_config in self._topics.items():
            try:
                resolved[topic_key] = resolve_message_class(topic_config.msg_type)
            except Exception as exc:
                failures.append(
                    f"- topic_key={topic_key} ros_topic={topic_config.topic} msg_type={topic_config.msg_type}: {exc}"
                )
        if failures:
            raise RuntimeError(
                "failed to resolve ROS message types before starting subscriptions:\n"
                + "\n".join(failures)
                + "\nEnsure required ROS packages are installed/built and source your workspace before running, for example:\n"
                + "  source /path/to/catkin_ws/devel/setup.bash\n"
                + "  source /path/to/catkin_ws/install/setup.bash"
            )
        return resolved

    def ingest_frame(self, topic_key: str, frame: FrameLike) -> None:
        session = self._session
        if session is None:
            return
        session.accept_frame(topic_key, frame)

    def _make_topic_callback(self, topic_key: str, topic_config: TopicConfig):
        def callback(msg: Any) -> None:
            with self._topic_message_condition:
                self._topic_message_counts[topic_key] = self._topic_message_counts.get(topic_key, 0) + 1
                self._topic_message_condition.notify_all()
            try:
                frame = frame_from_ros_message(topic_key, topic_config.topic, topic_config.role, topic_config.sensor_name, msg)
                self.ingest_frame(topic_key, frame)
            except Exception:
                self._logger.exception("failed to ingest ROS message topic_key=%s ros_topic=%s", topic_key, topic_config.topic)
                session = self._session
                if session is not None:
                    session.metrics.error()
                    session.status_manager.update_metrics(session.metrics.metrics)

        return callback

    def _status_loop(self) -> None:
        if "status_topic" not in self._publishers:
            return
        StringMsg = import_std_msgs_string()
        while not self._status_stop.is_set():
            msg = StringMsg()
            snapshot = self._get_status_snapshot()
            msg.data = "{}" if snapshot is None else json.dumps(snapshot.to_dict(), ensure_ascii=False)
            self._publishers["status_topic"].publish(msg)
            if snapshot is not None:
                self._maybe_log_runtime_summary(snapshot)
            self._status_stop.wait(0.5)

    def _on_control_topic(self, msg: Any) -> None:
        payload = getattr(msg, "data", "")
        try:
            command = json.loads(str(payload))
        except (TypeError, json.JSONDecodeError):
            self._logger.warning("ignoring invalid ROS control payload: expected JSON object")
            return
        if not isinstance(command, dict):
            self._logger.warning("ignoring invalid ROS control payload: expected JSON object")
            return
        command_name = command.get("command")
        if command_name not in {"start", "stop", "pause", "resume", "shutdown"}:
            self._logger.warning("ignoring invalid ROS control command=%r", command_name)
            return
        if command_name == "start":
            if self._start_callback is None:
                return
            pipeline_name = command.get("pipeline")
            if not isinstance(pipeline_name, str) or not pipeline_name.strip():
                self._logger.warning("ignoring start control command without non-empty pipeline")
                return
            self._start_callback(pipeline_name.strip())
            return
        if command_name == "stop":
            if self._stop_callback is not None:
                self._stop_callback()
            return
        if command_name == "pause":
            if self._pause_callback is not None:
                self._pause_callback()
            return
        if command_name == "resume":
            if self._resume_callback is not None:
                self._resume_callback()
            return
        if command_name == "shutdown":
            if self._shutdown_callback is not None:
                self._shutdown_callback()

    def _handle_status_service(self, _request: Any):
        _Trigger, TriggerResponse = import_std_srvs_trigger()
        return TriggerResponse(success=True, message=self.publish_status())

    def _handle_stop_service(self, _request: Any):
        _Trigger, TriggerResponse = import_std_srvs_trigger()
        if self._stop_callback is not None:
            self._stop_callback()
        return TriggerResponse(success=True, message="stopping")

    def _handle_start_service(self, _request: Any):
        _Trigger, TriggerResponse = import_std_srvs_trigger()
        if self._start_callback is not None:
            self._start_callback("")
        return TriggerResponse(success=True, message="starting")
