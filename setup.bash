#!/bin/bash

set -e
PWD=$(pwd -P)
MODE=$1

if [ -z "$MODE" ]; then
    echo "Usage: bash run.sh [gpu|cpu|arm]"
    exit 1
fi

echo "Selected mode: $MODE"

pip install -U pip setuptools wheel

case "$MODE" in
    gpu)
        echo "Installing for x86 GPU device..."
        pip install -e ".[yolo-export]"
        ;;

    cpu)
        echo "Installing for x86 CPU device..."
        pip install -e ".[yolo]"
        ;;

    arm)
        echo "Installing for ARM Jetson device..."
        pip install "ultralytics[export]"
        pip uninstall torch torchvision -y

        if [ -f wheels/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl ]; then
            pip install wheels/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl
        else
            pip install https://github.com/ultralytics/assets/releases/download/v0.0.0/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl
        fi

        if [ -f wheels/torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl ]; then
            pip install wheels/torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl
        else
            pip install https://github.com/ultralytics/assets/releases/download/v0.0.0/torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl
        fi
        ;;

    *)
        echo "Unknown mode: $MODE"
        echo "Usage: bash run.sh [gpu|cpu|arm]"
        exit 1
        ;;
esac

echo "Installing current project..."
echo "Package installed with the selected optional dependencies."
echo "Downloading models..."
mkdir -p demo/models && cd demo/models && python -c "from ultralytics import YOLO; m=YOLO('yolo26m.pt'); m=YOLO('yolo26l.pt')" && cd $PWD
echo "Done."