"""Top-level RSA token reader: frame in, stable 6-digit code out.

Per-frame pipeline:
    detect screen -> refine to quad -> KLT track + EMA smooth ->
    inflate + warp to canonical -> CLAHE + Otsu -> DP 3-3 split ->
    strict seven-seg decode per cell -> temporal vote.

Only frames where all 6 cells decode to a valid seven-seg digit produce a
candidate code; a code is *published* only once it agrees across enough recent
frames, which rejects motion blur, glare, and mid-refresh transitions.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .binarize import binarize_digits
from .config import Config, load_config
from .detector import ScreenDetector
from .geometry import draw_quad, inflate_quad, quad_from_bbox, warp_canonical
from .quad_refine import refine_quad
from .sevenseg import decode_cell
from .split import split_digits
from .tracking import ema_quad, track_quad

REDETECT_EVERY = 20  # force a fresh detection at least this often


def _align_quad(new, ref):
    """Roll `new`'s corner order to best match `ref` (handles the 180 flip)."""
    new = np.asarray(new, dtype=np.float32)
    ref = np.asarray(ref, dtype=np.float32)
    best, best_d = new, np.inf
    for k in range(4):
        cand = np.roll(new, k, axis=0)
        d = float(np.linalg.norm(cand - ref, axis=1).sum())
        if d < best_d:
            best_d, best = d, cand
    return best


@dataclass
class FrameResult:
    code: str | None = None          # this frame's decode, or None
    stable: str | None = None        # published code once temporally stable
    is_stable: bool = False
    quad: np.ndarray | None = None
    canonical: np.ndarray | None = None   # warped BGR screen
    mask: np.ndarray | None = None        # binarized / preprocessed canonical
    bounds: np.ndarray | None = None
    cell_confs: list | None = None
    detected: bool = False
    det_bbox: tuple | None = None    # stage-1 box (x1, y1, x2, y2, conf)
    digit_boxes: list | None = None  # stage-2 (digit, conf, x0, y0, x1, y1) in canon coords


