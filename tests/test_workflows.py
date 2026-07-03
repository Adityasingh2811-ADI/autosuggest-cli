"""Tests for EDA/CAD workflow next-step rules."""

import pytest

from autosuggest.engine import PredictionEngine
from autosuggest.next_steps import NextStepResolver


@pytest.fixture
def resolver(db_path):
    engine = PredictionEngine(db_path)
    yield NextStepResolver(engine)
    engine.close()


@pytest.mark.parametrize(
    "last_command,expected",
    [
        ("p4 sync //depot/...", "p4 opened"),
        ("vivado -mode batch -source build.tcl", "cat vivado.log"),
        ("xsim top_tb -runall", "cat simulate.log"),
        ("module load vivado/2023.2", "module list"),
    ],
)
def test_eda_workflow_next_steps(resolver, last_command, expected):
    steps = resolver.suggest(last_command, "/home/user/proj", limit=3)
    commands = [s.command for s in steps]
    assert expected in commands
