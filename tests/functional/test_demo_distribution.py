"""Exercise an offline installation built from the source distribution."""

import io
import os
from email.parser import Parser
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]


def _project_version():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE).group(1)


def _copy_source(source):
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


def test_sdist_wheel_and_offline_demo_export(tmp_path):
    source = tmp_path / "source"
    _copy_source(source)
    demo = source / "demo"
    version = _project_version()
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
    env["DCD_EXPECTED_VERSION"] = version

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
        assert any(name.endswith("demo/calibration/sa4/cali.json") for name in names)
        assert any(name.endswith("demo/calibration/sa14/cali.json") for name in names)
        assert not any(name.endswith("_cali.json") for name in names)
        assert not any("private" in name or "OUTPUT" in name for name in names)
        metadata_file = next(name for name in names if name.count("/") == 1 and name.endswith("/PKG-INFO"))
        metadata = Parser().parsestr(distribution.extractfile(metadata_file).read().decode("utf-8"))
        assert metadata["Version"] == version
        distribution.extractall(tmp_path / "extracted")
    extracted = next((tmp_path / "extracted").iterdir())
    run(["-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", str(tmp_path / "wheels"), "."], extracted)
    wheel = next((tmp_path / "wheels").glob("*.whl"))
    with ZipFile(wheel) as distribution:
        assert "data_collect_dag/_demo.zip" in distribution.namelist()
        assert not any(name.endswith((".pt", ".bag")) for name in distribution.namelist())
        metadata_file = next(name for name in distribution.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(distribution.read(metadata_file).decode("utf-8"))
        assert metadata["Version"] == version
        with ZipFile(io.BytesIO(distribution.read("data_collect_dag/_demo.zip"))) as bundle:
            bundled = {name: bundle.read(name) for name in bundle.namelist()}
    expected = {path.relative_to(demo).as_posix(): path.read_bytes()
                for path in list(demo.glob("*.yaml")) + list(demo.glob("*.yml"))
                + list((demo / "calibration").rglob("*.json"))}
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
assert data_collect_dag.__version__ == os.environ["DCD_EXPECTED_VERSION"]
from data_collect_dag.config_templates import export_config_templates
export_config_templates(Path("editable-export"))
assert Path("editable-export/live.yaml").read_text() == "editable_revision: live\n"
export_config_templates(Path("editable-sa14"), car_id="SA14")
assert {path.name for path in Path("editable-sa14/calibration").iterdir()} == {"sa14"}
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
from importlib import metadata
assert data_collect_dag.__version__ == metadata.version("data-collect-dag") == os.environ["DCD_EXPECTED_VERSION"]
from data_collect_dag.cli import main
from data_collect_dag.config_templates import available_car_ids, read_templates
sys.argv = ["data_collect_dag", "init", "exported"]
assert main() == 0
for selection in ([], ["--pipeline", "fps_undist_nomotion"]):
    sys.argv = ["data_collect_dag", "inspect", "--config", "exported/cfg.yaml"] + selection
    assert main() == 0
assert not {"data_collect_dag.app", "data_collect_dag.session", "data_collect_dag.nodes"}.intersection(sys.modules)
from importlib.util import find_spec
assert find_spec("data_collect_dag.demo_templates") is None
from data_collect_dag.app import load_calibration
from data_collect_dag.config import load_app_config
config = load_app_config(Path("exported/cfg.yaml"))
assert config.calibration_path == Path.cwd() / "exported/calibration/sa4/cali.json"
assert {path.name for path in Path("exported/calibration").iterdir()} == set(available_car_ids(read_templates()))
assert load_calibration(config.calibration_path)["images"]
assert "fps_undist_nomotion" in config.pipelines
assert not config.calibration_path.is_symlink()
assert not list(Path("exported/models").iterdir())
Path("exported/cfg.yaml").write_text("outdated: configuration\n")
Path("exported/custom.txt").write_bytes(b"user data")
Path("exported/models/yolo26m.pt").write_bytes(b"user weights")
Path("exported/calibration/sa4/cali.json").write_bytes(b"user SA4 calibration")
sys.argv = ["data_collect_dag", "init", "exported", "--car-id", "SA20", "--force"]
assert main() == 0
refreshed = load_app_config(Path("exported/cfg.yaml"))
assert "fps_undist_nomotion" in refreshed.pipelines
assert refreshed.calibration_path == Path.cwd() / "exported/calibration/sa20/cali.json"
assert Path("exported/custom.txt").read_bytes() == b"user data"
assert Path("exported/models/yolo26m.pt").read_bytes() == b"user weights"
assert Path("exported/calibration/sa4/cali.json").read_bytes() == b"user SA4 calibration"
for car in ["sa4", "sa14"]:
    sys.argv = ["data_collect_dag", "init", car, "--car-id", car.upper()]
    assert main() == 0
    config = load_app_config(Path(car) / "cfg.yaml")
    assert config.calibration_path == Path.cwd() / car / "calibration" / car / "cali.json"
    assert load_calibration(config.calibration_path)["images"]
    assert {path.name for path in (Path(car) / "calibration").iterdir()} == {car}
assert not blocked.intersection(sys.modules)
'''
    run(["-c", script], tmp_path)


def test_vcs_install_replaces_lower_version_without_upgrade_flag(tmp_path):
    source = tmp_path / "source"
    _copy_source(source)
    project = source / "pyproject.toml"
    package = source / "src/data_collect_dag/__init__.py"
    current_project = project.read_text(encoding="utf-8")
    current_package = package.read_text(encoding="utf-8")
    version = _project_version()
    # Simulate an older release without depending on historical Git objects.
    project.write_text(current_project.replace('version = "{}"'.format(version), 'version = "0.2.0"'), encoding="utf-8")
    package.write_text(current_package.replace('__version__ = "{}"'.format(version), '__version__ = "0.2.0"'), encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PIP_NO_INDEX"] = "1"
    env["PYTHONNOUSERSITE"] = "1"

    def run(command, cwd=tmp_path):
        result = subprocess.run(command, cwd=str(cwd), env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        assert result.returncode == 0, result.stdout
        return result.stdout

    def commit(message):
        run(["git", "add", "."], cwd=source)
        run(["git", "-c", "user.name=DCD Packaging Test", "-c", "user.email=dcd-test@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-m", message], cwd=source)

    run(["git", "init", "--quiet", str(source)])
    commit("older release")
    isolated = tmp_path / "venv"
    # Use the test environment's build tools/pip, but install the project only
    # inside this disposable venv, never into the developer's environment.
    run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", str(isolated)])
    # A nested --system-site-packages venv inherits the base interpreter's
    # packages, not build tools installed in the invoking test venv. Make the
    # caller's build tools available without exposing the source checkout.
    import setuptools
    import wheel
    build_paths = {str(Path(module.__file__).resolve().parent.parent) for module in (setuptools, wheel)}
    env["PYTHONPATH"] = os.pathsep.join(sorted(build_paths))
    python = str(isolated / "bin/python")
    requirement = "git+" + source.as_uri()
    install = [python, "-m", "pip", "install", "--no-deps", "--no-build-isolation", requirement]
    run(install)
    verify = r'''
import sys
from pathlib import Path
from importlib import metadata
import data_collect_dag
assert data_collect_dag.__version__ == metadata.version("data-collect-dag") == sys.argv[1]
assert str(Path(data_collect_dag.__file__).resolve()).startswith(str(Path(sys.prefix).resolve()) + "/")
'''
    run([python, "-c", verify, "0.2.0"])
    project.write_text(current_project, encoding="utf-8")
    package.write_text(current_package, encoding="utf-8")
    commit("current release")
    # The exact same command must install the changed version, with neither
    # --upgrade nor --force-reinstall. Dependencies stay untouched.
    run(install)
    run([python, "-c", verify, version])
    help_text = run([python, "-m", "data_collect_dag", "--help"])
    assert "inspect" in help_text
    assert "--car-id CAR_ID" in run([python, "-m", "data_collect_dag", "init", "--help"])
    destination = tmp_path / "exported"
    run([python, "-m", "data_collect_dag", "init", str(destination), "--car-id", "sa14"])
    assert {path.name for path in (destination / "calibration").iterdir()} == {"sa14"}
    run([python, "-c", r'''
import sys
from pathlib import Path
from data_collect_dag.config import load_app_config
root = Path(sys.argv[1])
assert load_app_config(root / "cfg.yaml").calibration_path == root / "calibration/sa14/cali.json"
''', str(destination)])
