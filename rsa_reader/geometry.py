"""Quad geometry helpers: corner ordering, area, perspective warp, drawing."""
from __future__ import annotations

import cv2
import numpy as np


def order_corners(pts) -> np.ndarray:
    """Order 4 points as [top-left, top-right, bottom-right, bottom-left]."""
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def quad_area(quad) -> float:
    """Shoelace area of a 4-point polygon."""
    q = np.asarray(quad, dtype=np.float32)
    a = 0.0
    for i in range(4):
        x1, y1 = q[i]
        x2, y2 = q[(i + 1) % 4]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5


def inflate_quad(quad, scale: float, imw: int, imh: int) -> np.ndarray:
    """Expand a quad about its centroid by `scale`, clipped to image bounds."""
    q = np.asarray(quad, dtype=np.float32)
    c = q.mean(axis=0, keepdims=True)
    q2 = c + (q - c) * float(scale)
    q2[:, 0] = np.clip(q2[:, 0], 0, imw - 1)
    q2[:, 1] = np.clip(q2[:, 1], 0, imh - 1)
    return order_corners(q2)


def warp_canonical(frame_bgr, quad, width: int, height: int) -> np.ndarray:
    """Perspective-warp the quad region to a fixed WxH canonical rectangle."""
    dst = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    m = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32), dst)
    return cv2.warpPerspective(frame_bgr, m, (width, height))


def quad_from_bbox(x1, y1, x2, y2) -> np.ndarray:
    """Axis-aligned bbox -> ordered quad (fallback when refinement fails)."""
    return order_corners(
        np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)
    )


def draw_quad(img, quad, color=(0, 255, 255), thickness=2) -> None:
    q = np.asarray(quad).astype(int)
    for i in range(4):
        cv2.line(img, tuple(q[i]), tuple(q[(i + 1) % 4]), color, thickness)
