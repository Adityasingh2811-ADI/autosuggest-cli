"""
Persistent shell environment for the interactive REPL.

Supports two backends:
  * **tcsh** — used when the user's login shell is tcsh/csh.  Runs commands
    natively so ``pinit``, ``source .csh``, ``foreach``, and all csh idioms
    work without translation.
  * **bash** — used otherwise.  Includes a tcsh→bash translation layer for
    simple one-liners (``setenv``, ``set path``).

Both backends capture the resulting environment (via ``env -0``) and working
directory after each command, so changes persist across REPL invocations.

On Windows (or when neither shell is available) it transparently falls back
to the previous per-command ``shell=True`` behaviour.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

DEFAULT_RCFILE = Path.home() / ".suggest_bashrc"
DEFAULT_TCSH_RCFILE = Path.home() / ".cshrc.user"


def _get_user_shell() -> str:
    """Return the basename of the user's login shell (e.g. 'tcsh', 'bash')."""
    shell = os.environ.get("SHELL", "")
    return os.path.basename(shell) if shell else ""


def _translate_tcsh_to_bash(command: str) -> str:
    """Translate common tcsh/csh idioms to bash equivalents.

    Handles:
      setenv VAR value        -> export VAR=value
      unsetenv VAR            -> unset VAR
      set path = (...)        -> export PATH=...
    """
    stripped = command.strip()

    if stripped.startswith("setenv "):
        parts = stripped.split(None, 2)
        if len(parts) == 3:
            return f"export {parts[1]}={shlex.quote(parts[2])}"
        elif len(parts) == 2:
            return f"export {parts[1]}="

    if stripped.startswith("unsetenv "):
        parts = stripped.split(None, 1)
        if len(parts) == 2:
            return f"unset {parts[1]}"

    if stripped.startswith("set path") and "=" in stripped:
        _, _, rhs = stripped.partition("=")
        rhs = rhs.strip()
        if rhs.startswith("(") and rhs.endswith(")"):
            items = rhs[1:-1].split()
            expanded = []
            for item in items:
                if item == "$path" or item == "$PATH":
                    expanded.append("$PATH")
                else:
                    expanded.append(item)
            return f"export PATH={':'.join(expanded)}"

    return command


def _find_bash() -> str | None:
    return shutil.which("bash")


def _find_tcsh() -> str | None:
    return shutil.which("tcsh") or shutil.which("csh")


