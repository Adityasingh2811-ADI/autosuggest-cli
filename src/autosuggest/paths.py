"""
Platform-aware path resolution — XDG base directories on Linux,
unchanged dotfile paths on Windows.
"""

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# Localhost TCP transport for the telemetry daemon (Windows, and WSL fallback).
# Defined here so lightweight clients can reach it without importing the daemon
# module (which pulls in asyncio and slows the per-prompt record path).
TCP_HOST = "127.0.0.1"
TCP_PORT = 19526

# Filesystems where SQLite WAL journaling is unsafe (may corrupt the DB).
# On these we fall back to a rollback journal (TRUNCATE) instead of WAL.
_NETWORK_FSTYPES = frozenset({
    "nfs", "nfs4", "cifs", "smbfs", "smb3", "afs", "9p",
    "fuse.sshfs", "lustre", "gpfs", "beegfs", "glusterfs", "ceph",
})


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


def _ensure_runtime_dir() -> Path:
    """Return the runtime dir, creating it private (0700) on POSIX.

    A restrictive mode is required on shared multi-user hosts so other users
    cannot connect to our socket or read the auth token before we lock it down.
    """
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        try:
            d.chmod(0o700)
        except OSError:
            pass
    return d


def db_path() -> Path:
    if IS_WINDOWS:
        return Path.home() / ".cli_autosuggest.db"
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "history.db"


def pid_path() -> Path:
    if IS_WINDOWS:
        return Path.home() / ".cli_autosuggest.pid"
    return _ensure_runtime_dir() / "autosuggest.pid"


def socket_path() -> str:
    if IS_WINDOWS:
        return ""
    return str(_ensure_runtime_dir() / "autosuggest.sock")


def token_path() -> Path:
    """Path to the per-user daemon auth token (protects the TCP transport)."""
    if IS_WINDOWS:
        return Path.home() / ".cli_autosuggest.token"
    return _ensure_runtime_dir() / "autosuggest.token"


def is_network_fs(path: Path) -> bool:
    """Best-effort check for whether ``path`` lives on a network filesystem.

    SQLite WAL mode can corrupt the database on NFS/CIFS (documented SQLite
    limitation), so callers use this to pick a safe journal mode. Returns False
    on Windows and whenever the filesystem type cannot be determined.
    """
    if IS_WINDOWS:
        return False
    try:
        target = path
        while not target.exists() and target != target.parent:
            target = target.parent
        target = target.resolve()
        target_str = str(target)

        best_mount = ""
        best_fstype = ""
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point, fstype = parts[1], parts[2]
                prefix = mount_point if mount_point.endswith("/") else mount_point + "/"
                if target_str == mount_point or target_str.startswith(prefix):
                    # Longest matching mount point wins (most specific).
                    if len(mount_point) >= len(best_mount):
                        best_mount = mount_point
                        best_fstype = fstype
        return best_fstype.lower() in _NETWORK_FSTYPES
    except OSError:
        return False


def journal_mode_for(path: Path) -> str:
    """Return a SQLite journal_mode safe for the filesystem hosting ``path``.

    WAL where local, TRUNCATE on network filesystems where WAL is unsafe.
    """
    return "TRUNCATE" if is_network_fs(path) else "WAL"


def apply_journal_mode(conn, path: Path) -> None:
    """Set a filesystem-appropriate journal mode without ever failing hard.

    Switching journal mode needs an exclusive lock, and switching *out* of WAL
    is impossible while another connection (e.g. the daemon) holds the DB open.
    Callers must have already set ``PRAGMA busy_timeout`` so this can wait for
    the lock; if it still can't switch, we keep whatever mode is current —
    reads and writes work in any journal mode, so this is always safe.
    """
    import sqlite3

    try:
        conn.execute(f"PRAGMA journal_mode={journal_mode_for(path)};")
    except sqlite3.OperationalError:
        pass


def workflows_path() -> Path:
    if IS_WINDOWS:
        return Path(__file__).parent / "workflows.yaml"
    user_path = config_dir() / "workflows.yaml"
    if user_path.exists():
        return user_path
    return Path(__file__).parent / "workflows.yaml"


def legacy_db_path() -> Path:
    return Path.home() / ".cli_autosuggest.db"
