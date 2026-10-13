"""Exercise an offline installation built from the source distribution."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]


def test_sdist_wheel_and_offline_demo_export(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ["pyproject.toml", "setup.py", "MANIFEST.in", "README.md"]:
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(ROOT / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    demo = source / "demo"
    demo.mkdir()
    for pattern in ["*.yaml", "*.yml"]:
        for path in (ROOT / "demo").glob(pattern):
            shutil.copy2(path, demo / path.name)
    shutil.copytree(ROOT / "demo/calibration", demo / "calibration", symlinks=True)
    # These must not leak into either distribution, even when present locally.
    (demo / "models").mkdir()
    (demo / "models/private.pt").write_bytes(b"weights")
    (demo / "private.bag").write_bytes(b"bag")
    (demo / "OUTPUT").mkdir()
    (demo / "OUTPUT/runtime.json").write_text("{}")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PIP_NO_INDEX"] = "1"
    env["PYTHONNOUSERSITE"] = "1"

    def run(arguments, cwd):
        result = subprocess.run([sys.executable] + arguments, cwd=str(cwd), env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        assert result.returncode == 0, result.stdout
        return result.stdout

    run(["setup.py", "sdist", "--dist-dir", str(tmp_path / "sdist")], source)
    archive = next((tmp_path / "sdist").glob("*.tar.gz"))
    with tarfile.open(archive) as distribution:
        names = distribution.getnames()
        assert any(name.endswith("demo/cfg.yaml") for name in names)
        assert any(name.endswith("demo/calibration/cali.json") for name in names)
        assert not any("private" in name or "OUTPUT" in name for name in names)
        distribution.extractall(tmp_path / "extracted")
    extracted = next((tmp_path / "extracted").iterdir())
    run(["-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", str(tmp_path / "wheels"), "."], extracted)
    wheel = next((tmp_path / "wheels").glob("*.whl"))
    with ZipFile(wheel) as distribution:
        assert "data_collect_dag/_demo.zip" in distribution.namelist()
        assert not any(name.endswith((".pt", ".bag")) for name in distribution.namelist())
        with ZipFile(io.BytesIO(distribution.read("data_collect_dag/_demo.zip"))) as bundle:
            bundled = {name: bundle.read(name) for name in bundle.namelist()}
    expected = {path.relative_to(demo).as_posix(): path.read_bytes()
                for path in list(demo.glob("*.yaml")) + list(demo.glob("*.yml"))
                + list((demo / "calibration").glob("*.json"))}
    assert bundled == expected
    run(["-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", str(tmp_path / "direct-wheels"), "."], source)
    with ZipFile(next((tmp_path / "direct-wheels").glob("*.whl"))) as distribution:
        with ZipFile(io.BytesIO(distribution.read("data_collect_dag/_demo.zip"))) as bundle:
            assert {name: bundle.read(name) for name in bundle.namelist()} == expected

    editable = tmp_path / "editable"
    run(["-m", "pip", "install", "--no-deps", "--no-build-isolation", "--target", str(editable), "--editable", str(source)], tmp_path)
    # A template added after installation proves that editable exports read live files.
    (demo / "live.yaml").write_text("editable_revision: live\n")
    env["DCD_EDITABLE"] = str(editable)
    env["DCD_SOURCE"] = str(source)
    run(["-c", r'''
import os
import site
import sys
from pathlib import Path
previous_paths = list(sys.path)
site.addsitedir(os.environ["DCD_EDITABLE"])
# Prefer this installation over any developer editable install in the test env.
sys.path = [entry for entry in sys.path if entry not in previous_paths] + previous_paths
import data_collect_dag
assert Path(data_collect_dag.__file__).parent.parent.parent == Path(os.environ["DCD_SOURCE"])
from data_collect_dag.demo_templates import export_demo
export_demo(Path("editable-export"))
assert Path("editable-export/live.yaml").read_text() == "editable_revision: live\n"
'''], tmp_path)
    installed = tmp_path / "installed"
    run(["-m", "pip", "install", "--no-deps", "--target", str(installed), str(wheel)], tmp_path)
    # Remove the build checkouts so the export cannot fall back to repository files.
    shutil.rmtree(source)
    shutil.rmtree(extracted)
    env["PYTHONPATH"] = str(installed)
    env["DCD_INSTALLED"] = str(installed)
    script = r'''
import builtins
import os
import sys
from pathlib import Path

blocked = {"rospy", "torch", "torchvision", "ultralytics", "onnx", "onnxruntime"}
original_import = builtins.__import__
def no_external_import(name, *args, **kwargs):
    if name.split(".", 1)[0] in blocked:
        raise AssertionError("unexpected external import: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = no_external_import
import data_collect_dag
assert Path(data_collect_dag.__file__).parent.parent == Path(os.environ["DCD_INSTALLED"])
from data_collect_dag.cli import main
sys.argv = ["data_collect_dag", "--init-demo", "exported"]
assert main() == 0
from data_collect_dag.app import load_calibration
from data_collect_dag.config import load_app_config
config = load_app_config(Path("exported/cfg.yaml"))
assert config.calibration_path == Path.cwd() / "exported/calibration/cali.json"
assert load_calibration(config.calibration_path)["images"]
assert "fps_undist" in config.pipelines
assert not config.calibration_path.is_symlink()
assert not list(Path("exported/models").iterdir())
assert not blocked.intersection(sys.modules)
'''
    run(["-c", script], tmp_path)
