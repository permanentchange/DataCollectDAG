#!/bin/bash

# 获取 run.sh 所在目录
SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"

# 参数
config_path="$1"
pipeline_name="$2"

# config_path 必填
if [ -z "$config_path" ]; then
    echo "Usage: bash run.sh <config_path> [pipeline_name]"
    exit 1
fi

# pipeline_name 可选
if [ -n "$pipeline_name" ]; then
    python -m data_collect_dag --config "$config_path" --pipeline "$pipeline_name"
else
    python -m data_collect_dag --config "$config_path"
fi