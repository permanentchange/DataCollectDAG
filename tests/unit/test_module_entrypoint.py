import runpy

import pytest

from data_collect_dag import cli


def test_module_entrypoint_invokes_cli_main(monkeypatch):
    calls = []

    def fake_main() -> int:
        calls.append("called")
        return 7

    monkeypatch.setattr(cli, "main", fake_main)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("data_collect_dag", run_name="__main__")

    assert excinfo.value.code == 7
    assert calls == ["called"]
