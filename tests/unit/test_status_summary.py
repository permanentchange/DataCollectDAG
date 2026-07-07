from pathlib import Path

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
    manager.set_idle(RecentSessionStatus.STOPPED)
    assert manager.snapshot().tool_state.value == "IDLE"


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
            end_status="STOPPED",
            end_reason="external_stop",
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

