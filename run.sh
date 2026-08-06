#!/bin/bash

# 获取本run.sh所在目录
SCRIPT_DIR=$(dirname "$0")
cd $SCRIPT_DIR
python -m data_collect_dag --config demo/xtreme1_demo.yaml