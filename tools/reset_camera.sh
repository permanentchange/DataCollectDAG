#!/bin/bash
set -e
tool_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$tool_dir/toolkit.py" ros camera-reset "$@"
