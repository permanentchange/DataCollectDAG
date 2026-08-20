#!/bin/bash

set -e

NODES=(
"/plan3a_stream_node"
"/miivii_gmsl_camera_node"
)

if ! rosnode list >/dev/null 2>&1; then
    echo "[ERROR] ROS master unavailable"
    exit 1
fi

for node in "${NODES[@]}"; do
    if rosnode list | grep -qx "$node"; then
        echo "[INFO] stopping $node"
        rosnode kill "$node"

        while rosnode list 2>/dev/null | grep -qx "$node"; do
            sleep 0.2
        done

        echo "[INFO] $node stopped"
    else
        echo "[INFO] $node not running"
    fi
done

sleep 2

echo "[INFO] launching data_collect.launch"
roslaunch miivii_gmsl_camera data_collect.launch