"""Lightweight local metrics — tracks install count, invocations, and sessions."""

import json
import os
import time
from pathlib import Path

from autosuggest.paths import data_dir, config_dir


def _metrics_path() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "metrics.json"


def _telemetry_enabled() -> bool:
    cfg = config_dir() / "config.yaml"
    if not cfg.exists():
        return True
    try:
        content = cfg.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("telemetry"):
                if "false" in stripped.lower() or "off" in stripped.lower():
                    return False
    except OSError:
        pass
    return True


def _load() -> dict:
    path = _metrics_path()
    if not path.exists():
        return {
            "installs": 0,
            "suggestions_served": 0,
            "daemon_starts": 0,
            "sessions": 0,
            "first_install": None,
            "last_active": None,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "installs": 0,
            "suggestions_served": 0,
            "daemon_starts": 0,
            "sessions": 0,
            "first_install": None,
            "last_active": None,
        }


def _save(data: dict) -> None:
    path = _metrics_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def increment(counter: str, amount: int = 1) -> None:
    if not _telemetry_enabled():
        return
    data = _load()
    data[counter] = data.get(counter, 0) + amount
    data["last_active"] = time.time()
    if counter == "installs" and data.get("first_install") is None:
        data["first_install"] = time.time()
    _save(data)


def get_metrics() -> dict:
    return _load()


def run() -> None:
    """CLI entry point: python -m autosuggest.metrics increment <counter>"""
    import sys
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "increment":
        increment(args[1])
    elif len(args) >= 1 and args[0] == "show":
        data = get_metrics()
        for k, v in data.items():
            print(f"  {k}: {v}")
    else:
        print("Usage: python -m autosuggest.metrics [increment <counter> | show]")


if __name__ == "__main__":
    run()
