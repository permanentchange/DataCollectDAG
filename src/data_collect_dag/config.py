from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import yaml

from data_collect_dag.models import (
    AppConfig,
    CachePolicy,
    ControlConfig,
    NodeConfig,
    PipelineDefinition,
    RuntimeConfig,
    TopicConfig,
)


class ConfigError(ValueError):
    pass


def load_app_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    output = raw.get("output") or {}
    ros = raw.get("ros") or {}
    cache = raw.get("cache") or {}
    runtime = raw.get("runtime") or {}
    pipelines = raw.get("pipelines") or {}
    calibration = raw.get("calibration") or {}
    topics = _parse_topics(ros.get("topics") or {})
    cache_policies = _parse_cache_policies(cache, topics)
    compiled_pipelines = _parse_pipelines(pipelines, topics)
    runtime_config = RuntimeConfig(
        debug=bool(runtime.get("debug", False)),
        sample_workers=int(runtime.get("sample_workers", 1)),
        node_workers=int(runtime.get("node_workers", 4)),
        main_frame_queue_size=int(runtime.get("main_frame_queue_size", 20)),
        main_frame_delay_ms=int(runtime.get("main_frame_delay_ms", 0)),
        stop_timeout_sec=float(runtime.get("stop_timeout_sec", 5.0)),
    )
    _validate_runtime_config(runtime_config)
    return AppConfig(
        config_path=path.resolve(),
        output_root_dir=Path(output.get("root_dir", "output")).resolve(),
        ros_node_name=str(ros.get("node_name", "data_collect_dag")),
        control=ControlConfig(**(ros.get("control") or {})),
        topics=topics,
        cache_policies=cache_policies,
        runtime=runtime_config,
        pipelines=compiled_pipelines,
        calibration_path=Path(calibration["path"]).resolve() if calibration.get("path") else None,
    )


def _validate_runtime_config(runtime: RuntimeConfig) -> None:
    if runtime.sample_workers < 1:
        raise ConfigError("runtime.sample_workers must be >= 1")
    if runtime.node_workers < 1:
        raise ConfigError("runtime.node_workers must be >= 1")
    if runtime.main_frame_queue_size < 1:
        raise ConfigError("runtime.main_frame_queue_size must be >= 1")
    if runtime.stop_timeout_sec <= 0:
        raise ConfigError("runtime.stop_timeout_sec must be > 0")


def _parse_topics(raw_topics: Dict[str, Any]) -> Dict[str, TopicConfig]:
    if not raw_topics:
        raise ConfigError("ros.topics must not be empty")
    topics: Dict[str, TopicConfig] = {}
    for topic_key, payload in raw_topics.items():
        if topic_key in topics:
            raise ConfigError(f"duplicate topic_key: {topic_key}")
        topics[topic_key] = TopicConfig(
            topic_key=topic_key,
            topic=str(payload["topic"]),
            msg_type=str(payload["msg_type"]),
            role=str(payload["role"]),
            sensor_name=str(payload.get("sensor_name", topic_key)),
        )
    return topics


def _parse_cache_policies(raw_cache: Dict[str, Any], topics: Dict[str, TopicConfig]) -> Dict[str, CachePolicy]:
    defaults = raw_cache.get("defaults_by_role") or {}
    overrides = raw_cache.get("topic_overrides") or {}
    result: Dict[str, CachePolicy] = {}
    for topic_key, topic in topics.items():
        payload = dict(defaults.get(topic.role) or {})
        payload.update(overrides.get(topic_key) or {})
        if "max_frames" not in payload or "max_age_sec" not in payload:
            raise ConfigError(f"missing cache policy for topic_key={topic_key}")
        result[topic_key] = CachePolicy(max_frames=int(payload["max_frames"]), max_age_sec=float(payload["max_age_sec"]))
    return result


