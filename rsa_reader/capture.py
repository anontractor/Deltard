"""Webcam capture helper with sane Windows defaults."""
from __future__ import annotations

import time

import cv2

_BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
}


class Camera:
    """Context-managed webcam. Use as `with Camera(cfg) as cam: frame = cam.read()`."""

    # Optional camera property overrides -> cv2.CAP_PROP_*. Any listed under
    # camera.controls in config.yaml are applied on open. Leave unset to let the
    # camera auto-expose (usually best).
    _CONTROLS = {
        "brightness": cv2.CAP_PROP_BRIGHTNESS,
        "contrast": cv2.CAP_PROP_CONTRAST,
        "gain": cv2.CAP_PROP_GAIN,
        "exposure": cv2.CAP_PROP_EXPOSURE,
        "gamma": cv2.CAP_PROP_GAMMA,
        "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
    }

    def __init__(self, camera_cfg: dict):
        self.index = camera_cfg.get("index", 0)
        self.width = camera_cfg.get("width", 1920)
        self.height = camera_cfg.get("height", 1080)
        self.backend = _BACKENDS.get(camera_cfg.get("backend", "dshow"), cv2.CAP_DSHOW)
        self.warmup = camera_cfg.get("warmup_frames", 3)
        self.controls = camera_cfg.get("controls", {}) or {}
        self.cap = None

    def open(self):
        self.cap = cv2.VideoCapture(self.index, self.backend)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not self.cap.isOpened():
            raise IOError(f"Cannot open webcam at index {self.index}")
        # auto_exposure first (manual mode must be selected before exposure sticks)
        if "auto_exposure" in self.controls:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, float(self.controls["auto_exposure"]))
        for name, val in self.controls.items():
            if name != "auto_exposure" and name in self._CONTROLS:
                self.cap.set(self._CONTROLS[name], float(val))
        time.sleep(0.3)
        for _ in range(self.warmup):  # discard auto-exposure warmup frames
            self.cap.read()
        return self

    def read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError("Failed to capture frame from webcam.")
        return frame

    def read_optional(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.release()
