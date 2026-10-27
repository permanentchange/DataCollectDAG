#!/usr/bin/env bash

set -euo pipefail

# Run consistently even if invoked from outside the repository root.
cd "$(dirname -- "${BASH_SOURCE[0]}")"

# Usage: bash setup.bash [base|yolo|yolo-arm|dev|yolo-dev]
# The default install never installs or changes inference packages.
MODE="${1:-base}"
WHEEL_DIR="${DCD_WHEEL_DIR:-$HOME/wheels}"

# Choose a wheel deterministically. Prefer the known Jetson build when
# available; otherwise accept exactly one local wheel for the package.
# Override explicitly with DCD_TORCH_WHEEL / DCD_TORCHVISION_WHEEL if
# ~/wheels contains multiple JetPack variants.
pick_arm_wheel() {
    local package="$1"
    local preferred="$2"
    local override="${3:-}"
    local selected=""

    if [[ -n "$override" ]]; then
        selected="$override"
    elif [[ -f "$WHEEL_DIR/$preferred" ]]; then
        selected="$WHEEL_DIR/$preferred"
    else
        local candidates=()
        shopt -s nullglob
        candidates=( "$WHEEL_DIR"/"$package"-*.whl )
        shopt -u nullglob
        if (( ${#candidates[@]} != 1 )); then
            echo "Expected exactly one $package wheel in $WHEEL_DIR, found ${#candidates[@]}." >&2
            echo "Provide the wheel or choose explicitly with DCD_${package^^}_WHEEL." >&2
            return 1
        fi
        selected="${candidates[0]}"
    fi

    if [[ ! -f "$selected" ]]; then
        echo "Missing $package wheel: $selected" >&2
        return 1
    fi
    case "${selected##*/}" in
        "$package"-*.whl) ;;
        *)
            echo "Not a $package wheel: $selected" >&2
            return 1
            ;;
    esac
    printf '%s\n' "$selected"
}

case "$MODE" in
    base)
        echo "Installing the base DataCollectDAG runtime (no inference packages)..."
        python -m pip install -e .
        ;;
    yolo|dev|yolo-dev)
        echo "Installing DataCollectDAG in $MODE mode..."
        python -m pip install -e ".[$MODE]"
        ;;
    yolo-arm)
        echo "Installing ARM YOLO with Jetson PyTorch wheels from $WHEEL_DIR..."
        if [[ ! -d "$WHEEL_DIR" ]]; then
            echo "ARM wheel directory does not exist: $WHEEL_DIR" >&2
            exit 1
        fi
        # Resolve *both* wheels before uninstalling existing packages.
        torch_wheel=$(pick_arm_wheel \
            torch \
            'torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl' \
            "${DCD_TORCH_WHEEL:-}")
        torchvision_wheel=$(pick_arm_wheel \
            torchvision \
            'torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl' \
            "${DCD_TORCHVISION_WHEEL:-}")

        echo "Using torch wheel: $torch_wheel"
        echo "Using torchvision wheel: $torchvision_wheel"
        # Constrain the source files, not just versions: even an index wheel
        # with the same version must never replace the selected Jetson build.
        constraint_file=$(mktemp)
        trap 'rm -f "$constraint_file"' EXIT
        python - "$constraint_file" "$torch_wheel" "$torchvision_wheel" <<'PY'
from pathlib import Path
import sys

constraints = [
    "{} @ {}".format(package, Path(wheel).resolve().as_uri())
    for package, wheel in zip(("torch", "torchvision"), sys.argv[2:])
]
Path(sys.argv[1]).write_text("\n".join(constraints) + "\n", encoding="utf-8")
PY
        python -m pip uninstall -y torch torchvision
        # Install only the local wheels before resolving any YOLO dependencies.
        python -m pip install --no-deps "$torch_wheel" "$torchvision_wheel"
        python -m pip install --constraint "$constraint_file" \
            --no-binary=psutil --find-links "$WHEEL_DIR" -e ".[yolo-arm]"
        ;;
    *)
        echo "Usage: bash setup.bash [base|yolo|yolo-arm|dev|yolo-dev]" >&2
        exit 1
        ;;
esac

echo "Installed. Export bundled configs with: data_collect_dag init DIR [--car-id CAR_ID]. Supply model weights separately."
