import json
from types import SimpleNamespace

import pytest

from data_collect_dag import ros_control_client as client
from data_collect_dag.cli import main


class FakeEndpoint:
    def __init__(self, connections=1):
        self.connections = connections
        self.messages = []
        self.closed = False

    def get_num_connections(self):
        return self.connections

    def publish(self, message):
        self.messages.append(json.loads(message.data))

    def unregister(self):
        self.closed = True


@pytest.fixture
def fake_ros(monkeypatch):
    endpoint = FakeEndpoint()
    calls = []
    snapshot = {"tool_state": "RUNNING", "current_pipeline_name": "fps", "current_session_id": "session-1",
                "samples_saved": 125, "samples_skipped": 3, "samples_failed": 0, "disk_free_gb": 120.5,
                "last_error": None}
    def subscribe(topic, msg_type, callback, **kwargs):
        calls.append(("subscribe", topic))
        if endpoint.connections:
            callback(SimpleNamespace(data=json.dumps(snapshot)))
        return endpoint
    def publish(topic, msg_type, **kwargs):
        calls.append(("publish", topic, kwargs))
        return endpoint
    rospy = SimpleNamespace(
        init_node=lambda *args, **kwargs: calls.append(("init", args, kwargs)),
        Subscriber=subscribe, Publisher=publish, is_shutdown=lambda: False,
        signal_shutdown=lambda reason: calls.append(("shutdown", reason)),
    )
    monkeypatch.setattr(client, "wait_for_master", lambda timeout: None)
    monkeypatch.setattr(client, "_import_ros", lambda: (rospy, SimpleNamespace))
    return endpoint, calls, snapshot


@pytest.mark.parametrize("command", ["start", "pause", "resume", "stop", "shutdown"])
def test_sends_existing_json_protocol_and_uses_anonymous_node(fake_ros, command, monkeypatch, capsys):
    endpoint, calls, _snapshot = fake_ros
    arguments = [command] if command == "shutdown" else ["run", command]
    if command == "start":
        arguments.append("fps")
    monkeypatch.setattr("sys.argv", ["data_collect_dag"] + arguments)
    assert main() == 0
    assert endpoint.messages == [{"command": command, **({"pipeline": "fps"} if command == "start" else {})}]
    assert calls[0][2]["anonymous"] is True
    assert calls[0][1][0] != "data_collect_dag"
    assert calls[1] == ("publish", "/data_collect/control", {"queue_size": None, "latch": False})
    assert endpoint.closed
    assert "execution is asynchronous" in capsys.readouterr().out


def test_status_prints_existing_snapshot_without_publishing_control(fake_ros, monkeypatch, capsys):
    endpoint, calls, _snapshot = fake_ros
    timeouts = []
    monkeypatch.setattr(client, "wait_for_master", timeouts.append)
    monkeypatch.setattr("sys.argv", ["data_collect_dag", "status", "--status-topic", "/custom/status", "--timeout", "0.2"])
    assert main() == 0
    assert calls[1] == ("subscribe", "/custom/status")
    assert timeouts == [0.2]
    assert not endpoint.messages and endpoint.closed
    assert capsys.readouterr().out == (
        "State:       RUNNING\nPipeline:    fps\nSession:     session-1\nSaved:       125\n"
        "Skipped:     3\nFailed:      0\nDisk Free:   120.5 GB\nLast Error:  None\n"
    )


@pytest.mark.parametrize("command", ["pause", "status"])
def test_missing_collector_times_out_and_cleans_up(fake_ros, command):
    endpoint, _calls, _snapshot = fake_ros
    endpoint.connections = 0
    with pytest.raises(RuntimeError, match="no (collection control subscriber|running collection program)"):
        client.run_control_command(command, timeout=0.001)
    assert endpoint.closed


def test_publish_error_is_reported_by_cli(fake_ros, monkeypatch, capsys):
    endpoint, _calls, _snapshot = fake_ros
    def fail(message):
        raise OSError("publish failed")
    endpoint.publish = fail
    monkeypatch.setattr("sys.argv", ["data_collect_dag", "run", "pause"])
    assert main() == 1
    assert "publish failed" in capsys.readouterr().err
    assert endpoint.closed


def test_transport_disconnect_during_publish_is_a_failure(fake_ros):
    endpoint, _calls, _snapshot = fake_ros
    def disconnect(message):
        endpoint.connections = 0
    endpoint.publish = disconnect
    with pytest.raises(RuntimeError, match="connection lost"):
        client.run_control_command("pause")
    assert endpoint.closed


def test_invalid_status_is_reported(fake_ros):
    _endpoint, _calls, snapshot = fake_ros
    snapshot.clear()
    with pytest.raises(RuntimeError, match="invalid status"):
        client.run_control_command("status")


def test_master_failure_occurs_before_ros_import(monkeypatch):
    def fail(timeout):
        raise RuntimeError("ROS Master unavailable")
    def forbidden():
        raise AssertionError("must not import ROS before checking master")
    monkeypatch.setattr(client, "wait_for_master", fail)
    monkeypatch.setattr(client, "_import_ros", forbidden)
    with pytest.raises(RuntimeError, match="ROS Master unavailable"):
        client.run_control_command("pause")


def test_master_probe_has_bounded_timeout(monkeypatch):
    probes = []
    class Proxy:
        def __init__(self, uri, transport):
            probes.append((uri, transport.timeout))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def getPid(self, caller):
            raise OSError("connection refused")
    monkeypatch.setattr(client, "ServerProxy", Proxy)
    with pytest.raises(RuntimeError, match="ROS Master unavailable"):
        client.wait_for_master(0.01)
    assert probes and 0 < probes[0][1] <= 0.01


@pytest.mark.parametrize("arguments", [
    ["run", "start", "fps"], ["run", "pause"], ["run", "resume"], ["run", "stop"], ["shutdown"],
])
def test_cli_control_overrides_without_config(fake_ros, monkeypatch, arguments):
    endpoint, calls, _snapshot = fake_ros
    timeouts = []
    monkeypatch.setattr(client, "wait_for_master", timeouts.append)
    monkeypatch.setattr("sys.argv", ["data_collect_dag"] + arguments + ["--control-topic", "/custom/control", "--timeout", "0.2"])
    assert main() == 0
    assert calls[1][1] == "/custom/control"
    assert endpoint.messages[0]["command"] == (arguments[1] if arguments[0] == "run" else arguments[0])
    assert timeouts == [0.2]


@pytest.mark.parametrize("arguments", [
    ["run", "start", "fps"], ["run", "pause"], ["run", "resume"], ["run", "stop"], ["shutdown"], ["status"],
])
def test_cli_missing_instance_fails(fake_ros, monkeypatch, capsys, arguments):
    endpoint, _calls, _snapshot = fake_ros
    endpoint.connections = 0
    monkeypatch.setattr("sys.argv", ["data_collect_dag"] + arguments + ["--timeout", "0.001"])
    assert main() == 1
    assert "within 0.001 seconds" in capsys.readouterr().err
    assert not endpoint.messages and endpoint.closed


@pytest.mark.parametrize("arguments", [
    ["run", "start"], ["run", "pause", "fps"], ["status", "--config", "cfg.yaml"], ["run", "stop", "--background"],
    ["run", "resume", "--pipeline", "fps"], ["--config", "cfg.yaml", "--timeout", "1"],
    ["status", "--timeout", "0"], ["status", "--timeout", "nan"], ["status", "--timeout", "inf"],
])
def test_control_argument_validation(arguments, monkeypatch):
    monkeypatch.setattr("sys.argv", ["data_collect_dag"] + arguments)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