class RSATokenReader:
    def __init__(self, config: Config | None = None, detector: ScreenDetector | None = None):
        self.cfg = config or load_config()
        d = self.cfg.detector
        self.stage1_mode = d.get("mode", "detect")  # "detect" (box) | "segment" (mask+de-skew)
        self.seg_detector = None
        self.detector = None
        # The box detector localizes the token on the full frame. In segment
        # mode it also bootstraps the crop the seg model runs on (the seg data is
        # close-ups, so it needs the token large in-frame).
        self.detector = detector or ScreenDetector(
            weights=self.cfg.weights_path(),
            imgsz=d.get("imgsz", 896),
            conf=d.get("conf", 0.20),
            iou=d.get("iou", 0.45),
            min_box_area_frac=d.get("min_box_area_frac", 0.0005),
            device=d.get("device", 0),
        )
        if self.stage1_mode == "segment":
            from .seg_detector import SegScreenDetector
            sw = d.get("seg_weights", "models/screen_seg.pt")
            swp = sw if Path(sw).is_absolute() else (self.cfg.root / sw)
            self.seg_detector = SegScreenDetector(
                weights=swp, imgsz=d.get("seg_imgsz", 640),
                conf=d.get("seg_conf", 0.25), iou=d.get("iou", 0.45),
                device=d.get("device", 0),
            )
            self.seg_crop_pad = d.get("seg_crop_pad", 2.75)  # square-crop side / box size
            self.seg_ema_alpha = d.get("seg_ema_alpha", 0.6)  # higher = more responsive
        can = self.cfg.canonical
        self.canon_w = can.get("width", 480)
        self.canon_h = can.get("height", 160)
        self.inflate = can.get("inflate_scale", 1.08)

        dec = self.cfg.decode
        self.seg_thresh = dec.get("seg_on_thresh", 0.60)
        self.n_digits = dec.get("expected_digits", 6)
        self.invert = dec.get("invert", True)
        self.method = dec.get("method", "classical")  # "classical" | "detector"
        self.auto_gain = dec.get("auto_gain", True)

        self.digit_detector = None
        if self.method == "detector":
            from .digit_detector import DigitDetector
            dd = self.cfg.raw.get("digit_detector", {})
            w = dd.get("weights", "models/digit_detector.pt")
            wpath = w if Path(w).is_absolute() else (self.cfg.root / w)
            self.digit_detector = DigitDetector(
                weights=wpath,
                imgsz=dd.get("imgsz", 384),
                conf=dd.get("conf", 0.30),
                iou=dd.get("iou", 0.45),
                device=dd.get("device", self.cfg.detector.get("device", 0)),
                n_digits=self.n_digits,
                nms_iou=dd.get("nms_iou", 0.6),  # only collapse near-identical (stacked) boxes
                min_digit_conf=dd.get("min_digit_conf", 0.0),
            )

        st = self.cfg.stability
        self.hist = deque(maxlen=st.get("history", 12))
        self.need_matches = st.get("need_matches", 8)
        self.need_window = st.get("need_window", 10)
        self.timeout_s = st.get("timeout_seconds", 20)

        self._reset_state()

    def _reset_state(self):
        self.last_quad = None
        self.prev_gray = None
        self.last_bounds = None
        self.last_angle = None
        self.frame_count = 0
        self.hist.clear()

    # -- per-frame ---------------------------------------------------------
    def process_frame(self, frame_bgr) -> FrameResult:
        curr_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        imh, imw = frame_bgr.shape[:2]
        result = FrameResult()

        canon = None
        if self.stage1_mode == "segment":
            # Rotate-then-box: use the seg mask only for the angle, straighten the
            # frame, then run the proven box pipeline (which frames the digits the
            # way stage 2 was trained on).
            canon = self._deskew_canonical(frame_bgr, result)
        else:
            # Box mode: KLT-track between periodic re-detections to save compute
            # and smooth jitter.
            tracked = None
            if self.last_quad is not None and self.prev_gray is not None:
                tracked = track_quad(self.prev_gray, curr_gray, self.last_quad)
            need_redetect = tracked is None or (self.frame_count % REDETECT_EVERY == 0)
            quad_update = tracked
            if need_redetect:
                best = self.detector.detect_best(frame_bgr)
                if best is not None:
                    x1, y1, x2, y2, _conf = best
                    result.detected = True
                    result.det_bbox = (x1, y1, x2, y2, _conf)
                    quad = refine_quad(curr_gray, (x1, y1, x2, y2))
                    if quad is None:
                        quad = quad_from_bbox(x1, y1, x2, y2)
                    quad_update = quad
            if quad_update is not None:
                self.last_quad = ema_quad(self.last_quad, quad_update)
            if self.last_quad is not None:
                result.quad = self.last_quad
                canon = warp_canonical(
                    frame_bgr, inflate_quad(self.last_quad, self.inflate, imw, imh),
                    self.canon_w, self.canon_h,
                )

        # 2) Decode.
        if canon is not None:
            result.canonical = canon
            if self.method == "detector":
                self._decode_detector(canon, result)
            else:
                self._decode_classical(canon, result)

        # 3) Temporal vote.
        result.is_stable, result.stable = self._vote(result.code)

        self.prev_gray = curr_gray
        self.frame_count += 1
        return result

    def _seg_angle(self, frame_bgr, bbox):
        """Screen tilt in degrees from the seg mask, plus the oriented quad, or
        (None, None). Seg runs on a square crop the box localizes (seg was
        trained on close-ups, so the token must fill the crop)."""
        x1, y1, x2, y2 = bbox
        imh, imw = frame_bgr.shape[:2]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        half = self.seg_crop_pad * max(x2 - x1, y2 - y1) / 2
        cx0 = int(max(0, cx - half)); cx1 = int(min(imw, cx + half))
        cy0 = int(max(0, cy - half)); cy1 = int(min(imh, cy + half))
        crop = frame_bgr[cy0:cy1, cx0:cx1]
        if crop.size == 0:
            return None, None
        seg = self.seg_detector.detect(crop)
        if seg is None:
            return None, None
        quad = np.asarray(seg[0], np.float32) + np.array([cx0, cy0], np.float32)
        tl, tr = quad[0], quad[1]
        angle = float(np.degrees(np.arctan2(tr[1] - tl[1], tr[0] - tl[0])))
        # Normalize to the minimal tilt [-90, 90); a rectangle at 167 deg is the
        # same as -13. The up/down (180) ambiguity is resolved by the decode's
        # flip fallback, so we only ever apply the small rotation.
        angle = ((angle + 90.0) % 180.0) - 90.0
        return angle, quad

    @staticmethod
    def _quad_angle(quad):
        """Minimal tilt [-90,90) of a quad's long (TL->TR) edge."""
        tl, tr = quad[0], quad[1]
        a = float(np.degrees(np.arctan2(tr[1] - tl[1], tr[0] - tl[0])))
        return ((a + 90.0) % 180.0) - 90.0

    def _deskew_canonical(self, frame_bgr, result):
        """Straighten the frame using the seg angle, then box-warp the digits.

        The box detector handles upright/mild tilt; the seg model handles any
        angle (it was trained with rotation). So: box-localize when we can, else
        fall back to seg on the full frame for steep angles the box misses.
        """
        imh, imw = frame_bgr.shape[:2]
        best = self.detector.detect_best(frame_bgr)
        if best is not None:
            x1, y1, x2, y2, conf = best
            result.detected = True
            result.det_bbox = (x1, y1, x2, y2, conf)
            angle, seg_quad = self._seg_angle(frame_bgr, (x1, y1, x2, y2))
            if angle is None:  # box but no seg: plain upright box warp
                q = quad_from_bbox(x1, y1, x2, y2)
                result.quad = q
                return warp_canonical(frame_bgr, inflate_quad(q, self.inflate, imw, imh),
                                      self.canon_w, self.canon_h)
            center = ((x1 + x2) / 2, (y1 + y2) / 2)
        else:
            # Steep angle: box detector missed it. Seg on the full frame.
            seg = self.seg_detector.detect(frame_bgr)
            if seg is None:
                return None
            seg_quad, bbox, conf = seg
            result.detected = True
            result.det_bbox = (*bbox, conf)
            angle = self._quad_angle(seg_quad)
            center = tuple(np.asarray(seg_quad, np.float32).mean(axis=0))

        result.quad = seg_quad  # oriented screen quad, for the preview

        # Smooth the angle (EMA) so the straightened crop doesn't jitter.
        if self.last_angle is None:
            self.last_angle = angle
        else:
            a = self.seg_ema_alpha
            self.last_angle = (1 - a) * self.last_angle + a * angle
        m = cv2.getRotationMatrix2D(center, self.last_angle, 1.0)
        frame_up = cv2.warpAffine(frame_bgr, m, (imw, imh))

        # Re-detect the digit region on the now-upright token -> correct framing.
        best2 = self.detector.detect_best(frame_up)
        if best2 is None:
            return None
        bx1, by1, bx2, by2, _ = best2
        q2 = quad_from_bbox(bx1, by1, bx2, by2)
        return warp_canonical(frame_up, inflate_quad(q2, self.inflate, imw, imh),
                              self.canon_w, self.canon_h)

    def _decode_classical(self, canon, result):
        mask = binarize_digits(canon, invert=self.invert)
        result.mask = mask
        cells, bounds, _dp = split_digits(mask, prev_bounds=self.last_bounds)
        self.last_bounds = bounds
        result.bounds = bounds
        digits, confs = [], []
        for x0, x1 in cells:
            d, c, _raw = decode_cell(mask[:, x0:x1], self.seg_thresh)
            digits.append(d)
            confs.append(c)
        result.cell_confs = confs
        if len(digits) == self.n_digits and all(d is not None for d in digits):
            result.code = "".join(str(d) for d in digits)

    # The flipped read must beat the upright read's mean digit confidence by
    # this much before we switch orientation. Measured on the eval set: the
    # upright crop is right in ~99% of frames and confidence gaps between a
    # true read and a plausible upside-down misread overlap below ~0.12, so
    # the incumbent orientation wins anything closer than this.
    FLIP_CONF_MARGIN = 0.15

    def _decode_detector(self, canon, result):
        # Feed grayscale (3-channel) to match how the detector was trained,
        # optionally auto-gained to pull dim crops into the trained range.
        from .preprocess import prep_gray3
        gray3 = prep_gray3(canon, enabled=self.auto_gain)
        code, boxes = self.digit_detector.read(gray3)

        # Resolve the 180-degree orientation ambiguity (segment mode). Upside-
        # down seven-seg digits can read as *valid* digits, so a flipped crop
        # sometimes yields a confident wrong upright read; conversely the
        # flipped read of a correct crop is pure noise. Policy from eval data:
        # keep the upright read unless it failed outright, or the flipped read
        # wins by a decisive confidence margin.
        if self.stage1_mode == "segment":
            flipped = cv2.rotate(gray3, cv2.ROTATE_180)
            fcode, fboxes = self.digit_detector.read(flipped)
            if fcode is not None:
                mean = lambda bs: sum(b[1] for b in bs) / len(bs)  # noqa: E731
                if code is None or mean(fboxes) >= mean(boxes) + self.FLIP_CONF_MARGIN:
                    gray3, code, boxes = flipped, fcode, fboxes

        result.mask = cv2.cvtColor(gray3, cv2.COLOR_BGR2GRAY)
        result.code = code
        result.digit_boxes = boxes
        result.cell_confs = [round(b[1], 2) for b in boxes]

    def _vote(self, code):
        if code is None:
            return False, None
        self.hist.append(code)
        window = list(self.hist)[-self.need_window:]
        matches = sum(1 for c in window if c == code)
        if matches >= self.need_matches:
            return True, code
        return False, None

    # -- high-level --------------------------------------------------------
    def read(self, camera=None, timeout_s: float | None = None) -> str:
        """Open the webcam, read frames until a stable code emerges. Returns it.

        Raises TimeoutError if no stable code appears within the timeout.
        """
        from .capture import Camera

        timeout_s = self.timeout_s if timeout_s is None else timeout_s
        self._reset_state()
        own_cam = camera is None
        cam = camera or Camera(self.cfg.camera).open()
        try:
            deadline = None  # set on first successful frame read
            while True:
                frame = cam.read_optional()
                if frame is None:
                    continue
                if deadline is None:
                    deadline = time.monotonic() + timeout_s
                res = self.process_frame(frame)
                if res.is_stable:
                    return res.stable
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"No stable {self.n_digits}-digit code within {timeout_s}s. "
                        "Check lighting / camera framing."
                    )
        finally:
            if own_cam:
                cam.release()
