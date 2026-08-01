"""Refine a YOLO bounding box into a tight, possibly-rotated screen quad.

Two strategies, tried in order:
  1. Scharr edges -> HoughLinesP -> cluster into horizontal/vertical -> fit 4
     lines -> intersect for corners.
  2. Otsu contour -> approxPolyDP for the largest convex 4-gon (fallback).

Refined quads are validated against the source bbox (area / aspect / inset)
so a bad refine falls back to the axis-aligned box instead of corrupting the
warp.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .geometry import order_corners, quad_area

# Guardrails for accepting a refined quad relative to the source bbox.
MIN_AREA_FRAC_VS_BBOX = 0.45
MAX_INSET_FRAC_VS_BBOX = 0.18
AR_MIN, AR_MAX = 2.0, 4.8


def _line_angle_deg(x1, y1, x2, y2) -> float:
    return abs(math.degrees(math.atan2(y2 - y1, x2 - x1)))


def _fit_line(segments):
    pts = np.float32(
        [(l[0], l[1]) for l in segments] + [(l[2], l[3]) for l in segments]
    ).reshape(-1, 1, 2)
    # cv2.fitLine returns shape (4, 1); ravel so float() gets 0-d scalars
    # (float() on a shape-(1,) array raises under numpy 2.x).
    vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
    return float(vx), float(vy), float(x0), float(y0)


def _intersect_lines(l1, l2):
    vx1, vy1, x01, y01 = l1
    vx2, vy2, x02, y02 = l2
    denom = vx1 * vy2 - vy1 * vx2
    if abs(denom) < 1e-6:
        return None
    t = ((x02 - x01) * vy2 - (y02 - y01) * vx2) / denom
    return np.array([x01 + t * vx1, y01 + t * vy1], dtype=np.float32)


def _refine_edges(gray, roi_xyxy):
    x1, y1, x2, y2 = [int(v) for v in roi_xyxy]
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    h, w = roi.shape[:2]
    if h < 20 or w < 60:
        return None

    work = cv2.createCLAHE(2.0, (8, 8)).apply(roi)
    gx = cv2.Scharr(work, cv2.CV_32F, 1, 0)
    gy = cv2.Scharr(work, cv2.CV_32F, 0, 1)
    mag8 = cv2.convertScaleAbs(cv2.magnitude(gx, gy))
    edges = cv2.threshold(mag8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)

    min_len = int(0.40 * min(w, h))
    max_gap = int(0.05 * max(w, h))
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=55, minLineLength=min_len, maxLineGap=max_gap
    )
    if lines is not None:
        horiz, vert = [], []
        for l in lines.reshape(-1, 4):
            a = _line_angle_deg(*l)
            if a < 25 or a > 155:
                horiz.append(l)
            elif 65 < a < 115:
                vert.append(l)
        if horiz and vert:
            horiz_sorted = sorted(horiz, key=lambda L: (L[1] + L[3]) / 2.0)
            vert_sorted = sorted(vert, key=lambda L: (L[0] + L[2]) / 2.0)
            top_group = horiz_sorted[: max(1, len(horiz_sorted) // 2)]
            bot_group = horiz_sorted[max(1, len(horiz_sorted) // 2):] or horiz_sorted
            left_group = vert_sorted[: max(1, len(vert_sorted) // 2)]
            right_group = vert_sorted[max(1, len(vert_sorted) // 2):] or vert_sorted

            top_line = _fit_line(top_group)
            bottom_line = _fit_line(bot_group)
            left_line = _fit_line(left_group)
            right_line = _fit_line(right_group)

            corners = [
                _intersect_lines(top_line, left_line),
                _intersect_lines(top_line, right_line),
                _intersect_lines(bottom_line, right_line),
                _intersect_lines(bottom_line, left_line),
            ]
            if all(c is not None for c in corners):
                quad = np.stack(corners, axis=0)
                if (
                    np.all(quad[:, 0] > -5)
                    and np.all(quad[:, 0] < w + 5)
                    and np.all(quad[:, 1] > -5)
                    and np.all(quad[:, 1] < h + 5)
                ):
                    quad[:, 0] += x1
                    quad[:, 1] += y1
                    return order_corners(quad)

    # Contour fallback
    blur = cv2.GaussianBlur(roi, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv = 255 - th
    inv = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:8]
        best, best_score = None, -1.0
        for c in cnts:
            area = cv2.contourArea(c)
            if area < 0.1 * w * h:
                continue
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                rect = cv2.minAreaRect(c)
                rw, rh = rect[1]
                if rw < 1 or rh < 1:
                    continue
                rect_score = area / (rw * rh + 1e-6)
                ar = max(rw, rh) / (min(rw, rh) + 1e-6)
                score = rect_score - 0.05 * abs(ar - 3.0)
                if score > best_score:
                    best_score = score
                    best = approx.reshape(-1, 2).astype(np.float32)
        if best is not None:
            best[:, 0] += x1
            best[:, 1] += y1
            return order_corners(best)

    return None


def _validate(candidate, bbox_xyxy) -> bool:
    x1, y1, x2, y2 = bbox_xyxy
    bw, bh = (x2 - x1), (y2 - y1)
    bbox_area = max(1.0, bw * bh)
    if quad_area(candidate) < MIN_AREA_FRAC_VS_BBOX * bbox_area:
        return False
    edges = [np.linalg.norm(candidate[(i + 1) % 4] - candidate[i]) for i in range(4)]
    ar = max(edges) / max(1e-6, min(edges))
    if not (AR_MIN <= ar <= AR_MAX):
        return False
    max_inset_x = MAX_INSET_FRAC_VS_BBOX * bw
    max_inset_y = MAX_INSET_FRAC_VS_BBOX * bh
    for cx, cy in candidate:
        deep_x = (cx - x1) > max_inset_x and (x2 - cx) > max_inset_x
        deep_y = (cy - y1) > max_inset_y and (y2 - cy) > max_inset_y
        if deep_x and deep_y:
            return False
    return True


def refine_quad(gray, roi_xyxy):
    """Return a validated refined quad, or None to signal 'use the bbox'."""
    quad = _refine_edges(gray, roi_xyxy)
    if quad is None:
        return None
    return quad if _validate(quad, roi_xyxy) else None
