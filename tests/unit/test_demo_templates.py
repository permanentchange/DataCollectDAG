from pathlib import Path
import sys

import pytest
import yaml

from data_collect_dag.app import load_calibration
from data_collect_dag.cli import main
from data_collect_dag.config import load_app_config
from data_collect_dag.demo_templates import export_demo, read_templates


def test_export_rewrites_paths_and_loads_config_and_calibrations(tmp_path, monkeypatch):
    destination = tmp_path / "demo with spaces '引号'"
    templates = read_templates()
    assert export_demo(destination) == destination
    assert (destination / "models").is_dir()
    assert not list((destination / "models").iterdir())
    assert (destination / "calibration/cali.json").is_file()
    assert not (destination / "calibration/cali.json").is_symlink()
    assert (destination / "calibration/cali.json").read_bytes() == (destination / "calibration/sa4_cali.json").read_bytes()
    exported = yaml.safe_load((destination / "cfg.yaml").read_text())
    original = yaml.safe_load(templates["cfg.yaml"])
    assert exported["output"] == original["output"]
    assert exported["calibration"]["path"] == str(destination / "calibration/cali.json")
    for name, pipeline in exported["pipelines"].items():
        for node in pipeline["nodes"]:
            if "model_path" in node.get("config", {}):
                assert node["config"]["model_path"] == str(destination / "models/yolo26m.pt")
                node["config"]["model_path"] = "demo/models/yolo26m.pt"
        assert pipeline == original["pipelines"][name]
    monkeypatch.chdir(tmp_path)
    config = load_app_config(destination / "cfg.yaml")
    assert "fps_undist" in config.pipelines
    assert load_calibration(config.calibration_path)["images"]
    for name, payload in templates.items():
        if name.endswith(".json"):
            assert (destination / name).read_bytes() == payload
            assert load_calibration(destination / name)["images"]


def test_conflict_is_preflighted_before_any_writes(tmp_path):
    destination = tmp_path / "demo"
    conflict = destination / "calibration/sa27_cali.json"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("user calibration")
    with pytest.raises(ValueError, match="--force"):
        export_demo(destination)
    assert conflict.read_text() == "user calibration"
    assert not (destination / "cfg.yaml").exists()
    assert not (destination / "models").exists()
    assert list(conflict.parent.iterdir()) == [conflict]


def test_force_only_overwrites_templates(tmp_path):
    destination = export_demo(tmp_path / "demo")
    (destination / "cfg.yaml").write_text("user config")
    weight = destination / "models/yolo26m.pt"
    weight.write_bytes(b"user model")
    extra = destination / "calibration/custom.json"
    extra.write_text("user calibration")
    export_demo(destination, force=True)
    assert "pipelines" in yaml.safe_load((destination / "cfg.yaml").read_text())
    assert weight.read_bytes() == b"user model"
    assert extra.read_text() == "user calibration"


@pytest.mark.parametrize("dangling", [False, True])
def test_file_symlinks_are_not_followed(tmp_path, dangling):
    destination = tmp_path / "demo"
    destination.mkdir()
    external = tmp_path / "external.yaml"
    if not dangling:
        external.write_text("outside data")
    (destination / "cfg.yaml").symlink_to(external)
    with pytest.raises(ValueError, match="--force"):
        export_demo(destination)
    export_demo(destination, force=True)
    assert not (destination / "cfg.yaml").is_symlink()
    if dangling:
        assert not external.exists()
    else:
        assert external.read_text() == "outside data"


@pytest.mark.parametrize("name", ["calibration", "models"])
def test_external_directory_symlinks_are_rejected_before_writes(tmp_path, name):
    destination = tmp_path / "demo"
    destination.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    (destination / name).symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink escapes"):
        export_demo(destination, force=True)
    assert not (destination / "cfg.yaml").exists()
    assert not list(external.iterdir())


def test_internal_directory_symlink_and_destination_alias_are_allowed(tmp_path):
    destination = tmp_path / "demo"
    (destination / "actual-calibration").mkdir(parents=True)
    (destination / "calibration").symlink_to("actual-calibration", target_is_directory=True)
    alias = tmp_path / "alias"
    alias.symlink_to(destination, target_is_directory=True)
    assert export_demo(alias) == destination
    assert (destination / "actual-calibration/cali.json").is_file()


@pytest.mark.parametrize("name", ["cfg.yaml", "calibration", "models"])
def test_nonreplaceable_directory_or_parent_file_is_preflighted(tmp_path, name):
    destination = tmp_path / "demo"
    destination.mkdir()
    target = destination / name
    if name == "cfg.yaml":
        target.mkdir()
    else:
        target.write_text("not a directory")
    with pytest.raises(ValueError, match="directory"):
        export_demo(destination, force=True)
    assert not (destination / "calibration/cali.json").exists()


def test_editable_install_uses_live_demo_files(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    demo = root / "demo"
    demo.mkdir(parents=True)
    (root / "pyproject.toml").touch()
    (demo / "custom.yaml").write_text("value: live\n")
    monkeypatch.setattr("data_collect_dag.demo_templates.__file__", str(root / "src/data_collect_dag/demo_templates.py"))
    assert read_templates() == {"custom.yaml": b"value: live\n"}
    (demo / "custom.yaml").write_text("value: changed\n")
    assert read_templates() == {"custom.yaml": b"value: changed\n"}


@pytest.mark.parametrize("arguments", [
    [], ["--init-demo", "demo", "--config", "cfg.yaml"],
    ["--init-demo", "demo", "--background"],
    ["--init-demo", "demo", "--clean", "0"],
    ["--config", "cfg.yaml", "--force"],
])
def test_invalid_cli_modes(arguments, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["data_collect_dag"] + arguments)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_cli_exports_without_runtime_and_reports_conflicts(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "demo"
    def forbidden(*args, **kwargs):
        raise AssertionError("export must not construct the runtime")
    monkeypatch.setattr("data_collect_dag.cli.AppRuntime", forbidden)
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--init-demo", str(destination), "--pipeline", "fps"])
    assert main() == 0
    assert "--pipeline fps_undist" in capsys.readouterr().out
    assert main() == 1
    assert "--force" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["data_collect_dag", "--init-demo", str(destination), "--force"])
    assert main() == 0
