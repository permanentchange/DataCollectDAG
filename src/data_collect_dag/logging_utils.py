from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level_name: Optional[str] = None) -> None:
    root = logging.getLogger()
    env_level = os.environ.get("DATA_COLLECT_LOG_LEVEL")
    level = _parse_level(level_name or env_level or "INFO")
    root.setLevel(level)
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


def attach_session_log_file(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(path), encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler


def detach_handler(handler: Optional[logging.Handler]) -> None:
    if handler is None:
        return
    root = logging.getLogger()
    root.removeHandler(handler)
    handler.close()


def _parse_level(level_name: str) -> int:
    level = getattr(logging, str(level_name).upper(), None)
    if isinstance(level, int):
        return level
    return logging.DEBUG
