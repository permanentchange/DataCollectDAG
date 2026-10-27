"""Regression checks for the declared third-party dependencies.

Uses stdlib importlib.metadata and does not import optional runtime packages.
"""
from importlib import metadata
from pathlib import Path
import re

from packaging.requirements import Requirement
import pytest

from data_collect_dag import __version__


def test_source_version_matches_project_metadata():
    project = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE).group(1)
    assert __version__ == declared


def test_runtime_dependencies_are_declared():
    requirements = metadata.requires("data-collect-dag") or []
    names = {item.split(";")[0].split(" ")[0].split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip().lower() for item in requirements if "extra ==" not in item}
    assert {"numpy", "pyyaml", "pillow", "opencv-python", "rospkg"} <= names


def test_optional_dependency_groups_are_declared():
    dist = metadata.distribution("data-collect-dag")
    extras = set(dist.metadata.get_all("Provides-Extra") or [])
    assert extras == {"base", "yolo", "yolo-arm", "dev", "yolo-dev"}


@pytest.mark.parametrize("mode, expected", [
    ("base", set()),
    ("yolo", {"ultralytics"}),
    ("yolo-arm", {"ultralytics", "psutil"}),
    ("dev", {"pytest"}),
    ("yolo-dev", {"ultralytics", "pytest"}),
])
def test_installation_mode_dependencies(mode, expected):
    requirements = [Requirement(line) for line in metadata.requires("data-collect-dag") or []]
    base = {requirement.name for requirement in requirements if requirement.marker is None}
    selected = {
        requirement.name for requirement in requirements
        if requirement.marker is None or requirement.marker.evaluate({"extra": mode})
    }
    assert selected - base == expected


def test_arm_psutil_dependency_is_pinned():
    requirements = [Requirement(line) for line in metadata.requires("data-collect-dag") or []]
    psutil = [requirement for requirement in requirements if requirement.name == "psutil"]
    assert len(psutil) == 1
    assert str(psutil[0].specifier) == "==6.1.1"
    assert psutil[0].marker is not None
    assert psutil[0].marker.evaluate({"extra": "yolo-arm"})


def test_inference_backends_not_required_by_base_install():
    requirements = metadata.requires("data-collect-dag") or []
    base_requirements = [
        line.split(";")[0].strip().lower()
        for line in requirements
        if "extra ==" not in line
    ]
    for backend in ("ultralytics", "torch", "torchvision", "onnxruntime", "onnx"):
        assert not any(
            requirement == backend
            or requirement.startswith(backend + " ")
            or requirement.startswith(backend + ">")
            or requirement.startswith(backend + "<")
            or requirement.startswith(backend + "=")
            for requirement in base_requirements
        ), "{} must remain an optional dependency".format(backend)
