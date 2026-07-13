"""Lightweight next-steps CLI for shell integration — prints suggestions one per line."""

import sys

from .engine import PredictionEngine
from .next_steps import NextStepResolver


def main() -> None:
    if len(sys.argv) < 3:
        return
    last_command = sys.argv[1]
    cwd = sys.argv[2]
    if not last_command:
        return

    limit = 3
    for i, arg in enumerate(sys.argv[3:], start=3):
        if arg == "--limit" and i + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[i + 1])
            except ValueError:
                pass

    try:
        engine = PredictionEngine()
        resolver = NextStepResolver(engine)
        for s in resolver.suggest(last_command, cwd, limit=limit):
            print(f"{s.command}\t{s.source}\t{s.confidence}")
        engine.close()
    except Exception:
        return


if __name__ == "__main__":
    main()
