"""Base install regression: build the real demo/cfg.yaml FPS DAG without ML backends."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_fps_pipeline_does_not_import_optional_inference_libraries(tmp_path):
    # A fresh process prevents an earlier test from hiding eager imports in
    # sys.modules. The import blocker simulates an environment without ML.
    script = r'''
import builtins
import os
import sys
from pathlib import Path

blocked = {"torch", "torchvision", "ultralytics", "onnx", "onnxruntime"}
attempted = []
original_import = builtins.__import__

def no_inference_import(name, *args, **kwargs):
    root = name.split(".", 1)[0]
    if root in blocked:
        attempted.append(name)
        raise ImportError("inference package not installed: " + name)
    return original_import(name, *args, **kwargs)

builtins.__import__ = no_inference_import

from data_collect_dag.app import load_calibration
from data_collect_dag.config import load_app_config
from data_collect_dag.ros_adapter import RosAdapter
from data_collect_dag.session import SessionRuntime
from data_collect_dag.status import StatusManager

config = load_app_config(Path("demo/cfg.yaml"))
config.output_root_dir = Path(os.environ["DCD_TEST_OUTPUT"])
pipeline = config.pipelines["fps"]
assert pipeline.nodes_by_id
assert all(node.node_type != "yolo_gate" for node in pipeline.nodes_by_id.values())

adapter = RosAdapter(config.topics, config.ros_node_name, config.control)
session = SessionRuntime(
    config,
    pipeline,
    load_calibration(config.calibration_path),
    adapter,
    StatusManager(disk_usage_path=config.output_root_dir),
    command_callback=lambda command: None,
)
try:
    session._build_nodes()
    assert session._dag is not None
finally:
    if session._node_pool is not None:
        session._node_pool.shutdown(wait=True)

assert not attempted, "FPS unexpectedly tried to import ML packages: " + repr(attempted)
assert not blocked.intersection(sys.modules)
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["DCD_TEST_OUTPUT"] = str(tmp_path)
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=40,
    )
    assert process.returncode == 0, process.stdout + "\n" + process.stderr
