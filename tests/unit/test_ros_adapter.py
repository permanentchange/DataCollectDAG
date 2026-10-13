from __future__ import annotations

import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from data_collect_dag.models import ControlConfig, StatusSnapshot, ToolState, TopicConfig
from data_collect_dag.ros_adapter import RosAdapter


class _FakeRospy:
    def __init__(self) -> None:
        self.core = SimpleNamespace(is_initialized=lambda: True)
        self.init_calls = []
        self.subscribers = []

    def init_node(self, *args, **kwargs) -> None:
        self.init_calls.append((args, kwargs))

    def Subscriber(self, *args, **kwargs):
        self.subscribers.append((args, kwargs))
        return SimpleNamespace(unregister=lambda: None)


class _StringMsg:
    def __init__(self, data: str = "") -> None:
        self.data = data


def _skip_ros_master_wait(monkeypatch, adapter: RosAdapter) -> None:
    monkeypatch.setattr(adapter, "_wait_for_ros_master", lambda: None)


def _message_tracking_adapter():
    return RosAdapter(
        {
            "front_camera": TopicConfig("front_camera", "/front", "sensor_msgs/Image", "image", "front_camera"),
            "top_lidar": TopicConfig("top_lidar", "/lidar", "sensor_msgs/PointCloud2", "pointcloud", "top_lidar"),
        },
        "data_collect_dag",
        ControlConfig(),
    )


def test_topic_precheck_ignores_messages_received_before_wait(monkeypatch):
    adapter = _message_tracking_adapter()
    monkeypatch.setattr("data_collect_dag.ros_adapter.frame_from_ros_message", lambda *_args: object())
    adapter._make_topic_callback("front_camera", adapter._topics["front_camera"])(object())

    missing = adapter.wait_for_topic_messages(["front_camera"], timeout_ms=10)

    assert missing == ["front_camera"]


def test_topic_precheck_accepts_new_raw_messages_even_when_frame_conversion_fails(monkeypatch):
    adapter = _message_tracking_adapter()
    monkeypatch.setattr(
        "data_collect_dag.ros_adapter.frame_from_ros_message",
        lambda *_args: (_ for _ in ()).throw(ValueError("invalid frame")),
    )
    result = {}

    waiter = threading.Thread(
        target=lambda: result.setdefault(
            "missing", adapter.wait_for_topic_messages(["front_camera", "top_lidar"], timeout_ms=500)
        )
    )
    waiter.start()
    time.sleep(0.02)
    adapter._make_topic_callback("front_camera", adapter._topics["front_camera"])(object())
    adapter._make_topic_callback("top_lidar", adapter._topics["top_lidar"])(object())
    waiter.join(timeout=1.0)

    assert waiter.is_alive() is False
    assert result["missing"] == []


def test_topic_precheck_timeout_returns_only_topics_without_new_messages(monkeypatch):
    adapter = _message_tracking_adapter()
    monkeypatch.setattr("data_collect_dag.ros_adapter.frame_from_ros_message", lambda *_args: object())
    result = {}

    waiter = threading.Thread(
        target=lambda: result.setdefault(
            "missing", adapter.wait_for_topic_messages(["front_camera", "top_lidar"], timeout_ms=80)
        )
    )
    waiter.start()
    time.sleep(0.02)
    adapter._make_topic_callback("front_camera", adapter._topics["front_camera"])(object())
    waiter.join(timeout=1.0)

    assert waiter.is_alive() is False
    assert result["missing"] == ["top_lidar"]


