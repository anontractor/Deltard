from pathlib import Path

import yaml

REQUIRED_KEYS = ("UNANET_BASE", "UNANET_PROJECT", "LOGIN_USERNAME", "LOGIN_PASSWORD")


def _parse_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_config(root: Path) -> dict:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8")) or {}
    config.update(_parse_env(root / "secrets.env"))

    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        raise ValueError(
            f"Missing required config values: {', '.join(missing)} "
            "(check config.yaml and secrets.env)"
        )
    return config
