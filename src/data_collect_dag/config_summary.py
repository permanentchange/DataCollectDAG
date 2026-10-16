"""Read-only presentation of an already validated configuration."""
from __future__ import annotations

from typing import Optional

from data_collect_dag import __version__
from data_collect_dag.models import AppConfig


def format_config(config: AppConfig, pipeline_name: Optional[str] = None) -> str:
    if pipeline_name is not None and pipeline_name not in config.pipelines:
        raise ValueError(f"unknown pipeline {pipeline_name!r}; available pipelines: {', '.join(config.pipelines)}")

    def display(value: object) -> str:
        return "not configured" if value is None else str(value)

    def storage(value: Optional[float]) -> str:
        return "disabled" if value is None else f"{value:g} GB"

    rows = [
        f"Config:       {config.config_path}",
        f"Version:      {__version__}",
        "Validation:   PASS (static configuration only)",
        f"Output:       {config.output_root_dir}",
        f"Storage limit: {storage(config.disk_storage_limit_gb)}",
        f"Minimum free:  {storage(config.disk_storage_need_gb)}",
        f"Calibration:  {display(config.calibration_path)}",
        f"ROS node:     {config.ros_node_name}",
        f"Control topic: {display(config.control.control_topic)}",
        f"Status topic:  {display(config.control.status_topic)}",
        f"Topics:       {len(config.topics)}",
        "",
    ]
    if pipeline_name is None:
        rows.append("Pipelines:")
        for pipeline in config.pipelines.values():
            rows.append(f"  {pipeline.name}: {pipeline.description or '(no description)'}")
            rows.append(f"    Main source: {pipeline.main_source}")
    else:
        pipeline = config.pipelines[pipeline_name]
        rows.extend([
            f"Pipeline: {pipeline.name}",
            f"Description: {pipeline.description or '(no description)'}",
            f"Main source: {pipeline.main_source}",
            "Precheck topics:",
        ])
        for key in pipeline.topics:
            rows.append(f"  {key}: {config.topics[key].topic}")
        if not pipeline.topics:
            rows.append("  not configured")
        rows.append(f"Precheck wait: {display(pipeline.time_delay_ms)} ms")
        stop = pipeline.stop_conditions
        rows.extend([
            f"Max duration: {display(stop.max_duration_sec if stop else None)} sec",
            f"Max saved samples: {display(stop.max_saved_samples if stop else None)}",
            "Nodes:",
        ])
        rows.extend(f"  {node.node_id}: {node.node_type}" for node in pipeline.nodes_by_id.values())
        rows.append("Edges:")
        for source, targets in pipeline.successors.items():
            rows.extend(f"  {source} -> {target}" for target in targets)
    rows.extend([
        "",
        "Runtime environment: not checked",
        "Not checked: topic availability, ROS message packages, calibration/model loading, CUDA.",
    ])
    return "\n".join(rows)
