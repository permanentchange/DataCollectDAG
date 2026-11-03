# DataCollectDAG

DataCollectDAG is a Python 3.8 / ROS1 Noetic application for collecting sensor data with YAML-defined directed acyclic graphs (DAGs). It synchronizes ROS messages, runs processing nodes and writes session results to disk. Each instance runs at most one pipeline at a time.

## Requirements

- Linux, Python 3.8 (`>=3.8,<3.9`) and ROS1 Noetic for startup and control.
- A running ROS master and the message packages referenced by the YAML. Build custom packages such as `bdstar` and `igv_msgs` in a catkin workspace and source its setup script.
- Readable calibration JSON and the live topics required by the selected pipeline. Bags, model weights and vehicle source files are external resources.

The base pip dependencies are NumPy, PyYAML, Pillow and rospkg. Image processing also needs OpenCV: reuse an importable system `cv2` (including Ubuntu/ROS OpenCV 4.2), or explicitly install the `opencv` extra. Base installation does not install pip OpenCV or inference packages. YOLO extras include pip OpenCV and Ultralytics requirements. Configuration initialization, inspection and cleanup run without a ROS master.

## Install

### Check the Python and ROS environment

Use Linux, Python 3.8 and the same interpreter for installation and execution. The following examples use `pip3` paired with system `/usr/bin/python3`; check `pip3 --version` before installing:

```bash
python3 --version
pip3 --version
source /opt/ros/noetic/setup.bash
# Source this too when using custom ROS messages:
source /path/to/catkin_ws/devel/setup.bash
python3 -c "import cv2; print(cv2.__version__); print(cv2.__file__)"
python3 -c "from importlib.metadata import version; print('setuptools:', version('setuptools')); print('wheel:', version('wheel'))"
```

An existing system `cv2` does not need a matching `opencv-python` entry in `pip3 list`. OpenCV 4.2 supports the runtime's image conversion, fisheye undistortion and projection interfaces. If `cv2` is missing, install the system package (on Ubuntu, `sudo apt-get install python3-opencv`) or use the explicit `opencv` extra below.

Source builds require `setuptools>=68` and `wheel`. If either is missing or too old, prepare them once:

```bash
pip3 install --index-url https://pypi.tuna.tsinghua.edu.cn/simple 'setuptools>=68' wheel
```

The installation examples below use `--no-build-isolation` to reuse those tools. Without that option, pip prepares a temporary isolated build environment and may download build dependencies even when the active environment has them. Compatible installed runtime dependencies are reused; missing or incompatible dependencies are resolved normally. Git must be installed for remote installation.

### Install directly from a remote repository

No local checkout is needed. The repository's `origin` remote is Gitee; `github` is GitHub. The Gitee examples select `master`; the GitHub examples select `main`. Run either command:

```bash
# Gitee (origin): primary example for domestic access
pip3 install --no-build-isolation \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  'git+https://gitee.com/xfzhou95/DataCollectDAG.git@master'
```

```bash
# GitHub (github): alternative repository
pip3 install --no-build-isolation \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  'git+https://github.com/permanentchange/DataCollectDAG.git@main'
```

These HTTPS URLs do not require the SSH setup used by the checkout's Git remotes. The [Tsinghua PyPI mirror](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/) supplies Python dependencies; Git source is downloaded directly from the selected repository. `--index-url` selects the mirror explicitly. Pip does not treat `--extra-index-url` as a lower-priority fallback. If a package is unavailable on the mirror, explicitly select another index for that operation.

Optionally make Tsinghua the default for future pip commands and `setup.bash` installations:

