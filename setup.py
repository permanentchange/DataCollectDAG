"""Build demo resources from their canonical repository files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithDemo(build_py):
    def run(self):
        super().run()
        if self.editable_mode:
            return
        demo = Path(__file__).resolve().parent / "demo"
        assets = sorted(list(demo.glob("*.yaml")) + list(demo.glob("*.yml"))
                        + list((demo / "calibration").glob("*.json")))
        if not assets:
            raise RuntimeError("demo templates are missing from the source distribution")
        archive = Path(self.build_lib) / "data_collect_dag" / "_demo.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
            for asset in assets:
                # Read symlinks as ordinary files (notably calibration/cali.json).
                bundle.writestr(asset.relative_to(demo).as_posix(), asset.read_bytes())

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        if not self.editable_mode:
            outputs.append(str(Path(self.build_lib) / "data_collect_dag" / "_demo.zip"))
        return outputs


setup(cmdclass={"build_py": BuildPyWithDemo})
