#!/bin/bash

# 参数
config_path="$1"
pipeline_name="$2"

# config_path 必填
if [ -z "$config_path" ]; then
    echo "Usage: bash run.sh <config_path> [pipeline_name]"
    exit 1
fi
config_path=$(realpath "$config_path")

# 获取 run.sh 所在目录
SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"

# 如果存在.venv目录，则使用其中的Python解释器
if [ -d ".venv" ]; then
    PYTHON_BIN=.venv/bin/python3
else
    PYTHON_BIN=python3
fi

# pipeline_name 可选
if [ -n "$pipeline_name" ]; then
    $PYTHON_BIN -m data_collect_dag run launch --config "$config_path" --pipeline "$pipeline_name"
else
    $PYTHON_BIN -m data_collect_dag run launch --config "$config_path"
fi