```bash
pip3 config --user set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

A virtual environment created with `/usr/bin/python3 -m venv --system-site-packages .venv` can access system ROS and OpenCV dependencies. Activate it before installation and check that `python3` and `pip3` select that environment.

### Verify and upgrade

```bash
pip3 show data-collect-dag
python3 -c "import cv2; print(cv2.__version__); print(cv2.__file__)"
data_collect_dag --version
data_collect_dag --help
data_collect_dag init ~/data_collect_config --car-id sa4
data_collect_dag inspect --config ~/data_collect_config/cfg.yaml
```

On a system-OpenCV installation, the `cv2` path should still point to the system module. Inspection validates configuration offline; it does not check live ROS topics or external resources.

To upgrade from Gitee while retaining compatible dependencies:

```bash
pip3 install --upgrade --upgrade-strategy only-if-needed --no-build-isolation \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  'git+https://gitee.com/xfzhou95/DataCollectDAG.git@master'
```

For GitHub, replace the URL with `git+https://github.com/permanentchange/DataCollectDAG.git@main`. Pip uses project versions for VCS upgrades; a different commit with the same version is not a new release. Use a tag or commit instead of a branch name when a fixed revision is required. Remote installation uses the version published to that repository, including its dependency declarations.

### Optional dependencies and local development

The installer supports five modes:

| Mode | Purpose |
| --- | --- |
| `base` | Base runtime using an existing OpenCV; also the default |
| `yolo` | Base runtime and pip OpenCV/Ultralytics, including PyTorch requirements |
| `yolo-arm` | YOLO with local Jetson PyTorch wheels and psutil 6.1.1 |
| `dev` | Base runtime and pytest |
| `yolo-dev` | YOLO and pytest |

The pip-only `opencv` extra installs `opencv-python>=4.2,<4.12` for environments without system OpenCV. It is not a `setup.bash` mode. Install it directly from either repository using the named extra syntax:

```bash
pip3 install --no-build-isolation \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  'data-collect-dag[opencv] @ git+https://gitee.com/xfzhou95/DataCollectDAG.git@master'
```

Use `[yolo]`, `[dev]` or `[yolo-dev]` in the same remote syntax for those extras. YOLO depends on pip OpenCV through Ultralytics; system OpenCV reuse applies to base and non-YOLO development installations.

From a local checkout, editable installation supports development:

```bash
pip3 install --no-build-isolation --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e .
# Development with an existing system OpenCV:
pip3 install --no-build-isolation --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e '.[dev]'
# Only when a pip OpenCV installation is needed:
pip3 install --no-build-isolation --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e '.[opencv]'
# YOLO:
pip3 install --no-build-isolation --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e '.[yolo]'
```

Plain `pip3 install .` and `pip3 install '.[base]'` install the same base runtime. `dev` adds only pytest; install standalone tool dependencies such as Open3D separately. Model export and ONNX dependencies are also installed separately when needed. ONNX models run through Ultralytics in the `yolo_gate` node.

`bash setup.bash [base|yolo|yolo-arm|dev|yolo-dev]` installs from the checkout using `python` on `PATH`, which must be Python 3.8. It inherits the configured pip index; omitting the mode selects `base`. The node's `device` setting selects the inference device. There is no `yolo-arm-dev` mode.

For Jetson YOLO installations, run `bash setup.bash yolo-arm`. It checks both local wheels before uninstalling packages, reinstalls `torch` and `torchvision` directly from `~/wheels` with `--no-deps`, then installs the YOLO extra with those exact wheel files as constraints. It never downloads remote torch or torchvision wheels, including same-version alternatives. Conflicting requirements fail rather than selecting another build; other dependencies can be downloaded normally. The script selects its preferred filenames or a single wheel per package. Set `DCD_WHEEL_DIR`, `DCD_TORCH_WHEEL` and `DCD_TORCHVISION_WHEEL` to choose specific compatible builds. Match Python, JetPack and CUDA to the device.

The `yolo-arm` extra pins `psutil==6.1.1`. The installer passes `--no-binary=psutil` to compile it from source as part of the same YOLO dependency installation; no separate psutil installation is needed. Prepare a C compiler and Python development headers, for example with `sudo apt-get install gcc python3-dev` on Ubuntu. Compilation failures stop installation and retain pip's diagnostics.

