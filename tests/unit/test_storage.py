from datetime import date
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from data_collect_dag.app import build_parser
from data_collect_dag.config import ConfigError, load_app_config, load_output_config, parse_output_config
from data_collect_dag.io_utils import write_json
from data_collect_dag.storage import StorageError, StorageTracker, check_disk_free, clean_sessions, scan_files


@pytest.mark.parametrize("key", ["disk_storage_limit_GB", "disk_storage_need_GB"])
@pytest.mark.parametrize("value", [True, False, "100", -1, float("inf"), float("nan"), 10 ** 1000])
def test_invalid_storage_config(key, value):
    with pytest.raises(ConfigError, match=key):
        parse_output_config({key: value})


@pytest.mark.parametrize("value", [None, 0, 0.0])
def test_disabled_storage_config(value):
    _, limit, need = parse_output_config({"disk_storage_limit_GB": value, "disk_storage_need_GB": value})
    assert limit is None and need is None


def test_demo_storage_config():
    config = load_app_config(Path("demo/cfg.yaml"))
    assert config.disk_storage_limit_gb == 100
    assert config.disk_storage_need_gb == 100


def test_output_only_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(f"output:\n  root_dir: {tmp_path}\n  disk_storage_limit_GB: 1.5\n")
    assert load_output_config(path) == (tmp_path, 1.5, None)


@pytest.mark.parametrize("days,remaining", [(0, set()), (1, {"20260101", "20260102"}), (2, {"20251231", "20260101", "20260102"})])
def test_clean_calendar_dates(tmp_path, days, remaining):
    dates = {"20251230", "20251231", "20260101", "20260102"}
    for value in dates:
        (tmp_path / f"{value}_120000_abcdef12").mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "preserved").write_text("keep")
    invalid = tmp_path / "20250230_120000_abcdef12"
    invalid.mkdir()
    invalid_time = tmp_path / "20250101_250000_abcdef12"
    invalid_time.mkdir()
    (tmp_path / "20250101_120000_12345678").symlink_to(other, target_is_directory=True)
    (tmp_path / "20250101_120000_abcdef12").write_text("ordinary file")
    assert clean_sessions(tmp_path, days, today=date(2026, 1, 1)) == len(dates - remaining)
    for value in dates:
        assert (tmp_path / f"{value}_120000_abcdef12").exists() == (value in remaining)
    assert (other / "preserved").read_text() == "keep"
    assert invalid.is_dir() and invalid_time.is_dir()
    assert (tmp_path / "20250101_120000_12345678").is_symlink()
    assert (tmp_path / "20250101_120000_abcdef12").is_file()


def test_clean_missing_root_and_huge_retention(tmp_path):
    assert clean_sessions(tmp_path / "missing", 0) == 0
    folder = tmp_path / "20250101_120000_abcdef12"
    folder.mkdir()
    assert clean_sessions(tmp_path, 10 ** 10) == 0


def test_clean_reports_deletion_failure(tmp_path, monkeypatch):
    folder = tmp_path / "20250101_120000_abcdef12"
    folder.mkdir()
    def fail(path):
        raise PermissionError("denied")
    monkeypatch.setattr("data_collect_dag.storage.shutil.rmtree", fail)
    with pytest.raises(OSError, match=str(folder)):
        clean_sessions(tmp_path, 0)


@pytest.mark.parametrize("value", ["-1", "1.5", "invalid"])
def test_clean_rejects_invalid_argument(value):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--config", "unused.yaml", "--clean", value])
    assert exc.value.code == 2


def test_scan_ignores_symlinks(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    file = root / "file"
    file.write_bytes(b"abc")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"outside")
    (root / "linked_dir").symlink_to(outside, target_is_directory=True)
    (root / "linked_file").symlink_to(outside / "data")
    assert scan_files(root) == {file: 3}


