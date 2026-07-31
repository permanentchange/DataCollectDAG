from __future__ import annotations

from types import SimpleNamespace

import pytest

from data_collect_dag.models import ControlConfig, TopicConfig
from data_collect_dag.ros_adapter import RosAdapter


class _FakeRospy:
    def __init__(self) -> None:
        self.core = SimpleNamespace(is_initialized=lambda: False)
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
    )
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_rospy", lambda: fake_rospy)
    monkeypatch.setattr("data_collect_dag.ros_adapter.import_std_msgs_string", lambda: _StringMsg)
    adapter.start()
    callback = fake_rospy.subscribers[0][0][2]

    callback(_StringMsg('{"command":"start","pipeline":"xtreme1_collect"}'))
    callback(_StringMsg('{"command":"pause"}'))
    callback(_StringMsg('{"command":"resume"}'))
    callback(_StringMsg('{"command":"stop","request_id":"abc"}'))

    assert calls == [
        ("start", "xtreme1_collect"),
        ("pause", None),
        ("resume", None),
        ("stop", None),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        "pause",
        '["pause"]',
        "{}",
        '{"command":"unknown"}',
        '{"command":"Start","pipeline":"xtreme1_collect"}',
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
    adapter.start()
    callback = fake_rospy.subscribers[0][0][2]

    callback(_StringMsg(payload))

    assert calls == []
