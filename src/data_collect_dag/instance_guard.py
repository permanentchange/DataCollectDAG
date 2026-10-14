from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Optional

import yaml


class InstanceAlreadyRunningError(RuntimeError):
    pass


def resolve_node_name(node_name: str) -> str:
    """Resolve YAML names using ROS_NAMESPACE without importing ROS or the runtime."""
    if not node_name or node_name.startswith("~"):
        raise ValueError("ros.node_name must be a non-empty ROS node name")
    namespace = os.environ.get("ROS_NAMESPACE", "/")
    name = node_name if node_name.startswith("/") else namespace + "/" + node_name
    resolved = "/" + "/".join(part for part in name.split("/") if part)
    if not re.fullmatch(r"/[A-Za-z0-9_/]+", resolved) or resolved == "/":
        raise ValueError(f"invalid ROS node name: {node_name}")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", resolved.rsplit("/", 1)[-1]):
        raise ValueError(f"invalid ROS node name: {node_name}")
    return resolved


def load_node_name(config_path: Path) -> str:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    ros = raw.get("ros") or {}
    if not isinstance(ros, dict):
        raise ValueError("ros must be a mapping")
    return resolve_node_name(str(ros.get("node_name", "data_collect_dag")))


def lock_path(node_name: str) -> Path:
    # A fixed host-local directory: neither TMPDIR nor the user/config/output
    # directory may change the identity of an instance lock.
    digest = hashlib.sha256(node_name.encode("utf-8")).hexdigest()
    return Path("/tmp") / f"data_collect_dag-{digest}.lock"


class InstanceGuard:
    """Hold an flock until the last inherited descriptor is closed."""

    def __init__(self, node_name: str, inherited_fd: Optional[int] = None) -> None:
        self.node_name = resolve_node_name(node_name)
        self.path = lock_path(self.node_name)
        self.fd = -1
        created = False
        if inherited_fd is None:
            # Linux permits exclusive flock on a read-only descriptor. This
            # allows other local users to check the lock. Open an existing file
            # without O_CREAT to respect Linux's protected_regular setting.
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            try:
                fd = os.open(self.path, flags)
            except FileNotFoundError:
                try:
                    fd = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o644)
                    created = True
                except FileExistsError:
                    fd = os.open(self.path, flags)
        else:
            if inherited_fd < 0:
                raise ValueError("invalid inherited instance lock descriptor")
            fd = inherited_fd
        try:
            if created:
                os.fchmod(fd, 0o644)
            info = os.fstat(fd)
            expected = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("inherited instance lock does not match the configured ROS node")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InstanceAlreadyRunningError(f"ROS node {self.node_name} is already running.") from exc
            os.set_inheritable(fd, False)
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    def close(self) -> None:
        if self.fd >= 0:
            # Do not LOCK_UN or unlink: the background child shares the open
            # file description, and must retain the same lock without a gap.
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> InstanceGuard:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
