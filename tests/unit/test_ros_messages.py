from types import SimpleNamespace

import numpy as np

from data_collect_dag.ros_messages import ensure_image_frame_decoded, ensure_pointcloud_frame_parsed, frame_from_ros_message


class Stamp:
    def __init__(self, ns):
        self._ns = ns

    def to_nsec(self):
        return self._ns


def test_frame_from_ros_image_message():
    msg = SimpleNamespace(
        header=SimpleNamespace(stamp=Stamp(1000), frame_id="cam"),
        encoding="bgr8",
        width=2,
        height=1,
        data=bytes([1, 2, 3, 4, 5, 6]),
    )
    frame = frame_from_ros_message("front_wide_camera", "/cam", "image", "front_wide_camera", msg)
    assert frame.image_bgr is None
    assert frame.raw_msg_ref is msg
    assert frame.meta.source_timestamp_ns == 1000
    decoded = ensure_image_frame_decoded(frame)
    assert decoded.image_bgr is not None
    assert decoded.image_bgr.shape == (1, 2, 3)
    np.testing.assert_array_equal(decoded.image_bgr[0, 0], np.array([1, 2, 3], dtype=np.uint8))


def test_frame_from_ros_pointcloud_message():
    fields = [
        SimpleNamespace(name="x", offset=0, datatype=7, count=1),
        SimpleNamespace(name="y", offset=4, datatype=7, count=1),
        SimpleNamespace(name="z", offset=8, datatype=7, count=1),
        SimpleNamespace(name="timestamp", offset=12, datatype=8, count=1),
    ]
    row = np.array([(1.0, 2.0, 3.0, 1_000_000_000.0)], dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("timestamp", "<f8")])
    msg = SimpleNamespace(
        header=SimpleNamespace(stamp=Stamp(1000), frame_id="lidar"),
        fields=fields,
        point_step=20,
        width=1,
        height=1,
        data=row.tobytes() + b"\x00" * (20 - row.dtype.itemsize),
    )
    frame = frame_from_ros_message("top_lidar", "/lidar", "pointcloud", "top_lidar", msg)
    assert frame.points_xyz is None
    assert frame.point_timestamps_ns is None
    assert frame.raw_msg_ref is msg
    parsed = ensure_pointcloud_frame_parsed(frame)
    assert parsed.points_xyz is not None
    assert parsed.points_xyz.shape == (1, 3)
    assert parsed.point_timestamps_ns is not None
    assert parsed.point_timestamps_ns.shape == (1,)
    np.testing.assert_allclose(parsed.points_xyz[0], np.array([1.0, 2.0, 3.0], dtype=np.float64))


def test_frame_from_ros_localization_message():
    msg = SimpleNamespace(
        timestamp=1782380245486758,
        status=1,
        state="ok",
        car_pose=SimpleNamespace(
            position=SimpleNamespace(x=1.0, y=2.0, z=3.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        ),
        vel=SimpleNamespace(x=4.0, y=5.0, z=6.0),
        gyr=SimpleNamespace(x=7.0, y=8.0, z=9.0),
    )
    frame = frame_from_ros_message("localization", "/localization", "localization", "localization", msg)
    assert frame.meta.source_timestamp_ns > 10**15
    assert frame.status == 1


def test_frame_from_ros_nmea_message():
    msg = SimpleNamespace(
        header=SimpleNamespace(stamp=Stamp(1000), frame_id="gps"),
        sentence="$GNGGA,test",
    )
    frame = frame_from_ros_message("bdstar_nmea", "/bdstar", "nmea", "bdstar", msg)
    assert frame.text == "$GNGGA,test"
