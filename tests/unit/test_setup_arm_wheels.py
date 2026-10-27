"""Exercise real Bash and pip resolution without changing installed packages."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TORCH = "torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl"
TORCHVISION = "torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl"


def _prepare_installer(tmp_path, monkeypatch):
    wheels = tmp_path / "home" / "wheels"
    wheels.mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "pip-commands.jsonl"

    # Execute the actual constraint-generation code; only mock pip mutations.
    wrapper = bindir / "python"
    wrapper.write_text(
        "#!{}\n".format(sys.executable)
        + "import json, os, shutil, sys\n"
        + "args = sys.argv[1:]\n"
        + "if args[0] == '-':\n"
        + "    os.execv(os.environ['DCD_TEST_REAL_PYTHON'], "
          "[os.environ['DCD_TEST_REAL_PYTHON']] + args)\n"
        + "with open(os.environ['DCD_TEST_PYTHON_CALLS'], 'a') as log:\n"
        + "    log.write(json.dumps(args) + '\\n')\n"
        + "if '--constraint' in args:\n"
        + "    source = args[args.index('--constraint') + 1]\n"
        + "    shutil.copyfile(source, os.environ['DCD_TEST_CONSTRAINTS'])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("HOME", str(wheels.parent))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("DCD_TEST_PYTHON_CALLS", str(log))
    monkeypatch.setenv("DCD_TEST_REAL_PYTHON", sys.executable)
    monkeypatch.setenv("DCD_TEST_CONSTRAINTS", str(log.with_suffix(".constraints")))
    for variable in ("DCD_WHEEL_DIR", "DCD_TORCH_WHEEL", "DCD_TORCHVISION_WHEEL"):
        monkeypatch.delenv(variable, raising=False)
    return wheels, log


def _run(tmp_path, mode=None):
    command = ["bash", str(ROOT / "setup.bash")]
    if mode is not None:
        command.append(mode)
    return subprocess.run(
        command,
        cwd=str(tmp_path),  # Installer must change to repo root itself.
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )


def _calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("mode", [None, "base"])
def test_base_install_does_not_touch_inference_packages(tmp_path, monkeypatch, mode):
    _, log = _prepare_installer(tmp_path, monkeypatch)
    result = _run(tmp_path, mode)

    assert result.returncode == 0, result.stderr
    assert _calls(log) == [["-m", "pip", "install", "-e", "."]]


@pytest.mark.parametrize("mode", ["yolo", "dev", "yolo-dev"])
def test_optional_installation_modes(tmp_path, monkeypatch, mode):
    _, log = _prepare_installer(tmp_path, monkeypatch)
    result = _run(tmp_path, mode)

    assert result.returncode == 0, result.stderr
    assert _calls(log) == [["-m", "pip", "install", "-e", ".[{}]".format(mode)]]


@pytest.mark.parametrize("mode", [
    "cpu", "gpu", "arm", "onnx-cpu", "onnx-gpu", "yolo-export", "tools", "test",
    "yolo-arm-dev", "unknown",
])
def test_removed_and_unknown_modes_fail_without_pip(tmp_path, monkeypatch, mode):
    _, log = _prepare_installer(tmp_path, monkeypatch)
    result = _run(tmp_path, mode)

    assert result.returncode != 0
    assert "[base|yolo|yolo-arm|dev|yolo-dev]" in result.stderr
    assert _calls(log) == []


@pytest.mark.parametrize("missing", [TORCH, TORCHVISION])
def test_arm_fails_before_uninstall_when_wheel_missing(tmp_path, monkeypatch, missing):
    wheels, log = _prepare_installer(tmp_path, monkeypatch)
    for name in (TORCH, TORCHVISION):
        if name != missing:
            (wheels / name).write_bytes(b"placeholder")
    result = _run(tmp_path, "yolo-arm")

    assert result.returncode != 0
    assert "wheel" in result.stderr
    assert _calls(log) == []


def test_arm_fails_before_uninstall_when_directory_missing(tmp_path, monkeypatch):
    _, log = _prepare_installer(tmp_path, monkeypatch)
    monkeypatch.setenv("DCD_WHEEL_DIR", str(tmp_path / "missing"))
    result = _run(tmp_path, "yolo-arm")

    assert result.returncode != 0
    assert "directory does not exist" in result.stderr
    assert _calls(log) == []


def test_arm_installs_local_wheels_before_yolo_with_file_constraints(tmp_path, monkeypatch):
    _, log = _prepare_installer(tmp_path, monkeypatch)
    wheels = tmp_path / "Jetson wheels"
    wheels.mkdir()
    monkeypatch.setenv("DCD_WHEEL_DIR", str(wheels))
    for name in (TORCH, TORCHVISION):
        (wheels / name).write_bytes(b"placeholder")
    result = _run(tmp_path, "yolo-arm")

    assert result.returncode == 0, result.stderr
    commands = _calls(log)
    assert len(commands) == 3
    assert commands[0] == ["-m", "pip", "uninstall", "-y", "torch", "torchvision"]
    assert commands[1] == [
        "-m", "pip", "install", "--no-deps", str(wheels / TORCH), str(wheels / TORCHVISION),
    ]
    constraint_file = commands[2][4]
    assert commands[2] == [
        "-m", "pip", "install", "--constraint", constraint_file,
        "--no-binary=psutil", "--find-links", str(wheels), "-e", ".[yolo-arm]",
    ]
    assert log.with_suffix(".constraints").read_text().splitlines() == [
        "torch @ " + (wheels / TORCH).as_uri(),
        "torchvision @ " + (wheels / TORCHVISION).as_uri(),
    ]
    assert not Path(constraint_file).exists()


def test_arm_requires_explicit_selection_if_multiple_nondefault_wheels(tmp_path, monkeypatch):
    wheels, log = _prepare_installer(tmp_path, monkeypatch)
    torch_1 = wheels / "torch-1.0-cp38-cp38-linux_aarch64.whl"
    torch_2 = wheels / "torch-2.0-cp38-cp38-linux_aarch64.whl"
    vision = wheels / "torchvision-0.1-cp38-cp38-linux_aarch64.whl"
    for item in (torch_1, torch_2, vision):
        item.write_bytes(b"placeholder")
    result = _run(tmp_path, "yolo-arm")

    assert result.returncode != 0
    assert "DCD_TORCH_WHEEL" in result.stderr
    assert _calls(log) == []

    monkeypatch.setenv("DCD_TORCH_WHEEL", str(torch_2))
    result = _run(tmp_path, "yolo-arm")
    assert result.returncode == 0, result.stderr
    assert str(torch_2) in _calls(log)[1]


def _make_wheel(directory, name, version, requirements=()):
    """Minimal portable wheels let real pip test resolution on non-Jetson CI."""
    path = directory / "{}-{}-py3-none-any.whl".format(name, version)
    dist_info = "{}-{}.dist-info".format(name, version)
    metadata = "Metadata-Version: 2.1\nName: {}\nVersion: {}\n".format(name, version)
    metadata += "".join("Requires-Dist: {}\n".format(item) for item in requirements)
    with ZipFile(path, "w") as wheel:
        wheel.writestr(dist_info + "/METADATA", metadata)
        wheel.writestr(
            dist_info + "/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(dist_info + "/RECORD", "")
    return path


def _make_sdist(directory, name, version):
    path = directory / "{}-{}.tar.gz".format(name, version)
    sources = {
        "setup.py": "from setuptools import setup\nsetup(name={!r}, version={!r}, packages=[])\n".format(name, version),
        "pyproject.toml": '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n',
    }
    with tarfile.open(path, "w:gz") as archive:
        for filename, content in sources.items():
            data = content.encode("utf-8")
            member = tarfile.TarInfo("{}-{}/{}".format(name, version, filename))
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return path


@pytest.mark.parametrize("conflicting", [False, True])
def test_real_pip_uses_psutil_source_and_selected_torch_wheels(tmp_path, monkeypatch, conflicting):
    pip_environment = os.environ.copy()
    wheels, log = _prepare_installer(tmp_path, monkeypatch)
    torch = _make_wheel(wheels, "torch", "1.0")
    vision = _make_wheel(wheels, "torchvision", "1.0", ["torch==1.0"])
    installer = _run(tmp_path, "yolo-arm")
    assert installer.returncode == 0, installer.stderr

    # Stand-in index supplies both same-version and newer alternative builds.
    index = tmp_path / "index"
    index.mkdir()
    _make_wheel(index, "torch", "1.0")
    _make_wheel(index, "torch", "2.0")
    _make_wheel(index, "torchvision", "1.0", ["torch>=1.0"])
    _make_wheel(index, "psutil", "6.1.1")
    psutil = _make_sdist(index, "psutil", "6.1.1")
    _make_wheel(index, "dcd_test_yolo", "1.0", [
        "torch>=2.0" if conflicting else "torch>=1.0", "torchvision>=1.0",
        "psutil==6.1.1",
    ])
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check",
            "install", "--dry-run", "--ignore-installed", "--no-index",
            "--no-binary=psutil", "--no-build-isolation",
            "--find-links", str(index), "--constraint", str(log.with_suffix(".constraints")),
            "--report", str(report), "dcd_test_yolo",
        ],
        env=pip_environment,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    if conflicting:
        assert result.returncode != 0
        assert "ResolutionImpossible" in result.stderr
        assert not report.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        sources = {
            item["metadata"]["name"]: item["download_info"]["url"]
            for item in json.loads(report.read_text())["install"]
        }
        assert sources["torch"] == torch.as_uri()
        assert sources["torchvision"] == vision.as_uri()
        assert sources["psutil"] == psutil.as_uri()
