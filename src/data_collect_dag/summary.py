from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from data_collect_dag.io_utils import write_json
from data_collect_dag.models import SessionSummary


class SummaryWriter:
    def write(self, path: Path, summary: SessionSummary) -> None:
        write_json(
            path,
            {
                "session_id": summary.session_id,
                "pipeline_name": summary.pipeline_name,
                "start_time": summary.start_time,
                "end_time": summary.end_time,
                "end_status": summary.end_status,
                "end_reason": summary.end_reason,
                "config_path": summary.config_path,
                "session_root": summary.session_root,
                "pipeline_params": summary.pipeline_params,
                "metrics": summary.metrics,
                "save_outputs": summary.save_outputs,
                "last_error": summary.last_error,
                "warnings": summary.warnings,
            },
        )

