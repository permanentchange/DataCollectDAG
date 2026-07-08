from pathlib import Path

from data_collect_dag.app import AppRuntime
from data_collect_dag.nodes import vision
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import SessionManager
from data_collect_dag.status import StatusManager


class _FakeYOLO:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def predict(self, **kwargs):
        return []


def test_session_manager_start_stop_replace(tmp_path, monkeypatch):
    runtime = AppRuntime(Path("demo/xtreme1_demo.yaml"), "xtreme1_collect")
    model_path = tmp_path / "person.pt"
    model_path.write_text("fake-model", encoding="utf-8")
    runtime.app_config.pipelines["xtreme1_collect"].nodes_by_id["front_person_gate"].config["model_path"] = str(model_path)
    runtime.app_config.output_root_dir = tmp_path
    monkeypatch.setattr(vision, "_load_yolo_class", lambda: _FakeYOLO)
    runtime.session_manager = SessionManager(
        runtime.app_config,
        runtime.calibration,
        RosAdapter(runtime.app_config.topics, runtime.app_config.ros_node_name, runtime.app_config.control),
        StatusManager(),
    )
    session1 = runtime.session_manager.start("xtreme1_collect")
    assert session1.session_root.exists()
    session2 = runtime.session_manager.start("xtreme1_collect")
    assert session2.session_id != session1.session_id
    runtime.session_manager.stop()
