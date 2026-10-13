# DataCollectDAG

DataCollectDAG is a Python 3.8 / ROS1 Noetic application for collecting sensor data with YAML-defined directed acyclic graphs (DAGs). It subscribes to configured ROS topics, synchronizes frames, runs processing nodes, and writes session results to disk.

## Requirements

- Ubuntu with ROS1 Noetic, a running ROS master, and Python 3.8 (`>=3.8,<3.9`).
- ROS message packages referenced by the YAML, including any custom packages such as `bdstar` and `igv_msgs`. Build them in a catkin workspace and source its setup script.
- The calibration JSON and live ROS topics required by the selected pipeline. A ROS bag may be used for playback but is **not included** in this repository.

The base Python package installs NumPy, PyYAML, Pillow, OpenCV and rospkg. It does **not** depend on Ultralytics, PyTorch, Torchvision, ONNX or ONNX Runtime. Inference libraries are imported only when an inference node is initialized.

## Install

From a repository checkout:

```bash
source /opt/ros/noetic/setup.bash
# If your YAML uses custom ROS messages:
source /path/to/catkin_ws/devel/setup.bash

python3.8 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e .
data_collect_dag --help
```

Alternatively, install the base package directly from GitHub:

```bash
python3.8 -m pip install 'git+https://github.com/permanentchange/DataCollectDAG.git'
data_collect_dag --init-demo ~/dcd-demo --car-id sa4
data_collect_dag --config ~/dcd-demo/cfg.yaml --pipeline fps_undist
```

Wheel and source distributions include demo YAML and calibration JSON templates. `--init-demo DIR` exports them offline without starting ROS, and creates a `models/` directory for your own weights. Use `--car-id sa4` to export only that car's calibration; car IDs are case-insensitive. Omit `--car-id` to export all available cars with SA4 selected in the configuration. Unknown IDs fail before writing and list the available cars. All demo YAML files are exported; `--pipeline` does not filter them. Model weights, ROS bags and collection results are not bundled. Review the example calibration, topics and storage settings before collecting on your device.

Calibrations are organized as `calibration/<car-id>/cali.json`, without a top-level default file or symlink. Exported calibration and model references use absolute paths under `DIR`, so you can run from any working directory. For example:

```bash
data_collect_dag --init-demo ~/dcd-sa14 --car-id sa14
# cfg.yaml now points to ~/dcd-sa14/calibration/sa14/cali.json as an absolute path
data_collect_dag --config ~/dcd-sa14/cfg.yaml --pipeline fps_undist
```

Other settings, including `output.root_dir`, stay unchanged. If you move the exported directory, update these paths or export again.

By default, any existing template filename aborts the export before files are written. After upgrading the package, export to a new directory to compare configurations, or explicitly replace the templates:

```bash
data_collect_dag --init-demo ~/dcd-demo --force
```

`--force` replaces only exported template filenames (including any edits you made to them); unrelated files and existing model weights are preserved. Combine it with `--car-id` to switch the configuration's car without replacing or removing other cars' calibration files. Export refuses directory symlinks that lead outside the destination. Package upgrades never change previously exported configurations. Editable installations export the checkout's current demo files; ordinary installations export templates shipped with that package version.

The old `calibration/saN_cali.json` and `calibration/cali.json` paths have been replaced by `calibration/saN/cali.json`. Update external YAML references or export into a new directory. Re-exporting with `--force` updates the configuration but does not delete old-layout files from an existing directory.

### Optional inference and tools

In a checkout, install only the extras you need:

| Extra | Purpose |
| --- | --- |
| `yolo` | Ultralytics inference, including its transitive PyTorch requirements |
| `yolo-export` | Ultralytics export dependencies |
| `onnx-cpu` | ONNX and CPU ONNX Runtime |
| `onnx-gpu` | ONNX and GPU ONNX Runtime |
| `tools` | Open3D and tqdm for standalone scripts in `tools/` |
| `test` | pytest |

```bash
python -m pip install -e '.[yolo]'
# Or, for a remote installation:
python -m pip install 'data-collect-dag[yolo] @ git+https://github.com/permanentchange/DataCollectDAG.git'
```

`onnx-cpu` and `onnx-gpu` are alternative environments; do not install both ONNX Runtime distributions together. The project does **not** implement an independent ONNX DAG node: YOLO inference, including an ONNX model loaded through Ultralytics, uses the `yolo_gate` node.

`setup.bash` installs from a checkout. Its modes are `base` (default), `yolo`, `cpu`, `gpu`, `arm`, `onnx-cpu` and `onnx-gpu`. The `cpu` and `gpu` modes install the same Ultralytics distribution; the model's runtime `device` setting selects the device.

**Jetson/ARM:** `bash setup.bash arm` explicitly reinstalls `torch` and `torchvision` from `~/wheels` before installing the YOLO extra. Both wheel files must exist before any uninstall; afterward the Ultralytics resolver is constrained to those installed versions. The script recognizes one local wheel per package or its built-in preferred Jetson filenames. Use `DCD_WHEEL_DIR`, `DCD_TORCH_WHEEL` and `DCD_TORCHVISION_WHEEL` to select a different compatible pair. Confirm the wheel versions match the device's JetPack, CUDA and Python environment. For non-inference workflows use `bash setup.bash base`.

## Run a pipeline

Start `roscore` in a separate terminal; source ROS and your Python environment in the terminal running the CLI. From the repository root:

```bash
# No YOLO/PyTorch required
data_collect_dag --config demo/cfg.yaml --pipeline fps

# Original images, with dist_model and dist_coeffs in each camera JSON
data_collect_dag --config demo/cfg.yaml --pipeline fps_undist

# YOLO and model weights required
data_collect_dag --config demo/cfg.yaml --pipeline fps_with_yolo
```

