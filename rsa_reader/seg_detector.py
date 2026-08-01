"""Stage-1 screen *segmentation* + orientation recovery.

A YOLO-seg model outputs the screen's mask; we fit its minimum-area rotated
rectangle and emit an upright, correctly-ordered quad (long axis horizontal).
Warping that quad de-rotates the digits, so a tilted or diagonal token still
produces a horizontal canonical crop for stage 2 -- the de-skew we couldn't do
reliably from a plain box detector.

There's a 180-degree ambiguity (which short edge is "up"); the reader resolves
it by trying the flip when a decode fails.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def oriented_quad_from_rect(rect) -> np.ndarray:
    """minAreaRect -> [TL, TR, BR, BL] with the long axis horizontal."""
    (cx, cy), (w, h), ang = rect
    if w < h:                      # make width the long side
        w, h = h, w
        ang += 90.0
    theta = np.deg2rad(ang)
    dx = np.array([np.cos(theta), np.sin(theta)], np.float32) * (w / 2)   # half long axis
    dy = np.array([-np.sin(theta), np.cos(theta)], np.float32) * (h / 2)  # half short axis
    c = np.array([cx, cy], np.float32)
    return np.array([c - dx - dy, c + dx - dy, c + dx + dy, c - dx + dy], np.float32)


class SegScreenDetector:
    def __init__(self, weights, imgsz=640, conf=0.25, iou=0.45, device=0):
        from ultralytics import YOLO

        from .config import resolve_device

        weights = Path(weights)
        if not weights.exists():
            raise FileNotFoundError(f"Seg weights not found: {weights}")
        self.model = YOLO(str(weights))
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou
        self.device = resolve_device(device)

    def detect(self, frame_bgr):
        """Return (oriented_quad, bbox, conf) for the best screen, or None.

        oriented_quad: 4x2 float32 [TL,TR,BR,BL], long axis horizontal.
        bbox: (x1,y1,x2,y2) axis-aligned, for HUD/tracking bootstrap.
        """
        res = self.model.predict(
            frame_bgr, imgsz=self.imgsz, conf=self.conf, iou=self.iou,
            device=self.device, verbose=False,
        )
        r = res[0]
        if r.masks is None or len(r.masks) == 0:
            return None

        # Best instance by detection confidence.
        confs = r.boxes.conf.cpu().numpy()
        i = int(np.argmax(confs))
        conf = float(confs[i])
        poly = r.masks.xy[i]                       # Nx2 polygon in pixel coords
        if poly is None or len(poly) < 3:
            return None
        poly = np.asarray(poly, dtype=np.float32)

        rect = cv2.minAreaRect(poly)
        quad = oriented_quad_from_rect(rect)
        x0, y0 = poly.min(0)
        x1, y1 = poly.max(0)
        return quad, (float(x0), float(y0), float(x1), float(y1)), conf
