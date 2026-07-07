from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np


def normalize_quaternion_xyzw(quaternion: np.ndarray) -> np.ndarray:
    quat = np.asarray(quaternion, dtype=np.float64)
    norm = np.linalg.norm(quat)
    if norm == 0.0:
        raise ValueError("quaternion norm must be positive")
    return quat / norm


def quaternion_to_rotation_matrix(quaternion_xyzw: Sequence[float]) -> np.ndarray:
    x, y, z, w = normalize_quaternion_xyzw(np.asarray(quaternion_xyzw, dtype=np.float64))
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float64,
    )


def quaternion_to_rotation_matrix_many(quaternions_xyzw: np.ndarray) -> np.ndarray:
    quats = np.asarray(quaternions_xyzw, dtype=np.float64)
    if quats.ndim != 2 or quats.shape[1] != 4:
        raise ValueError("quaternions_xyzw must have shape Nx4")
    norms = np.linalg.norm(quats, axis=1, keepdims=True)
    if np.any(norms == 0.0):
        raise ValueError("quaternion norm must be positive")
    q = quats / norms
    x = q[:, 0]
    y = q[:, 1]
    z = q[:, 2]
    w = q[:, 3]
    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z
    matrices = np.empty((q.shape[0], 3, 3), dtype=np.float64)
    matrices[:, 0, 0] = 1.0 - 2.0 * (yy + zz)
    matrices[:, 0, 1] = 2.0 * (xy - wz)
    matrices[:, 0, 2] = 2.0 * (xz + wy)
    matrices[:, 1, 0] = 2.0 * (xy + wz)
    matrices[:, 1, 1] = 1.0 - 2.0 * (xx + zz)
    matrices[:, 1, 2] = 2.0 * (yz - wx)
    matrices[:, 2, 0] = 2.0 * (xz - wy)
    matrices[:, 2, 1] = 2.0 * (yz + wx)
    matrices[:, 2, 2] = 1.0 - 2.0 * (xx + yy)
    return matrices


def build_transform_matrix(position_xyz: Sequence[float], quaternion_xyzw: Sequence[float]) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = quaternion_to_rotation_matrix(quaternion_xyzw)
    matrix[:3, 3] = np.asarray(position_xyz, dtype=np.float64)
    return matrix


def invert_transform(matrix: np.ndarray) -> np.ndarray:
    rotation = matrix[:3, :3]
    translation = matrix[:3, 3]
    inv = np.eye(4, dtype=np.float64)
    inv[:3, :3] = rotation.T
    inv[:3, 3] = -rotation.T @ translation
    return inv


def transform_points(points_xyz: np.ndarray, transform: np.ndarray) -> np.ndarray:
    if points_xyz.size == 0:
        return np.empty((0, 3), dtype=np.float64)
    homogeneous = np.concatenate([points_xyz.astype(np.float64), np.ones((points_xyz.shape[0], 1), dtype=np.float64)], axis=1)
    transformed = homogeneous @ transform.T
    return transformed[:, :3]


def slerp_quaternion_xyzw(q0: np.ndarray, q1: np.ndarray, t: np.ndarray) -> np.ndarray:
    q0n = np.asarray(q0, dtype=np.float64)
    q1n = np.asarray(q1, dtype=np.float64)
    tt = np.asarray(t, dtype=np.float64)
    if q0n.ndim == 1:
        q0n = np.broadcast_to(q0n, (tt.shape[0], 4))
    if q1n.ndim == 1:
        q1n = np.broadcast_to(q1n, (tt.shape[0], 4))
    result = np.empty_like(q0n)
    for idx, factor in enumerate(tt):
        qa = normalize_quaternion_xyzw(q0n[idx])
        qb = normalize_quaternion_xyzw(q1n[idx])
        dot = float(np.dot(qa, qb))
        if dot < 0.0:
            qb = -qb
            dot = -dot
        dot = min(1.0, max(-1.0, dot))
        if dot > 0.9995:
            result[idx] = normalize_quaternion_xyzw(qa + factor * (qb - qa))
            continue
        theta_0 = math.acos(dot)
        theta = theta_0 * factor
        q2 = normalize_quaternion_xyzw(qb - qa * dot)
        result[idx] = qa * math.cos(theta) + q2 * math.sin(theta)
    return result
