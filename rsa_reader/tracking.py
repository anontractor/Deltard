"""Frame-to-frame quad tracking (Lucas-Kanade optical flow) + EMA smoothing.

The detector is comparatively slow and jittery; tracking the four corners
between detections keeps the quad steady and cheap. A forward-backward flow
check plus a per-corner drift cap reject bad tracks so we re-detect instead of
drifting off the screen.
"""
from __future__ import annotations

import cv2
import numpy as np

EMA_ALPHA = 0.35
MAX_CORNER_DRIFT = 8.0  # px per frame a corner may move under tracking
_FB_ERROR_TOL = 1.5     # forward-backward consistency tolerance (px)


def track_quad(prev_gray, curr_gray, quad):
    """LK-track the 4 corners. Returns new quad, or None if the track is unreliable."""
    p0 = np.asarray(quad, dtype=np.float32).reshape(-1, 1, 2)
    lk = dict(
        winSize=(17, 17),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.02),
    )
    p1, st, _ = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, p0, None, **lk)
    p0r, _, _ = cv2.calcOpticalFlowPyrLK(curr_gray, prev_gray, p1, None, **lk)

    st = st.reshape(-1).astype(bool)
    fb = np.linalg.norm(p0r - p0, axis=2).reshape(-1)
    valid = st & (fb < _FB_ERROR_TOL)
    if valid.sum() < 3:
        return None

    new_quad = p1.reshape(-1, 2)
    drift = np.linalg.norm(new_quad - np.asarray(quad, dtype=np.float32), axis=1)
    if (drift > MAX_CORNER_DRIFT).any():
        return None
    return new_quad


def ema_quad(prev_quad, new_quad, alpha: float = EMA_ALPHA):
    """Exponential moving average of quad corners for temporal smoothing."""
    new_quad = np.asarray(new_quad, dtype=np.float32)
    if prev_quad is None:
        return new_quad.copy()
    return (1 - alpha) * np.asarray(prev_quad, dtype=np.float32) + alpha * new_quad
