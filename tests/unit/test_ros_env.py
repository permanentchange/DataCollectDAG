from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_collect_dag import ros_env


class _FakeResourceNotFound(Exception):
    pass


def test_resolve_message_definition_path_prefers_standard_msg_root(tmp_path, monkeypatch):
    msg_path = tmp_path / "sensor_msgs" / "msg" / "Image.msg"
    msg_path.parent.mkdir(parents=True)
    msg_path.write_text("uint32 height\n", encoding="utf-8")
    monkeypatch.setattr(ros_env, "STANDARD_MSG_ROOT", tmp_path)

    resolved = ros_env.resolve_message_definition_path("sensor_msgs/Image")

    assert resolved == msg_path


def test_resolve_message_definition_path_uses_rospkg_for_custom_package(tmp_path, monkeypatch):
    package_root = tmp_path / "bdstar_ros"
    msg_path = package_root / "msg" / "string.msg"
    msg_path.parent.mkdir(parents=True)
    msg_path.write_text("Header header\nstring sentence\n", encoding="utf-8")
    monkeypatch.setattr(ros_env, "STANDARD_MSG_ROOT", tmp_path / "missing")
    monkeypatch.setitem(
        sys.modules,
        "rospkg",
        SimpleNamespace(
            RosPack=lambda: SimpleNamespace(get_path=lambda package: str(package_root)),
            ResourceNotFound=_FakeResourceNotFound,
        ),
    )

    resolved = ros_env.resolve_message_definition_path("bdstar/string")

    assert resolved == msg_path


def test_resolve_message_definition_path_reports_missing_package(tmp_path, monkeypatch):
    monkeypatch.setattr(ros_env, "STANDARD_MSG_ROOT", tmp_path / "missing")

    def raise_missing(_package: str):
        raise _FakeResourceNotFound()

    monkeypatch.setitem(
        sys.modules,
        "rospkg",
        SimpleNamespace(
            RosPack=lambda: SimpleNamespace(get_path=raise_missing),
            ResourceNotFound=_FakeResourceNotFound,
        ),
    )

    with pytest.raises(ros_env.MessageDefinitionNotFoundError, match="ROS package 'bdstar' was not found"):
        ros_env.resolve_message_definition_path("bdstar/string")


def test_resolve_message_definition_path_reports_missing_msg_file(tmp_path, monkeypatch):
    package_root = tmp_path / "igv_msgs"
    package_root.mkdir(parents=True)
    monkeypatch.setattr(ros_env, "STANDARD_MSG_ROOT", tmp_path / "missing")
    monkeypatch.setitem(
        sys.modules,
        "rospkg",
        SimpleNamespace(
            RosPack=lambda: SimpleNamespace(get_path=lambda package: str(package_root)),
            ResourceNotFound=_FakeResourceNotFound,
        ),
    )

    with pytest.raises(ros_env.MessageDefinitionNotFoundError, match="message file was missing"):
        ros_env.resolve_message_definition_path("igv_msgs/location")
