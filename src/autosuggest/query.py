"""Lightweight query CLI for shell integration — prints suggestions one per line."""

import sys
from .engine import PredictionEngine


def main() -> None:
    if len(sys.argv) < 3:
        return
    prefix = sys.argv[1]
    cwd = sys.argv[2]
    if not prefix:
        return
    engine = PredictionEngine()
    for s in engine.get_suggestions(prefix, cwd, limit=8):
        print(s.command)


if __name__ == "__main__":
    main()
