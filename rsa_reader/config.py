"""Load non-secret config (config.yaml) and secrets (secrets.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional at import time; only needed for secrets
    load_dotenv = None

# Repo root = parent of this package directory.
REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    """Parsed config.yaml, plus resolved absolute paths and secrets access."""

    raw: dict[str, Any] = field(default_factory=dict)
    root: Path = REPO_ROOT

    # --- convenience accessors -------------------------------------------
    @property
    def camera(self) -> dict[str, Any]:
        return self.raw.get("camera", {})

    @property
    def detector(self) -> dict[str, Any]:
        return self.raw.get("detector", {})

    @property
    def canonical(self) -> dict[str, Any]:
        return self.raw.get("canonical", {})

    @property
    def decode(self) -> dict[str, Any]:
        return self.raw.get("decode", {})

    @property
    def stability(self) -> dict[str, Any]:
        return self.raw.get("stability", {})

    def weights_path(self) -> Path:
        w = self.detector.get("weights", "models/screen_detector.pt")
        p = Path(w)
        return p if p.is_absolute() else (self.root / p)

    def secret(self, name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)


def resolve_device(device):
    """Resolve a configured device to one that actually exists.

    "auto" -> GPU 0 if CUDA is available else "cpu". A specific GPU request
    falls back to "cpu" (with the model still usable) when CUDA is unavailable,
    so a CPU-only install just works instead of erroring.
    """
    try:
        import torch
        cuda = torch.cuda.is_available()
    except Exception:
        cuda = False
    if device == "cpu":
        return "cpu"
    if device in ("auto", None, ""):
        return 0 if cuda else "cpu"
    return device if cuda else "cpu"


def load_secrets(env_path: Path | None = None) -> None:
    """Load secrets.env into os.environ. Silently no-ops if the file is absent."""
    if load_dotenv is None:
        return
    path = env_path or (REPO_ROOT / "secrets.env")
    if path.exists():
        load_dotenv(path, override=False)


def load_config(config_path: Path | None = None, load_env: bool = True) -> Config:
    path = config_path or (REPO_ROOT / "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if load_env:
        load_secrets()
    return Config(raw=raw, root=REPO_ROOT)
