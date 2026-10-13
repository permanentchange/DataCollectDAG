# Repository Guidelines

## Source of truth

Use the implementation in `src/data_collect_dag/`, the currently committed demo YAML files, and `pyproject.toml` as the authority for documented behavior. Do not turn design proposals, uncommitted files, or local ROS bags into claims about supported features. Keep documentation concise and executable; update it when changing interfaces or packaging.

## Layout

- `src/data_collect_dag/app.py`, `session.py`, `dag.py`: application, session lifecycle and DAG execution.
- `src/data_collect_dag/config.py` and `models.py`: config validation and shared types.
- `src/data_collect_dag/ros_adapter.py`, `ros_messages.py`, `ros_env.py`: ROS1 integration and message resolution.
- `src/data_collect_dag/nodes/`: built-in processing nodes. Register new nodes in `session.py::NODE_TYPES`.
- `demo/cfg.yaml`: example pipelines; vehicle calibrations are under `demo/calibration/<car-id>/cali.json`.
- `src/data_collect_dag/demo_templates.py`, `setup.py`, `MANIFEST.in`: offline template export and bundled demo resources.
- `tests/unit/` and `tests/functional/`: pytest coverage.
- `README.md`: user setup and commands; `docs/architecture.md`: runtime/config reference; `docs/ros_external_system_architecture.md`: ROS control contract.

## Development and tests

Target Python 3.8 (`>=3.8,<3.9`) and ROS1 Noetic. The Python package is not a replacement for the ROS installation or generated custom message packages.

```bash
python -m pip install -e '.[test]'
python -m pytest tests/unit -q
python -m pytest tests/functional -q
```

For an inference pipeline, add the optional `yolo` extra. For Jetson installations, use `bash setup.bash arm` with device-compatible wheels in `~/wheels`; the base installation must not install or import torch, torchvision, Ultralytics, or ONNX.

`demo/cfg.yaml` provides `init`, `fps`, `fps_undist` and `compare_motion` without inference dependencies; `fps_with_yolo` requires the YOLO extra and a model at `demo/models/yolo26m.pt`. `fps_undist` saves images without undistortion. The checkout defaults to SA4 calibration. A ROS master, configured topics and any playback bag are external resources.

`data_collect_dag --init-demo DIR [--car-id CAR_ID] [--force]` exports templates without ROS. Car selection is case-insensitive; omitting it exports all calibrations with SA4 selected in YAML. Exported calibration/model paths are absolute. Collection and `--clean DAY` require `--config`; `--background` applies only to collection. Preserve unrelated files during export and keep packaging tests consistent with source templates. Avoid documenting removed examples or historical migration discussions.

## Code and review conventions

Use 4-space indentation, type hints for module boundaries, `snake_case` names and `PascalCase` classes. Prefer `pathlib.Path` for file paths. Follow nearby code conventions; no formatter or linter configuration is committed. Keep ROS subscription callbacks lightweight; processing belongs in DAG nodes.

Add focused unit tests for config changes and nodes. Include functional coverage for control/session lifecycle changes. For optional backends, test that the base FPS pipeline can be imported and built without them. PR descriptions should state the behavior affected, any config or installation impact, and how it was verified. Use specific conventional commit subjects such as `fix:`, `feat:`, `docs:`, `test:` or `build:`.
