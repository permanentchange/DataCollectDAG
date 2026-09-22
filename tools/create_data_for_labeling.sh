#!/bin/bash
set -e

roots=(
"/home/xfzhou/workspace/DataCollectDAG/data_collect_0821/part01"
"/home/xfzhou/workspace/DataCollectDAG/data_collect_0821/part02"
"/home/xfzhou/workspace/DataCollectDAG/data_collect_0821/part03"
"/home/xfzhou/workspace/DataCollectDAG/data_collect_0821/part04"
"/home/xfzhou/workspace/DataCollectDAG/data_collect_0821/part05"
)

output_root="/mnt/c/Users/Xiufeng Zhou/Desktop/data_collect_0821"

for index_id in {0..4}; do
    root="${roots[$index_id]}"
    output_zip="${output_root}_part${index_id}.zip"

    echo
    echo "========== part $index_id =========="
    echo "src: $root"
    echo "dst: $output_zip"

    (
        cd "$root"
        7z a -tzip -mx=1 -bsp1 -bb1 "$output_zip" .
    )

    echo "========== done =========="
done