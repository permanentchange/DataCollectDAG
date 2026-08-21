import numpy as np
import pytest

from data_collect_dag.io_utils import save_ascii_pcd


def test_save_ascii_pcd_writes_uint8_intensity_and_availability_flag(tmp_path):
    path = tmp_path / "cloud.pcd"

    save_ascii_pcd(
        path,
        np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        intensities=np.array([0.0, 255.0]),
        has_intensity=np.array([True, True]),
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert "FIELDS x y z intensity has_intensity" in lines
    assert "SIZE 4 4 4 1 1" in lines
    assert "TYPE F F F U U" in lines
    assert lines[-2:] == ["1.000000 2.000000 3.000000 0 1", "4.000000 5.000000 6.000000 255 1"]


def test_save_ascii_pcd_marks_missing_intensity_with_zeroes(tmp_path):
    path = tmp_path / "cloud.pcd"

    save_ascii_pcd(path, np.array([[1.0, 2.0, 3.0]]))

    assert path.read_text(encoding="utf-8").splitlines()[-1] == "1.000000 2.000000 3.000000 0 0"


@pytest.mark.parametrize("intensity", [-1.0, 1.5, 256.0, np.nan])
def test_save_ascii_pcd_rejects_invalid_available_intensity(tmp_path, intensity):
    path = tmp_path / "cloud.pcd"

    with pytest.raises(ValueError, match="invalid intensity"):
        save_ascii_pcd(
            path,
            np.array([[1.0, 2.0, 3.0]]),
            intensities=np.array([intensity]),
            has_intensity=np.array([True]),
        )

    assert not path.exists()
