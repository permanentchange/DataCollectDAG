from __future__ import annotations

import logging
from typing import Any, Dict

from data_collect_dag.models import NodeConfig, NodeOutcome, NodeResult


class BaseNode:
    def __init__(self, node_config: NodeConfig, session: Any) -> None:
        self.node_id = node_config.node_id
        self.node_type = node_config.node_type
        self.inputs = node_config.inputs
        self.outputs = node_config.outputs
        self.config = node_config.config
        self.session = session
        self.logger = logging.getLogger(f"data_collect_dag.node.{self.node_id}")

    def setup(self) -> None:
        return

    def teardown(self) -> None:
        return

    def run(self, sample) -> NodeOutcome:
        raise NotImplementedError

    def ok(self, **kwargs) -> NodeOutcome:
        return NodeOutcome(status=NodeResult.OK, **kwargs)