For remote pip installation without a checkout, explicitly supply both local wheels and `--no-binary=psutil` in the same command. This example assumes exactly one compatible wheel per package in `~/wheels`:

```bash
pip3 install --no-build-isolation \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --no-binary=psutil \
  "$HOME"/wheels/torch-*.whl \
  "$HOME"/wheels/torchvision-*.whl \
  'data-collect-dag[yolo-arm] @ git+https://gitee.com/xfzhou95/DataCollectDAG.git@master'
```

Replace the globs with exact paths when multiple builds are present. The wheel paths fix the torch and torchvision sources during dependency resolution. The `[yolo-arm]` extra alone does not scan `~/wheels` or enforce source installation of psutil: `--no-binary=psutil` must be supplied to pip. `--find-links` alone does not guarantee selection of local builds.

## Initialize configuration

Wheel and source distributions bundle demo YAML and vehicle calibration JSON. Export them to a working directory:

```bash
data_collect_dag init ~/data_collect_config --car-id sa4
```

Car IDs are case-insensitive. Omitting `--car-id` exports all available calibrations and selects SA4 in the YAML. Unknown IDs fail before writing and list the available cars. The export includes all bundled YAML files, the selected `calibration/<car-id>/cali.json` files and a `models/` directory for your weights. Calibration paths and `demo/` model references in exported YAML are absolute; `output.root_dir` and other settings retain their configured values.

Existing template filenames require `--force`:

```bash
data_collect_dag init ~/data_collect_config --car-id sa14 --force
```

`--force` replaces exported template files and preserves unrelated files, other car directories and model weights. Directory symlinks escaping the destination are rejected. Editable installations export the checkout's demo files; ordinary installations export packaged templates. Review topics, calibration and storage settings before collecting. If you move the export directory, update its absolute references or export again.

Python callers can use the same exporter, which returns the resolved destination as a `Path`:

```python
from pathlib import Path
from data_collect_dag.config_templates import export_config_templates

destination = export_config_templates(
    Path.home() / "data_collect_config",
    car_id="sa4",
)
```

The Python interface also accepts `force=True` for the same template replacement behavior.

## Inspect configuration offline

```bash
data_collect_dag inspect --config ~/data_collect_config/cfg.yaml
data_collect_dag inspect --config ~/data_collect_config/cfg.yaml --pipeline fps_undist_nomotion
```

`inspect` validates the entire file with the same static parser used at startup, then displays its absolute path, program version, output/storage settings, calibration path, configured ROS node and control/status topics, topic count, and pipeline names, descriptions and main sources. Selecting a pipeline adds its topic precheck, wait time, stop conditions, node IDs/types and DAG edges. A broken unselected pipeline also fails validation; an unknown selection lists available names.

A static PASS does not check live topics, ROS message packages, calibration/model loading or CUDA. Inspection is offline and read-only: it creates no output directory or session and imports no ROS or inference runtime. It does not require calibration/model files to exist. Use `status` to query a running process. Invalid arguments return exit code 2; configuration or operation failures return 1; successful operations return 0.

## Start and control

Start `roscore` in a separate ROS-enabled terminal. Source ROS and any custom message workspace in both the startup and control terminals. From the repository root:

```bash
# Start with the default init pipeline:
data_collect_dag run launch --config demo/cfg.yaml
# Or use the configuration exported above to collect in the background:
data_collect_dag run launch --config ~/data_collect_config/cfg.yaml --pipeline fps_undist_nomotion --background
tail -f ~/DATA_COLLECT/background.log
```

The CLI supports these forms, also available through `/usr/bin/python3 -m data_collect_dag`:

