"""
One-time migration of legacy ~/.cli_autosuggest.db to XDG data directory.
No-op on Windows or if already migrated.
"""

import shutil
import sys

from autosuggest.paths import IS_WINDOWS, db_path, legacy_db_path


def maybe_migrate() -> None:
    if IS_WINDOWS:
        return

    old = legacy_db_path()
    new = db_path()

    if not old.exists() or new.exists():
        return

    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(old), str(new))

    try:
        old.rename(old.with_suffix(".db.migrated"))
    except OSError:
        pass

    print(
        f"[autosuggest] migrated history: {old} -> {new}",
        file=sys.stderr,
    )
