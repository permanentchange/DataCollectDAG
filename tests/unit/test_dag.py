import concurrent.futures
import logging

from data_collect_dag.dag import DagExecutor
from data_collect_dag.models import NodeConfig, NodeOutcome, NodeResult, PipelineDefinition
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.sample_context import SampleContext


class _OkNode(BaseNode):
    def run(self, sample):
        return NodeOutcome(status=NodeResult.OK)


class _FailingNode(BaseNode):
    def run(self, sample):
        raise RuntimeError("deliberate node failure")


def test_dag_logs_traceback_when_node_raises(caplog):
    start_config = NodeConfig("start", "test", {}, {}, {})
    failing_config = NodeConfig("failing", "test", {}, {}, {})
    pipeline = PipelineDefinition(
        name="test",
        main_source="main",
        nodes_by_id={"start": start_config, "failing": failing_config},
        predecessors={"start": [], "failing": ["start"]},
        successors={"start": ["failing"], "failing": []},
        start_node_id="start",
        end_node_id="failing",
    )
    nodes = {"start": _OkNode(start_config, None), "failing": _FailingNode(failing_config, None)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        dag = DagExecutor(pipeline, nodes, executor)
        with caplog.at_level(logging.ERROR, logger="data_collect_dag.dag"):
            result = dag.run_sample(SampleContext(sample_id="sample-1"))

    assert result.status == NodeResult.FAIL_SAMPLE
    assert result.reason == "failing: deliberate node failure"
    assert "node raised exception sample_id=sample-1 node_id=failing" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "RuntimeError: deliberate node failure" in caplog.text
