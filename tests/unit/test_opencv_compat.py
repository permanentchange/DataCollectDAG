import builtins
from types import ModuleType, SimpleNamespace
import sys

import pytest

from data_collect_dag.opencv_compat import load_cv2
from data_collect_dag.ros_messages import _parse_ros_image_message


def test_reuses_importable_system_module_without_distribution_metadata(monkeypatch):
    system_cv2 = ModuleType("cv2")
    system_cv2.__version__ = "4.2.0"
    system_cv2.__file__ = "/usr/lib/python3/dist-packages/cv2.so"
    monkeypatch.setitem(sys.modules, "cv2", system_cv2)
    assert load_cv2() is system_cv2


@pytest.mark.parametrize("missing_name", ["cv2", "numpy.core"])
def test_image_decode_reports_missing_opencv_but_preserves_other_import_errors(monkeypatch, missing_name):
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "cv2":
            raise ModuleNotFoundError("No module named '{}'".format(missing_name), name=missing_name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    message = SimpleNamespace(data=bytes(3), encoding="rgb8", height=1, width=1)
    if missing_name == "cv2":
        with pytest.raises(RuntimeError, match="OpenCV.*required") as error:
            _parse_ros_image_message(message)
        assert ".[opencv]" in str(error.value)
        assert "4.2" in str(error.value)
    else:
        with pytest.raises(ModuleNotFoundError) as error:
            _parse_ros_image_message(message)
        assert error.value.name == missing_name
