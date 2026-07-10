#!/bin/bash

set -e

MODE=$1

if [ -z "$MODE" ]; then
    echo "Usage: bash run.sh [gpu|cpu|arm]"
    exit 1
fi

echo "Selected mode: $MODE"

# 基础依赖
pip install -U pip setuptools wheel
pip install rospkg


case "$MODE" in

    gpu)
        echo "Installing for x86 GPU device..."
        pip install "ultralytics[export]"
        ;;

    cpu)
        echo "Installing for x86 CPU device..."
        pip install ultralytics
        ;;

    arm)
        echo "Installing for ARM Jetson device..."

        pip uninstall torch torchvision -y

        pip install https://github.com/ultralytics/assets/releases/download/v0.0.0/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl

        pip install https://github.com/ultralytics/assets/releases/download/v0.0.0/torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl
        ;;

    *)
        echo "Unknown mode: $MODE"
        echo "Usage: bash run.sh [gpu|cpu|arm]"
        exit 1
        ;;

esac


echo "Installing current project..."
pip install -e .

echo "Done."