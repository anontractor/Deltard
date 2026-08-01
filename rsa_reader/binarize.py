"""Binarize a canonical screen crop into white digit-ink on black.

The RSA SecurID LCD shows *dark* seven-segment digits on a *light* backlit
background, so we invert. The refined warp still catches some dark bezel/shadow
at the edges; after inversion that would read as ink, so we drop connected
components that are bezel-shaped (spanning most of the canvas or very large) or
speckle-sized, keeping digit strokes.

If your token is the opposite polarity (light digits on dark), set
`decode.invert: false` in config.yaml.
"""
from __future__ import annotations

import cv2
import numpy as np


def binarize_digits(
    canon_bgr,
    invert: bool = True,
    block_size: int = 31,
    c: int = 7,
    min_area_frac: float = 0.0008,
    max_area_frac: float = 0.10,
    max_h_frac: float = 0.95,
    max_w_frac: float = 0.60,
):
    """Return a uint8 mask (0/255) with digit segments as 255."""
    gray = cv2.cvtColor(canon_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(2.5, (8, 8)).apply(gray)
    h, w = clahe.shape

    thresh_type = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    block = block_size if block_size % 2 == 1 else block_size + 1
    binary = cv2.adaptiveThreshold(
        clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, thresh_type, block, c
    )
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    # Keep only digit-shaped connected components.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    out = np.zeros_like(binary)
    hw = h * w
    for i in range(1, n):
        _, _, cw, ch, area = stats[i]
        if area < min_area_frac * hw:       # speckle
            continue
        if area > max_area_frac * hw:       # large blob -> bezel
            continue
        if ch > max_h_frac * h or cw > max_w_frac * w:  # spans canvas -> bezel/line
            continue
        if ch < 0.15 * h and cw > 0.15 * w:  # wide + flat -> bezel streak
            continue
        out[labels == i] = 255

    # Reconnect strokes broken by the open.
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    # Restrict to the dominant vertical ink band (drops stray top/bottom noise
    # so per-cell ink-bbox tightening isn't pulled off the digits).
    row_ink = (out > 0).sum(axis=1).astype(np.float32)
    if row_ink.max() > 0:
        rows = np.where(row_ink > 0.15 * row_ink.max())[0]
        if len(rows):
            band = np.zeros_like(out)
            band[rows.min():rows.max() + 1] = out[rows.min():rows.max() + 1]
            out = band
    return out
