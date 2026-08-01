"""Adaptive brightness/contrast normalization for dim frames.

The digit detector was trained on crops spanning a range of brightness/gamma,
but very dim real crops can still fall outside it. Normalizing a crop toward a
consistent brightness and local contrast pulls dim inputs back into the trained
range. This helps the dim-but-not-dark margin; it cannot recover a frame with no
signal (a reflective LCD in the dark).
"""
from __future__ import annotations

import cv2
import numpy as np


def auto_gain_gray(gray, target_mean: float = 130.0, clahe_clip: float = 2.5):
    """Normalize a single-channel image: percentile stretch + CLAHE + mean pin.

    Only brightens/normalizes; a frame that is already bright and contrasty comes
    back essentially unchanged.
    """
    g = gray.astype(np.float32)
    lo, hi = np.percentile(g, 2), np.percentile(g, 98)
    if hi - lo > 1e-3:
        g = np.clip((g - lo) * 255.0 / (hi - lo), 0, 255)
    g = g.astype(np.uint8)
    g = cv2.createCLAHE(clahe_clip, (8, 8)).apply(g)
    # gentle pull toward target mean (gamma), capped so we never over-boost
    m = min(254.0, max(1.0, float(g.mean())))  # avoid log(1)=0 at the extremes
    denom = np.log(m / 255.0)
    if abs(denom) > 1e-6:
        gamma = float(np.clip(np.log(target_mean / 255.0) / denom, 0.5, 2.0))
        g = np.clip(((g / 255.0) ** gamma) * 255.0, 0, 255).astype(np.uint8)
    return g


def prep_gray3(canon_bgr, enabled: bool = True):
    """Canonical BGR crop -> 3-channel grayscale for the detector, optionally
    auto-gained."""
    gray = cv2.cvtColor(canon_bgr, cv2.COLOR_BGR2GRAY)
    if enabled:
        gray = auto_gain_gray(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
