from __future__ import annotations

import concurrent.futures
import logging
import time
import threading
from typing import Dict, List, Optional, Set

from data_collect_dag.models import NodeOutcome, NodeResult, PipelineDefinition
from data_collect_dag.sample_context import SampleContext


class DagExecutor:
    def __init__(
        self,
        pipeline: PipelineDefinition,
        nodes_by_id: Dict[str, object],
        executor: concurrent.futures.Executor,
    ) -> None:
        self.pipeline = pipeline
        self.nodes_by_id = nodes_by_id
        self.executor = executor
        self.logger = logging.getLogger("data_collect_dag.dag")

    def run_sample(self, sample: SampleContext) -> NodeOutcome:
        completed: Set[str] = set()
        ready: List[str] = [self.pipeline.start_node_id]
        outcomes: Dict[str, NodeOutcome] = {}
        cancel = False
        final_outcome = NodeOutcome(status=NodeResult.OK)
        futures: Dict[concurrent.futures.Future, str] = {}
        while ready or futures:
            while ready and not cancel:
                node_id = ready.pop(0)
                node = self.nodes_by_id[node_id]
                sample.metadata.setdefault("_node_start_monotonic", {})[node_id] = time.monotonic()
                self.logger.debug("submit node sample_id=%s node_id=%s", sample.sample_id, node_id)
                futures[self.executor.submit(node.run, sample)] = node_id
            if not futures:
                break
            done, _ = concurrent.futures.wait(futures.keys(), return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                node_id = futures.pop(future)
                started_at = sample.metadata.get("_node_start_monotonic", {}).pop(node_id, None)
                try:
                    outcome = future.result()
                except Exception as exc:
                    outcome = NodeOutcome(status=NodeResult.FAIL_SAMPLE, reason=f"{node_id}: {exc}")
                elapsed_ms = None
                if started_at is not None:
                    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
                    sample.metadata.setdefault("node_timings_ms", {})[node_id] = elapsed_ms
                if outcome.warnings:
                    sample.metadata.setdefault("node_warnings", []).extend(outcome.warnings)
                if outcome.metadata:
                    sample.metadata.setdefault("node_metadata", {})[node_id] = dict(outcome.metadata)
                outcomes[node_id] = outcome
                completed.add(node_id)
                self.logger.debug(
                    "node finished sample_id=%s node_id=%s status=%s reason=%s elapsed_ms=%s",
                    sample.sample_id,
                    node_id,
                    outcome.status.value,
                    outcome.reason,
                    f"{elapsed_ms:.3f}" if elapsed_ms is not None else "n/a",
                )
                if outcome.status != NodeResult.OK:
                    cancel = True
                    if sample.cancel_event is not None:
                        sample.cancel_event.set()
                    final_outcome = outcome
                    continue
                if node_id == self.pipeline.end_node_id:
                    final_outcome = outcome
                for successor in self.pipeline.successors.get(node_id, []):
                    if successor in completed:
                        continue
                    predecessors = self.pipeline.predecessors.get(successor, [])
                    if all(pred in completed and outcomes[pred].status == NodeResult.OK for pred in predecessors):
                        ready.append(successor)
        return final_outcome
