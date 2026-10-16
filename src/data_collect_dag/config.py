from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml

from data_collect_dag.node_registry import NODE_TYPES
from data_collect_dag.models import (
    AppConfig,
    CachePolicy,
    ControlConfig,
    NodeConfig,
    PipelineDefinition,
    RuntimeConfig,
    StopConditions,
    TopicConfig,
)


class ConfigError(ValueError):
    pass


def _mapping(value: Any, location: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{location} must be a mapping")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise ConfigError(f"{location} keys must be non-empty strings")
    return value


def _sequence(value: Any, location: str) -> List[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{location} must be a list")
    return value


def _required(mapping: Dict[str, Any], key: str, location: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{location}.{key} is required")
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")
    return value


def _convert(converter: Any, value: Any, location: str) -> Any:
    try:
        return converter(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"{location}: {exc}") from exc


def _read_config(path: Path) -> Dict[str, Any]:
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "config root")


def _validate_structure(raw: Dict[str, Any]) -> None:
    for section in ("ros", "cache", "runtime", "pipelines", "calibration"):
        _mapping(raw.get(section), section)
    ros = _mapping(raw.get("ros"), "ros")
    topics = _mapping(ros.get("topics"), "ros.topics")
    for key, value in topics.items():
        topic = _mapping(value, f"ros.topics.{key}")
        for field in ("topic", "msg_type", "role"):
            _required(topic, field, f"ros.topics.{key}")
    _mapping(ros.get("control"), "ros.control")
    cache = _mapping(raw.get("cache"), "cache")
    for field in ("defaults_by_role", "topic_overrides"):
        for key, value in _mapping(cache.get(field), f"cache.{field}").items():
            _mapping(value, f"cache.{field}.{key}")
    for name, value in _mapping(raw.get("pipelines"), "pipelines").items():
        location = f"pipelines.{name}"
        pipeline = _mapping(value, location)
        _required(pipeline, "main_source", location)
        if not isinstance(pipeline.get("description", ""), str):
            raise ConfigError(f"{location}.description must be a string")
        _mapping(pipeline.get("stop_conditions"), f"{location}.stop_conditions")
        for index, value in enumerate(_sequence(pipeline.get("nodes", []), f"{location}.nodes")):
            node_location = f"{location}.nodes[{index}]"
            node = _mapping(value, node_location)
            for field in ("node_id", "type"):
                _required(node, field, node_location)
            for field in ("inputs", "outputs", "config"):
                _mapping(node.get(field), f"{node_location}.{field}")
        for index, edge in enumerate(_sequence(pipeline.get("edges", []), f"{location}.edges")):
            if not isinstance(edge, list) or len(edge) != 2 or not all(isinstance(item, str) for item in edge):
                raise ConfigError(f"{location}.edges[{index}] must contain two node IDs")


def parse_output_config(raw: Any) -> Tuple[Path, Optional[float], Optional[float]]:
    output = {} if raw is None else raw
    if not isinstance(output, dict):
        raise ConfigError("output must be a mapping")
    values: List[Optional[float]] = []
    for key in ("disk_storage_limit_GB", "disk_storage_need_GB"):
        value = output.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"output.{key} must be a finite non-negative number")
            try:
                value = float(value)
            except OverflowError:
                raise ConfigError(f"output.{key} must be a finite non-negative number")
            if not math.isfinite(value) or value < 0:
                raise ConfigError(f"output.{key} must be a finite non-negative number")
        values.append(value or None)
    root = _convert(Path, output.get("root_dir", "output"), "output.root_dir")
    return root.expanduser().resolve(), values[0], values[1]


def load_output_config(path: Path) -> Tuple[Path, Optional[float], Optional[float]]:
    raw = _read_config(path)
    return parse_output_config(raw.get("output"))


def load_app_config(path: Path) -> AppConfig:
    raw = _read_config(path)
    _validate_structure(raw)
    output_root, storage_limit, storage_need = parse_output_config(raw.get("output"))
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
        sample_workers=_convert(int, runtime.get("sample_workers", 1), "runtime.sample_workers"),
        node_workers=_convert(int, runtime.get("node_workers", 4), "runtime.node_workers"),
        main_frame_queue_size=_convert(int, runtime.get("main_frame_queue_size", 20), "runtime.main_frame_queue_size"),
        main_frame_delay_ms=_convert(int, runtime.get("main_frame_delay_ms", 0), "runtime.main_frame_delay_ms"),
        stop_timeout_sec=_convert(float, runtime.get("stop_timeout_sec", 5.0), "runtime.stop_timeout_sec"),
    )
    _validate_runtime_config(runtime_config)
    return AppConfig(
        config_path=path.resolve(),
        output_root_dir=output_root,
        disk_storage_limit_gb=storage_limit,
        disk_storage_need_gb=storage_need,
        ros_node_name=str(ros.get("node_name", "data_collect_dag")),
        control=_parse_control_config(ros.get("control") or {}),
        topics=topics,
        cache_policies=cache_policies,
        runtime=runtime_config,
        pipelines=compiled_pipelines,
        calibration_path=_convert(Path, calibration["path"], "calibration.path").expanduser().resolve() if calibration.get("path") else None,
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


def _parse_control_config(raw_control: Dict[str, Any]) -> ControlConfig:
    if not isinstance(raw_control, dict):
        raise ConfigError("ros.control must be a mapping")
    legacy_keys = {"start_topic", "stop_topic", "pause_topic", "resume_topic"} & set(raw_control)
    if legacy_keys:
        legacy = ", ".join(sorted(legacy_keys))
        raise ConfigError(
            f"unsupported legacy ros.control keys: {legacy}; use control_topic: /data_collect/control"
        )
    allowed_keys = {"control_topic", "status_topic", "start_service", "stop_service", "status_service"}
    unknown_keys = set(raw_control) - allowed_keys
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ConfigError(f"unknown ros.control keys: {unknown}")
    return ControlConfig(**raw_control)


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
        result[topic_key] = CachePolicy(
            max_frames=_convert(int, payload["max_frames"], f"cache.{topic_key}.max_frames"),
            max_age_sec=_convert(float, payload["max_age_sec"], f"cache.{topic_key}.max_age_sec"),
        )
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
            if node_config.node_type not in NODE_TYPES:
                raise ConfigError(f"pipeline {name} node {node_id} unknown node type: {node_config.node_type}")
            try:
                _validate_node_config(name, node_config, topics)
            except ConfigError:
                raise
            except (TypeError, ValueError, OverflowError) as exc:
                raise ConfigError(f"pipeline {name} node {node_id}: {exc}") from exc
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
        precheck_topics, time_delay_ms = _parse_pipeline_topic_precheck(name, payload, topics)
        pipelines[name] = PipelineDefinition(
            name=name,
            description=payload.get("description", ""),
            main_source=main_source,
            nodes_by_id=nodes_by_id,
            predecessors={key: list(value) for key, value in predecessors.items()},
            successors={key: list(value) for key, value in successors.items()},
            start_node_id=start_nodes[0],
            end_node_id=end_nodes[0],
            stop_conditions=_parse_stop_conditions(name, payload.get("stop_conditions") or {}),
            topics=precheck_topics,
            time_delay_ms=time_delay_ms,
        )
    return pipelines


def _parse_pipeline_topic_precheck(
    pipeline_name: str, payload: Dict[str, Any], configured_topics: Dict[str, TopicConfig]
) -> Tuple[List[str], Optional[int]]:
    has_topics = "topics" in payload
    has_time_delay = "time_delay_ms" in payload
    if not has_topics and not has_time_delay:
        return [], None
    if not has_topics:
        raise ConfigError(f"pipeline {pipeline_name} time_delay_ms requires topics")

    raw_topics = payload["topics"]
    if not isinstance(raw_topics, list) or not raw_topics:
        raise ConfigError(f"pipeline {pipeline_name} topics must be a non-empty list")
    topic_keys = []
    for raw_topic in raw_topics:
        if not isinstance(raw_topic, str) or not raw_topic.strip():
            raise ConfigError(f"pipeline {pipeline_name} topics must contain non-empty topic keys")
        topic_key = raw_topic.strip()
        if topic_key not in configured_topics:
            raise ConfigError(f"pipeline {pipeline_name} topics references unknown topic_key: {topic_key}")
        topic_keys.append(topic_key)
    if len(set(topic_keys)) != len(topic_keys):
        raise ConfigError(f"pipeline {pipeline_name} topics must not contain duplicates")

    if not has_time_delay:
        raise ConfigError(f"pipeline {pipeline_name} topics requires time_delay_ms")
    time_delay_ms = payload["time_delay_ms"]
    if isinstance(time_delay_ms, bool) or not isinstance(time_delay_ms, int) or time_delay_ms <= 0:
        raise ConfigError(f"pipeline {pipeline_name} time_delay_ms must be a positive integer")
    return topic_keys, time_delay_ms


def _parse_stop_conditions(pipeline_name: str, payload: Dict[str, Any]) -> StopConditions:
    stop_conditions = StopConditions()
    if "max_duration_sec" in payload:
        stop_conditions.max_duration_sec = _convert(float, payload["max_duration_sec"], f"pipeline {pipeline_name} stop_conditions.max_duration_sec")
        if stop_conditions.max_duration_sec <= 0:
            raise ConfigError(f"pipeline {pipeline_name} max_duration_sec must be > 0")
    if "max_saved_samples" in payload:
        stop_conditions.max_saved_samples = _convert(int, payload["max_saved_samples"], f"pipeline {pipeline_name} stop_conditions.max_saved_samples")
        if stop_conditions.max_saved_samples <= 0:
            raise ConfigError(f"pipeline {pipeline_name} max_saved_samples must be > 0")
    return stop_conditions


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
    elif node_config.node_type == "fps_guard":
        _validate_non_empty_gate_id(pipeline_name, node_config)
        if "max_fps" not in node_config.config:
            raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing max_fps")
        try:
            max_fps = float(node_config.config["max_fps"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} max_fps must be > 0") from exc
        if not math.isfinite(max_fps) or max_fps <= 0.0:
            raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} max_fps must be > 0")
    elif node_config.node_type == "fps_marker":
        _validate_non_empty_gate_id(pipeline_name, node_config)
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
    elif node_config.node_type == "yolo_gate":
        _validate_yolo_gate_config(pipeline_name, node_config)
    elif node_config.node_type == "image_undistort":
        _validate_image_undistort_config(pipeline_name, node_config, topics)
    elif node_config.node_type == "xtreme1_structured_save":
        if not isinstance(node_config.config.get("include_distortion_params", False), bool):
            raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} include_distortion_params must be a boolean")


