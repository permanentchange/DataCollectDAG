from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from data_collect_dag.instance_guard import InstanceAlreadyRunningError, InstanceGuard, load_node_name, resolve_node_name
from data_collect_dag.logging_utils import BACKGROUND_LOG_FILENAME


def start_background(config_path: Path, pipeline_name: str, guard: InstanceGuard) -> int:
    from data_collect_dag.config import load_output_config

    root, _, _ = load_output_config(config_path)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / BACKGROUND_LOG_FILENAME
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "data_collect_dag", "--config", str(config_path.resolve()),
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.force and args.init_demo is None:
        parser.error("--force requires --init-demo")
    if args.car_id is not None and args.init_demo is None:
        parser.error("--car-id requires --init-demo")
    if args.command is not None:
        if any((args.config is not None, args.init_demo is not None, args.clean is not None,
                args.background, args.pipeline is not None, args.instance_lock_fd is not None)):
            parser.error("control commands cannot be combined with collection, cleanup or export options")
        if args.command == "start" and not args.target_pipeline:
            parser.error("start requires a pipeline name")
        if args.command != "start" and args.target_pipeline is not None:
            parser.error(f"{args.command} does not accept a pipeline name")
        from data_collect_dag.ros_control_client import run_control_command

        try:
            run_control_command(
                args.command, args.target_pipeline,
                control_topic=args.control_topic or "/data_collect/control",
                status_topic=args.status_topic or "/data_collect/status",
                timeout=args.timeout if args.timeout is not None else 5.0,
            )
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.target_pipeline is not None:
        parser.error("pipeline name is only valid after start")
    if any(value is not None for value in (args.control_topic, args.status_topic, args.timeout)):
        parser.error("--control-topic, --status-topic and --timeout require a control command")
    if args.instance_lock_fd is not None and (args.config is None or args.background or args.clean is not None):
        parser.error("inherited instance lock requires foreground collection")
    if args.init_demo is not None:
        from data_collect_dag.demo_templates import DEFAULT_CAR_ID, export_demo

        if args.background:
            parser.error("--init-demo cannot be combined with --background")
        if args.clean is not None:
            parser.error("--init-demo cannot be combined with --clean")
        try:
            destination = export_demo(args.init_demo, force=args.force, car_id=args.car_id)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"demo export failed: {exc}", file=sys.stderr)
            return 1
        print(f"demo templates exported to {destination}")
        config_car = DEFAULT_CAR_ID if args.car_id is None else args.car_id.lower()
        print(f"calibrations: {'all cars' if args.car_id is None else config_car}; config car: {config_car}")
        print(f"data_collect_dag --config {shlex.quote(str(destination / 'cfg.yaml'))} --pipeline fps_undist")
        return 0
    if args.config is None:
        if args.clean is not None:
            parser.error("--clean requires --config CONFIG to determine output.root_dir")
        if args.background:
            parser.error("--background requires --config CONFIG")
        if len(sys.argv) == 1:
            parser.error("provide --config CONFIG to run collection, or --init-demo DIR to export templates; see --help")
        parser.error("--config CONFIG is required to run collection")
    if args.clean is not None:
        from data_collect_dag.config import load_output_config
        from data_collect_dag.storage import clean_sessions

        try:
            root, _, _ = load_output_config(args.config)
            deleted = clean_sessions(root, args.clean)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"cleanup failed: {exc}", file=sys.stderr)
            return 1
        print(f"deleted {deleted} session directories from {root}")
        return 0
    try:
        with InstanceGuard(load_node_name(args.config), args.instance_lock_fd) as guard:
            if args.background:
                return start_background(args.config, args.pipeline or "init", guard)
            from data_collect_dag.app import AppRuntime

            runtime = AppRuntime(config_path=args.config, pipeline_name=args.pipeline or "init")
            if resolve_node_name(runtime.app_config.ros_node_name) != guard.node_name:
                raise ValueError("ros.node_name changed during startup; retry with a stable configuration")
            return runtime.run()
    except InstanceAlreadyRunningError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        action = "background launch" if args.background else "collection startup"
        print(f"{action} failed: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Data collection DAG runtime and demo utilities",
        usage=(
            "%(prog)s --config CONFIG [--pipeline PIPELINE] [--background]\n"
            "       %(prog)s start PIPELINE [--control-topic TOPIC] [--timeout SEC]\n"
            "       %(prog)s {pause,resume,stop,shutdown} [--control-topic TOPIC] [--timeout SEC]\n"
            "       %(prog)s status [--status-topic TOPIC] [--timeout SEC]\n"
            "       %(prog)s --config CONFIG --clean DAY\n"
            "       %(prog)s --init-demo DIR [--car-id CAR_ID] [--force]"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("command", nargs="?", choices=("start", "pause", "resume", "stop", "status", "shutdown"),
                        help="control a running instance without --config")
    parser.add_argument("target_pipeline", nargs="?", metavar="PIPELINE", help="pipeline name for start")
    parser.add_argument("--control-topic", metavar="TOPIC", help="control topic (default: /data_collect/control)")
    parser.add_argument("--status-topic", metavar="TOPIC", help="status topic (default: /data_collect/status)")
    parser.add_argument("--timeout", type=_positive_timeout, metavar="SEC", help="ROS connection timeout (default: 5 seconds)")
    parser.add_argument("--instance-lock-fd", type=int, help=argparse.SUPPRESS)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path, help="YAML configuration file; required for collection and cleanup")
    source.add_argument("--init-demo", type=Path, metavar="DIR", help="export bundled demo templates and exit; no config or ROS needed")
    parser.add_argument(
        "--car-id", metavar="CAR_ID",
        help="export only this car's calibration (case-insensitive); only valid with --init-demo\n"
             "if omitted, export all cars and use sa4 in the configuration",
    )
    parser.add_argument("--force", action="store_true", help="overwrite template files; only valid with --init-demo")
    parser.add_argument("--pipeline", help="pipeline to run (default: init); ignored during cleanup/export")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--clean", type=_nonnegative_days, metavar="DAY",
        help=(
            "clean sessions under output.root_dir and exit:\n"
            "0=delete all, 1=keep today, 2=keep today and yesterday\n"
            "requires --config; stop collection before cleanup"
        ),
    )
    mode.add_argument("--background", action="store_true", help="run collection in the background; requires --config; print PID and log path")
    return parser


def _nonnegative_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--clean must be a non-negative integer")
    if days < 0:
        raise argparse.ArgumentTypeError("--clean must be a non-negative integer")
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
