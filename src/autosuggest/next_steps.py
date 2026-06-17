"""
Next-step suggestion resolver — combines predefined workflow rules with
learned sequential patterns from command history.
"""

import fnmatch
from pathlib import Path

import yaml

from autosuggest.engine import NextStep, PredictionEngine
from autosuggest.paths import workflows_path

WORKFLOWS_PATH = workflows_path()


class NextStepResolver:
    def __init__(
        self,
        engine: PredictionEngine,
        workflows_path: Path = WORKFLOWS_PATH,
    ) -> None:
        self._engine = engine
        self._rules: list[dict] = []
        self._load_rules(workflows_path)

    def _load_rules(self, path: Path) -> None:
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or "workflows" not in data:
            return
        for workflow in data["workflows"]:
            for step in workflow.get("steps", []):
                self._rules.append(step)

    def _match_rules(self, last_command: str) -> list[NextStep]:
        results = []
        for rule in self._rules:
            pattern = rule["pattern"]
            if fnmatch.fnmatch(last_command, pattern) or last_command == pattern:
                for cmd in rule["next"]:
                    if cmd not in [r.command for r in results]:
                        results.append(NextStep(cmd, 1.0, "workflow"))
        return results

    def suggest(self, last_command: str, cwd: str, limit: int = 3) -> list[NextStep]:
        rule_suggestions = self._match_rules(last_command)
        learned_suggestions = self._engine.get_next_steps(last_command, cwd, limit=limit)

        merged: list[NextStep] = []
        seen: set[str] = set()

        for s in rule_suggestions:
            if s.command not in seen:
                merged.append(s)
                seen.add(s.command)

        for s in learned_suggestions:
            if s.command not in seen:
                merged.append(s)
                seen.add(s.command)

        return merged[:limit]
