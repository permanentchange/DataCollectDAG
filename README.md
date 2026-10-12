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

The **base installation does not install** `ultralytics`, `torch`,
`torchvision`, `onnx`, or `onnxruntime`. Pip installs only the core
dependencies used by regular DAG pipelines, such as `fps` in
`demo/cfg.yaml`. ROS1 Noetic and message packages must be provided
by the host environment.

Use Python 3.8. For example, using `venv`:

```bash
python3.8 -m venv --system-site-packages .venv
source .venv/bin/activate
source /opt/ros/noetic/setup.bash
python -m pip install -e .
data_collect_dag --help
```

Alternatively use `conda create -n dcd python=3.8 -y` and `conda activate dcd`
before installation. Ensure ROS Python packages can be imported in that
environment.

Install the base package directly from GitHub without cloning:

```bash
python -m pip install 'git+https://github.com/permanentchange/DataCollectDAG.git'
```

### Run without inference dependencies (FPS)

From a repository checkout, with `roscore` running in another shell and
the correct ROS workspace sourced:

```bash
data_collect_dag --config demo/cfg.yaml --pipeline fps
```

This pipeline does not use YOLO/ONNX inference; it **does** require ROS
topics, custom ROS messages (for example `igv_msgs` / `bdstar`),
calibration and ordinary image/point-cloud dependencies. For remote pip
installs, supply your own local YAML and calibration files; `demo/` is
not included in the installed Python distribution.

### Opt-in inference dependencies

Install only what the selected pipeline needs:

```bash
# YOLO with Ultralytics (Ultralytics resolves its own dependencies)
python -m pip install -e '.[yolo]'

# Optional ONNX Runtime on a CPU system (only if needed)
python -m pip install -e '.[onnx-cpu]'

# Optional ONNX Runtime on a compatible CUDA system (only if needed)
python -m pip install -e '.[onnx-gpu]'

# Test dependencies, without inference backends
python -m pip install -e '.[test]'
```

For remote YOLO installation:

```bash
python -m pip install 'data-collect-dag[yolo] @ git+https://github.com/permanentchange/DataCollectDAG.git'
```

`onnx-cpu` and `onnx-gpu` each include `onnx` and one ONNX Runtime
distribution, but **must not be installed together**: the `onnxruntime`
and `onnxruntime-gpu` distributions conflict. The optional `yolo-export`
extra installs further Ultralytics export dependencies. If you run an
ONNX YOLO model *through Ultralytics*, you still need `[yolo]` in
addition to an ONNX Runtime backend. Pure FPS workflows need neither.

There are **not separate CPU/CUDA Ultralytics pip packages**. The PyTorch
build and the YOLO node's `device` setting determine the device.
For non-TensorRT models, set `device: auto` in a YOLO node to prefer CUDA
and fall back to CPU if CUDA is unavailable or inference fails; `device: cpu` forces CPU. An explicitly configured CUDA device still fails fast
if CUDA is unavailable. TensorRT `.engine` models cannot be moved to
CPU automatically.

**Jetson / ARM:** the `arm` installer intentionally **uninstalls and
reinstalls** PyTorch/Torchvision using vendor-specific wheels supplied
in `~/wheels`. Do not use the generic `[yolo]` pip install on Jetson
unless you have separately handled its PyTorch requirements. Place
the compatible wheel pair in `~/wheels`, then run from a repository
checkout:

```bash
# Example supported wheel names for one known Jetson / Python 3.8 build:
# ~/wheels/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl
# ~/wheels/torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl

bash setup.bash arm
```

The script **checks that both files exist before uninstalling** either
package, prefers the two known filenames when present, or accepts a
single `torch-*.whl` and `torchvision-*.whl` from the directory.
When you maintain multiple JetPack/Python builds, select the desired
files explicitly:

```bash
DCD_WHEEL_DIR="$HOME/wheels" \
DCD_TORCH_WHEEL="$HOME/wheels/torch-YOUR_VERSION.whl" \
DCD_TORCHVISION_WHEEL="$HOME/wheels/torchvision-YOUR_VERSION.whl" \
bash setup.bash arm
```

PyTorch/Torchvision are installed from the supplied wheel files with
their dependencies temporarily disabled; the script then installs
`[yolo]` while constraining pip to the **just-installed wheel versions**.
If an Ultralytics dependency conflicts, installation fails rather than
silently replacing Jetson PyTorch with an incompatible build. Verify
that your wheel pair is compatible with the device's CUDA/JetPack
and Python version.

For a non-YOLO ARM pipeline, run `bash setup.bash base`; no PyTorch
is installed, uninstalled, or imported by the FPS DAG. The optional
`onnx-cpu` and `onnx-gpu` modes also look in `~/wheels` when it exists.
GPU ONNX Runtime wheels depend on the CUDA/JetPack version and may need
vendor-provided builds. Uninstall `onnxruntime-gpu` before switching
to `onnxruntime`; they should not coexist.

`setup.bash` modes are `base` (the default), `yolo`, `cpu`, `gpu`,
`arm`, `onnx-cpu`, and `onnx-gpu`. The `cpu` and `gpu` installer
aliases both install the same Ultralytics package; the inference device
is chosen at runtime by YAML configuration.

### Verify

Base package (no inference imports required):

```bash
python -c "import data_collect_dag; from data_collect_dag.cli import main; print(data_collect_dag.__version__)"
data_collect_dag --help
```

If you installed `[yolo]`, you can additionally check it:

```bash
python -c "from ultralytics import YOLO; print('YOLO ready')"
```

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

This is expected on base-only installations if you start a YOLO pipeline.
Install the optional `[yolo]` extra in the active environment:

```bash
python -m pip install -e '.[yolo]'
```

On Jetson, use `bash setup.bash arm` with compatible wheels in
`~/wheels`; the script reinstalls those specific builds rather than
fetching generic PyTorch from an online package index.

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
