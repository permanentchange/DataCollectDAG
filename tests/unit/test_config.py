from pathlib import Path

import pytest
import yaml

from data_collect_dag.config import ConfigError, load_app_config


def test_load_demo_config():
    config = load_app_config(Path("demo/xtreme1_demo.yaml"))
    assert "xtreme1_collect" in config.pipelines
    assert "localization" in config.topics
    assert config.runtime.debug is True


def test_missing_localization_config_fails():
    with pytest.raises(ConfigError):
        load_app_config(Path("demo/xtreme1_demo_missing_localization.yaml"))


def test_invalid_sample_workers_fails(tmp_path):
    payload = yaml.safe_load(Path("demo/xtreme1_demo.yaml").read_text(encoding="utf-8"))
    payload["runtime"]["sample_workers"] = 0
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="sample_workers"):
        load_app_config(path)
