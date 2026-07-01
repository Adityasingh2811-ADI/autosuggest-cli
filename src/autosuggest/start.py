"""
``suggest-start`` — drop into a hooked bash that already has the ADI module
environment loaded (sourced from ``~/.suggest_bashrc``), matching the
persistent environment the ``suggest`` REPL now provides.

Packaged as a console script so pip installs it into ``~/.local/bin`` alongside
the other ``suggest-*`` commands, instead of relying on a tcsh alias that only
exists inside ``~/.cshrc.user``.
"""

import os
import shutil
import sys
from pathlib import Path

RCFILE = Path.home() / ".suggest_bashrc"


def main() -> None:
    if sys.platform == "win32":
        print(
            "suggest-start is only available under bash on POSIX hosts. "
            "On Windows just run 'suggest'.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    bash = shutil.which("bash")
    if not bash:
        print("suggest-start: bash not found on PATH.", file=sys.stderr)
        raise SystemExit(1)

    if RCFILE.exists():
        args = [bash, "--rcfile", str(RCFILE), "-i"]
    else:
        # No rc file yet: still give the user an interactive bash so the
        # command works, but the ADI modules won't be loaded until they run
        # install-linux.sh (which writes ~/.suggest_bashrc).
        print(
            f"suggest-start: {RCFILE} not found; starting a plain bash. "
            "Run install-linux.sh to set up the ADI module environment.",
            file=sys.stderr,
        )
        args = [bash, "-i"]

    os.execv(bash, args)


if __name__ == "__main__":
    main()
