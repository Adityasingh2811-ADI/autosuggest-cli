"""
Platform-aware path resolution — XDG base directories on Linux,
unchanged dotfile paths on Windows.
"""

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def _xdg(env_var: str, fallback_subdir: str) -> Path:
    val = os.environ.get(env_var)
    if val:
        return Path(val)
    return Path.home() / fallback_subdir


def data_dir() -> Path:
    if IS_WINDOWS:
        return Path.home()
    return _xdg("XDG_DATA_HOME", ".local/share") / "autosuggest"


def config_dir() -> Path:
    if IS_WINDOWS:
        return Path(__file__).parent
    return _xdg("XDG_CONFIG_HOME", ".config") / "autosuggest"


def runtime_dir() -> Path:
    if IS_WINDOWS:
        return Path.home()
    val = os.environ.get("XDG_RUNTIME_DIR")
    if val:
        return Path(val)
    return Path(f"/tmp/autosuggest-{os.getuid()}")


def db_path() -> Path:
    if IS_WINDOWS:
        return Path.home() / ".cli_autosuggest.db"
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "history.db"


def pid_path() -> Path:
    if IS_WINDOWS:
        return Path.home() / ".cli_autosuggest.pid"
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "autosuggest.pid"


def socket_path() -> str:
    if IS_WINDOWS:
        return ""
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "autosuggest.sock")


def workflows_path() -> Path:
    if IS_WINDOWS:
        return Path(__file__).parent / "workflows.yaml"
    user_path = config_dir() / "workflows.yaml"
    if user_path.exists():
        return user_path
    return Path(__file__).parent / "workflows.yaml"


def legacy_db_path() -> Path:
    return Path.home() / ".cli_autosuggest.db"
