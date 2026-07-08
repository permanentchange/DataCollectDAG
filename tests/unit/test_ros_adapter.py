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