The CLI also supports `python -m data_collect_dag --config ... --pipeline ...`. Choose either `--config` to run/clean or `--init-demo DIR [--car-id CAR_ID]` to export; `--pipeline` defaults to `init`. Export cannot be combined with `--config`, `--clean` or `--background`; `--force` and `--car-id` are only valid for export.

`data_collect_dag --help` lists collection, cleanup and export as separate command forms. Collection (including `--background`) and cleanup require a YAML file via `--config`; export does not. For example, `data_collect_dag --clean 1` reports `--clean requires --config CONFIG to determine output.root_dir`. No configuration or cleanup directory is selected automatically.

To run in the background and keep running after closing the terminal:

```bash
data_collect_dag --config demo/cfg.yaml --background
tail -f ~/DATA_COLLECT/background.log
```

`--background` immediately prints the process PID and log path, then returns. It inherits the current Python environment, ROS environment and working directory. stdout/stderr append to `<output.root_dir>/background.log`, which counts toward the storage limit. The PID confirms process creation; check the log and status for successful collection. Background mode and `--clean` are mutually exclusive. There is no automatic restart or startup on boot.

To stop the entire program, in either foreground or background mode:

```bash
rostopic pub -1 /data_collect/control std_msgs/String "data: '{\"command\":\"shutdown\"}'"
# Alternatively, using the PID printed by --background:
kill -TERM PID
```

Both perform session cleanup and save the summary before exiting. Foreground Ctrl+C also remains supported. The ROS `stop` command ends the collection session and keeps the program available for another `start`.

To clean expired session directories while collection is stopped:

```bash
data_collect_dag --config demo/cfg.yaml --clean 2
```

`--clean` only cleans and exits; it needs no ROS master, calibration or pipeline. `0` deletes all session directories, `1` keeps today, and `2` keeps today and yesterday. Dates come from session directory names in local time, not modification times. Other files/directories and symbolic links are preserved; future session dates are preserved when the value is greater than zero. An accompanying `--pipeline` is ignored.

Under `output`, `disk_storage_limit_GB` limits the total ordinary file size in `root_dir`; `disk_storage_need_GB` requires minimum free space on its filesystem at each task start. Both use decimal GB (1,000,000,000 bytes); omit them or use `null`/`0` to disable. Values must be finite non-negative numbers. The demo sets both to `100`.

Before creating a session, collection is refused when output size is at least the limit or free space is below the requirement. During collection, file writes update cached sizes and each frame checks the limit; logs are refreshed every second and a background directory scan every 30 seconds detects external changes. At the limit, new frames stop and in-flight frames finish, so output may exceed the limit by those frames and final metadata/logs. The session ends as `FAILED` with the reason in status and its summary; ROS control remains available. Free space is only an admission check, not a continuous requirement. After freeing space, send a new start command to retry.

Available sample pipelines:

| Configuration | Pipeline | Inference required |
| --- | --- | --- |
| `demo/cfg.yaml` | `init`, `fps`, `fps_undist`, `compare_motion` | No |
| `demo/cfg.yaml` | `fps_with_yolo` | YOLO |

The checkout's demo YAML references `demo/calibration/sa4/cali.json`. Its YOLO pipeline references `demo/models/yolo26m.pt`; supply a compatible model checkpoint yourself (or put it in `<export-dir>/models/` for an exported configuration):

```bash
mkdir -p demo/models
# Place your compatible checkpoint at demo/models/yolo26m.pt
```

Some sample configurations explicitly request CUDA, so a CPU-only environment must use a non-inference pipeline or change the YOLO node's `device` setting.

Relative values in `output.root_dir`, `calibration.path` and YOLO `model_path` are resolved against the **current working directory**, not the YAML's directory. Use absolute paths when launching from elsewhere; `--init-demo` already does this for the demo's calibration/model references. The convenience script `bash run.sh /absolute/path/config.yaml [pipeline_name]` changes its working directory to the repository root.

`fps_undist` saves original images without undistortion, encoded as JPEG; its FPS control, synchronization and lidar processing match `fps`. Each camera entry in `camera_config/<timestamp>.json` includes the source `dist_model` and `dist_coeffs`, with intrinsics scaled to the saved image resolution. Existing pipelines keep their camera JSON format.

Camera calibration entries now require `"dist_model": "equidistant"` and four finite numeric `dist_coeffs` in OpenCV fisheye order `[k1, k2, k3, k4]`. This is a breaking calibration-format change: replace `"is_fisheye": true` with the new field in external calibration files. Legacy `is_fisheye`, missing fields and other models are rejected. The repository's calibration files and conversion tool use the new field; only the existing equidistant algorithm is supported.

## ROS control and results

The demo configurations use `/data_collect/control` and `/data_collect/status`, both with `std_msgs/String` messages containing JSON. The control commands are `start`, `pause`, `resume`, `stop` and `shutdown`. Optional `std_srvs/Trigger` services can also be configured. See [ROS control interface](docs/ros_external_system_architecture.md).

Results are written under `<output.root_dir>/<session_id>/`:

- `debug.log` — session log.
- `session_summary.json` — end state, timings/metrics and errors.
- `saved_samples.json` — index of saved samples.
- `xtreme1/<dataset_name>/` — JPEG images, ASCII PCD and camera JSON, if an Xtreme1 save node runs.

See [architecture and configuration](docs/architecture.md) for node types, execution rules and YAML fields.

## Tests

```bash
python -m pip install -e '.[test]'
python -m pytest tests/unit -q
python -m pytest tests/functional -q
```

The Python tests mock ROS interactions where needed; actual acquisition still requires a live ROS environment. The repository's base-install CI also verifies that FPS DAG construction does not import inference packages.
