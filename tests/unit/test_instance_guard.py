import os
import uuid

import pytest

from data_collect_dag.instance_guard import (
    InstanceAlreadyRunningError, InstanceGuard, load_node_name, resolve_node_name,
)


def test_resolves_names_and_namespace(monkeypatch):
    monkeypatch.setenv("ROS_NAMESPACE", "/vehicle/")
    assert resolve_node_name("collector") == "/vehicle/collector"
    assert resolve_node_name("/collector") == "/collector"
    assert resolve_node_name("//vehicle//collector/") == "/vehicle/collector"
    monkeypatch.delenv("ROS_NAMESPACE")
    assert resolve_node_name("collector") == "/collector"


@pytest.mark.parametrize("name", ["", "~collector", "/", "has spaces", "123", "/car/123", "../collector"])
def test_rejects_invalid_names(name):
    with pytest.raises(ValueError):
        resolve_node_name(name)


def test_same_node_across_configs_and_outputs_uses_same_lock(tmp_path):
    name = "lock_test_" + uuid.uuid4().hex
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(f"ros:\n  node_name: {name}\noutput:\n  root_dir: first\n")
    second.write_text(f"ros:\n  node_name: /{name}\noutput:\n  root_dir: second\n")
    with InstanceGuard(load_node_name(first)) as guard:
        with pytest.raises(InstanceAlreadyRunningError, match=f"/{name} is already running"):
            InstanceGuard(load_node_name(second))
        with InstanceGuard(name + "_other") as other:
            assert other.path != guard.path
    with InstanceGuard(load_node_name(second)):
        pass


def test_closing_parent_descriptor_does_not_unlock_inherited_descriptor():
    name = "inherit_test_" + uuid.uuid4().hex
    guard = InstanceGuard(name)
    child = InstanceGuard(name, os.dup(guard.fd))
    guard.close()
    try:
        with pytest.raises(InstanceAlreadyRunningError):
            InstanceGuard(name)
    finally:
        child.close()
    with InstanceGuard(name):
        pass


def test_rejects_inherited_descriptor_for_another_node():
    name = "mismatch_test_" + uuid.uuid4().hex
    with InstanceGuard(name) as guard, InstanceGuard(name + "_other"):
        with pytest.raises(ValueError, match="does not match"):
            InstanceGuard(name + "_other", os.dup(guard.fd))


def test_lock_remains_readable_under_restrictive_umask():
    name = "umask_test_" + uuid.uuid4().hex
    previous = os.umask(0o077)
    try:
        with InstanceGuard(name) as guard:
            assert guard.path.stat().st_mode & 0o777 == 0o644
    finally:
        os.umask(previous)


@pytest.mark.parametrize("payload", ["[invalid]", "ros: []", "ros: nope"])
def test_only_reads_node_config_and_rejects_bad_mappings(tmp_path, payload):
    config = tmp_path / "cfg.yaml"
    config.write_text(payload)
    if payload == "ros: []":
        # Match the config loader's handling of empty sections.
        assert load_node_name(config).endswith("/data_collect_dag")
    else:
        with pytest.raises(ValueError, match="mapping"):
            load_node_name(config)