def test_start_fails_fast_when_any_topic_message_type_is_missing(monkeypatch):
    adapter = RosAdapter(
        {
            "front_wide_camera": TopicConfig("front_wide_camera", "/front", "sensor_msgs/Image", "image", "front_wide_camera"),
            "bdstar_nmea": TopicConfig("bdstar_nmea", "/bdstar/nmea_sentence", "bdstar/string", "nmea", "bdstar"),
            "localization": TopicConfig("localization", "/localization", "igv_msgs/location", "localization", "localization"),
        },
        "data_collect_dag",
        ControlConfig(),
    )
    fake_rospy = _FakeRospy()

    def fake_resolve_message_class(msg_type: str):
        if msg_type == "sensor_msgs/Image":
            return object()
        raise FileNotFoundError(f"message definition not found for {msg_type}")

    monkeypatch.setattr("data_collect_dag.ros_adapter.import_rospy", lambda: fake_rospy)
    monkeypatch.setattr("data_collect_dag.ros_adapter.resolve_message_class", fake_resolve_message_class)
    _skip_ros_master_wait(monkeypatch, adapter)

    with pytest.raises(RuntimeError, match="failed to resolve ROS message types before starting subscriptions") as exc_info:
        adapter.start()

    message = str(exc_info.value)
    assert "topic_key=bdstar_nmea ros_topic=/bdstar/nmea_sentence msg_type=bdstar/string" in message
    assert "topic_key=localization ros_topic=/localization msg_type=igv_msgs/location" in message
    assert "source /path/to/catkin_ws/devel/setup.bash" in message
    assert fake_rospy.subscribers == []


def test_control_topic_subscribes_to_single_json_control_topic(monkeypatch):
    adapter = RosAdapter(
        {},
        "data_collect_dag",
        ControlConfig(control_topic="/data_collect/control"),
    )
    fake_rospy = _FakeRospy()
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_rospy", lambda: fake_rospy)
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_std_msgs_string", lambda: _StringMsg)
    _skip_ros_master_wait(monkeypatch, adapter)

    adapter.start()

    subscribed_topics = [args[0] for args, _kwargs in fake_rospy.subscribers]
    assert subscribed_topics == ["/data_collect/control"]


def test_control_topic_dispatches_json_commands(monkeypatch):
    adapter = RosAdapter(
        {},
        "data_collect_dag",
        ControlConfig(control_topic="/data_collect/control"),
    )
    fake_rospy = _FakeRospy()
    calls = []
    adapter.bind_app(
        status_callback=lambda: None,
        start_callback=lambda pipeline_name: calls.append(("start", pipeline_name)),
        stop_callback=lambda: calls.append(("stop", None)),
        pause_callback=lambda: calls.append(("pause", None)),
        resume_callback=lambda: calls.append(("resume", None)),
        shutdown_callback=lambda: calls.append(("shutdown", None)),
    )
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_rospy", lambda: fake_rospy)
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_std_msgs_string", lambda: _StringMsg)
    _skip_ros_master_wait(monkeypatch, adapter)
    adapter.start()
    callback = fake_rospy.subscribers[0][0][2]

    callback(_StringMsg('{"command":"start","pipeline":"xtreme1_collect"}'))
    callback(_StringMsg('{"command":"pause"}'))
    callback(_StringMsg('{"command":"resume"}'))
    callback(_StringMsg('{"command":"stop","request_id":"abc"}'))
    callback(_StringMsg('{"command":"shutdown"}'))

    assert calls == [
        ("start", "xtreme1_collect"),
        ("pause", None),
        ("resume", None),
        ("stop", None),
        ("shutdown", None),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        "pause",
        '["pause"]',
        "{}",
        '{"command":"unknown"}',
        '{"command":"Start","pipeline":"xtreme1_collect"}',
        '{"command":"Shutdown"}',
        '{"command":"start"}',
        '{"command":"start","pipeline":""}',
    ],
)
def test_control_topic_ignores_invalid_json_commands(monkeypatch, payload):
    adapter = RosAdapter(
        {},
        "data_collect_dag",
        ControlConfig(control_topic="/data_collect/control"),
    )
    fake_rospy = _FakeRospy()
    calls = []
    adapter.bind_app(
        status_callback=lambda: None,
        start_callback=lambda pipeline_name: calls.append(("start", pipeline_name)),
        stop_callback=lambda: calls.append(("stop", None)),
        pause_callback=lambda: calls.append(("pause", None)),
        resume_callback=lambda: calls.append(("resume", None)),
    )
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_rospy", lambda: fake_rospy)
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_std_msgs_string", lambda: _StringMsg)
    _skip_ros_master_wait(monkeypatch, adapter)
    adapter.start()
    callback = fake_rospy.subscribers[0][0][2]

    callback(_StringMsg(payload))

    assert calls == []


