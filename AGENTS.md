# Repository Guidelines

## Source of truth

Use the implementation in `src/data_collect_dag/` and `tools/lib/`, the committed demo YAML files and `setup.cfg` / `pyproject.toml` as the authority for documented behavior. Document implemented interfaces and executable examples. Keep design proposals, local datasets and historical migration discussions out of maintained documentation.

Keep the [runtime/config reference](docs/architecture.md) and [ROS control contract](docs/ros_external_system_architecture.md) aligned with implementation changes. Distinguish static validation, application startup and session setup; document when outputs are written and what failures can leave behind. Use the actual demo graph for pipeline behavior rather than inferring it from names. Keep installation details in [README](README.md) and repository tool parameters in the [tool guide](tools/README.md).

## Layout

- `src/data_collect_dag/cli.py`: argument parsing, command dispatch, foreground/background launch and offline utilities. `app.py::build_parser` re-exports the CLI parser.
- `src/data_collect_dag/instance_guard.py`: Linux instance lock keyed by the fully resolved ROS node name, including `ROS_NAMESPACE` for relative names.
- `src/data_collect_dag/ros_control_client.py`: anonymous ROS CLI client for control commands and one-shot status queries.
- `src/data_collect_dag/app.py`, `session.py`, `dag.py`: application, session lifecycle and DAG execution.
- `src/data_collect_dag/config.py`, `models.py`, `status.py`: configuration validation, shared types and status snapshots.
- `src/data_collect_dag/config_summary.py`: offline summaries of validated configuration; `node_registry.py`: shared node names and lazy loading information.
- `src/data_collect_dag/ros_adapter.py`, `ros_messages.py`, `ros_env.py`: ROS1 interfaces, message resolution and Python path setup.
- `src/data_collect_dag/camera_calibration.py`: shared validation of explicit camera distortion parameters; `opencv_compat.py`: lazy OpenCV loading and installation hints.
- `src/data_collect_dag/storage.py`: session cleanup, admission checks and cached storage accounting; `summary.py`: session summary serialization.
- `src/data_collect_dag/nodes/`: built-in nodes; register new types in `node_registry.py::NODE_TYPES`.
- `demo/cfg.yaml`: demo pipelines. Vehicle calibrations are under `demo/calibration/<car-id>/cali.json`.
- `src/data_collect_dag/config_templates.py`, `setup.py`, `MANIFEST.in`: template export and bundled resources.
- `tests/unit/`, `tests/functional/`: pytest coverage.
- `README.md`: installation and CLI usage. `docs/architecture.md`: runtime/config reference. `docs/ros_external_system_architecture.md`: ROS control contract.
- `tools/toolkit.py`, `tools/lib/`: repository-only toolkit dispatch and shared implementations; `tools/lib/viewer.html`: offline viewer template. Existing scripts delegate to these implementations. `tools/README.md`: tool contracts and workflows.
- `tools/batch_cali_parser.sh`: batch wrapper around `calibration convert`; source directories are external inputs. Its default conversion list is distinct from the calibrations actually bundled for `init`.
- `tests/helpers.py`: shared minimal configurations and bounded process helpers. Configuration error cases are data in `tests/unit/config_cases.py`; `tests/unit/test_toolkit.py` exercises offline tool workflows.

## Development and tests

Target Linux, Python 3.8 (`>=3.8,<3.9`) and ROS1 Noetic. Use system `/usr/bin/python3` for local tests. Install into the same interpreter; the Python package relies on the ROS installation and generated custom message packages. Builds support `setuptools>=45.2.0` and `wheel>=0.34.2`; installation must reuse existing build tools with `--no-build-isolation`.

```bash
/usr/bin/python3 -m pip install --no-build-isolation -e '.[dev]'
PYTHONPATH=src /usr/bin/python3 -m pytest tests/unit -q
PYTHONPATH=src /usr/bin/python3 -m pytest tests/functional -q
```

Real ROS integration tests use isolated local masters and standard messages:

```bash
source /opt/ros/noetic/setup.bash
PYTHONPATH=src DCD_TEST_REAL_ROS=1 /usr/bin/python3 -m pytest tests/functional/test_ros_cli_integration.py -q
```

Python 3.8 environments such as `dcd` may run the same suites when dependencies are available; real TCPROS tests also need system ROS Python dependencies such as `netifaces`. Keep thread-test assertions in the test thread, propagate background exceptions, and use bounded shutdown/join cleanup. Prefer focused unit cases plus representative real ROS scenarios over a full Cartesian product of subprocess tests.

For documentation changes, run the existing consistency checks and inspect the affected demo pipelines offline:

```bash
PYTHONPATH=src /usr/bin/python3 -m pytest tests/unit/test_documentation_links.py -q
PYTHONPATH=src /usr/bin/python3 -m data_collect_dag inspect --config demo/cfg.yaml --pipeline fps_undist_nomotion
```

These checks cover local links, code fences, documented node/status fields and executable examples; they do not establish runtime semantics. Verify descriptions against the owning implementation, especially failure paths and multi-output behavior.

