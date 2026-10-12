#!/bin/bash
set -e

roots=(
"/mnt/c/Users/Xiufeng Zhou/Desktop/workspace/20260923_pici2/part01"
"/mnt/c/Users/Xiufeng Zhou/Desktop/workspace/20260923_pici2/part02"
"/mnt/c/Users/Xiufeng Zhou/Desktop/workspace/20260923_pici2/part03"
"/mnt/c/Users/Xiufeng Zhou/Desktop/workspace/20260923_pici2/part04"
)

output_root="/mnt/c/Users/Xiufeng Zhou/Desktop/workspace/20260923_pici2"

len_roots=${#roots[@]}
for index_id in {0..$len_roots-1}; do
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