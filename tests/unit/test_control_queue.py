from data_collect_dag.control import ControlCommand, ControlCommandQueue


def test_control_queue_roundtrip():
    queue = ControlCommandQueue()
    queue.put(ControlCommand(kind="start", pipeline_name="p"))
    item = queue.get(timeout=0.1)
    assert item.kind == "start"
    assert item.pipeline_name == "p"

