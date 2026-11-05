"""Regression checks for the declared third-party dependencies.

Uses stdlib importlib.metadata and does not import optional runtime packages.
"""
from configparser import ConfigParser
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
import pytest

from data_collect_dag import __version__


def _matches_extra(requirement, extra):
    # Legacy setuptools spells extra names with underscores in wheel metadata.
    return any(requirement.marker.evaluate({"extra": alias})
               for alias in (extra, extra.replace("-", "_")))


def test_source_version_matches_project_metadata():
    project = (Path(__file__).resolve().parents[2] / "setup.cfg").read_text(encoding="utf-8")
    config = ConfigParser()
    config.read_string(project)
    declared = config["metadata"]["version"]
    assert __version__ == declared


def test_runtime_dependencies_are_declared():
    requirements = metadata.requires("data-collect-dag") or []
    names = {item.split(";")[0].split(" ")[0].split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip().lower() for item in requirements if "extra ==" not in item}
    assert names == {"numpy", "pyyaml", "pillow", "rospkg"}


def test_optional_dependency_groups_are_declared():
    dist = metadata.distribution("data-collect-dag")
    extras = {extra.replace("_", "-") for extra in dist.metadata.get_all("Provides-Extra") or []}
    assert extras == {"base", "opencv", "yolo", "yolo-arm", "dev", "yolo-dev"}


@pytest.mark.parametrize("mode, expected", [
    ("base", set()),
    ("opencv", {"opencv-python"}),
    ("yolo", {"ultralytics", "opencv-python"}),
    ("yolo-arm", {"ultralytics", "opencv-python", "psutil"}),
    ("dev", {"pytest"}),
    ("yolo-dev", {"ultralytics", "opencv-python", "pytest"}),
])
def test_installation_mode_dependencies(mode, expected):
    requirements = [Requirement(line) for line in metadata.requires("data-collect-dag") or []]
    base = {requirement.name for requirement in requirements if requirement.marker is None}
    selected = {
        requirement.name for requirement in requirements
        if requirement.marker is None or _matches_extra(requirement, mode)
    }
    assert selected - base == expected


def test_arm_psutil_dependency_is_pinned():
    requirements = [Requirement(line) for line in metadata.requires("data-collect-dag") or []]
    psutil = [requirement for requirement in requirements if requirement.name == "psutil"]
    assert len(psutil) == 1
    assert str(psutil[0].specifier) == "==6.1.1"
    assert psutil[0].marker is not None
    assert _matches_extra(psutil[0], "yolo-arm")


def test_opencv_version_range_and_optional_groups():
    requirements = [Requirement(line) for line in metadata.requires("data-collect-dag") or []]
    opencv = [requirement for requirement in requirements if requirement.name == "opencv-python"]
    assert opencv
    for requirement in opencv:
        assert requirement.marker is not None
        assert "4.2.0" in requirement.specifier
        assert "4.11.0.86" in requirement.specifier
        assert "4.1.0" not in requirement.specifier
        assert "4.12.0" not in requirement.specifier
        assert not requirement.marker.evaluate({"extra": ""})


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
