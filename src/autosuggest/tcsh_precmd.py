"""
tcsh precmd helper — records the just-finished command and prints next-step
suggestions in a single Python invocation.

tcsh has no programmable line editor, so the tcsh hook cannot do inline ghost
text or completion. It calls this module once per prompt (via the ``precmd``
alias) so telemetry recording and next-step recall still work in a native tcsh
login shell. Combining record + next-steps here keeps it to one interpreter
start-up per prompt.

Usage: python -m autosuggest.tcsh_precmd <command> <cwd> [exit_status]
Set AUTOSUGGEST_NO_NEXTSTEPS=1 to record silently (no next-step output).
"""

import os
import sys

from autosuggest.record import send_record


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 2:
        return
    command = args[0].strip()
    cwd = args[1]
    if not command:
        return
    try:
        status = int(args[2]) if len(args) >= 3 else 0
    except ValueError:
        status = 0

    send_record(command, cwd, status)

    if os.environ.get("AUTOSUGGEST_NO_NEXTSTEPS"):
        return

    try:
        from autosuggest.engine import PredictionEngine
        from autosuggest.next_steps import NextStepResolver

        engine = PredictionEngine()
        resolver = NextStepResolver(engine)
        steps = resolver.suggest(command, cwd, limit=3)
        engine.close()
    except Exception:
        return

    if not steps:
        return

    lines = ["\n  \033[36mNext steps:\033[0m"]
    for i, s in enumerate(steps, start=1):
        src = getattr(s, "source", "")
        if src:
            lines.append(
                f"  \033[1m[{i}]\033[0m {s.command:<40} \033[33m({src})\033[0m"
            )
        else:
            lines.append(f"  \033[1m[{i}]\033[0m {s.command}")
    lines.append("")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
