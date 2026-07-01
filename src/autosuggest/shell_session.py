"""
Persistent shell environment for the interactive REPL.

Historically the `suggest` REPL ran every command in a fresh ``/bin/sh -c``.
On managed ADI hosts that shell never sources ``~/.suggest_bashrc``, so the
Environment Modules system and the ADI toolchain it loads (Perforce, Python,
Cadence, ...) were unavailable — unlike the ``suggest-start`` bash session,
which sources that rc file once and keeps its environment for the whole
session.

``CommandRunner`` closes that gap. On POSIX with bash available it captures the
environment produced by sourcing the login rc file once, then keeps that
environment live across commands: ``module load``, ``export`` and ``cd`` issued
inside the REPL persist for subsequent commands, exactly like a normal shell.
Standard streams are inherited so interactive programs keep working.

On Windows (or when bash is unavailable) it transparently falls back to the
previous per-command ``shell=True`` behaviour.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# Written by install-linux.sh: sources the Modules system and loads the ADI
# toolchain modules, then re-adds ~/.local/bin and activates the hook.
DEFAULT_RCFILE = Path.home() / ".suggest_bashrc"


def _find_bash() -> str | None:
    return shutil.which("bash")


def _parse_env0(data: bytes) -> dict[str, str]:
    """Parse the NUL-delimited output of ``env -0`` into a dict.

    Values may legitimately contain newlines (e.g. exported bash functions),
    which is exactly why ``env -0`` is used instead of a line-based dump.
    """
    env: dict[str, str] = {}
    for chunk in data.split(b"\x00"):
        if not chunk:
            continue
        key, sep, val = chunk.partition(b"=")
        if not sep:
            continue
        env[key.decode("utf-8", "surrogateescape")] = val.decode(
            "utf-8", "surrogateescape"
        )
    return env


class CommandRunner:
    """Runs REPL commands, persisting environment and cwd across calls."""

    def __init__(
        self,
        start_cwd: str | None = None,
        rcfile: Path | None = None,
    ) -> None:
        self._cwd = start_cwd or os.getcwd()
        self._bash = None if IS_WINDOWS else _find_bash()
        self._env: dict[str, str] | None = None
        rcfile = rcfile if rcfile is not None else DEFAULT_RCFILE

        if self._bash:
            self._env = self._capture_initial_env(rcfile)

    @property
    def persistent(self) -> bool:
        """True when a persistent, module-aware environment is in effect."""
        return self._env is not None

    # ``cd`` is handled inside the persistent shell (and its result read back),
    # so the REPL must not special-case it in that mode.
    @property
    def handles_cd(self) -> bool:
        return self.persistent

    @property
    def cwd(self) -> str:
        return self._cwd

    def _capture_initial_env(self, rcfile: Path) -> dict[str, str] | None:
        """Source the login rc file once and snapshot the resulting env."""
        env_fd, env_file = tempfile.mkstemp(prefix="autosuggest-env-")
        os.close(env_fd)
        try:
            source = ""
            if rcfile.exists():
                source = f"source {shlex.quote(str(rcfile))} >/dev/null 2>&1; "
            script = f"{source}env -0 > {shlex.quote(env_file)}"
            subprocess.run(
                [self._bash, "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            data = Path(env_file).read_bytes()
            env = _parse_env0(data)
            return env or None
        except OSError:
            return None
        finally:
            try:
                os.unlink(env_file)
            except OSError:
                pass

    def run(self, command: str) -> int:
        """Execute ``command`` and return its exit status."""
        if self.persistent:
            return self._run_persistent(command)
        return self._run_fallback(command)

    def _run_fallback(self, command: str) -> int:
        try:
            result = subprocess.run(command, shell=True, cwd=self._cwd)
            return result.returncode
        except KeyboardInterrupt:
            return 130
        except Exception:
            return 1

    def _run_persistent(self, command: str) -> int:
        env_fd, env_file = tempfile.mkstemp(prefix="autosuggest-env-")
        pwd_fd, pwd_file = tempfile.mkstemp(prefix="autosuggest-pwd-")
        os.close(env_fd)
        os.close(pwd_fd)

        # Ensure the `module` function is defined for live `module load`,
        # without reloading already-loaded modules. Then run the user's
        # command, and dump the resulting env + cwd so they persist.
        wrapper = (
            'if [ -n "$MODULESHOME" ]; then '
            'if [ -f "$MODULESHOME/init/bash" ]; then . "$MODULESHOME/init/bash" 2>/dev/null; '
            'elif [ -f "$MODULESHOME/module.sh" ]; then . "$MODULESHOME/module.sh" 2>/dev/null; '
            "fi; fi\n"
            f"{command}\n"
            "__as_rc=$?\n"
            f"command env -0 > {shlex.quote(env_file)} 2>/dev/null\n"
            f"command pwd > {shlex.quote(pwd_file)} 2>/dev/null\n"
            "exit $__as_rc\n"
        )

        try:
            result = subprocess.run(
                [self._bash, "-c", wrapper],
                cwd=self._cwd,
                env=self._env,
            )
            rc = result.returncode
        except KeyboardInterrupt:
            rc = 130
        except OSError:
            rc = 1
        finally:
            self._reload_state(env_file, pwd_file)

        return rc

    def _reload_state(self, env_file: str, pwd_file: str) -> None:
        """Refresh env and cwd from the wrapper's dumps, ignoring failures."""
        try:
            data = Path(env_file).read_bytes()
            env = _parse_env0(data)
            if env:
                self._env = env
        except OSError:
            pass
        finally:
            try:
                os.unlink(env_file)
            except OSError:
                pass

        try:
            new_cwd = Path(pwd_file).read_text().strip()
            if new_cwd and os.path.isdir(new_cwd):
                self._cwd = new_cwd
        except OSError:
            pass
        finally:
            try:
                os.unlink(pwd_file)
            except OSError:
                pass
