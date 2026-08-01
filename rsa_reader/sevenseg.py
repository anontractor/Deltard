"""Seven-segment digit decoding from a binarized digit cell.

Segments are labelled a-g:

        aaa
       f   b
       f   b
        ggg
       e   c
       e   c
        ddd

Each segment is sampled from a small relative box inside the digit cell; a
segment is "on" if its mean ink exceeds a threshold. `template_scores` gives a
soft per-digit score (used by the DP splitter) that tolerates faint segments
better than an exact on/off lookup.
"""
from __future__ import annotations

import numpy as np

# On/off pattern (a,b,c,d,e,f,g) -> digit.
SEG_MAP = {
    (1, 1, 1, 1, 1, 1, 0): 0,
    (0, 1, 1, 0, 0, 0, 0): 1,
    (1, 1, 0, 1, 1, 0, 1): 2,
    (1, 1, 1, 1, 0, 0, 1): 3,
    (0, 1, 1, 0, 0, 1, 1): 4,
    (1, 0, 1, 1, 0, 1, 1): 5,
    (1, 0, 1, 1, 1, 1, 1): 6,
    (1, 1, 1, 0, 0, 0, 0): 7,
    (1, 1, 1, 1, 1, 1, 1): 8,
    (1, 1, 1, 1, 0, 1, 1): 9,
}

SEG_KEYS = ("a", "b", "c", "d", "e", "f", "g")

# Relative sampling boxes (cy, cx, half-height, half-width) in [0..1] of the cell.
SEG_REL = {
    "a": (0.12, 0.50, 0.10, 0.60),
    "b": (0.32, 0.84, 0.32, 0.12),
    "c": (0.72, 0.84, 0.32, 0.12),
    "d": (0.92, 0.50, 0.10, 0.60),
    "e": (0.72, 0.16, 0.32, 0.12),
    "f": (0.32, 0.16, 0.32, 0.12),
    "g": (0.52, 0.50, 0.10, 0.60),
}

SEG_ON_THRESH = 0.60

# Center segment 'g' is often faint under glare; weight it slightly less.
_SEG_WEIGHTS = np.array([1, 1, 1, 1, 1, 1, 0.8], dtype=np.float32)


def segment_scores(cell_mask) -> np.ndarray:
    """Mean ink (0..1) for each of the 7 segments in a binarized digit cell."""
    h, w = cell_mask.shape
    out = []
    for key in SEG_KEYS:
        cy, cx, hh, ww = SEG_REL[key]
        r0 = max(0, int((cy - hh / 2) * h))
        r1 = min(h, int((cy + hh / 2) * h))
        c0 = max(0, int((cx - ww / 2) * w))
        c1 = min(w, int((cx + ww / 2) * w))
        roi = cell_mask[r0:r1, c0:c1]
        out.append(0.0 if roi.size == 0 else float(roi.mean()) / 255.0)
    return np.array(out, dtype=np.float32)


def template_scores(seg7) -> np.ndarray:
    """Soft score (10,) per digit; higher = more likely. Tolerates faint segments."""
    seg7 = np.asarray(seg7, dtype=np.float32)
    logits_on = np.clip((seg7 - 0.5) * 2.5, -2.5, 2.5)   # reward confident 'on'
    logits_off = np.clip((0.5 - seg7) * 2.0, -2.0, 2.0)  # penalize leakage
    scores = np.full(10, -5.0, dtype=np.float32)
    for pat, d in SEG_MAP.items():
        pat = np.array(pat, dtype=np.float32)
        s_on = (logits_on * _SEG_WEIGHTS * pat).sum()
        s_off = (logits_off * _SEG_WEIGHTS * (1 - pat)).sum()
        scores[d] = s_on + s_off
    return scores


# A '1' lights only the two right verticals, so its ink is a narrow column that
# fills little of its cell's width; every other digit fills most of it. Detect
# it before segment sampling (otherwise a tightened '1' looks like an '8', its
# narrow box filling every segment window). Measured against cell width so it's
# independent of the canonical image's aspect ratio.
ONE_MAX_WIDTH_FRAC = 0.42  # ink width / cell width below this => digit is 1
ONE_MIN_HEIGHT_FRAC = 0.40  # ...and ink must be tall enough to be a digit


def _ink_bbox(cell_mask):
    """(x0, x1, y0, y1) inclusive ink bounds, ignoring sparse speckle.

    A column/row only counts toward the bounding box if its ink exceeds ~6% of
    the perpendicular dimension, so isolated salt-noise pixels don't inflate the
    box (which would break both the '1' test and the tightened sampling).
    """
    h, w = cell_mask.shape[:2]
    ink = cell_mask > 0
    col_ink = ink.sum(axis=0)
    row_ink = ink.sum(axis=1)
    col_thr = max(1, int(0.06 * h))
    row_thr = max(1, int(0.06 * w))
    cols = np.where(col_ink >= col_thr)[0]
    rows = np.where(row_ink >= row_thr)[0]
    if cols.size == 0 or rows.size == 0:
        return None
    return int(cols.min()), int(cols.max()), int(rows.min()), int(rows.max())


def decode_cell(cell_mask, thresh: float = SEG_ON_THRESH):
    """Strict lookup decode of one cell.

    The cell is first tightened to its ink bounding box so the decode is robust
    to loose or slightly-offset cell boundaries from the splitter. Returns
    (digit_or_None, confidence, raw_scores); `digit` is None when the on/off
    pattern is not a valid seven-seg digit.
    """
    bbox = _ink_bbox(cell_mask)
    if bbox is None:
        return None, 0.0, np.zeros(7, dtype=np.float32)
    x0, x1, y0, y1 = bbox
    w_ink, h_ink = (x1 - x0 + 1), (y1 - y0 + 1)
    cell_h, cell_w = cell_mask.shape[:2]

    narrow = cell_w > 0 and (w_ink / cell_w) < ONE_MAX_WIDTH_FRAC
    tall = cell_h > 0 and (h_ink / cell_h) > ONE_MIN_HEIGHT_FRAC
    if narrow and tall:
        return 1, 1.0, np.array([0, 1, 1, 0, 0, 0, 0], dtype=np.float32)

    tight = cell_mask[y0:y1 + 1, x0:x1 + 1]
    raw = segment_scores(tight)
    on = tuple(1 if s > thresh else 0 for s in raw)
    if on in SEG_MAP:
        conf = float(np.mean([abs(s - thresh) for s in raw]))
        return SEG_MAP[on], conf, raw
    return None, 0.0, raw
