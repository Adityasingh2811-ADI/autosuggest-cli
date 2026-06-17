"""
Unified launcher for autosuggest-cli.
Ensures the daemon is running, performs one-time migration, and drops into
the interactive shell.
"""

import argparse
import signal
import sys

from autosuggest import __version__
from autosuggest.paths import IS_WINDOWS


def _sigterm_handler(signum, frame):
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="autosuggest",
        description="Context-aware CLI autosuggestion engine",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run the telemetry daemon in foreground (for systemd)",
    )
    parser.add_argument(
        "--daemon-bg", action="store_true",
        help="Start daemon in background and exit",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show daemon status",
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop the running daemon",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args()

    if not IS_WINDOWS:
        from autosuggest.migrate import maybe_migrate
        maybe_migrate()

    if args.daemon:
        from autosuggest.daemon import _cmd_start
        _cmd_start()
        return

    if args.daemon_bg:
        from autosuggest.main import _ensure_daemon
        _ensure_daemon()
        print("[autosuggest] daemon started in background")
        return

    if args.status:
        from autosuggest.daemon import _cmd_status
        _cmd_status()
        return

    if args.stop:
        from autosuggest.daemon import _cmd_stop
        _cmd_stop()
        return

    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, _sigterm_handler)

    from autosuggest.main import main as repl_main
    try:
        repl_main()
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
