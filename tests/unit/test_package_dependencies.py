"""Regression checks for the declared third-party dependencies.

Uses stdlib importlib.metadata and does not import optional runtime packages.
"""
from importlib import metadata


def test_runtime_dependencies_are_declared():
    requirements = metadata.requires("data-collect-dag") or []
    names = {item.split(";")[0].split(" ")[0].split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip().lower() for item in requirements if "extra ==" not in item}
    assert {"numpy", "pyyaml", "pillow", "opencv-python", "rospkg"} <= names


def test_optional_dependency_groups_are_declared():
    dist = metadata.distribution("data-collect-dag")
    extras = set(dist.metadata.get_all("Provides-Extra") or [])
    assert {"yolo", "yolo-export", "tools", "test"} <= extras
