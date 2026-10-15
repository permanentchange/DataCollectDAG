# DataCollectDAG

DataCollectDAG is a Python 3.8 / ROS1 Noetic application for collecting sensor data with YAML-defined directed acyclic graphs (DAGs). It synchronizes ROS messages, runs processing nodes and writes session results to disk. Each instance runs at most one pipeline at a time.

## Requirements

- Linux, Python 3.8 (`>=3.8,<3.9`) and ROS1 Noetic for startup and control.
- A running ROS master and the message packages referenced by the YAML. Build custom packages such as `bdstar` and `igv_msgs` in a catkin workspace and source its setup script.
- Readable calibration JSON and the live topics required by the selected pipeline. Bags, model weights and vehicle source files are external resources.

The base package depends on NumPy, PyYAML, Pillow, OpenCV and rospkg. Inference packages are optional and loaded when a YOLO node is initialized. Template export and cleanup run without a ROS master.

## Install

On Ubuntu with system Python 3.8, from the repository root:

```bash
source /opt/ros/noetic/setup.bash
# Source this too when using custom ROS messages:
source /path/to/catkin_ws/devel/setup.bash
/usr/bin/python3 --version
/usr/bin/python3 -m pip install -e .
data_collect_dag --help
```

Use the same Python environment for installation and execution. A virtual environment created with `/usr/bin/python3 -m venv --system-site-packages .venv` can also access system ROS dependencies. The build backend requires `setuptools>=68` and `wheel`; pip normally supplies these in its isolated build environment. For offline builds with `--no-build-isolation`, prepare them in the selected Python environment first.

Installation from Git requires Git:

```bash
/usr/bin/python3 -m pip install --upgrade 'git+https://github.com/permanentchange/DataCollectDAG.git@main'
# Alternative repository:
# /usr/bin/python3 -m pip install --upgrade 'git+https://gitee.com/xfzhou95/DataCollectDAG.git@main'
/usr/bin/python3 -m pip show data-collect-dag
```

### Optional inference and tools

| Extra | Purpose |
| --- | --- |
| `yolo` | Ultralytics inference, including its PyTorch requirements |
| `yolo-export` | Ultralytics model export dependencies |
| `onnx-cpu` | ONNX and CPU ONNX Runtime |
| `onnx-gpu` | ONNX and GPU ONNX Runtime |
| `tools` | Open3D and tqdm for standalone tools |
| `test` | pytest |

```bash
/usr/bin/python3 -m pip install -e '.[yolo]'
```

Choose one ONNX Runtime extra for the target device. ONNX models run through Ultralytics in the `yolo_gate` node.

`bash setup.bash [base|yolo|cpu|gpu|arm|onnx-cpu|onnx-gpu]` installs from the checkout using `python` on `PATH`, which must be Python 3.8. `base` is the default; `yolo`, `cpu` and `gpu` install the YOLO extra. The node's `device` setting selects the inference device.

For Jetson YOLO installations, `bash setup.bash arm` reinstalls device-compatible `torch` and `torchvision` wheels from `~/wheels`, then installs the YOLO extra with those versions constrained. Both wheels must exist before uninstalling packages. The script selects its preferred filenames or a single wheel per package. Set `DCD_WHEEL_DIR`, `DCD_TORCH_WHEEL` and `DCD_TORCHVISION_WHEEL` to choose specific compatible builds. Match Python, JetPack and CUDA to the device.

## Export a demo

Wheel and source distributions bundle demo YAML and vehicle calibration JSON. Export them to a working directory:

```bash
data_collect_dag --init-demo ~/dcd-demo --car-id sa4
```

Car IDs are case-insensitive. Omitting `--car-id` exports all available calibrations and selects SA4 in the YAML. Unknown IDs fail before writing and list the available cars. The export includes all bundled YAML files, the selected `calibration/<car-id>/cali.json` files and a `models/` directory for your weights. Calibration paths and `demo/` model references in exported YAML are absolute; `output.root_dir` and other settings retain their configured values.

Existing template filenames require `--force`:

```bash
data_collect_dag --init-demo ~/dcd-demo --car-id sa14 --force
```

