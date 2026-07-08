from data_collect_dag.control import ControlCommand, ControlCommandQueue


def test_control_queue_roundtrip():
    queue = ControlCommandQueue()
    queue.put(ControlCommand(kind="start", pipeline_name="p"))
    item = queue.get(timeout=0.1)
    assert item.kind == "start"
    assert item.pipeline_name == "p"


def test_control_queue_supports_extended_command_fields():
    queue = ControlCommandQueue()
    queue.put(
        ControlCommand(
            kind="complete",
            pipeline_name="p",
            session_id="sid",
            reason="max_saved_samples_reached",
            end_status="COMPLETED",
        )
    )
    item = queue.get(timeout=0.1)
    assert item.kind == "complete"
    assert item.pipeline_name == "p"
    assert item.session_id == "sid"
    assert item.reason == "max_saved_samples_reached"
    assert item.end_status == "COMPLETED"
