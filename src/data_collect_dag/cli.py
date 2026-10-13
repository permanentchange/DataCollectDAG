from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from data_collect_dag.app import AppRuntime, build_parser
from data_collect_dag.config import load_output_config
from data_collect_dag.demo_templates import export_demo
from data_collect_dag.logging_utils import BACKGROUND_LOG_FILENAME
from data_collect_dag.storage import clean_sessions


def start_background(config_path: Path, pipeline_name: str) -> int:
    root, _, _ = load_output_config(config_path)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / BACKGROUND_LOG_FILENAME
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "data_collect_dag", "--config", str(config_path.resolve()),
             "--pipeline", pipeline_name],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    print(f"background process created: PID={process.pid}, log={log_path}")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.force and args.init_demo is None:
        parser.error("--force requires --init-demo")
    if args.init_demo is not None:
        if args.background or args.clean is not None:
            parser.error("--init-demo cannot be combined with --background or --clean")
        try:
            destination = export_demo(args.init_demo, force=args.force)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"demo export failed: {exc}", file=sys.stderr)
            return 1
        print(f"demo templates exported to {destination}")
        print(f"data_collect_dag --config {shlex.quote(str(destination / 'cfg.yaml'))} --pipeline fps_undist")
        return 0
    if args.background:
        try:
            return start_background(args.config, args.pipeline)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"background launch failed: {exc}", file=sys.stderr)
            return 1
    if args.clean is not None:
        try:
            root, _, _ = load_output_config(args.config)
            deleted = clean_sessions(root, args.clean)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"cleanup failed: {exc}", file=sys.stderr)
            return 1
        print(f"deleted {deleted} session directories from {root}")
        return 0
    runtime = AppRuntime(config_path=args.config, pipeline_name=args.pipeline)
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())

