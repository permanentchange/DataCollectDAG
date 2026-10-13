from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from data_collect_dag.app import build_parser
from data_collect_dag.cli import main


def test_background_launch_uses_current_python_and_detaches(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "output with spaces"
    root.mkdir()
    log = root / "background.log"
    log.write_bytes(b"existing\n")
    config = tmp_path / "config.yaml"
    config.write_text(f"output:\n  root_dir: {root}\n")
    calls = []
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].write(b"new output\n")
        return SimpleNamespace(pid=31415)
    monkeypatch.setattr("data_collect_dag.cli.subprocess.Popen", popen)
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--config", "config.yaml", "--pipeline", "fps", "--background"])
    def forbidden(*args, **kwargs):
        raise AssertionError("parent must not construct a runtime")
    monkeypatch.setattr("data_collect_dag.cli.AppRuntime", forbidden)
    assert main() == 0
    command, kwargs = calls[0]
    assert command == [sys.executable, "-u", "-m", "data_collect_dag", "--config", str(config), "--pipeline", "fps"]
    assert kwargs["start_new_session"] is True and kwargs["close_fds"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.STDOUT
    assert "cwd" not in kwargs and "env" not in kwargs
    assert kwargs["stdout"].closed
    assert log.read_bytes() == b"existing\nnew output\n"
    printed = capsys.readouterr().out
    assert "PID=31415" in printed and str(log) in printed


@pytest.mark.parametrize("failure", ["missing_config", "bad_yaml", "unwritable_output", "spawn"])
def test_background_launch_reports_errors(tmp_path, monkeypatch, capsys, failure):
    config = tmp_path / "config.yaml"
    root = tmp_path / "output"
    if failure != "missing_config":
        config.write_text("output: [" if failure == "bad_yaml" else f"output:\n  root_dir: {root}\n")
    if failure == "unwritable_output":
        # A file at root_dir deterministically prevents creation, even as root.
        root.write_text("not a directory")
    def popen(*args, **kwargs):
        if failure == "spawn":
            raise OSError("spawn failed")
        raise AssertionError("invalid config/output must fail before spawning")
    monkeypatch.setattr("data_collect_dag.cli.subprocess.Popen", popen)
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--config", str(config), "--background"])
    assert main() == 1
    assert "background launch failed:" in capsys.readouterr().err


def test_background_and_clean_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--config", "config.yaml", "--clean", "0", "--background"])
    assert exc.value.code == 2


def test_foreground_remains_default(monkeypatch):
    calls = []
    class Runtime:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def run(self):
            return 7
    monkeypatch.setattr("data_collect_dag.cli.AppRuntime", Runtime)
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--config", "config.yaml"])
    assert main() == 7
    assert calls == [{"config_path": Path("config.yaml"), "pipeline_name": "init"}]
