#!/bin/bash

set -e

NODES=(
"/plan3a_stream_node"
"/miivii_gmsl_camera_node"
)

rosnode list >/dev/null 2>&1 || {
    echo "[ERROR] ROS master unavailable"
    exit 1
}

for node in "${NODES[@]}"; do
    if rosnode list | grep -qx "$node"; then
        echo "[INFO] stopping $node"
        rosnode kill "$node"

        for i in {1..50}; do
            rosnode list 2>/dev/null | grep -qx "$node" || break
            sleep 0.2
        done

        echo "[INFO] $node stopped"
    else
        echo "[INFO] $node not running"
    fi
done

echo "[INFO] launching data_collect.launch"

roslaunch miivii_gmsl_camera data_collect.launch \
    > /tmp/data_collect.launch.log 2>&1 &

echo "[INFO] launch pid: $!"