`--force` replaces exported template files and preserves unrelated files, other car directories and model weights. Directory symlinks escaping the destination are rejected. Editable installations export the checkout's demo files; ordinary installations export packaged templates. Review topics, calibration and storage settings before collecting. If you move the export directory, update its absolute references or export again.

## Start and control

Start `roscore` in a separate ROS-enabled terminal. Source ROS and any custom message workspace in both the startup and control terminals. From the repository root:

```bash
# Start with the default init pipeline:
data_collect_dag --config demo/cfg.yaml
# Or start a collection pipeline in the background:
data_collect_dag --config demo/cfg.yaml --pipeline fps_undist --background
tail -f ~/DATA_COLLECT/background.log
```

The CLI supports these forms, also available through `/usr/bin/python3 -m data_collect_dag`:

```text
data_collect_dag --config CONFIG [--pipeline PIPELINE] [--background]
data_collect_dag start PIPELINE [--control-topic TOPIC] [--timeout SEC]
data_collect_dag {pause,resume,stop,shutdown} [--control-topic TOPIC] [--timeout SEC]
data_collect_dag status [--status-topic TOPIC] [--timeout SEC]
data_collect_dag --config CONFIG --clean DAY
data_collect_dag --init-demo DIR [--car-id CAR_ID] [--force]
```

Startup and cleanup require `--config`; control and export commands use no collection configuration. `--pipeline` defaults to `init` at startup and is ignored during cleanup/export. Control commands cannot be combined with startup, cleanup or export options. `--force` and `--car-id` apply only to export; export cannot be combined with `--config`, `--clean` or `--background`.

On one Linux device, each fully resolved `ros.node_name` permits one instance, including while `IDLE`. Relative names use `ROS_NAMESPACE`; absolute names specify their namespace. Foreground and background startup acquire the same local lock before initializing ROS or loading calibration. Different node names can run together; give them distinct control/status topics to control them separately.

