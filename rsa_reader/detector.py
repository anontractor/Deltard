"""YOLO screen/number-region detector wrapper (stage 1)."""
from __future__ import annotations

from pathlib import Path

import numpy as np


class ScreenDetector:
    """Loads the trained YOLO model and returns the best screen candidate."""

    def __init__(
        self,
        weights: str | Path,
        imgsz: int = 896,
        conf: float = 0.20,
        iou: float = 0.45,
        min_box_area_frac: float = 0.0005,
        device: int | str = 0,
    ):
        # Import here so the rest of the package (pure CV utils) stays importable
        # without ultralytics/torch installed.
        from ultralytics import YOLO

        from .config import resolve_device

        weights = Path(weights)
        if not weights.exists():
            raise FileNotFoundError(f"Detector weights not found: {weights}")
        self.model = YOLO(str(weights))
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou
        self.min_box_area_frac = min_box_area_frac
        self.device = resolve_device(device)

    def detect_best(self, frame_bgr):
        """Return (x1, y1, x2, y2, conf) of the best screen box, or None."""
        res = self.model.predict(
            frame_bgr,
            imgsz=self.imgsz,
            conf=self.conf,
            iou=self.iou,
            device=self.device,
            verbose=False,
        )
        boxes = res[0].boxes
        if boxes is None or len(boxes) == 0:
            return None

        imh, imw = frame_bgr.shape[:2]
        best = None
        best_score = -1.0
        for b in boxes:
            x1, y1, x2, y2 = b.xyxy[0].cpu().numpy()
            conf = float(b.conf[0].cpu().numpy())
            w = max(1.0, x2 - x1)
            h = max(1.0, y2 - y1)
            area_frac = (w * h) / (imw * imh)
            if area_frac < self.min_box_area_frac or conf < self.conf:
                continue
            ar = max(w, h) / (min(w, h) + 1e-6)
            # Prefer confident, reasonably-sized, ~3:1 boxes.
            score = conf + 0.5 * area_frac - 0.02 * abs(ar - 3.0)
            if score > best_score:
                best_score = score
                best = (float(x1), float(y1), float(x2), float(y2), conf)
        return best
