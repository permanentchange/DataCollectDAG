import numpy as np

from data_collect_dag.transforms import slerp_quaternion_xyzw


def test_slerp_quaternion_vectorized_keeps_endpoints():
    q0 = np.array([[0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 1.0, 0.0]], dtype=np.float64)
    q1 = np.array([[0.0, 0.0, 1.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype=np.float64)

    result = slerp_quaternion_xyzw(q0, q1, np.array([0.0, 1.0], dtype=np.float64))

    np.testing.assert_allclose(result[0], q0[0])
    np.testing.assert_allclose(result[1], q1[1], atol=1e-12)


def test_slerp_quaternion_vectorized_handles_opposite_sign_same_rotation():
    q0 = np.array([[0.0, 0.0, 0.0, 1.0]], dtype=np.float64)
    q1 = -q0

    result = slerp_quaternion_xyzw(q0, q1, np.array([0.5], dtype=np.float64))

    np.testing.assert_allclose(result[0], q0[0])


def test_slerp_quaternion_vectorized_broadcasts_single_start_quaternion():
    q0 = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
    q1 = np.array(
        [
            [0.0, 0.0, 0.0, 1.0],
            [0.0, 0.0, np.sqrt(0.5), np.sqrt(0.5)],
        ],
        dtype=np.float64,
    )

    result = slerp_quaternion_xyzw(q0, q1, np.array([0.25, 0.5], dtype=np.float64))

    norms = np.linalg.norm(result, axis=1)
    np.testing.assert_allclose(norms, np.ones(2))
    np.testing.assert_allclose(result[0], q0)
