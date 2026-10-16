"""Subcommand arguments fail before runtime or file work."""
import sys

import pytest

from data_collect_dag import __version__
from data_collect_dag.cli import build_parser, main


@pytest.mark.parametrize("arguments", [
    [], ["--config", "cfg.yaml"], ["--init-demo", "config"], ["--clean", "0"],
    ["run"], ["inspect"], ["init"], ["clean", "--config", "cfg.yaml"],
    ["run", "--config", "cfg.yaml", "--force"],
    ["run", "--config", "cfg.yaml", "--car-id", "sa4"],
    ["init", "config", "--pipeline", "fps"], ["init", "config", "--background"],
    ["inspect", "--config", "cfg.yaml", "--background"],
    ["clean", "--config", "cfg.yaml", "--keep-days", "-1"],
    ["clean", "--config", "cfg.yaml", "--keep-days", "1.5"],
    ["clean", "--config", "cfg.yaml", "--keep-days", "0", "--pipeline", "fps"],
    ["status", "--control-topic", "/control"], ["pause", "--status-topic", "/status"],
    ["run", "--config", "cfg.yaml", "--background", "--instance-lock-fd", "3"],
])
def test_invalid_arguments_do_no_work(arguments, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid arguments must not dispatch")
    for name in ("_init", "_inspect", "_run", "_clean", "_control"):
        monkeypatch.setattr("data_collect_dag.cli." + name, forbidden)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["data_collect_dag"] + arguments)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", [None, "init", "inspect", "run", "clean", "start", "pause", "resume", "stop", "shutdown", "status"])
def test_help(command, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag"] + ([command] if command else []) + ["--help"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    output = capsys.readouterr()
    assert not output.err
    assert "usage:" in output.out
    if command is None:
        assert all(name in output.out for name in ("init", "inspect", "run", "clean", "status"))
        assert "--config" not in output.out


def test_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--version"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_run_defaults_and_control_timeout():
    assert build_parser().parse_args(["run", "--config", "cfg.yaml"]).pipeline == "init"
    assert build_parser().parse_args(["status"]).timeout == 5.0
