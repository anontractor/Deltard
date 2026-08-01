"""Stage-2 digit detector: read 6 digits off the normalized display crop.

A small YOLO (classes 0-9) detects each digit as its own box on the canonical
crop that stage 1 produces. We keep the 6 best boxes (with a light
non-adjacent-duplicate filter), sort left to right, and join their classes.
Unlike the classical decoder this learns digit *appearance*, so the bar
indicator, the "Secured by RSA" label, tilt, and glare don't derail it.
"""
from __future__ import annotations

from itertools import combinations
from pathlib import Path

import cv2
import numpy as np


class DigitDetector:
    def __init__(
        self,
        weights: str | Path,
        imgsz: int = 384,
        conf: float = 0.30,
        iou: float = 0.45,
        device: int | str = 0,
        n_digits: int = 6,
        nms_iou: float = 0.45,
        min_digit_conf: float = 0.0,
    ):
        from ultralytics import YOLO

        from .config import resolve_device

        weights = Path(weights)
        if not weights.exists():
            raise FileNotFoundError(f"Digit detector weights not found: {weights}")
        self.model = YOLO(str(weights))
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou
        self.device = resolve_device(device)
        self.n_digits = n_digits
        self.nms_iou = nms_iou
        self.min_digit_conf = min_digit_conf

    def read(self, canon_bgr):
        """Return (code_or_None, boxes). code is a string of n_digits, or None.

        boxes: list of (digit, conf, x0, y0, x1, y1) that were used, left-to-right.
        """
        res = self.model.predict(
            canon_bgr, imgsz=self.imgsz, conf=self.conf, iou=self.iou,
            device=self.device, verbose=False,
        )
        boxes = res[0].boxes
        dets = []
        if boxes is not None and len(boxes) > 0:
            for b in boxes:
                x0, y0, x1, y1 = b.xyxy[0].cpu().numpy()
                dets.append((
                    int(b.cls[0].cpu().numpy()),
                    float(b.conf[0].cpu().numpy()),
                    float(x0), float(y0), float(x1), float(y1),
                ))

        dets = self._nms(dets, self.nms_iou)    # class-agnostic: collapse stacked dups
        dets.sort(key=lambda d: -d[1])          # highest-conf first

        # The display is a known layout: 6 same-height digits in one row. Pick
        # the 6 detections that best satisfy that prior instead of blindly
        # trusting the top-6 confidences -- this rejects the decimal point, the
        # countdown bar, and merged boxes that would otherwise shift the code.
        keep = self._best_row(dets[: self.n_digits + 3])
        if keep is None:
            fallback = sorted(dets[: self.n_digits], key=lambda d: d[2])
            return None, fallback               # boxes kept for the debug preview
        if self.min_digit_conf and min(d[1] for d in keep) < self.min_digit_conf:
            return None, keep                   # one weak digit poisons the code
        code = "".join(str(d[0]) for d in keep)
        return code, keep

    def _best_row(self, cands):
        """Highest-total-confidence 6-subset of `cands` that forms a valid row,
        sorted left-to-right; None if no subset qualifies."""
        best, best_score = None, -1.0
        for idxs in combinations(range(len(cands)), self.n_digits):
            row = sorted((cands[i] for i in idxs), key=lambda d: d[2])
            if not self._row_ok(row):
                continue
            score = sum(d[1] for d in row)
            if score > best_score:
                best_score, best = score, row
        return best

    @staticmethod
    def _row_ok(row):
        """Geometric sanity for 6 left-to-right boxes: consistent heights,
        vertically aligned, no merged boxes, no heavy horizontal overlap.

        Thresholds calibrated on eval-set distributions (see git history):
        real rows have h/med_h in [0.91, 1.14] (p5..p95), y-offset p95 0.35,
        and w/h median 0.96 with p95 1.24 -- the canonical warp stretches
        digits nearly square, so only ~2x-wide boxes indicate a merge."""
        hs = sorted(d[5] - d[3] for d in row)
        med_h = (hs[2] + hs[3]) / 2
        if med_h <= 0 or hs[-1] > 1.55 * med_h or hs[0] < 0.55 * med_h:
            return False
        ycs = sorted((d[3] + d[5]) / 2 for d in row)
        med_yc = (ycs[2] + ycs[3]) / 2
        if any(abs((d[3] + d[5]) / 2 - med_yc) > 0.55 * med_h for d in row):
            return False
        if any((d[4] - d[2]) > 1.9 * (d[5] - d[3]) for d in row):
            return False
        for a, b in zip(row, row[1:]):
            overlap = a[4] - b[2]               # a.x1 - b.x0, boxes x-sorted
            if overlap > 0.40 * min(a[4] - a[2], b[4] - b[2]):
                return False
        return True

    @staticmethod
    def _iou(a, b):
        ax0, ay0, ax1, ay1 = a[2:6]
        bx0, by0, bx1, by1 = b[2:6]
        ix0, iy0 = max(ax0, bx0), max(ay0, by0)
        ix1, iy1 = min(ax1, bx1), min(ay1, by1)
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
        return inter / ua if ua > 0 else 0.0

    def _nms(self, dets, iou_thr):
        """Class-agnostic NMS: two boxes on one digit (different classes) can't
        both survive, but genuinely-adjacent digits (low IoU) are kept."""
        dets = sorted(dets, key=lambda d: -d[1])
        kept = []
        for d in dets:
            if all(self._iou(d, k) < iou_thr for k in kept):
                kept.append(d)
        return kept
