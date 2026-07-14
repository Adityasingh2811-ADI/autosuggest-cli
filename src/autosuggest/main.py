"""
Context-aware CLI shell wrapper — ghost-text suggestions, Shift+Tab cycling,
and fire-and-forget telemetry to the background daemon.
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
TCP_HOST = "127.0.0.1"
TCP_PORT = 19526

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from autosuggest.arg_completers import get_arg_completions
from autosuggest.daemon import is_daemon_running
from autosuggest.engine import PredictionEngine
from autosuggest.next_steps import NextStepResolver
from autosuggest.paths import socket_path, token_path
from autosuggest.shell_session import CommandRunner

SOCKET_PATH = socket_path()

STYLE = Style.from_dict({
    "auto-suggestion": "fg:#6c6c6c italic",
    "completion-menu.completion": "bg:#303030 fg:#cccccc",
    "completion-menu.completion.current": "bg:#005f87 fg:#ffffff bold",
})


class FrecencyCompleter(Completer):
    def __init__(self, engine: PredictionEngine) -> None:
        self._engine = engine

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text:
            return
        cwd = os.getcwd()

        # Argument-aware completions take priority
        arg_completions = get_arg_completions(text, cwd)
        if arg_completions:
            for ac in arg_completions[:10]:
                yield Completion(
                    ac.text,
                    start_position=-len(text),
                    display_meta=f"({ac.source})",
                )
            return

        suggestions = self._engine.get_suggestions(text, cwd, limit=5)
        for s in suggestions:
            yield Completion(
                s.command,
                start_position=-len(text),
                display_meta=f"{s.score:.2f}",
            )


class FrecencyAutoSuggest(AutoSuggest):
    def __init__(self, engine: PredictionEngine) -> None:
        self._engine = engine

    def get_suggestion(self, buffer, document: Document) -> Suggestion | None:
        text = document.text_before_cursor.lstrip()
        if not text:
            return None
        cwd = os.getcwd()

        # Try argument-aware ghost-text first
        arg_completions = get_arg_completions(text, cwd)
        if arg_completions:
            best = arg_completions[0].text
            if best != text:
                suffix = best[len(text):]
                if suffix:
                    return Suggestion(suffix)

        suggestions = self._engine.get_suggestions(text, cwd, limit=1)
        if suggestions and suggestions[0].command != text:
            suffix = suggestions[0].command[len(text):]
            if suffix:
                return Suggestion(suffix)
        return None


def _build_keybindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add(Keys.Right, filter=True)
    def accept_suggestion(event):
        buf = event.current_buffer
        if buf.suggestion:
            buf.insert_text(buf.suggestion.text)
        else:
            buf.cursor_right()

    @kb.add(Keys.Tab)
    def next_completion(event):
        event.current_buffer.complete_next()

    @kb.add(Keys.BackTab)
    def prev_completion(event):
        event.current_buffer.complete_previous()

    return kb


def _read_token() -> str:
    try:
        return token_path().read_text().strip()
    except OSError:
        return ""


def _send_telemetry(command: str, cwd: str, exit_status: int) -> None:
    payload = json.dumps({
        "command": command,
        "cwd": cwd,
        "exit_status": exit_status,
        "token": _read_token(),
    }).encode("utf-8")
    try:
        if IS_WINDOWS:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect((TCP_HOST, TCP_PORT))
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect(SOCKET_PATH)
        s.sendall(payload)
        s.close()
    except OSError:
        pass


def _prompt_text() -> HTML:
    cwd = os.getcwd()
    home = str(Path.home())
    display = "~" + cwd[len(home):] if cwd.startswith(home) else cwd
    return HTML(f"<b>{display}</b> ▶ ")


def _show_next_steps(suggestions: list) -> None:
    if not suggestions:
        return
    print("\n  \033[36mNext steps:\033[0m")
    for i, s in enumerate(suggestions, 1):
        source_tag = f"\033[33m({s.source})\033[0m"
        print(f"  \033[1m[{i}]\033[0m {s.command:<40} {source_tag}")
    print()


def _ensure_daemon() -> None:
    """Auto-start the daemon if not already running."""
    running, _ = is_daemon_running()
    if running:
        return
    try:
        creation_flags = 0
        if IS_WINDOWS:
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [sys.executable, "-m", "autosuggest.daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            start_new_session=not IS_WINDOWS,
        )
        print("  [auto-started daemon]")
    except OSError:
        pass


def main() -> None:
    if "--version" in sys.argv[1:]:
        from autosuggest import __version__

        print(f"suggest {__version__}")
        return
    _ensure_daemon()
    engine = PredictionEngine()
    resolver = NextStepResolver(engine)
    runner = CommandRunner(start_cwd=os.getcwd())
    session: PromptSession = PromptSession(
        completer=FrecencyCompleter(engine),
        auto_suggest=FrecencyAutoSuggest(engine),
        key_bindings=_build_keybindings(),
        style=STYLE,
        complete_while_typing=True,
    )

    print("autosuggest-cli | Ctrl+D to exit | Tab/Shift+Tab: cycle | ->: accept ghost")
    print("  Type a number [1-3] after suggestions to accept a next step.\n")
    if runner.persistent:
        if runner.backend == "tcsh":
            print("  [native tcsh backend: pinit, source .csh, module load all work natively]\n")
        else:
            print("  [persistent bash shell: module/env changes carry across commands]\n")

    last_suggestions: list = []

    while True:
        try:
            text = session.prompt(_prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("exit", "quit"):
            break

        if text.isdigit() and last_suggestions:
            idx = int(text) - 1
            if 0 <= idx < len(last_suggestions):
                text = last_suggestions[idx].command
                print(f"  >> {text}")

        cwd = os.getcwd()

        # Without a persistent shell, `cd` must be handled by the REPL itself
        # (a per-command subprocess cannot change our cwd). With a persistent
        # shell, `cd` flows through it and its new cwd is read back below.
        if not runner.handles_cd and text.startswith("cd "):
            target = os.path.expanduser(text[3:].strip())
            try:
                os.chdir(target)
            except OSError as e:
                print(f"cd: {e}")
            _send_telemetry(text, cwd, 0)
            last_suggestions = resolver.suggest(text, cwd)
            _show_next_steps(last_suggestions)
            continue

        exit_status = runner.run(text)
        if runner.persistent and runner.cwd != cwd:
            try:
                os.chdir(runner.cwd)
            except OSError:
                pass
        _send_telemetry(text, cwd, exit_status)

        last_suggestions = resolver.suggest(text, cwd)
        _show_next_steps(last_suggestions)

    engine.close()
    print("\nSession ended.")


run = main

if __name__ == "__main__":
    main()