def test_tracker_incremental_overwrites_and_failed_writes(tmp_path, monkeypatch):
    tracker = StorageTracker(tmp_path, 1, lambda: None)
    tracker.reconcile()
    path = tmp_path / "file"
    with tracker.writing(path):
        path.write_bytes(b"123456")
    assert tracker.size_bytes == 6
    with tracker.writing(path):
        path.write_bytes(b"12")
    assert tracker.size_bytes == 2
    with pytest.raises(OSError):
        with tracker.writing(path):
            path.write_bytes(b"partial")
            raise OSError("write failed")
    assert tracker.size_bytes == 7
    def no_scan(_root):
        raise AssertionError("per-frame operations must not scan")
    monkeypatch.setattr("data_collect_dag.storage.scan_files", no_scan)
    write_json(tmp_path / "metadata.json", {"value": 1}, storage_tracker=tracker)
    tracker.check()
    assert tracker.size_bytes == 7 + (tmp_path / "metadata.json").stat().st_size


def test_tracker_external_changes_and_deletions(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"a")
    tracker = StorageTracker(tmp_path, 1, lambda: None)
    tracker.reconcile()
    path.write_bytes(b"abc")
    new = tmp_path / "new"
    new.write_bytes(b"de")
    tracker.reconcile()
    assert tracker.size_bytes == 5
    path.unlink()
    tracker.reconcile()
    assert tracker.size_bytes == 2


def test_reconcile_preserves_concurrent_write(tmp_path, monkeypatch):
    path = tmp_path / "file"
    path.write_bytes(b"old")
    tracker = StorageTracker(tmp_path, 1, lambda: None)
    tracker.reconcile()
    entered = threading.Event()
    release = threading.Event()
    def stale_scan(root):
        entered.set()
        assert release.wait(2)
        return {path: 3}
    monkeypatch.setattr("data_collect_dag.storage.scan_files", stale_scan)
    scanner = threading.Thread(target=tracker.reconcile)
    scanner.start()
    try:
        assert entered.wait(2)
        with tracker.writing(path):
            path.write_bytes(b"new data")
        release.set()
        scanner.join(2)
        assert not scanner.is_alive()
        assert tracker.size_bytes == 8
    finally:
        release.set()
        scanner.join(2)


def test_reconcile_ignores_incomplete_write(tmp_path):
    tracker = StorageTracker(tmp_path, 1, lambda: None)
    tracker.reconcile()
    path = tmp_path / "file"
    with tracker.writing(path):
        path.write_bytes(b"partial")
        tracker.reconcile()
        assert tracker.size_bytes == 0
        path.write_bytes(b"completed")
    assert tracker.size_bytes == 9


def test_storage_limit_exact_boundary_and_scan_failure(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.write_bytes(b"123")
    tracker = StorageTracker(tmp_path, 3 / 1_000_000_000, lambda: None)
    tracker.reconcile()
    with pytest.raises(StorageError, match="storage_limit_reached"):
        tracker.check()
    def fail(root):
        raise PermissionError("denied")
    monkeypatch.setattr("data_collect_dag.storage.scan_files", fail)
    tracker.reconcile()
    assert tracker.size_bytes is None
    with pytest.raises(StorageError, match="storage_check_failed"):
        tracker.check()


def test_disk_free_boundary_and_nearest_existing_parent(tmp_path, monkeypatch):
    paths = []
    def usage(path):
        paths.append(path)
        return SimpleNamespace(free=1_000_000_000)
    monkeypatch.setattr("data_collect_dag.storage.shutil.disk_usage", usage)
    root = tmp_path / "missing" / "output"
    check_disk_free(root, 1)
    with pytest.raises(StorageError, match="insufficient_disk_space"):
        check_disk_free(root, 1.000000001)
    check_disk_free(root, None)
    assert paths == [tmp_path, tmp_path]


def test_disk_free_query_failure(tmp_path, monkeypatch):
    def fail(path):
        raise OSError("unavailable")
    monkeypatch.setattr("data_collect_dag.storage.shutil.disk_usage", fail)
    with pytest.raises(StorageError, match="storage_check_failed"):
        check_disk_free(tmp_path, 1)
