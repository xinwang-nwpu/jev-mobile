"""YAML configuration loader: one file describes a run.

    python -m jev_mobile                      # reads config.yaml next to the CWD or the package
    python -m jev_mobile --task "..."         # CLI flags still override the file

Credentials stay in .env (TYPESAFE_API_KEY, TEXT_MODEL_*); this file carries the
task, device, and pacing parameters.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

CONFIG_NAME = "config.yaml"

DEFAULTS: Dict[str, Any] = {
    "task": "",
    "adb_path": "",
    "device": None,
    "start_package": None,
    "record_dir": None,
    "screenshots": False,
    "action_interval": 0.0,
    "quiet": False,
}


def default_config_paths() -> list:
    return [Path.cwd() / CONFIG_NAME, Path(__file__).resolve().parent.parent / CONFIG_NAME]


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load config.yaml and reject unknown keys to catch typos early."""
    candidates = [Path(path)] if path else default_config_paths()
    source = next((p for p in candidates if p.exists()), None)
    config = dict(DEFAULTS)
    if source is None:
        return config
    with open(source, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("%s must contain a YAML mapping." % source)
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        raise ValueError("Unknown config keys in %s: %s" % (source, ", ".join(unknown)))
    explicit = set(data)
    config.update(data)
    config["task"] = str(config.get("task") or "").strip()
    config["action_interval"] = float(config.get("action_interval") or 0)
    config["screenshots"] = bool(config.get("screenshots"))
    config["quiet"] = bool(config.get("quiet"))
    if "action_interval" not in explicit:
        # The environment is the fallback only when the file does not set the value.
        try:
            config["action_interval"] = float(os.environ.get("ACTION_INTERVAL", "0") or 0)
        except ValueError:
            pass
    return config