The base installation must not install or import torch, torchvision, Ultralytics or ONNX. `demo/cfg.yaml` supplies non-inference `init`, `fps`, `fps_undist`, `fps_undist_nomotion` and `compare_motion` pipelines; `fps_with_yolo` requires the YOLO extra and `demo/models/yolo26m.pt`. `fps_undist_nomotion` saves original images and statically transformed lidar clouds; its precheck covers the five cameras, five lidars and `/iccp_status` (`vcu_msgs/ICCPStatus`). All five collection pipelines use `speed_gate` before FPS control to skip frames below an absolute speed of 0.01 m/s or without valid status within 500 ms; `init` prechecks the status topic and still synchronizes at zero speed. The demo selects SA4 calibration. Installer modes are `base`, `yolo`, `yolo-arm`, `dev` and `yolo-dev`; `dev` adds only pytest. Base pip dependencies are NumPy, PyYAML, Pillow and rospkg. Reuse system OpenCV 4.2 or newer through `cv2`; pip OpenCV is optional through the pip-only `opencv` extra (`opencv-python>=4.2,<4.12`). YOLO extras retain that OpenCV constraint. Keep OpenCV imports lazy and report a useful installation hint when it is missing. README remote installation examples use Gitee (`origin`) and GitHub (`github`) HTTPS URLs and the Tsinghua PyPI index. Use `--no-build-isolation` with existing setuptools>=45.2.0 and wheel>=0.34.2 without upgrading those tools; compatible existing dependencies should be reused. Jetson YOLO installations use `bash setup.bash yolo-arm` with compatible wheels in `~/wheels` and Python 3.8 on `PATH` as `python`. Resolve both wheels before uninstalling packages, install them with `--no-deps`, and constrain subsequent dependency resolution to those local files. Do not download remote torch or torchvision wheels as a fallback. The `yolo-arm` extra pins `psutil==6.1.1`; pass `--no-binary=psutil` in the final pip install to compile it alongside the YOLO dependencies. Source builds require a C compiler and Python development headers. Remote pip installs must explicitly supply both local wheel paths and `--no-binary=psutil`; extras do not scan the wheel directory or enforce source builds.

`run launch`, `inspect` and `clean` require `--config`; `clean` also requires `--keep-days N`. `run launch` creates a process and defaults to pipeline `init`; `run start NAME` controls an existing process and fails when none is available. Bare `run` prints group help and returns 0. The other pipeline controls are `run pause`, `run resume` and `run stop`; `status` and `shutdown` stay at the root. Control commands require no collection config. The four pipeline controls and `shutdown` accept `--control-topic` and `--timeout`; `status` accepts `--status-topic` and `--timeout`, with existing topics and a five-second timeout by default. One fully resolved ROS node name permits one local instance, including while `IDLE`; each instance permits one active pipeline. Acquire the lock before runtime construction, retain it through foreground/background execution and pass its descriptor to a background child using `python -m data_collect_dag run launch`. Close descriptors without explicitly unlocking the shared file description or deleting the lock file. Validate the requested pipeline name before stopping the active session. Keep control commands on the existing ROS topics and status protocol.

`init DIR [--car-id CAR_ID] [--force]` exports templates offline. Car IDs are case-insensitive; omission exports all calibrations with SA4 selected. Calibration and demo model references are absolute. Preserve unrelated files, other calibrations and weights during export, and keep packaging checks consistent with templates. Place arguments after the leaf command. `--background` and the mutually exclusive hidden `--instance-lock-fd` apply only to `run launch`; `--pipeline` applies only to `run launch` and `inspect`. `inspect` reuses full static validation without importing runtime/node implementations or checking external resources.

ROS lifecycle shutdown notifications enter the same standard-library command queue as control commands. Startup must not start a pipeline after ROS shutdown. Session setup failure and normal stopping share resource release, while preserving their different summary/error semantics. Keep `recent_session_status` as the most recently ended session's result across later starts; `RUNNING` with a prior `STOPPED` is valid.

Application startup requires `calibration.path` and validates every camera entry with the shared distortion validator, which currently accepts only `equidistant` with four finite coefficients. Offline `inspect` does not load calibration. Topic prechecks count newly received messages before conversion; they do not validate synchronization, timestamps or speed. Capacity admission failure creates no session directory; setup failure after directory creation attempts a `setup_failed` summary without a sample index. Normal stopping writes the sample index and summary, with write errors reported through logs/status.

Saved counts are per sample, even with multiple save nodes. A sample's shared `save_result` holds the last successful save node's metadata; `saved_samples.json` therefore does not enumerate every dataset written by `compare_motion`. Summary `save_outputs` collects successful nodes' camera JSON and PCD paths. Control publish success means asynchronous submission, and `IDLE` alone does not guarantee successful output writes.

The toolkit remains repository-only; do not add installed entry points or inference dependencies to the base package for it. Root/group help must stay lightweight, and command help must not load optional ROS, Plotly, Open3D or Ultralytics packages. Dataset exports use hard links only, never silent copies; dry runs perform indexing/preflight without output creation. Preserve the two existing YOLO resolution presets. Offline HTML embeds local Plotly. Calibration output, existing script arguments and the collection status protocol remain compatible; complete tool behavior is documented in `tools/README.md`.

## Code and review conventions

Use 4-space indentation, type hints at module boundaries, `snake_case` names, `PascalCase` classes and `pathlib.Path` for paths. Follow nearby code conventions. Keep ROS callbacks lightweight; processing belongs in DAG nodes.

Add focused unit coverage for configuration and node changes, functional coverage for session/control changes, and process coverage for locking/background changes. Verify that base FPS construction works without inference packages. Synchronize package `__version__` and `setup.cfg` when changing the release version. PR descriptions should state the affected behavior, config/installation impact and verification. Use specific conventional commit subjects such as `fix:`, `feat:`, `docs:`, `test:` or `build:`.
