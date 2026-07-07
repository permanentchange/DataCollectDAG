from data_collect_dag.models import NodeConfig, NodeResult
from data_collect_dag.nodes.common import EndNode, StartNode
from data_collect_dag.sample_context import SampleContext


def test_start_end_nodes_return_ok(dummy_session):
    sample = SampleContext(sample_id="s")
    start = StartNode(NodeConfig("start", "start", {}, {}, {}), dummy_session)
    end = EndNode(NodeConfig("end", "end", {}, {}, {}), dummy_session)
    assert start.run(sample).status == NodeResult.OK
    assert end.run(sample).status == NodeResult.OK