```text
data_collect_dag init DIR [--car-id CAR_ID] [--force]
data_collect_dag inspect --config CFG [--pipeline NAME]
data_collect_dag run launch --config CFG [--pipeline NAME] [--background]
data_collect_dag run start NAME [--control-topic TOPIC] [--timeout SEC]
data_collect_dag run {pause,resume,stop} [--control-topic TOPIC] [--timeout SEC]
data_collect_dag status [--status-topic TOPIC] [--timeout SEC]
data_collect_dag shutdown [--control-topic TOPIC] [--timeout SEC]
data_collect_dag clean --config CONFIG --keep-days N
```

Each command owns its arguments, placed after the leaf command. `run launch` creates a process and defaults to pipeline `init`; `run start NAME` controls an existing process and fails if none is available. Bare `run` displays the command group's help and returns 0. `init` exports the installed version's templates; it does not upgrade the program, download weights or start collection. Root options are `--help` and `--version`; each subcommand has its own `--help`. `--config` belongs to `run launch`, `inspect` and `clean`; `--pipeline` belongs to `run launch` and `inspect`; `--background` belongs to `run launch`; `--car-id` and `--force` belong to `init`.

```bash
data_collect_dag --version
data_collect_dag init --help
data_collect_dag inspect --help
data_collect_dag run --help
data_collect_dag run launch --help
```

Installation and upgrades do not update configurations already exported. After upgrading, use `init DIR --force` to replace template files; preserve any custom configuration edits first. Initialization prints the actual configuration path, calibration selection and pipeline names.

On one Linux device, each fully resolved `ros.node_name` permits one instance, including while `IDLE`. Relative names use `ROS_NAMESPACE`; absolute names specify their namespace. Foreground and background startup acquire the same local lock before initializing ROS or loading calibration. Different node names can run together; give them distinct control/status topics to control them separately.

