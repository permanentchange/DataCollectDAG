"""Installer regression tests that cannot modify the test machine's Python packages."""

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
TORCH = "torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl"
TORCHVISION = "torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl"


def _prepare_installer(tmp_path, monkeypatch):
    home = tmp_path / "home"
    wheels = home / "wheels"
    wheels.mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "pip-commands.txt"

    # The installer still executes real Bash; the "python" command is
    # stubbed to prevent any actual pip uninstall/install in CI.
    wrapper = bindir / "python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -e\n"
        'if [[ "$1" == "-" ]]; then\n'
        '  printf "%s\\n" "torch==2.1.0a0+41361538.nv23.06" '
        '"torchvision==0.16.2+c6f3977" > "$2"\n'
        "else\n"
        '  printf "%s\\n" "$*" >> "$DCD_TEST_PYTHON_CALLS"\n'
        "fi\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("DCD_TEST_PYTHON_CALLS", str(log))
    return wheels, log


def _run(tmp_path, mode):
    return subprocess.run(
        ["bash", str(ROOT / "setup.bash"), mode],
        cwd=str(tmp_path),  # Installer must change to repo root itself.
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )


def _calls(log):
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_base_install_does_not_touch_inference_packages(tmp_path, monkeypatch):
    _, log = _prepare_installer(tmp_path, monkeypatch)
    result = _run(tmp_path, "base")

    assert result.returncode == 0, result.stderr
    assert _calls(log) == ["-m pip install -e ."]


def test_arm_fails_before_uninstall_when_torchvision_wheel_missing(
    tmp_path, monkeypatch
):
    wheels, log = _prepare_installer(tmp_path, monkeypatch)
    (wheels / TORCH).write_bytes(b"placeholder")
    result = _run(tmp_path, "arm")

    assert result.returncode != 0
    assert "torchvision wheel" in result.stderr
    assert _calls(log) == []  # Existing PyTorch is still installed.


def test_arm_reinstalls_both_local_wheels_before_ultralytics(tmp_path, monkeypatch):
    wheels, log = _prepare_installer(tmp_path, monkeypatch)
    (wheels / TORCH).write_bytes(b"placeholder")
    (wheels / TORCHVISION).write_bytes(b"placeholder")
    result = _run(tmp_path, "arm")

    assert result.returncode == 0, result.stderr
    commands = _calls(log)
    assert len(commands) == 3, commands
    assert commands[0] == "-m pip uninstall -y torch torchvision"
    assert commands[1] == (
        "-m pip install --no-deps {} {}".format(wheels / TORCH, wheels / TORCHVISION)
    )
    assert commands[2].startswith("-m pip install --constraint ")
    assert "--find-links {}".format(wheels) in commands[2]
    assert "-e .[yolo]" in commands[2]


def test_arm_requires_explicit_selection_if_multiple_nondefault_wheels(
    tmp_path, monkeypatch
):
    wheels, log = _prepare_installer(tmp_path, monkeypatch)
    torch_1 = wheels / "torch-1.0-cp38-cp38-linux_aarch64.whl"
    torch_2 = wheels / "torch-2.0-cp38-cp38-linux_aarch64.whl"
    vision = wheels / "torchvision-0.1-cp38-cp38-linux_aarch64.whl"
    for item in (torch_1, torch_2, vision):
        item.write_bytes(b"placeholder")
    result = _run(tmp_path, "arm")

    assert result.returncode != 0
    assert "DCD_TORCH_WHEEL" in result.stderr
    assert _calls(log) == []

    monkeypatch.setenv("DCD_TORCH_WHEEL", str(torch_2))
    result = _run(tmp_path, "arm")
    assert result.returncode == 0, result.stderr
    assert str(torch_2) in _calls(log)[1]


def test_onnx_mode_can_find_local_wheels(tmp_path, monkeypatch):
    wheels, log = _prepare_installer(tmp_path, monkeypatch)
    result = _run(tmp_path, "onnx-gpu")

    assert result.returncode == 0, result.stderr
    assert _calls(log) == [
        "-m pip install --find-links {} -e .[onnx-gpu]".format(wheels)
    ]
