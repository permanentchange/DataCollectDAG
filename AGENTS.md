# Repository Guidelines

## Project Structure & Module Organization

Core package code lives in `src/data_collect_dag/`. Keep runtime orchestration in top-level modules such as `app.py`, `session.py`, and `dag.py`; place reusable node implementations under `src/data_collect_dag/nodes/`. Tests are split by scope into `tests/unit/` and `tests/functional/`. Demo and calibration inputs live in `demo/`, and design notes live in `docs/` (`architecture.md`, `requirements.md`).

## Build, Test, and Development Commands

- `conda activate dcd` enters the expected local development and test environment.
- `conda create -n dcd python=3.8 -y` creates that environment when it does not already exist.
- `python -m pip install -e .` installs the package in editable mode.
- `python -m pip install "ultralytics>=8.4,<9"` is only a fallback if `python -m pip install -e .` did not install the declared YOLO dependency in the current environment.
- `python -m pytest` runs the full test suite.
- `python -m pytest tests/unit -q` runs fast unit coverage only.
- `python -m pytest tests/functional -q` runs session/runtime integration checks.
- `data_collect_dag --config demo/xtreme1_demo.yaml --pipeline xtreme1_collect` starts the demo pipeline through the published CLI entry point.

Use Python `3.8` for local work; `pyproject.toml` pins `>=3.8,<3.9`. Install dependencies inside the `dcd` environment with either `pip` or `conda`, depending on package availability and local constraints.

## Coding Style & Naming Conventions

Follow the existing Python style: 4-space indentation, explicit imports, type hints where behavior crosses module boundaries, and `pathlib.Path` for filesystem paths. Use `snake_case` for functions, modules, variables, and test names; use `PascalCase` for classes. Match the current package layout instead of introducing new top-level folders. No formatter or linter config is committed here, so keep changes consistent with nearby code and avoid broad style-only edits.

## Testing Guidelines

Tests use `pytest`. Name files `test_*.py` and keep unit tests close to a single module or node behavior. Add functional coverage when changing session lifecycle, CLI behavior, ROS adapter flow, or DAG execution wiring. Prefer assertions against observable outputs and status transitions over internal implementation details.

## Commit & Pull Request Guidelines

Recent history uses short conventional prefixes. Preferred examples include `feat:`, `fix:`, `perf:`, `refactor:`, `test:`, `docs:`, `build:`, `ci:`, `style:`, and `chore:`; use another standard prefix when it matches the change more precisely. Keep commit subjects imperative and specific, for example `fix: handle empty sync window`. PRs should state the behavior change, note config or pipeline impacts, link the relevant issue if one exists, and include test evidence. Attach sample output or screenshots only when the change affects operator-visible status or generated artifacts.

## Configuration & ROS Notes

Configuration is YAML-driven; use `demo/xtreme1_demo.yaml` as the reference shape for new pipelines. Keep ROS-facing code lightweight and confined to adapter/control modules; heavy processing belongs in DAG nodes. The demo pipeline expects a YOLO weight file at `demo/models/yolo_person_model.pt`; see `demo/models/README.md` for a manual download step before running `demo/xtreme1_demo.yaml`. A local `test_data.bag` ROS bag file is available for testing; use `roscore`, then start `data_collect_dag --config demo/xtreme1_demo.yaml --pipeline xtreme1_collect`, and finally run `rosbag play test_data.bag` to simulate a recording scenario when validating ROS-integrated behavior.
