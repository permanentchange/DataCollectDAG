#!/usr/bin/env bash

set -euo pipefail

# Usage: bash setup.bash [base|yolo|cpu|gpu|arm|onnx-cpu|onnx-gpu]
# Base installs no Ultralytics, PyTorch, Torchvision, ONNX, or ONNX Runtime.
MODE="${1:-base}"

case "$MODE" in
    base)
        echo "Installing the base DataCollectDAG runtime (no inference packages)..."
        python -m pip install -e .
        ;;
    yolo|cpu|gpu)
        echo "Installing DataCollectDAG with optional Ultralytics inference..."
        # Ultralytics is the same package for CUDA and CPU. The selected
        # PyTorch build and the pipeline's 'device' setting determine where
        # inference runs; 'device: auto' can fall back to CPU.
        python -m pip install -e ".[yolo]"
        ;;
    arm)
        echo "Installing YOLO on ARM, preserving the preinstalled vendor PyTorch..."
        # Do NOT replace the device-specific torch/torchvision wheels.
        # Constrain pip to their exact installed versions and fail on conflict.
        constraint_file=$(mktemp)
        trap 'rm -f "$constraint_file"' EXIT
        python - "$constraint_file" <<'PY'
from importlib import metadata
from pathlib import Path
import sys

constraints = []
for package in ("torch", "torchvision"):
    try:
        version = metadata.version(package)
    except metadata.PackageNotFoundError:
        raise SystemExit(
            "ARM YOLO requires a JetPack-compatible " + package +
            " to be installed first. Use 'base' for workflows without YOLO."
        )
    constraints.append("{}=={}".format(package, version))
Path(sys.argv[1]).write_text("\n".join(constraints) + "\n")
PY
        python -m pip install -c "$constraint_file" -e ".[yolo]"
        ;;
    onnx-cpu|onnx-gpu)
        echo "Installing optional $MODE dependencies..."
        python -m pip install -e ".[$MODE]"
        ;;
    *)
        echo "Usage: bash setup.bash [base|yolo|cpu|gpu|arm|onnx-cpu|onnx-gpu]" >&2
        exit 1
        ;;
esac

echo "Installed. Example models and configs remain separate from the package."
