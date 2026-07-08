from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, Optional

from data_collect_dag.models import FrameLike, StatusSnapshot, TopicConfig
from data_collect_dag.ros_messages import (
    frame_from_ros_message,
    import_rospy,
    resolve_message_class,
    import_std_msgs_string,
    import_std_srvs_trigger,
)


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
        self._status_thread: Optional[threading.Thread] = None
        self._status_stop = threading.Event()
        self._subscribers = []
        self._publishers = {}
        self._services = []
        self._rospy = None
        self._logger = logging.getLogger("data_collect_dag.ros")

    def bind_app(
        self,
        *,
        status_callback: Callable[[], StatusSnapshot],
        start_callback: Callable[[str], None],
        stop_callback: Callable[[], None],
        pause_callback: Callable[[], None],
        resume_callback: Callable[[], None],
    ) -> None:
        self._status_callback = status_callback
        self._start_callback = start_callback
        self._stop_callback = stop_callback
        self._pause_callback = pause_callback
        self._resume_callback = resume_callback

    def bind_session(self, session: Any) -> None:
        self._session = session

    def unbind_session(self, session: Any) -> None:
        if self._session is session:
            self._session = None

    def start(self) -> None:
        rospy = import_rospy()
        self._rospy = rospy
        if not rospy.core.is_initialized():
            rospy.init_node(self._ros_node_name, anonymous=False, disable_signals=True)
        self._logger.info("starting ROS adapter node=%s subscribed_topics=%s", self._ros_node_name, sorted(self._topics))
        resolved_message_classes = self._resolve_topic_message_classes()
        self._subscribers = []
        for topic_key, topic_config in self._topics.items():
            msg_class = resolved_message_classes[topic_key]
            subscriber = rospy.Subscriber(topic_config.topic, msg_class, self._make_topic_callback(topic_key, topic_config), queue_size=50)
            self._subscribers.append(subscriber)
        StringMsg = import_std_msgs_string()
        if self._control.start_topic:
            self._subscribers.append(rospy.Subscriber(self._control.start_topic, StringMsg, self._on_start_topic, queue_size=10))
        if self._control.stop_topic:
            self._subscribers.append(rospy.Subscriber(self._control.stop_topic, StringMsg, self._on_stop_topic, queue_size=10))
        if self._control.pause_topic:
            self._subscribers.append(rospy.Subscriber(self._control.pause_topic, StringMsg, self._on_pause_topic, queue_size=10))
        if self._control.resume_topic:
            self._subscribers.append(rospy.Subscriber(self._control.resume_topic, StringMsg, self._on_resume_topic, queue_size=10))
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
        if self._status_callback is None:
            return "{}"
        return json.dumps(self._status_callback().to_dict(), ensure_ascii=False)

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
            msg.data = self.publish_status()
            self._publishers["status_topic"].publish(msg)
            self._status_stop.wait(0.5)

    def _on_start_topic(self, msg: Any) -> None:
        if self._start_callback is None:
            return
        payload = getattr(msg, "data", "")
        pipeline_name = str(payload).strip() or ""
        if not pipeline_name:
            pipeline_name = ""
        self._start_callback(pipeline_name)

    def _on_stop_topic(self, msg: Any) -> None:
        if self._stop_callback is not None:
            self._stop_callback()

    def _on_pause_topic(self, msg: Any) -> None:
        if self._pause_callback is not None:
            self._pause_callback()

    def _on_resume_topic(self, msg: Any) -> None:
        if self._resume_callback is not None:
            self._resume_callback()

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
