from types import SimpleNamespace

from data_collect_dag.models import RecentSessionStatus, SessionSummary, SessionMetrics
from data_collect_dag.status import StatusManager
from data_collect_dag.summary import SummaryWriter


def test_status_manager_snapshot_updates():
    manager = StatusManager()
    manager.set_running("sid", "pipe", "t0")
    metrics = SessionMetrics()
    metrics.main_frame_events = 3
    metrics.samples_saved = 2
    manager.update_metrics(metrics)
    snapshot = manager.snapshot()
    assert snapshot.current_session_id == "sid"
    assert snapshot.main_frame_events == 3
    manager.set_paused()
    assert manager.snapshot().tool_state.value == "PAUSED"
    manager.set_resumed()
    assert manager.snapshot().tool_state.value == "RUNNING"
    manager.set_idle(RecentSessionStatus.STOPPED)
    assert manager.snapshot().tool_state.value == "IDLE"


def test_status_manager_snapshot_reports_output_disk_free_gb(monkeypatch, tmp_path):
    missing_output_root = tmp_path / "missing" / "output"
    calls = []

    def fake_disk_usage(path):
        calls.append(path)
        return SimpleNamespace(free=12_345_678_901)

    monkeypatch.setattr("data_collect_dag.status.shutil.disk_usage", fake_disk_usage)
    manager = StatusManager(disk_usage_path=missing_output_root)

    payload = manager.snapshot().to_dict()

    assert payload["disk_free_gb"] == 12.35
    assert calls == [tmp_path]


def test_status_manager_snapshot_sets_disk_free_gb_to_none_when_query_fails(monkeypatch, tmp_path):
    def fake_disk_usage(_path):
        raise OSError("disk unavailable")

    monkeypatch.setattr("data_collect_dag.status.shutil.disk_usage", fake_disk_usage)
    manager = StatusManager(disk_usage_path=tmp_path)

    payload = manager.snapshot().to_dict()

    assert payload["disk_free_gb"] is None


def test_summary_writer_writes_json(tmp_path):
    writer = SummaryWriter()
    path = tmp_path / "session_summary.json"
    writer.write(
        path,
        SessionSummary(
            session_id="s",
            pipeline_name="p",
            start_time="a",
            end_time="b",
            end_status="COMPLETED",
            end_reason="max_saved_samples_reached",
            config_path="c",
            session_root="r",
            pipeline_params={},
            metrics={},
            save_outputs=[],
            last_error=None,
            warnings=[],
        ),
    )
    assert path.exists()
    assert '"session_id": "s"' in path.read_text(encoding="utf-8")
    assert '"end_reason": "max_saved_samples_reached"' in path.read_text(encoding="utf-8")
