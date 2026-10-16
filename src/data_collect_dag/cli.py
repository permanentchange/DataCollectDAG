from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

from data_collect_dag import __version__
from data_collect_dag.instance_guard import InstanceAlreadyRunningError, InstanceGuard, load_node_name, resolve_node_name
from data_collect_dag.logging_utils import BACKGROUND_LOG_FILENAME


def start_background(config_path: Path, pipeline_name: str, guard: InstanceGuard) -> int:
    from data_collect_dag.config import load_output_config

    root, _, _ = load_output_config(config_path)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / BACKGROUND_LOG_FILENAME
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "data_collect_dag", "run", "--config", str(config_path.resolve()),
             "--pipeline", pipeline_name, "--instance-lock-fd", str(guard.fd)],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            pass_fds=(guard.fd,),
        )
    print(f"background process created: PID={process.pid}, log={log_path}")
    return 0


def _run(args: argparse.Namespace) -> int:
    with InstanceGuard(load_node_name(args.config), args.instance_lock_fd) as guard:
        if args.background:
            return start_background(args.config, args.pipeline, guard)
        from data_collect_dag.app import AppRuntime

        runtime = AppRuntime(config_path=args.config, pipeline_name=args.pipeline)
        if resolve_node_name(runtime.app_config.ros_node_name) != guard.node_name:
            raise ValueError("ros.node_name changed during startup; retry with a stable configuration")
        return runtime.run()


def _init(args: argparse.Namespace) -> int:
    from data_collect_dag.config import load_app_config
    from data_collect_dag.config_templates import DEFAULT_CAR_ID, export_config_templates

    destination = export_config_templates(args.directory, force=args.force, car_id=args.car_id)
    config = load_app_config(destination / "cfg.yaml")
    config_car = DEFAULT_CAR_ID if args.car_id is None else args.car_id.lower()
    print(f"configuration templates exported to {destination}")
    print(f"Config: {config.config_path}")
    print(f"calibrations: {'all cars' if args.car_id is None else config_car}; config car: {config_car}")
    print(f"Calibration: {config.calibration_path}")
    print("Pipelines: " + ", ".join(config.pipelines))
    return 0


def _inspect(args: argparse.Namespace) -> int:
    from data_collect_dag.config import load_app_config
    from data_collect_dag.config_summary import format_config

    print(format_config(load_app_config(args.config), args.pipeline))
    return 0


def _clean(args: argparse.Namespace) -> int:
    from data_collect_dag.config import load_output_config
    from data_collect_dag.storage import clean_sessions

    root, _, _ = load_output_config(args.config)
    deleted = clean_sessions(root, args.keep_days)
    print(f"deleted {deleted} session directories from {root}")
    return 0


def _control(args: argparse.Namespace) -> int:
    from data_collect_dag.ros_control_client import run_control_command

    if args.command == "status":
        run_control_command("status", status_topic=args.status_topic, timeout=args.timeout)
    else:
        run_control_command(
            args.command, getattr(args, "target_pipeline", None),
            control_topic=args.control_topic, timeout=args.timeout,
        )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except InstanceAlreadyRunningError as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except Exception as exc:
        action = {
            "run": "background launch" if getattr(args, "background", False) else "collection startup",
            "init": "configuration export",
            "inspect": "configuration inspection",
            "clean": "cleanup",
        }.get(args.command, args.command)
        print(f"{action} failed: {exc}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Data collection DAG runtime and offline configuration utilities")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="export bundled configuration templates offline")
    init.add_argument("directory", type=Path, metavar="DIR")
    init.add_argument("--car-id", metavar="CAR_ID", help="case-insensitive car ID; omitted: export all cars and select sa4")
    init.add_argument("--force", action="store_true", help="overwrite configuration template files")
    init.set_defaults(handler=_init)

    inspect = commands.add_parser("inspect", help="validate and display configuration offline; no runtime checks")
    inspect.add_argument("--config", type=Path, required=True, metavar="CFG")
    inspect.add_argument(
        "--pipeline", metavar="NAME",
        help="display details for this pipeline after validating the entire configuration",
    )
    inspect.set_defaults(handler=_inspect)

    run = commands.add_parser("run", help="create a collection system process")
    run.add_argument("--config", type=Path, required=True, metavar="CFG")
    run.add_argument("--pipeline", default="init", metavar="NAME", help="pipeline to run (default: init)")
    launch = run.add_mutually_exclusive_group()
    launch.add_argument("--background", action="store_true", help="detach and print PID and log path")
    launch.add_argument("--instance-lock-fd", type=int, help=argparse.SUPPRESS)
    run.set_defaults(handler=_run)

    clean = commands.add_parser("clean", help="clean historical sessions; stop collection before cleanup")
    clean.add_argument("--config", type=Path, required=True, metavar="CFG")
    clean.add_argument("--keep-days", type=_nonnegative_days, required=True, metavar="N",
                       help="0=delete all, 1=keep today, 2=keep today and yesterday")
    clean.set_defaults(handler=_clean)

    for name in ("start", "pause", "resume", "stop", "shutdown", "status"):
        command = commands.add_parser(
            name, help="query a running instance" if name == "status" else f"send {name} to a running instance",
        )
        if name == "start":
            command.add_argument("target_pipeline", metavar="NAME")
        if name == "status":
            command.add_argument("--status-topic", default="/data_collect/status", metavar="TOPIC")
        else:
            command.add_argument("--control-topic", default="/data_collect/control", metavar="TOPIC")
        command.add_argument(
            "--timeout", type=_positive_timeout, default=5.0, metavar="SEC",
            help="ROS connection timeout (default: 5 seconds)",
        )
        command.set_defaults(handler=_control)
    return parser


def _nonnegative_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--keep-days must be a non-negative integer")
    if days < 0:
        raise argparse.ArgumentTypeError("--keep-days must be a non-negative integer")
    return days


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--timeout must be a finite positive number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("--timeout must be a finite positive number")
    return timeout


if __name__ == "__main__":
    raise SystemExit(main())
