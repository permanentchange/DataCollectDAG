# DataCollectDAG

`DataCollectDAG` is a ROS1-based data collection runtime built around configurable DAG pipelines.
It subscribes to sensor topics, buffers and synchronizes incoming data, runs processing nodes, and saves accepted samples as structured output.

The repository includes a runnable demo:

- config: `demo/xtreme1_demo.yaml`
- calibration: `demo/calibration/sa1_cali.json`
- test rosbag: `test_data.bag`

## Highlights

- YAML-driven pipeline configuration
- ROS1 topic subscription and control interface
- time synchronization for multi-sensor data
- point cloud motion compensation and transform nodes
- YOLO-based image gating
- structured dataset export

## Repository Layout

```text
src/data_collect_dag/       package source
src/data_collect_dag/nodes/ reusable DAG nodes
tests/unit/                 unit tests
tests/functional/           functional tests
demo/                       demo config, calibration, model setup notes
docs/                       design notes
```

## Requirements

- Python `3.8`
- ROS1 Noetic
- One Python environment manager:
  - `conda`, or
  - the standard library `venv` module
- ROS message definitions required by your config

The demo config uses custom message types such as `igv_msgs/...` and `bdstar/...`.
Make sure those message packages are available in your ROS/Python environment before running the demo.

## Installation

Choose one of the following environment setups.

### Option 1: Install With `conda`

Create and activate the environment:

```bash
conda create -n dcd python=3.8 -y
conda activate dcd
```

Install the project:

```bash
python -m pip install -e '.[yolo]'
```

### Option 2: Install With `python -m venv`

Make sure `python3.8` is available on your machine, then create and activate a virtual environment:

```bash
python3.8 -m venv .venv
source .venv/bin/activate
```

Upgrade packaging tools, then install the project:

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[yolo]'
```

If you are on Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

### Verify The Installation

Check that the package and YOLO dependency import correctly:

```bash
python -c "import data_collect_dag; import ultralytics; print(ultralytics.__version__)"
```

If `ultralytics` is still missing after the editable install, install it explicitly:

```bash
python -m pip install '.[yolo]'
```

## Install directly from GitHub

On Ubuntu 20.04 with ROS Noetic and Python 3.8, after activating the
intended Python environment and sourcing your ROS workspace:

```bash
python -m pip install 'data-collect-dag[yolo] @ git+https://github.com/permanentchange/DataCollectDAG.git'
data_collect_dag --help
```

For the Python runtime without YOLO, omit `[yolo]`. For development and
tests use `python -m pip install -e '.[test,yolo]'`. Optional groups:

- `yolo`: Ultralytics-based inference (PyTorch is pulled in by its requirements).
- `yolo-export`: additional Ultralytics model export dependencies.
- `tools`: point-cloud utility Open3D and YOLO filtering utility tqdm;
  these scripts remain in the repository and are not installed as CLI entry points.
- `test`: pytest.

The project requires **Python 3.8**. ROS1 Noetic (`rospy`, `roslib`,
`genpy`, ROS message packages including custom `igv_msgs`/`bdstar`)
must still be installed or built separately, and the corresponding setup
scripts must be sourced. Pip cannot install or configure a ROS master.
For Jetson/ARM, install the platform-compatible PyTorch and torchvision
builds before installing the YOLO extra; generic pip dependency
resolution can replace hardware-specific builds. Consult `setup.bash`
for the platform-specific path.

The example YAML, calibration JSON, ROS bag and YOLO model weights are
not included in the installed Python distribution. Keep them separately,
and use absolute paths in your configuration to avoid depending on
the current working directory.

## Demo Setup

The demo pipeline expects a YOLO weight file at:

```text
demo/models/yolo_person_model.pt
```

Setup instructions for that file are in:

- `demo/models/README.md`

## Run The Demo

Open three shells from the repository root.

Shell 1:

```bash
source /opt/ros/noetic/setup.bash
conda activate dcd  # or: source .venv/bin/activate
roscore
```

Shell 2:

```bash
source /opt/ros/noetic/setup.bash
conda activate dcd  # or: source .venv/bin/activate
data_collect_dag --config demo/xtreme1_demo.yaml --pipeline xtreme1_collect
```

Shell 3:

```bash
source /opt/ros/noetic/setup.bash
conda activate dcd  # or: source .venv/bin/activate
rosbag play test_data.bag
```

After playback finishes, stop the runtime with `Ctrl+C`.

## Demo Pipeline Summary

The `xtreme1_collect` demo pipeline:

- uses `front_wide_camera` as the main trigger source
- runs a YOLO `person` gate on the main image
- synchronizes roof lidar, corner lidars, and fisheye cameras
- motion-compensates point clouds directly into the base frame
- aggregates the lidar outputs
- saves images, point cloud, and camera config in an Xtreme1-style layout

## Output

Output is written under:

```text
output/<session_id>/
```

Useful files:

- `output/<session_id>/session_summary.json`
- `output/<session_id>/saved_samples.json`
- `output/<session_id>/debug.log`
- `output/<session_id>/xtreme1/collect_demo/...`

## Tests

```bash
conda activate dcd
python -m pytest
python -m pytest tests/unit -q
python -m pytest tests/functional -q
```

If you are using `venv`, activate it first instead:

```bash
source .venv/bin/activate
python -m pytest
python -m pytest tests/unit -q
python -m pytest tests/functional -q
```

## Common Issues

### `missing yolo model_path`

Place a valid `.pt` file at `demo/models/yolo_person_model.pt`, or update `model_path` in `demo/xtreme1_demo.yaml`.

### `ModuleNotFoundError: ultralytics`

Reinstall the package in the active environment:

```bash
python -m pip install -e '.[yolo]'
```

If needed, install the dependency directly:

```bash
python -m pip install "ultralytics>=8.4,<9"
```

If you are using `venv`, confirm the environment is active before reinstalling:

```bash
source .venv/bin/activate
python -m pip install -e '.[yolo]'
```

### ROS message resolution errors

The required custom ROS message packages are not available in the current environment, or their local paths need to be adjusted for your machine.

### No samples saved

Typical causes:

- the YOLO gate rejected all frames
- required topics were missing during playback
- synchronization thresholds were too strict

Check `output/<session_id>/debug.log` and `session_summary.json` first.

## Related Files

- `demo/xtreme1_demo.yaml`
- `demo/models/README.md`
- `docs/requirements.md`
