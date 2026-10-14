"""Action-specific CLI guidance must fail before doing any work."""

import sys

import pytest

from data_collect_dag.cli import main


@pytest.fixture
def no_cli_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("help and invalid arguments must not perform any work")

    for name in ["data_collect_dag.app.AppRuntime", "data_collect_dag.config.load_output_config",
                 "data_collect_dag.storage.clean_sessions", "data_collect_dag.cli.start_background",
                 "data_collect_dag.demo_templates.export_demo", "data_collect_dag.cli.load_node_name"]:
        monkeypatch.setattr(name, forbidden)


@pytest.mark.parametrize("arguments,message", [
    ([], "provide --config CONFIG to run collection, or --init-demo DIR to export templates; see --help"),
    (["--clean", "1"], "--clean requires --config CONFIG to determine output.root_dir"),
    (["--clean", "0"], "--clean requires --config CONFIG to determine output.root_dir"),
    (["--background"], "--background requires --config CONFIG"),
    (["--pipeline", "fps"], "--config CONFIG is required to run collection"),
    (["--pipeline", "init"], "--config CONFIG is required to run collection"),
    (["--force"], "--force requires --init-demo"),
    (["--config", "cfg.yaml", "--force"], "--force requires --init-demo"),
    (["--car-id", "sa4"], "--car-id requires --init-demo"),
    (["--config", "cfg.yaml", "--car-id", "sa4"], "--car-id requires --init-demo"),
    (["--config", "cfg.yaml", "--clean", "1", "--car-id", "sa4"], "--car-id requires --init-demo"),
    (["--init-demo", "demo", "--clean", "0"], "--init-demo cannot be combined with --clean"),
    (["--init-demo", "demo", "--background"], "--init-demo cannot be combined with --background"),
])
def test_action_specific_errors(arguments, message, no_cli_work, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    session = tmp_path / "output/20250101_120000_abcdef12"
    session.mkdir(parents=True)
    sentinel = session / "data"
    sentinel.write_bytes(b"preserve collected data")
    monkeypatch.setattr(sys, "argv", ["data_collect_dag"] + arguments)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "error: " + message in captured.err
    assert "one of the arguments --config --init-demo is required" not in captured.err
    assert not captured.out
    assert sentinel.read_bytes() == b"preserve collected data"
    assert not (tmp_path / "demo").exists()


@pytest.mark.parametrize("arguments,message", [
    (["--clean"], "argument --clean: expected one argument"),
    (["--init-demo", "demo", "--car-id"], "argument --car-id: expected one argument"),
    (["--config", "cfg.yaml", "--clean", "-1"], "--clean must be a non-negative integer"),
    (["--config", "cfg.yaml", "--clean", "1.5"], "--clean must be a non-negative integer"),
    (["--config", "cfg.yaml", "--init-demo", "demo"], "not allowed with argument --config"),
    (["--config", "cfg.yaml", "--clean", "0", "--background"], "not allowed with argument --clean"),
])
def test_argparse_validation_is_preserved(arguments, message, no_cli_work, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag"] + arguments)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_help_lists_operation_forms_and_requirements(no_cli_work, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--help"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    captured = capsys.readouterr()
    assert not captured.err
    for text in [
        "data_collect_dag --config CONFIG [--pipeline PIPELINE] [--background]",
        "data_collect_dag --config CONFIG --clean DAY",
        "data_collect_dag --init-demo DIR [--car-id CAR_ID] [--force]",
        "YAML configuration file; required for collection and cleanup",
        "no config or ROS needed",
        "default: init",
        "ignored during cleanup/export",
        "0=delete all, 1=keep today, 2=keep today and yesterday",
        "requires --config; stop collection before cleanup",
        "only valid with --init-demo",
        "if omitted, export all cars and use sa4 in the configuration",
    ]:
        assert text in captured.out