def _parse_env0(data: bytes) -> dict[str, str]:
    """Parse the NUL-delimited output of ``env -0`` into a dict."""
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
        inherit_env: bool = True,
        force_backend: str | None = None,
    ) -> None:
        self._cwd = start_cwd or os.getcwd()
        self._env: dict[str, str] | None = None
        self._backend: str = "fallback"  # "tcsh", "bash", or "fallback"
        self._shell: str | None = None

        if IS_WINDOWS:
            pass
        elif force_backend == "bash":
            self._shell = _find_bash()
            if self._shell:
                self._backend = "bash"
                bash_rc = rcfile if rcfile is not None else DEFAULT_RCFILE
                self._env = self._capture_initial_env_bash(bash_rc)
        elif force_backend == "tcsh":
            self._shell = _find_tcsh()
            if self._shell:
                self._backend = "tcsh"
                if inherit_env and rcfile is None:
                    self._env = self._inherit_current_env()
                else:
                    tcsh_rc = rcfile if rcfile is not None else DEFAULT_TCSH_RCFILE
                    self._env = self._capture_initial_env_tcsh(tcsh_rc)
        else:
            user_shell = _get_user_shell()
            if user_shell in ("tcsh", "csh"):
                self._shell = _find_tcsh()
                if self._shell:
                    self._backend = "tcsh"
                    if inherit_env and rcfile is None:
                        self._env = self._inherit_current_env()
                    else:
                        tcsh_rc = rcfile if rcfile is not None else DEFAULT_TCSH_RCFILE
                        self._env = self._capture_initial_env_tcsh(tcsh_rc)
            else:
                self._shell = _find_bash()
                if self._shell:
                    self._backend = "bash"
                    bash_rc = rcfile if rcfile is not None else DEFAULT_RCFILE
                    self._env = self._capture_initial_env_bash(bash_rc)

    @property
    def persistent(self) -> bool:
        """True when a persistent, module-aware environment is in effect."""
        return self._env is not None

    @property
    def handles_cd(self) -> bool:
        return self.persistent

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def backend(self) -> str:
        return self._backend

    def _inherit_current_env(self) -> dict[str, str] | None:
        """Inherit the calling shell's full environment into the REPL."""
        env = dict(os.environ)
        return env if env else None

    def _capture_initial_env_bash(self, rcfile: Path) -> dict[str, str] | None:
        """Source a bash rc file and snapshot the resulting env."""
        env_fd, env_file = tempfile.mkstemp(prefix="autosuggest-env-")
        os.close(env_fd)
        try:
            source = ""
            if rcfile.exists():
                source = f"source {shlex.quote(str(rcfile))} >/dev/null 2>&1; "
            script = f"{source}env -0 > {shlex.quote(env_file)}"
            subprocess.run(
                [self._shell, "-c", script],
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

    def _capture_initial_env_tcsh(self, rcfile: Path) -> dict[str, str] | None:
        """Source a tcsh rc file and snapshot the resulting env."""
        env_fd, env_file = tempfile.mkstemp(prefix="autosuggest-env-")
        os.close(env_fd)
        try:
            source = ""
            if rcfile.exists():
                source = f"source {shlex.quote(str(rcfile))} >& /dev/null; "
            script = f"{source}env -0 >! {shlex.quote(env_file)}"
            subprocess.run(
                [self._shell, "-c", script],
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
            if self._backend == "tcsh":
                return self._run_tcsh(command)
            return self._run_bash(command)
        return self._run_fallback(command)

    def _run_fallback(self, command: str) -> int:
        try:
            result = subprocess.run(command, shell=True, cwd=self._cwd)
            return result.returncode
        except KeyboardInterrupt:
            return 130
        except Exception:
            return 1

    def _run_tcsh(self, command: str) -> int:
        """Run command in a native tcsh subprocess, capturing env + cwd."""
        env_fd, env_file = tempfile.mkstemp(prefix="autosuggest-env-")
        pwd_fd, pwd_file = tempfile.mkstemp(prefix="autosuggest-pwd-")
        rc_fd, rc_file = tempfile.mkstemp(prefix="autosuggest-rc-", suffix=".csh")
        os.close(env_fd)
        os.close(pwd_fd)
        os.close(rc_fd)

        wrapper_lines = [
            "#!/bin/tcsh -f",
            'if ( $?MODULESHOME ) then',
            '    if ( -f "$MODULESHOME/init/tcsh" ) source "$MODULESHOME/init/tcsh" >& /dev/null',
            'endif',
            command,
            'set _as_rc = $status',
            f'env -0 >! {shlex.quote(env_file)}',
            f'pwd >! {shlex.quote(pwd_file)}',
            'exit $_as_rc',
        ]
        Path(rc_file).write_text("\n".join(wrapper_lines) + "\n")

        try:
            result = subprocess.run(
                [self._shell, "-f", rc_file],
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
            try:
                os.unlink(rc_file)
            except OSError:
                pass

        return rc

    def _run_bash(self, command: str) -> int:
        """Run command in bash with tcsh→bash translation."""
        env_fd, env_file = tempfile.mkstemp(prefix="autosuggest-env-")
        pwd_fd, pwd_file = tempfile.mkstemp(prefix="autosuggest-pwd-")
        os.close(env_fd)
        os.close(pwd_fd)

        translated = _translate_tcsh_to_bash(command)

        wrapper = (
            'if [ -n "$MODULESHOME" ]; then '
            'if [ -f "$MODULESHOME/init/bash" ]; then . "$MODULESHOME/init/bash" 2>/dev/null; '
            'elif [ -f "$MODULESHOME/module.sh" ]; then . "$MODULESHOME/module.sh" 2>/dev/null; '
            "fi; fi\n"
            f"{translated}\n"
            "__as_rc=$?\n"
            f"command env -0 > {shlex.quote(env_file)} 2>/dev/null\n"
            f"command pwd > {shlex.quote(pwd_file)} 2>/dev/null\n"
            "exit $__as_rc\n"
        )

        try:
            result = subprocess.run(
                [self._shell, "-c", wrapper],
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
