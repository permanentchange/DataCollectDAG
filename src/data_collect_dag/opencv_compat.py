"""Load system or pip OpenCV only when image processing needs it."""

from types import ModuleType


def load_cv2() -> ModuleType:
    try:
        import cv2
    except ModuleNotFoundError as exc:
        if exc.name != "cv2":
            raise
        raise RuntimeError(
            "OpenCV (cv2) is required for image processing. Use system OpenCV "
            "4.2 or newer, or install the optional dependency with "
            "python3 -m pip install 'opencv-python>=4.2,<4.12'. "
            "From a checkout, use python3 -m pip install '.[opencv]'."
        ) from exc
    return cv2