def _parse_pipelines(raw_pipelines: Dict[str, Any], topics: Dict[str, TopicConfig]) -> Dict[str, PipelineDefinition]:
    if not raw_pipelines:
        raise ConfigError("pipelines must not be empty")
    pipelines: Dict[str, PipelineDefinition] = {}
    for name, payload in raw_pipelines.items():
        main_source = str(payload["main_source"])
        if main_source not in topics:
            raise ConfigError(f"pipeline {name} main_source not found in ros.topics: {main_source}")
        nodes_by_id: Dict[str, NodeConfig] = {}
        for node_payload in payload.get("nodes") or []:
            node_id = str(node_payload["node_id"])
            if node_id in nodes_by_id:
                raise ConfigError(f"pipeline {name} duplicate node_id: {node_id}")
            node_config = NodeConfig(
                node_id=node_id,
                node_type=str(node_payload["type"]),
                inputs=dict(node_payload.get("inputs") or {}),
                outputs=dict(node_payload.get("outputs") or {}),
                config=dict(node_payload.get("config") or {}),
            )
            _validate_node_config(name, node_config, topics)
            nodes_by_id[node_id] = node_config
        predecessors: Dict[str, List[str]] = defaultdict(list)
        successors: Dict[str, List[str]] = defaultdict(list)
        for src, dst in payload.get("edges") or []:
            if src not in nodes_by_id or dst not in nodes_by_id:
                raise ConfigError(f"pipeline {name} edge references unknown node: {src}->{dst}")
            predecessors[dst].append(src)
            successors[src].append(dst)
        start_nodes = [node_id for node_id, node in nodes_by_id.items() if node.node_type == "start"]
        end_nodes = [node_id for node_id, node in nodes_by_id.items() if node.node_type == "end"]
        if len(start_nodes) != 1:
            raise ConfigError(f"pipeline {name} must contain exactly one start node")
        if len(end_nodes) != 1:
            raise ConfigError(f"pipeline {name} must contain exactly one end node")
        _validate_pipeline_graph(name, nodes_by_id, predecessors, successors, start_nodes[0], end_nodes[0])
        pipelines[name] = PipelineDefinition(
            name=name,
            main_source=main_source,
            nodes_by_id=nodes_by_id,
            predecessors={key: list(value) for key, value in predecessors.items()},
            successors={key: list(value) for key, value in successors.items()},
            start_node_id=start_nodes[0],
            end_node_id=end_nodes[0],
        )
    return pipelines


def _validate_pipeline_graph(
    pipeline_name: str,
    nodes_by_id: Dict[str, NodeConfig],
    predecessors: Dict[str, List[str]],
    successors: Dict[str, List[str]],
    start_node_id: str,
    end_node_id: str,
) -> None:
    in_degree = {node_id: len(predecessors.get(node_id, [])) for node_id in nodes_by_id}
    queue = deque(node_id for node_id, degree in in_degree.items() if degree == 0)
    visited: List[str] = []
    while queue:
        node_id = queue.popleft()
        visited.append(node_id)
        for successor in successors.get(node_id, []):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)
    if len(visited) != len(nodes_by_id):
        raise ConfigError(f"pipeline {pipeline_name} contains a cycle")
    reachable = _dfs(start_node_id, successors)
    if reachable != set(nodes_by_id):
        missing = sorted(set(nodes_by_id) - reachable)
        raise ConfigError(f"pipeline {pipeline_name} nodes unreachable from start: {missing}")
    reverse_successors = {key: list(value) for key, value in predecessors.items()}
    can_reach_end = _dfs(end_node_id, reverse_successors)
    if can_reach_end != set(nodes_by_id):
        missing = sorted(set(nodes_by_id) - can_reach_end)
        raise ConfigError(f"pipeline {pipeline_name} nodes cannot reach end: {missing}")


def _validate_node_config(pipeline_name: str, node_config: NodeConfig, topics: Dict[str, TopicConfig]) -> None:
    if node_config.node_type == "time_sync":
        source = str(node_config.config.get("source", ""))
        if source not in topics:
            raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} references unknown source topic_key: {source}")
    elif node_config.node_type == "multi_time_sync":
        for source in list(node_config.config.get("sources") or []):
            if source not in topics:
                raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} references unknown source topic_key: {source}")
    elif node_config.node_type == "pointcloud_motion_compensation":
        localization_source = str(node_config.config.get("localization_source", ""))
        if localization_source and localization_source not in topics:
            raise ConfigError(
                f"pipeline {pipeline_name} node {node_config.node_id} references unknown localization_source topic_key: {localization_source}"
            )


def _dfs(root: str, adjacency: Dict[str, List[str]]) -> Set[str]:
    visited: Set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(adjacency.get(node, []))
    return visited
