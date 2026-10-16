"""Subcommand arguments fail before runtime or file work."""
import sys

import pytest

from data_collect_dag import __version__
from data_collect_dag.cli import build_parser, main


@pytest.mark.parametrize("arguments", [
    [], ["--config", "cfg.yaml"], ["--init-demo", "config"], ["--clean", "0"],
    ["run", "launch"], ["inspect"], ["init"], ["clean", "--config", "cfg.yaml"],
    ["run", "launch", "--config", "cfg.yaml", "--force"],
    ["run", "launch", "--config", "cfg.yaml", "--car-id", "sa4"],
    ["run", "--config", "cfg.yaml"],
    ["run", "--config", "cfg.yaml", "launch"],
    ["run", "--pipeline", "fps", "launch", "--config", "cfg.yaml"],
    ["run", "--background", "launch", "--config", "cfg.yaml"],
    ["start", "fps"], ["pause"], ["resume"], ["stop"],
    ["run", "status"], ["run", "shutdown"], ["launch", "--config", "cfg.yaml"],
    ["init", "config", "--pipeline", "fps"], ["init", "config", "--background"],
    ["inspect", "--config", "cfg.yaml", "--background"],
    ["clean", "--config", "cfg.yaml", "--keep-days", "-1"],
    ["clean", "--config", "cfg.yaml", "--keep-days", "1.5"],
    ["clean", "--config", "cfg.yaml", "--keep-days", "0", "--pipeline", "fps"],
    ["status", "--control-topic", "/control"], ["run", "pause", "--status-topic", "/status"],
    ["run", "launch", "--config", "cfg.yaml", "--background", "--instance-lock-fd", "3"],
    ["run", "start", "fps", "--instance-lock-fd", "3"],
    ["run", "launch", "--config", "cfg.yaml", "--timeout", "1"],
])
def test_invalid_arguments_do_no_work(arguments, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid arguments must not dispatch")
    for name in ("_init", "_inspect", "_launch", "_clean", "_control"):
        monkeypatch.setattr("data_collect_dag.cli." + name, forbidden)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["data_collect_dag"] + arguments)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("arguments", [
    [], ["init"], ["inspect"], ["run"], ["clean"], ["shutdown"], ["status"],
    ["run", "launch"], ["run", "start"], ["run", "pause"], ["run", "resume"], ["run", "stop"],
])
def test_help(arguments, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag"] + arguments + ["--help"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    output = capsys.readouterr()
    assert not output.err
    assert "usage:" in output.out
    if not arguments:
        assert all(name in output.out for name in ("init", "inspect", "run", "clean", "status"))
        assert "--config" not in output.out
    if arguments == ["run"]:
        assert "{launch,start,pause,resume,stop}" in output.out
        assert "--config" not in output.out
    if arguments == ["run", "launch"]:
        assert all(option in output.out for option in ("--config", "--pipeline", "--background"))
        assert "--instance-lock-fd" not in output.out


def test_bare_run_prints_group_help_without_work(monkeypatch, capsys, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("run without an action must only print help")
    monkeypatch.setattr("data_collect_dag.cli._launch", forbidden)
    monkeypatch.setattr("data_collect_dag.cli._control", forbidden)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "run"])
    assert main() == 0
    output = capsys.readouterr()
    assert "{launch,start,pause,resume,stop}" in output.out
    assert not output.err and not list(tmp_path.iterdir())


def test_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--version"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_run_defaults_and_control_timeout():
    launch = build_parser().parse_args(["run", "launch", "--config", "cfg.yaml"])
    assert (launch.command, launch.run_command, launch.pipeline, launch.background) == ("run", "launch", "init", False)
    for arguments in (["run", "start", "fps"], ["run", "pause"], ["run", "resume"], ["run", "stop"], ["shutdown"]):
        args = build_parser().parse_args(arguments)
        assert args.timeout == 5.0 and args.control_topic == "/data_collect/control"
    status = build_parser().parse_args(["status"])
    assert status.timeout == 5.0 and status.status_topic == "/data_collect/status"