Background startup passes the lock descriptor to the detached child, prints its PID and log path, and returns. The child inherits the Python environment, ROS environment and working directory. stdout/stderr append to `<output.root_dir>/background.log`. The PID confirms process creation; use the log and status to check initialization and collection. The program releases its lock on exit, including abnormal termination. Lock implementation details are in [runtime architecture](docs/architecture.md#1-运行路径).

Control a running instance from another terminal:

```bash
data_collect_dag start fps
data_collect_dag pause
data_collect_dag resume
data_collect_dag stop
data_collect_dag status
data_collect_dag shutdown
```

`start` checks the pipeline name before replacing the current session. An unknown name preserves the active session, including its paused state, and records `last_error`. `stop` ends the session and leaves the program available for another `start`. `shutdown` ends the session, writes its summary and exits. Foreground Ctrl+C and `kill -TERM PID` also request cleanup and exit.

The anonymous control client uses `/data_collect/control` and `/data_collect/status` by default. Match custom YAML topics with CLI overrides:

```bash
data_collect_dag start fps --control-topic /vehicle/control --timeout 5
data_collect_dag status --status-topic /vehicle/status --timeout 5
```

The client loads no collection YAML, calibration, DAG or inference model. `--timeout` is a finite positive number, defaulting to five seconds for the Master check and the topic connection/message wait. Missing Master, missing control subscriber, missing status publisher and detected send failures return nonzero. A sent command is asynchronous; query `status` to inspect State, Pipeline, Session, Saved, Skipped, Failed, Disk Free and Last Error. See [ROS control interface](docs/ros_external_system_architecture.md) for the message contract.

### Demo pipelines

| Pipeline in `demo/cfg.yaml` | Behavior | Inference required |
| --- | --- | --- |
| `init` | Topic precheck and synchronization; two-second active-duration limit, no save node | No |
| `fps` | 1 FPS collection with image undistortion and point-cloud motion compensation | No |
| `fps_undist` | FPS collection with motion compensation and five original camera images | No |
| `fps_undist_nomotion` | FPS collection with static lidar transforms and five original camera images | No |
| `fps_with_yolo` | YOLO-filtered FPS collection with undistortion | YOLO |
| `compare_motion` | Motion-compensated and uncompensated point clouds in separate datasets | No |

Collection pipelines have a 6,000-second active-duration limit. Completed or failed sessions leave the program available for control. Startup resolves message types for every configured `ros.topics` entry. Demo pipelines precheck configured topics for new messages within 500 ms on each session start. `fps_undist_nomotion` checks only the five cameras and five lidars; it needs no incoming localization, IMU, odometry or NMEA messages. It transforms each lidar into `base` using static calibration and merges the clouds without ego-motion compensation.

```bash
data_collect_dag --config demo/cfg.yaml --pipeline fps_undist_nomotion
# Or control an instance started with this pipeline in its configuration:
data_collect_dag start fps_undist_nomotion
```

Pipeline definitions are loaded at program startup. Configuration edits require restarting the collection program; exported configurations must be updated or re-exported separately.

The demo selects `demo/calibration/sa4/cali.json`. `fps_with_yolo` requires the YOLO extra and `demo/models/yolo26m.pt`; supply your compatible weights in the checkout or export's `models/` directory. Its configured `device: gpu` requires CUDA; select `cpu` when using CPU inference.

Relative `output.root_dir`, `calibration.path` and YOLO `model_path` values use the process working directory. The convenience command `bash run.sh /absolute/path/config.yaml [pipeline_name]` changes to the repository root and uses `.venv/bin/python3` when `.venv` exists, otherwise `python3` on `PATH`.

## Storage and cleanup

`output.disk_storage_limit_GB` limits ordinary file sizes under `output.root_dir`; `output.disk_storage_need_GB` sets the minimum free space required on each session start. Both use decimal GB (1,000,000,000 bytes), accept finite non-negative numbers, and are disabled by omission, `null` or `0`. The demo sets both to `100`.

At startup, output size at or above the limit or free space below the requirement rejects the session. During collection, writes update cached sizes, logs are checked every second and directory scans reconcile external changes every 30 seconds. Reaching the limit stops new samples, finishes in-flight work and ends the session as `FAILED`. In-flight writes and final metadata can exceed the limit. Free space is checked at session admission. After freeing space, use `start` to retry.

Clean session directories while collection is stopped:

```bash
data_collect_dag --config demo/cfg.yaml --clean 2
```

`--clean` exits after cleanup and needs only the output configuration. `0` removes all valid session directories, `1` keeps today, and `2` keeps today and yesterday. The date comes from the local-time directory name `YYYYMMDD_HHMMSS_<eight lowercase hex digits>`. For positive values, future dates are preserved. Unrelated files/directories and symbolic links are preserved; failures return nonzero.

## Calibration and results

Calibration files use `demo/calibration/<car-id>/cali.json`. Camera entries require `dist_model: equidistant` and four finite numeric `dist_coeffs` in OpenCV fisheye order `[k1, k2, k3, k4]`. Generate a calibration from an external vehicle source directory:

```bash
/usr/bin/python3 tools/cali_parser.py /path/to/argus/SA14 -o demo/calibration/sa14/cali.json
```

The tool creates output parent directories. Omitting `-o` writes `cali.json` inside the input vehicle directory.

Session results under `<output.root_dir>/<session_id>/` include `debug.log`, `session_summary.json` and `saved_samples.json`. A configured Xtreme1 save node writes JPEG images, ASCII PCD and camera JSON under `xtreme1/<dataset_name>/`. `fps_undist` and `fps_undist_nomotion` save under `collect_demo` and include source distortion parameters in each camera entry; intrinsics scale to the saved image resolution. See [configuration and output formats](docs/architecture.md#4-数据与文件).

## Tests

Run with system Python 3.8 from the repository root:

```bash
/usr/bin/python3 -m pip install -e '.[test]'
PYTHONPATH=src /usr/bin/python3 -m pytest tests/unit -q
PYTHONPATH=src /usr/bin/python3 -m pytest tests/functional -q
```

The default tests simulate ROS and use real Linux processes for locking and background inheritance. Opt-in integration tests start isolated local ROS1 masters and exercise the CLI over TCPROS:

```bash
source /opt/ros/noetic/setup.bash
PYTHONPATH=src DCD_TEST_REAL_ROS=1 /usr/bin/python3 -m pytest tests/functional/test_ros_cli_integration.py -q
```

These integration tests use standard ROS messages and temporary configurations. Base-install tests check FPS DAG construction without inference packages; packaging tests check template export from wheels, source distributions and editable installs. Development rules are in [AGENTS.md](AGENTS.md).
