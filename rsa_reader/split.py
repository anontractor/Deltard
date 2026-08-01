"""Split a canonical binarized screen into 6 digit cells and decode them.

The 6 digits form two groups of three with a wider gap in the middle. We:

  1. locate that middle gap as the widest low-ink *run* in a central band, and
     treat it as belonging to neither group;
  2. split each tight 3-digit group into three near-equal cells, snapping the
     two internal boundaries to local ink valleys;
  3. temporally regularise the boundaries against the previous frame (EMA) so
     the cells don't jitter frame to frame.

Digits on this display are near-equal width, so equal-split + valley-snap is
more robust and far easier to reason about than a free-form DP over cut
positions.
"""
from __future__ import annotations

import cv2
import numpy as np

from .sevenseg import segment_scores, template_scores

SMOOTH_KERNEL = 9
SNAP_RADIUS_FRAC = 0.06   # snap a boundary within +/- this frac of group width
BOUND_EMA_ALPHA = 0.4     # temporal smoothing of boundaries (0..1, higher=snappier)
MIN_CELL_FRAC = 0.12      # each cell >= this frac of its group width


def _gap_edges(band):
    """(gap_start, gap_end): boundaries of the widest low-ink run in `band`.

    The run is bracketed by the inner edges of the two digit groups. Using the
    widest low run (rather than a global argmin) is robust to a sparse leading
    digit like '1', which leaves its own deep valley.
    """
    band = np.asarray(band, dtype=np.float32)
    n = len(band)
    if n == 0:
        return 0, 0
    lo = band.min()
    thresh = lo + 0.20 * float(band.max() - lo)
    low = band <= thresh
    best = (0, n - 1)
    best_len = -1
    i = 0
    while i < n:
        if not low[i]:
            i += 1
            continue
        j = i
        while j < n and low[j]:
            j += 1
        if (j - i) > best_len:
            best_len = j - i
            best = (i, j - 1)
        i = j
    return best


def _split_group(col_blur, x0, x1, prev_internal, snap_radius):
    """Split [x0, x1] into 3 cells; return the two internal boundary x-positions.

    Starts from equal thirds (or the previous frame's boundaries), then snaps
    each to the nearest ink valley within +/- snap_radius.
    """
    width = x1 - x0
    if width < 6:
        return x0 + width // 3, x0 + 2 * width // 3
    min_cell = max(3, int(MIN_CELL_FRAC * width))

    if prev_internal is not None:
        anchors = [int(prev_internal[0]), int(prev_internal[1])]
    else:
        anchors = [x0 + width // 3, x0 + 2 * width // 3]

    bounds = []
    prev_edge = x0
    for i, anchor in enumerate(anchors):
        next_edge = x1 if i == len(anchors) - 1 else anchors[i + 1]
        lo = max(prev_edge + min_cell, anchor - snap_radius)
        hi = min(next_edge - min_cell, anchor + snap_radius)
        if hi <= lo:
            b = int(np.clip(anchor, prev_edge + min_cell, x1 - min_cell))
        else:
            seg = col_blur[lo:hi]
            b = lo + int(np.argmin(seg))
        bounds.append(b)
        prev_edge = b
    return bounds[0], bounds[1]


def split_digits(mask, prev_bounds=None):
    """Return (cells, bounds, digits).

    cells:  list of 6 (x0, x1) column ranges
    bounds: array [0, b1, b2, gap_start, gap_end, b4, b5, W] (8 edges)
    digits: list of 6 ints (best per-cell seven-seg guess; may be noise)
    """
    h, w = mask.shape
    col = mask.sum(axis=0).astype(np.float32)
    col_blur = cv2.blur(col[:, None], (SMOOTH_KERNEL, 1)).ravel()

    # Middle gap as a run inside a central band (robust to sparse leading digit).
    mid0, mid1 = int(0.35 * w), int(0.65 * w)
    if mid1 <= mid0 + 4:
        mid0, mid1 = w // 2 - 10, w // 2 + 10
    gs_rel, ge_rel = _gap_edges(col_blur[mid0:mid1])
    gap_start = gs_rel + mid0
    gap_end = ge_rel + mid0
    if gap_end <= gap_start:
        gap_end = gap_start + 1

    prev_left = prev_right = None
    if prev_bounds is not None and len(prev_bounds) == 8:
        prev_left = (prev_bounds[1], prev_bounds[2])
        prev_right = (prev_bounds[5], prev_bounds[6])

    lr = max(2, int(SNAP_RADIUS_FRAC * gap_start))
    rr = max(2, int(SNAP_RADIUS_FRAC * (w - gap_end)))
    b1, b2 = _split_group(col_blur, 0, gap_start, prev_left, lr)
    b4, b5 = _split_group(col_blur, gap_end, w, prev_right, rr)

    bounds = np.array([0, b1, b2, gap_start, gap_end, b4, b5, w], dtype=int)

    # Temporal EMA of the four internal boundaries.
    if prev_bounds is not None and len(prev_bounds) == 8:
        a = BOUND_EMA_ALPHA
        for i in (1, 2, 5, 6):
            bounds[i] = int(round((1 - a) * prev_bounds[i] + a * bounds[i]))
        # keep monotonic after smoothing
        order = [0, 1, 2, 3, 4, 5, 6, 7]
        for i in order[1:]:
            bounds[i] = max(int(bounds[i]), int(bounds[i - 1]) + 2)
        bounds = np.clip(bounds, 0, w)

    cells = [
        (int(bounds[0]), int(bounds[1])),
        (int(bounds[1]), int(bounds[2])),
        (int(bounds[2]), int(bounds[3])),
        (int(bounds[4]), int(bounds[5])),
        (int(bounds[5]), int(bounds[6])),
        (int(bounds[6]), int(bounds[7])),
    ]
    digits = [int(np.argmax(template_scores(segment_scores(mask[:, a:b]))))
              if (b - a) >= 6 else 0 for a, b in cells]
    return cells, bounds, digits
