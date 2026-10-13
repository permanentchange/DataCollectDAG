from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import os
from pathlib import Path
import re
import shutil
import stat
import threading
from typing import Callable, Dict, Iterator, Optional


GB = 1_000_000_000
SESSION_NAME = re.compile(r"\d{8}_\d{6}_[0-9a-f]{8}\Z")


class StorageError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


def clean_sessions(root: Path, days: int, today: Optional[date] = None) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("--clean must be a non-negative integer")
    if not root.exists():
        return 0
    current_date = today or date.today()
    deleted = 0
    errors = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_dir() or not SESSION_NAME.fullmatch(path.name):
            continue
        try:
            session_date = datetime.strptime(path.name[:15], "%Y%m%d_%H%M%S").date()
        except ValueError:
            continue
        if days and (current_date - session_date).days < days:
            continue
        try:
            shutil.rmtree(path)
            deleted += 1
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    if errors:
        raise OSError(f"deleted {deleted} session directories; cleanup failed: {'; '.join(errors)}")
    return deleted


def nearest_existing_path(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def scan_files(root: Path) -> Dict[Path, int]:
    sizes: Dict[Path, int] = {}
    if not root.exists():
        return sizes
    if not root.is_dir():
        raise OSError(f"output root is not a directory: {root}")
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    path = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        sizes[path] = info.st_size
        except FileNotFoundError:
            continue
    return sizes


class StorageTracker:
    """Cached logical file sizes, reconciled without overwriting concurrent writes."""

    def __init__(self, root: Path, limit_gb: float, on_change: Callable[[], None]) -> None:
        self.root = root
        self.limit_bytes = limit_gb * GB
        self.on_change = on_change
        self._lock = threading.Lock()
        self._sizes: Dict[Path, int] = {}
        self._versions: Dict[Path, int] = {}
        self._writing: Dict[Path, int] = {}
        self._revision = 0
        self._size = 0
        self._error: Optional[str] = None

    @property
    def size_bytes(self) -> Optional[int]:
        with self._lock:
            return self._size if self._error is None else None

    def check(self) -> None:
        with self._lock:
            if self._error is not None:
                raise StorageError("storage_check_failed", f"{self.root}: {self._error}")
            if self._size >= self.limit_bytes:
                raise StorageError(
                    "storage_limit_reached",
                    f"{self.root}: size={self._size / GB:.9f} GB, limit={self.limit_bytes / GB:.9f} GB",
                )

    def reconcile(self) -> None:
        with self._lock:
            revision = self._revision
            writing = set(self._writing)
        try:
            scanned = scan_files(self.root)
        except OSError as exc:
            self.fail(exc)
            return
        with self._lock:
            for path in self._sizes.keys() | scanned.keys():
                if path in writing or path in self._writing or self._versions.get(path, 0) > revision:
                    continue
                self._sizes[path] = scanned.get(path, 0)
            self._size = sum(self._sizes.values())
        self.on_change()

    def fail(self, exc: OSError) -> None:
        with self._lock:
            self._error = str(exc)
        self.on_change()

    def refresh(self, path: Path) -> None:
        with self._lock:
            try:
                info = path.lstat()
                size = info.st_size if stat.S_ISREG(info.st_mode) else 0
            except FileNotFoundError:
                size = 0
            except OSError as exc:
                self._error = str(exc)
                size = self._sizes.get(path, 0)
            self._revision += 1
            self._versions[path] = self._revision
            self._size += size - self._sizes.get(path, 0)
            self._sizes[path] = size
        self.on_change()

    @contextmanager
    def writing(self, path: Path) -> Iterator[None]:
        with self._lock:
            self._writing[path] = self._writing.get(path, 0) + 1
        try:
            yield
        finally:
            # Keep the pending marker until the final size is accounted for.
            self.refresh(path)
            with self._lock:
                self._writing[path] -= 1
                if self._writing[path] == 0:
                    del self._writing[path]


@contextmanager
def tracked_write(path: Path, tracker: Optional[StorageTracker]) -> Iterator[None]:
    if tracker is None:
        yield
    else:
        with tracker.writing(path):
            yield


def check_disk_free(root: Path, need_gb: Optional[float]) -> None:
    if need_gb is None:
        return
    try:
        free = shutil.disk_usage(nearest_existing_path(root)).free
    except OSError as exc:
        raise StorageError("storage_check_failed", f"{root}: {exc}") from exc
    if free < need_gb * GB:
        raise StorageError("insufficient_disk_space", f"{root}: free={free / GB:.9f} GB, need={need_gb:.9f} GB")