Background startup passes the lock descriptor to the detached child, prints its PID and log path, and returns. The child inherits the Python environment, ROS environment and working directory. stdout/stderr append to `<output.root_dir>/background.log`. The PID confirms process creation; use the log and status to check initialization and collection. The program releases its lock on exit, including abnormal termination. Lock implementation details are in [runtime architecture](docs/architecture.md#1-运行路径).

Control a running instance from another terminal:

```bash
data_collect_dag run start fps
data_collect_dag run pause
data_collect_dag run resume
data_collect_dag run stop
data_collect_dag status
data_collect_dag shutdown
```

`run start` checks the pipeline name before replacing the current session. An unknown name preserves the active session, including its paused state, and records `last_error`. `run pause` pauses collection; `run resume` continues it. `run stop` ends the session and leaves the program available for another `run start`. `shutdown` ends the session, writes its summary and exits. Foreground Ctrl+C and `kill -TERM PID` also request cleanup and exit.

An external ROS shutdown request, including `rosnode kill` or a same-name node registering with the same Master from another host, also requests session cleanup and process exit. An active session records `end_reason: ros_shutdown`; after the process exits, its local instance lock is released. This does not detect or reconnect after Master loss.

The anonymous control client uses `/data_collect/control` and `/data_collect/status` by default. Write commands accept `--control-topic`; `status` accepts `--status-topic`. Match custom YAML topics with CLI overrides:

```bash
data_collect_dag run start fps --control-topic /vehicle/control --timeout 5
data_collect_dag status --status-topic /vehicle/status --timeout 5
```

The client loads no collection YAML, calibration, DAG or inference model. `--timeout` is a finite positive number, defaulting to five seconds for the Master check and the topic connection/message wait. Missing Master, missing control subscriber, missing status publisher and detected send failures return nonzero. A sent command is asynchronous; query `status` to inspect State, Pipeline, Session, Saved, Skipped, Failed, Disk Free and Last Error. See [ROS control interface](docs/ros_external_system_architecture.md) for the message contract.

The JSON status topic also carries `recent_session_status`, the result of the most recently ended session. It remains available when another session starts: `tool_state: RUNNING` with `recent_session_status: STOPPED` means the current session is running and a previous session stopped. Use `tool_state` and `current_session_id` to identify current activity. `run stop` leaves the program `IDLE`, clears current-session identifiers and retains counters until the next successful start.

The one-shot control publisher uses synchronous publishing (`queue_size=None`). Noetic may print a `queue_size` SyntaxWarning for this setting; the command's exit code and subsequent status determine whether it succeeded.

### Demo pipelines

| Pipeline in `demo/cfg.yaml` | Behavior | Inference required |
| --- | --- | --- |
| `init` | Topic precheck and synchronization; two-second active-duration limit, no save node | No |
| `fps` | 1 FPS collection with image undistortion and point-cloud motion compensation | No |
| `fps_undist` | FPS collection with motion compensation and five original camera images | No |
| `fps_undist_nomotion` | FPS collection with static lidar transforms and five original camera images | No |
| `fps_with_yolo` | YOLO-filtered FPS collection with undistortion | YOLO |
| `compare_motion` | Motion-compensated and uncompensated point clouds in separate datasets | No |

Collection pipelines have a 6,000-second active-duration limit. Completed or failed sessions leave the program available for control. Startup resolves message types for every configured `ros.topics` entry. Demo pipelines precheck configured topics for new messages within 500 ms on each session start, including `/iccp_status` (`vcu_msgs/ICCPStatus`). The ROS environment must provide this message type. `fps_undist_nomotion` checks the five cameras, five lidars and vehicle status; it needs no incoming localization, IMU, odometry or NMEA messages. It transforms each lidar into `base` using static calibration and merges the clouds without ego-motion compensation.

The five collection pipelines run `start -> speed_gate -> fps_guard` before synchronization and processing. The gate reads `Current_Speed` in m/s and skips frames whose absolute speed is below `min_abs_speed_mps` (default `0.01`); equality passes at float32 precision. It uses the latest status timestamp at or before the main image timestamp, with `max_age_ms: 500` by default. Missing, stale or non-finite speed also skips the frame; collection resumes when valid speed reaches the threshold. The image and status timestamps must use the same clock. Skip reasons appear in the ROS status `skip_reasons` and session summary, with speed details in the debug log. `init` checks the status topic but runs synchronization even at zero speed. See [speed gate configuration](docs/architecture.md#3-内置节点).

```bash
data_collect_dag run launch --config demo/cfg.yaml --pipeline fps_undist_nomotion
# Or control an instance started with this pipeline in its configuration:
data_collect_dag run start fps_undist_nomotion
```

Pipeline definitions are loaded at program startup. Configuration edits require restarting the collection program; exported configurations must be updated or re-exported separately.

The demo selects `demo/calibration/sa4/cali.json`. `fps_with_yolo` requires the YOLO extra and `demo/models/yolo26m.pt`; supply your compatible weights in the checkout or export's `models/` directory. Its configured `device: gpu` requires CUDA; select `cpu` when using CPU inference.

Relative `output.root_dir`, `calibration.path` and YOLO `model_path` values use the process working directory. The convenience command `bash run.sh /absolute/path/config.yaml [pipeline_name]` changes to the repository root and uses `.venv/bin/python3` when `.venv` exists, otherwise `python3` on `PATH`.

## Storage and cleanup

`output.disk_storage_limit_GB` limits ordinary file sizes under `output.root_dir`; `output.disk_storage_need_GB` sets the minimum free space required on each session start. Both use decimal GB (1,000,000,000 bytes), accept finite non-negative numbers, and are disabled by omission, `null` or `0`. The demo sets both to `100`.

At startup, output size at or above the limit or free space below the requirement rejects the session. During collection, writes update cached sizes, logs are checked every second and directory scans reconcile external changes every 30 seconds. Reaching the limit stops new samples, finishes in-flight work and ends the session as `FAILED`. In-flight writes and final metadata can exceed the limit. Free space is checked at session admission. After freeing space, use `run start` to retry.

Clean session directories while collection is stopped:

```bash
data_collect_dag clean --config demo/cfg.yaml --keep-days 2
```

`clean` exits after cleanup and needs only the output configuration. `0` removes all valid session directories, `1` keeps today, and `2` keeps today and yesterday. The date comes from the local-time directory name `YYYYMMDD_HHMMSS_<eight lowercase hex digits>`. For positive values, future dates are preserved. Unrelated files/directories and symbolic links are preserved; failures return nonzero.

## Calibration and results

Calibration files use `demo/calibration/<car-id>/cali.json`. Camera entries require `dist_model: equidistant` and four finite numeric `dist_coeffs` in OpenCV fisheye order `[k1, k2, k3, k4]`. Generate a calibration from an external vehicle source directory:

```bash
/usr/bin/python3 tools/cali_parser.py /path/to/argus/SA14 -o demo/calibration/sa14/cali.json
# Equivalent grouped tool command:
/usr/bin/python3 tools/toolkit.py calibration convert /path/to/argus/SA14 -o demo/calibration/sa14/cali.json
```

The tool creates output parent directories. Omitting `-o` writes `cali.json` inside the input vehicle directory.

Session results under `<output.root_dir>/<session_id>/` include `debug.log`, `session_summary.json` and `saved_samples.json`. A configured Xtreme1 save node writes JPEG images, ASCII PCD and camera JSON under `xtreme1/<dataset_name>/`. `fps_undist` and `fps_undist_nomotion` save under `collect_demo` and include source distortion parameters in each camera entry; intrinsics scale to the saved image resolution. See [configuration and output formats](docs/architecture.md#4-数据与文件).

## Repository tools

The checkout includes a separate toolkit; it is not an installed `data_collect_dag` subcommand and is not exported by `init`:

```bash
/usr/bin/python3 tools/toolkit.py --help
/usr/bin/python3 tools/toolkit.py cloud --help
/usr/bin/python3 tools/toolkit.py dataset check /path/to/session/xtreme1/collect_demo
```

| Group | Commands |
| --- | --- |
| `cloud` | `view`, `bev`, `project` |
| `dataset` | `check`, `select`, `merge`, `split`, `archive` |
| `yolo` | `filter`, `frames` |
| `calibration` | `convert` |
| `ros` | `sync`, `record`, `camera-reset` |
| `monitor` | `jetson`, `report` |

Existing script paths delegate to shared implementations in `tools/lib/`. The toolkit supports offline HTML viewers, pinhole/fisheye camera projection, hard-link dataset preparation, ZIP archives and vehicle diagnostics. Optional tool dependencies remain separate from the base install. See the [toolkit guide](tools/README.md) for dependencies, defaults, compatibility details and complete workflows.

## Tests

Run with system Python 3.8 from the repository root:

```bash
pip3 install --no-build-isolation --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e '.[dev]'
PYTHONPATH=src /usr/bin/python3 -m pytest tests/unit -q
PYTHONPATH=src /usr/bin/python3 -m pytest tests/functional -q
```

The default tests simulate ROS and use real Linux processes for locking and background inheritance. Opt-in integration tests start isolated local ROS1 masters and exercise the CLI over TCPROS:

```bash
source /opt/ros/noetic/setup.bash
PYTHONPATH=src DCD_TEST_REAL_ROS=1 /usr/bin/python3 -m pytest tests/functional/test_ros_cli_integration.py -q
```

These integration tests use standard ROS messages and temporary configurations. They cover foreground/background controls, external ROS shutdown, same-name node replacement, signal handling, summary writing, lock release and restart. The selected Python must have the ROS Python dependencies available, including `netifaces` for TCPROS connections.

Base-install tests check FPS DAG construction without inference packages. Packaging tests use temporary installations to check template export and VCS upgrades. Toolkit tests use small offline datasets, fake predictions, recorded metrics and dry runs; they do not operate vehicle hardware. Test helpers are shared under `tests/helpers.py`. Python 3.8 environments such as `dcd` can run the same suites when their dependencies are available. Development rules are in [AGENTS.md](AGENTS.md).