def test_ros_master_probe_retries_until_available(monkeypatch):
    adapter = RosAdapter({}, "data_collect_dag", ControlConfig())
    attempts = []

    def probe(_uri, _timeout_sec):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("connection refused")

    monkeypatch.setattr(adapter, "_probe_ros_master", probe)
    monkeypatch.setattr("data_collect_dag.ros_adapter.ROS_MASTER_WAIT_TIMEOUT_SEC", 0.1)
    monkeypatch.setattr("data_collect_dag.ros_adapter.time.sleep", lambda _seconds: None)

    adapter._wait_for_ros_master()

    assert len(attempts) == 2


def test_ros_master_probe_fails_after_timeout(monkeypatch):
    adapter = RosAdapter({}, "data_collect_dag", ControlConfig())
    monkeypatch.setattr(adapter, "_probe_ros_master", lambda _uri, _timeout_sec: (_ for _ in ()).throw(OSError("connection refused")))
    monkeypatch.setattr("data_collect_dag.ros_adapter.ROS_MASTER_WAIT_TIMEOUT_SEC", 0.0)

    with pytest.raises(RuntimeError, match="ROS master.*roscore") as exc_info:
        adapter._wait_for_ros_master()

    assert "ROS_MASTER_URI" in str(exc_info.value)


def test_start_checks_ros_master_before_importing_rospy(monkeypatch):
    adapter = RosAdapter({}, "data_collect_dag", ControlConfig())
    imported = []

    def fail_master_check():
        raise RuntimeError("master unavailable")

    monkeypatch.setattr(adapter, "_wait_for_ros_master", fail_master_check)
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_rospy", lambda: imported.append(True))

    with pytest.raises(RuntimeError, match="master unavailable"):
        adapter.start()

    assert imported == []


def test_publish_status_keeps_existing_full_payload():
    adapter = RosAdapter({}, "data_collect_dag", ControlConfig())
    snapshot = StatusSnapshot(
        tool_state=ToolState.RUNNING,
        current_session_id="session-1",
        current_pipeline_name="xtreme1_collect",
        received_messages={"front_wide_camera": 4},
        main_frame_events=4,
        samples_saved=2,
    )
    adapter.bind_app(
        status_callback=lambda: snapshot,
        start_callback=lambda _pipeline: None,
        stop_callback=lambda: None,
        pause_callback=lambda: None,
        resume_callback=lambda: None,
    )

    assert json.loads(adapter.publish_status()) == snapshot.to_dict()


def test_runtime_summary_uses_main_source_count_and_logs_once_per_second(monkeypatch, caplog):
    adapter = RosAdapter({}, "data_collect_dag", ControlConfig())
    adapter.bind_session(SimpleNamespace(pipeline=SimpleNamespace(main_source="front_wide_camera")))
    snapshot = StatusSnapshot(
        tool_state=ToolState.RUNNING,
        current_pipeline_name="xtreme1_collect",
        received_messages={"front_wide_camera": 42, "top_lidar": 20},
        samples_saved=3,
        samples_skipped=7,
        samples_failed=1,
    )
    clock = iter((10.0, 10.5, 11.0))
    monkeypatch.setattr("data_collect_dag.ros_adapter.time.monotonic", lambda: next(clock))
    caplog.set_level(logging.INFO, logger="data_collect_dag.ros")

    adapter._maybe_log_runtime_summary(snapshot)
    adapter._maybe_log_runtime_summary(snapshot)
    adapter._maybe_log_runtime_summary(snapshot)

    messages = [record.getMessage() for record in caplog.records if record.name == "data_collect_dag.ros"]
    assert messages == [
        "runtime state=RUNNING pipeline=xtreme1_collect received=42 saved=3 skipped=7 failed=1",
        "runtime state=RUNNING pipeline=xtreme1_collect received=42 saved=3 skipped=7 failed=1",
    ]
