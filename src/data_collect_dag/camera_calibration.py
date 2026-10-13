from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np


def read_distortion_params(camera_params: Dict[str, Any]) -> Tuple[str, np.ndarray]:
    """Validate the explicit model and its coefficients without guessing a model."""
    if not isinstance(camera_params, dict):
        raise ValueError("camera calibration must be a mapping")
    if "is_fisheye" in camera_params:
        raise ValueError("is_fisheye is no longer supported; replace it with an explicit dist_model")
    model = camera_params.get("dist_model")
    if not isinstance(model, str) or not model:
        raise ValueError("camera calibration missing valid dist_model")
    if model != "equidistant":
        raise ValueError(f"unsupported camera dist_model: {model!r}; supported: equidistant")
    try:
        raw = np.asarray(camera_params["dist_coeffs"])
        values = np.asarray(camera_params["dist_coeffs"], dtype=object).reshape(-1)
        if raw.dtype.kind not in "fiu" or any(isinstance(value, (bool, np.bool_)) for value in values):
            raise ValueError("coefficients must be numbers")
        coeffs = raw.astype(np.float64).reshape(-1, 1)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("camera calibration missing valid dist_coeffs") from exc
    if coeffs.shape != (4, 1) or not np.all(np.isfinite(coeffs)):
        raise ValueError("equidistant dist_coeffs must contain four finite values")
    return model, coeffs