def _validate_non_empty_gate_id(pipeline_name: str, node_config: NodeConfig) -> None:
    raw_gate_id = node_config.config.get("gate_id")
    gate_id = str(raw_gate_id).strip() if raw_gate_id is not None else ""
    if not gate_id:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing gate_id")


def _validate_yolo_gate_config(pipeline_name: str, node_config: NodeConfig) -> None:
    deprecated_keys = {"class_name", "confidence_threshold", "min_area_ratio"} & set(node_config.config)
    if deprecated_keys:
        deprecated = ", ".join(sorted(deprecated_keys))
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} unsupported yolo_gate config keys: {deprecated}")
    model_path = str(node_config.config.get("model_path", "")).strip()
    if not model_path:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing model_path")
    if "image" not in node_config.inputs:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing image input")

    class_names = _validate_non_empty_string_list(pipeline_name, node_config, "class_names")
    confidence_thresholds = _validate_ratio_list(pipeline_name, node_config, "confidence_thresholds")
    min_area_ratios = _validate_ratio_list(pipeline_name, node_config, "min_area_ratios")
    if len(set(class_names)) != len(class_names):
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} class_names must not contain duplicates")
    if len(confidence_thresholds) != len(class_names):
        raise ConfigError(
            f"pipeline {pipeline_name} node {node_config.node_id} confidence_thresholds length must match class_names"
        )
    if len(min_area_ratios) != len(class_names):
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} min_area_ratios length must match class_names")

    predict_conf = _validate_ratio_value(pipeline_name, node_config, "predict_conf", node_config.config.get("predict_conf"))
    if predict_conf > min(confidence_thresholds):
        raise ConfigError(
            f"pipeline {pipeline_name} node {node_config.node_id} predict_conf must be <= min(confidence_thresholds)"
        )
    failure_policy = str(node_config.config.get("failure_policy", "skip_sample"))
    if failure_policy != "skip_sample":
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} unsupported failure_policy: {failure_policy}")


