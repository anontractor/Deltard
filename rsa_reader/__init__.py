"""rsa-reader: read the 6 digits off an RSA SecurID token from a webcam.

Pipeline: webcam frame -> YOLO screen detector -> quad refinement ->
KLT+EMA temporal smoothing -> perspective warp to a canonical screen ->
Otsu binarize -> DP 3-3 digit split -> seven-segment decode -> temporal vote.
"""

from .config import Config, load_config
from .reader import RSATokenReader

__all__ = ["Config", "load_config", "RSATokenReader"]
