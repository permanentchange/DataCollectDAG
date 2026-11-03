#!/usr/bin/env bash
set -euo pipefail

tool_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(dirname -- "$tool_dir")"
input_root="$repo_dir/../argus"
output_root="$repo_dir/demo/calibration"
python_bin=python3
dry_run=false
default_cars=(SA1 SA3 SA4 SA5 SA6 SA7 SA8 SA9 SA10 SA11 SA12 SA13 SA14 SA15 SA16 SA20 SA21 SA22 SA23 SA24 SA25 SA26 SA27 SA28 SA29)
cars=()

usage() {
    cat <<'EOF'
Usage: bash tools/batch_cali_parser.sh [OPTIONS] [CAR_ID ...]

Convert vehicle calibration directories to <output-root>/<car-id>/cali.json.
CAR_ID is case-insensitive (e.g. sa4); source directory names use uppercase.
Without CAR_ID, process the original 25-vehicle list: SA1, SA3-SA16, SA20-SA29.
Existing cali.json files are overwritten. Other files are left in place.

Options:
  -i, --input-root DIR   Vehicle parent directory (default: <checkout>/../argus)
  -o, --output-root DIR  Output parent directory (default: <checkout>/demo/calibration)
      --python PATH     Python interpreter (default: python3)
      --dry-run         Check source directories and print commands without writing
  -h, --help            Show this help

Relative paths are resolved from the caller's working directory.
Failures do not stop the remaining vehicles; any failure returns exit code 1.
EOF
}

argument_error() {
    printf 'Error: %s\n' "$1" >&2
    exit 2
}

while (($#)); do
    case "$1" in
        -i|--input-root|-o|--output-root|--python)
            (($# >= 2)) && [[ -n "$2" && "$2" != -* ]] || argument_error "$1 requires a value"
            case "$1" in
                -i|--input-root) input_root="$2" ;;
                -o|--output-root) output_root="$2" ;;
                --python) python_bin="$2" ;;
            esac
            shift 2
            ;;
        --dry-run) dry_run=true; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; cars+=("$@"); break ;;
        -*) argument_error "unknown option: $1" ;;
        *) cars+=("$1"); shift ;;
    esac
done

if ((${#cars[@]} == 0)); then
    cars=("${default_cars[@]}")
fi
for idx in "${!cars[@]}"; do
    car="${cars[$idx]^^}"
    [[ "$car" =~ ^SA[1-9][0-9]*$ ]] || argument_error "invalid vehicle ID: ${cars[$idx]} (expected SA followed by a positive integer)"
    cars[$idx]="$car"
done

if [[ ! -d "$input_root" ]]; then
    printf 'Error: input root not found: %s\n' "$input_root" >&2
    exit 1
fi
if [[ "$dry_run" == false ]] && ! command -v "$python_bin" >/dev/null 2>&1; then
    printf 'Error: Python interpreter not found: %s\n' "$python_bin" >&2
    exit 1
fi

success=0
failed=()
for car in "${cars[@]}"; do
    source_dir="$input_root/$car"
    output_path="$output_root/${car,,}/cali.json"
    printf '\n===== Processing %s =====\n' "$car"
    if [[ ! -d "$source_dir/camera" || ! -f "$source_dir/freespace/freespace.yaml" ]]; then
        printf 'Error: %s needs camera/ and freespace/freespace.yaml\n' "$source_dir" >&2
        failed+=("$car")
        continue
    fi
    command_args=("$python_bin" "$tool_dir/toolkit.py" calibration convert "$source_dir" -o "$output_path")
    if [[ "$dry_run" == true ]]; then
        printf '%q ' "${command_args[@]}"
        printf '\n'
    elif ! "${command_args[@]}"; then
        printf 'Error: conversion failed for %s\n' "$car" >&2
        failed+=("$car")
        continue
    fi
    success=$((success + 1))
done

if [[ "$dry_run" == true ]]; then
    printf '\nDry run: %d ready, %d failed\n' "$success" "${#failed[@]}"
else
    printf '\nSummary: %d succeeded, %d failed\n' "$success" "${#failed[@]}"
fi
if ((${#failed[@]})); then
    printf 'Failed vehicles: %s\n' "${failed[*]}" >&2
    exit 1
fi
