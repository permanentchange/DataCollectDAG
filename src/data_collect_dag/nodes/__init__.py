"""Public node classes, loaded only when requested."""
from __future__ import annotations

from typing import Any

from data_collect_dag.node_registry import NODE_TYPES, load_node_type


__all__ = ["BaseNode"] + [class_name for _, class_name in NODE_TYPES.values()]


def __getattr__(name: str) -> Any:
    if name == "BaseNode":
        from data_collect_dag.nodes.base import BaseNode

        value = BaseNode
    else:
        for node_type, (_, class_name) in NODE_TYPES.items():
            if class_name == name:
                value = load_node_type(node_type)
                break
        else:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
