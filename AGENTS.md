# Repository Guidelines

## Source of truth

Use the implementation in `src/data_collect_dag/`, the committed demo YAML files and `pyproject.toml` as the authority for documented behavior. Document implemented interfaces and executable examples. Keep design proposals, local datasets and historical migration discussions out of maintained documentation.

## Layout

- `src/data_collect_dag/cli.py`: argument parsing, command dispatch, foreground/background launch and offline utilities. `app.py::build_parser` re-exports the CLI parser.
- `src/data_collect_dag/instance_guard.py`: Linux instance lock keyed by the fully resolved ROS node name, including `ROS_NAMESPACE` for relative names.
- `src/data_collect_dag/ros_control_client.py`: anonymous ROS CLI client for control commands and one-shot status queries.
- `src/data_collect_dag/app.py`, `session.py`, `dag.py`: application, session lifecycle and DAG execution.
- `src/data_collect_dag/config.py`, `models.py`, `status.py`: configuration validation, shared types and status snapshots.
- `src/data_collect_dag/config_summary.py`: offline summaries of validated configuration; `node_registry.py`: shared node names and lazy loading information.
- `src/data_collect_dag/ros_adapter.py`, `ros_messages.py`, `ros_env.py`: ROS1 interfaces, message resolution and Python path setup.
- `src/data_collect_dag/nodes/`: built-in nodes; register new types in `node_registry.py::NODE_TYPES`.
- `demo/cfg.yaml`: demo pipelines. Vehicle calibrations are under `demo/calibration/<car-id>/cali.json`.
- `src/data_collect_dag/config_templates.py`, `setup.py`, `MANIFEST.in`: template export and bundled resources.
- `tests/unit/`, `tests/functional/`: pytest coverage.
- `README.md`: installation and CLI usage. `docs/architecture.md`: runtime/config reference. `docs/ros_external_system_architecture.md`: ROS control contract.

## Development and tests

Target Linux, Python 3.8 (`>=3.8,<3.9`) and ROS1 Noetic. Use system `/usr/bin/python3` for local tests. Install into the same interpreter; the Python package relies on the ROS installation and generated custom message packages. Builds require `setuptools>=68` and `wheel`.

```bash
/usr/bin/python3 -m pip install -e '.[dev]'
PYTHONPATH=src /usr/bin/python3 -m pytest tests/unit -q
PYTHONPATH=src /usr/bin/python3 -m pytest tests/functional -q
```

Real ROS integration tests use isolated local masters and standard messages:

```bash
source /opt/ros/noetic/setup.bash
PYTHONPATH=src DCD_TEST_REAL_ROS=1 /usr/bin/python3 -m pytest tests/functional/test_ros_cli_integration.py -q
```

The base installation must not install or import torch, torchvision, Ultralytics or ONNX. `demo/cfg.yaml` supplies non-inference `init`, `fps`, `fps_undist`, `fps_undist_nomotion` and `compare_motion` pipelines; `fps_with_yolo` requires the YOLO extra and `demo/models/yolo26m.pt`. `fps_undist_nomotion` saves original images and statically transformed lidar clouds; its precheck covers the five cameras, five lidars and `/iccp_status` (`vcu_msgs/ICCPStatus`). All five collection pipelines use `speed_gate` before FPS control to skip frames below an absolute speed of 0.01 m/s or without valid status within 500 ms; `init` prechecks the status topic and still synchronizes at zero speed. The demo selects SA4 calibration. Installation modes and pip extras are `base`, `yolo`, `yolo-arm`, `dev` and `yolo-dev`; `dev` adds only pytest. Jetson YOLO installations use `bash setup.bash yolo-arm` with compatible wheels in `~/wheels` and Python 3.8 on `PATH` as `python`. Resolve both wheels before uninstalling packages, install them with `--no-deps`, and constrain subsequent dependency resolution to those local files. Do not download remote torch or torchvision wheels as a fallback. Remote pip installs must explicitly supply both local wheel paths; extras do not scan the wheel directory.

`run launch`, `inspect` and `clean` require `--config`; `clean` also requires `--keep-days N`. `run launch` creates a process and defaults to pipeline `init`; `run start NAME` controls an existing process and fails when none is available. Bare `run` prints group help and returns 0. The other pipeline controls are `run pause`, `run resume` and `run stop`; `status` and `shutdown` stay at the root. Control commands require no collection config. The four pipeline controls and `shutdown` accept `--control-topic` and `--timeout`; `status` accepts `--status-topic` and `--timeout`, with existing topics and a five-second timeout by default. One fully resolved ROS node name permits one local instance, including while `IDLE`; each instance permits one active pipeline. Acquire the lock before runtime construction, retain it through foreground/background execution and pass its descriptor to a background child using `python -m data_collect_dag run launch`. Close descriptors without explicitly unlocking the shared file description or deleting the lock file. Validate the requested pipeline name before stopping the active session. Keep control commands on the existing ROS topics and status protocol.

`init DIR [--car-id CAR_ID] [--force]` exports templates offline. Car IDs are case-insensitive; omission exports all calibrations with SA4 selected. Calibration and demo model references are absolute. Preserve unrelated files, other calibrations and weights during export, and keep packaging checks consistent with templates. Place arguments after the leaf command. `--background` and the mutually exclusive hidden `--instance-lock-fd` apply only to `run launch`; `--pipeline` applies only to `run launch` and `inspect`. `inspect` reuses full static validation without importing runtime/node implementations or checking external resources.

## Code and review conventions

Use 4-space indentation, type hints at module boundaries, `snake_case` names, `PascalCase` classes and `pathlib.Path` for paths. Follow nearby code conventions. Keep ROS callbacks lightweight; processing belongs in DAG nodes.

Add focused unit coverage for configuration and node changes, functional coverage for session/control changes, and process coverage for locking/background changes. Verify that base FPS construction works without inference packages. Synchronize package `__version__` and `pyproject.toml` when changing the release version. PR descriptions should state the affected behavior, config/installation impact and verification. Use specific conventional commit subjects such as `fix:`, `feat:`, `docs:`, `test:` or `build:`.
