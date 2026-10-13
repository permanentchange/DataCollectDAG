import numpy as np
import pytest

from data_collect_dag.models import ImageFrame, NodeConfig, NodeResult
from data_collect_dag.nodes.image_undistort import ImageUndistortNode
from data_collect_dag.sample_context import SampleContext
from data_collect_dag.storage import StorageTracker, scan_files
from tests.unit.conftest import make_meta


def _make_node(dummy_session):
    calibration = dummy_session.calibration["images"]["/miivii_gmsl_camera_node/miivii_gmsl/image2"]
    calibration.update(
        {
            "dist_coeffs": [-0.1, 0.01, 0.0, 0.0],
            "is_fisheye": True,
            "resolution": [6, 4],
            "camera_matrix": [[4.0, 0.0, 3.0], [0.0, 4.0, 2.0], [0.0, 0.0, 1.0]],
        }
    )
    return ImageUndistortNode(
        NodeConfig(
            "undistort_front_wide",
            "image_undistort",
            {"image": "front_wide_raw"},
            {"image": "front_wide_undistorted"},
            {"source": "front_wide_camera", "write_debug_artifacts": True},
        ),
        dummy_session,
    )


@pytest.mark.parametrize("track_storage", [False, True])
def test_image_undistort_writes_corrected_frame_with_original_metadata(dummy_session, track_storage):
    if track_storage:
        dummy_session.storage_tracker = StorageTracker(dummy_session.app_config.output_root_dir, 1, lambda: None)
        dummy_session.storage_tracker.reconcile()
    node = _make_node(dummy_session)
    node.setup()
    raw = np.dstack(
        [
            np.tile(np.arange(6, dtype=np.uint8), (4, 1)),
            np.zeros((4, 6), dtype=np.uint8),
            np.zeros((4, 6), dtype=np.uint8),
        ]
    )
    image = ImageFrame(
        meta=make_meta("front_wide_camera", "image", 1_000_000_000),
        image_bgr=raw,
        encoding="bgr8",
        width=6,
        height=4,
    )
    sample = SampleContext(sample_id="sample")
    sample.put("front_wide_raw", image, "test")

    outcome = node.run(sample)

    assert outcome.status == NodeResult.OK
    corrected = sample.get("front_wide_undistorted")
    assert corrected.meta == image.meta
    assert corrected.encoding == "bgr8"
    assert corrected.image_bgr.shape == raw.shape
    assert not np.array_equal(corrected.image_bgr, raw)
    debug_root = dummy_session.session_root / "diagnostics" / "undistort" / "sample" / "front_wide_camera"
    assert (debug_root / "raw.jpg").exists()
    assert (debug_root / "undistorted.jpg").exists()
    assert (debug_root / "calibration.json").exists()
    if track_storage:
        assert dummy_session.storage_tracker.size_bytes == sum(scan_files(dummy_session.app_config.output_root_dir).values())


def test_image_undistort_setup_rejects_missing_distortion_coefficients(dummy_session):
    node = _make_node(dummy_session)
    dummy_session.calibration["images"]["/miivii_gmsl_camera_node/miivii_gmsl/image2"].pop("dist_coeffs")

    with pytest.raises(ValueError, match="dist_coeffs"):
        node.setup()


def test_image_undistort_scales_intrinsics_for_same_aspect_ratio_input(dummy_session):
    node = _make_node(dummy_session)
    node.setup()
    image = ImageFrame(
        meta=make_meta("front_wide_camera", "image", 1_000_000_000),
        image_bgr=np.zeros((2, 3, 3), dtype=np.uint8),
        encoding="bgr8",
        width=3,
        height=2,
    )
    sample = SampleContext(sample_id="scaled")
    sample.put("front_wide_raw", image, "test")

    outcome = node.run(sample)

    assert outcome.status == NodeResult.OK
    assert sample.metadata["undistort_camera_params"]["front_wide_camera"]["camera_matrix"] == [
        [2.0, 0.0, 1.5],
        [0.0, 2.0, 1.0],
        [0.0, 0.0, 1.0],
    ]