def _validate_image_undistort_config(
    pipeline_name: str, node_config: NodeConfig, topics: Dict[str, TopicConfig]
) -> None:
    source = str(node_config.config.get("source", "")).strip()
    if not source:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing source")
    topic = topics.get(source)
    if topic is None:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} references unknown source topic_key: {source}")
    if topic.role != "image":
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} source must have image role: {source}")
    if "image" not in node_config.inputs:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing image input")
    if "image" not in node_config.outputs:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing image output")


def _validate_non_empty_string_list(pipeline_name: str, node_config: NodeConfig, key: str) -> List[str]:
    values = node_config.config.get(key)
    if not isinstance(values, list) or not values:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} {key} must be a non-empty list")
    result = []
    for value in values:
        item = str(value).strip()
        if not item:
            raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} {key} must not contain empty values")
        result.append(item)
    return result


def _validate_ratio_list(pipeline_name: str, node_config: NodeConfig, key: str) -> List[float]:
    values = node_config.config.get(key)
    if not isinstance(values, list) or not values:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} {key} must be a non-empty list")
    return [_validate_ratio_value(pipeline_name, node_config, key, value) for value in values]


def _validate_ratio_value(pipeline_name: str, node_config: NodeConfig, key: str, value: Any) -> float:
    if value is None:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} missing {key}")
    try:
        ratio = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} {key} must be between 0 and 1") from exc
    if not math.isfinite(ratio) or ratio < 0.0 or ratio > 1.0:
        raise ConfigError(f"pipeline {pipeline_name} node {node_config.node_id} {key} must be between 0 and 1")
    return ratio


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
