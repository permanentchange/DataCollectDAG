"""Offline export of bundled demo templates; no ROS or inference dependencies."""

from __future__ import annotations

import io
import os
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any, Dict
from zipfile import ZipFile

import yaml


def read_templates() -> Dict[str, bytes]:
    module_path = Path(__file__).resolve()
    checkout = module_path.parents[2]
    demo = checkout / "demo"
    if module_path.parent.parent.name == "src" and (checkout / "pyproject.toml").is_file() and demo.is_dir():
        # Editable installs use live templates, rather than a stale build artifact.
        assets = sorted(list(demo.glob("*.yaml")) + list(demo.glob("*.yml"))
                        + list((demo / "calibration").glob("*.json")))
        return {asset.relative_to(demo).as_posix(): asset.read_bytes() for asset in assets}
    with ZipFile(io.BytesIO(resources.read_binary("data_collect_dag", "_demo.zip"))) as bundle:
        return {name: bundle.read(name) for name in bundle.namelist()}


def _demo_path(value: Any, destination: Path) -> Any:
    if isinstance(value, str) and value.startswith("demo/"):
        relative = Path(value).relative_to("demo")
        if ".." in relative.parts:
            raise ValueError(f"unsafe demo reference: {value}")
        return str(destination / relative)
    return value


def _rewrite_config(payload: bytes, destination: Path) -> bytes:
    config = yaml.safe_load(payload)
    if not isinstance(config, dict):
        raise ValueError("demo configuration must be a YAML mapping")
    calibration = config.get("calibration")
    if isinstance(calibration, dict) and "path" in calibration:
        calibration["path"] = _demo_path(calibration["path"], destination)

    def rewrite_models(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "model_path":
                    value[key] = _demo_path(item, destination)
                else:
                    rewrite_models(item)
        elif isinstance(value, list):
            for item in value:
                rewrite_models(item)

    rewrite_models(config)
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True).encode("utf-8")


def _check_directory(directory: Path, destination: Path) -> None:
    # The destination itself may be a user-selected symlink; it is already resolved.
    for ancestor in [directory] + list(directory.parents):
        if ancestor == destination.parent:
            break
        try:
            ancestor.resolve().relative_to(destination)
        except ValueError:
            raise ValueError(f"directory symlink escapes demo destination: {ancestor}")
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError(f"expected a directory: {ancestor}")
        if ancestor.is_symlink() and not ancestor.exists():
            raise ValueError(f"dangling directory symlink: {ancestor}")


def export_demo(destination: Path, force: bool = False) -> Path:
    destination = destination.expanduser().resolve()
    templates = read_templates()
    if not templates:
        raise ValueError("no demo templates found")
    files: Dict[Path, bytes] = {}
    for name, payload in templates.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(f"unsafe demo template path: {name}")
        target = destination / relative
        _check_directory(target.parent, destination)
        if target.is_dir() and not target.is_symlink():
            raise ValueError(f"template destination is a directory: {target}")
        if not force and (target.exists() or target.is_symlink()):
            raise ValueError(f"demo template already exists: {target}; choose another directory or use --force")
        files[target] = _rewrite_config(payload, destination) if relative.suffix in {".yaml", ".yml"} else payload
    models = destination / "models"
    _check_directory(models, destination)

    # Validate every filename before making changes. Each replacement is atomic and
    # never follows a destination file symlink, even when --force is requested.
    models.mkdir(parents=True, exist_ok=True)
    for target, payload in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
            if force:
                os.replace(str(temporary), str(target))
            else:
                # Also refuse a filename created concurrently after preflight.
                os.link(str(temporary), str(target))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return destination
