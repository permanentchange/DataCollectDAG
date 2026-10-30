"""Invalid configuration data shared by parser and CLI boundary tests."""


def pipeline(**changes):
    value = {
        "main_source": "camera",
        "nodes": [{"node_id": "begin", "type": "start"}, {"node_id": "finish", "type": "end"}],
        "edges": [["begin", "finish"]],
    }
    value.update(changes)
    return {"pipelines": {"custom": value}}


BEGIN = {"node_id": "begin", "type": "start"}
END = {"node_id": "finish", "type": "end"}
EXTRA = {"node_id": "extra", "type": "aggregate_pointclouds"}
STRUCTURE_ERRORS = [
    ({"ros": []}, "ros must be a mapping"),
    ({"ros": {"topics": {"camera": {"topic": "/camera", "role": "image"}}}}, "ros.topics.camera.msg_type"),
    ({"runtime": {"node_workers": "bad"}}, "runtime.node_workers"),
    ({"cache": {"defaults_by_role": {"image": {"max_frames": "bad", "max_age_sec": 1}}}}, "cache.camera.max_frames"),
    (pipeline(nodes={}), "pipelines.custom.nodes must be a list"),
    (pipeline(edges=[["begin"]]), "pipelines.custom.edges[0]"),
    (pipeline(main_source="missing"), "main_source not found"),
    (pipeline(description=[]), "description must be a string"),
]
NODE_ERRORS = [
    (pipeline(nodes=[{"node_id": "begin"}, END]), "pipelines.custom.nodes[0].type"),
    (pipeline(nodes=[{"node_id": "begin", "type": "typo"}, END]), "unknown node type"),
    (pipeline(nodes=[BEGIN, END, BEGIN]), "duplicate node_id"),
    (pipeline(nodes=[BEGIN, END, {"node_id": "sync", "type": "time_sync", "config": {"source": "missing"}}]), "unknown source"),
]
GRAPH_ERRORS = [
    (pipeline(edges=[["begin", "missing"]]), "edge references unknown node"),
    (pipeline(edges=[["begin", "finish"], ["finish", "begin"]]), "contains a cycle"),
    (pipeline(nodes=[{"node_id": "begin", "type": "aggregate_pointclouds"}, END]), "exactly one start"),
    (pipeline(nodes=[BEGIN, {"node_id": "finish", "type": "aggregate_pointclouds"}]), "exactly one end"),
    (pipeline(nodes=[BEGIN, END, EXTRA]), "unreachable from start"),
    (pipeline(nodes=[BEGIN, END, EXTRA], edges=[["begin", "finish"], ["begin", "extra"]]), "cannot reach end"),
]
CLI_ERRORS = [STRUCTURE_ERRORS[0], NODE_ERRORS[1], GRAPH_ERRORS[1]]
