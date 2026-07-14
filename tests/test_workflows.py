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
        # perforce
        ("p4 sync //depot/...", "p4 opened"),
        ("p4 edit src/foo.v", "p4 diff"),
        ("p4 submit", "pi rel"),
        ("p4 status ./...", "p4 edit"),
        # vivado
        ("vivado -mode batch -source build.tcl", "cat vivado.log"),
        # simulation
        ("xsim top_tb -runall", "cat simulate.log"),
        ("xrun -f filelist.f", "cat xrun.log"),
        ("vcs -full64 -f filelist.f", "cat vcs.log"),
        ("./simv +UVM_TESTNAME=my_test", "cat simv.log"),
        # modules
        ("module load vivado/2023.2", "module list"),
        # adsim
        ("adsim -co my_test", "adsim -no_compile"),
        ("adsim -regr passlist.regr", "cat regression_report.html"),
        ("adsim -coverage my_test", "adsim -cov_merge"),
        ("adsim -gate my_test", "adsim -gate -best"),
        ("adsim -debug shm my_test", "simvision"),
        # adv-workspace
        ("adv --login", "adv"),
        ("pinit me30", "pi ws st -v"),
        # percipient
        ("pi ws st -v", "pi update"),
        ("pi ip local me30.rtl", "p4 edit"),
        ("pi rel me30.rtl", "pi ws st -v"),
        # synthesis-pnr
        ("genus -f syn.tcl", "cat genus.log"),
        ("innovus -f pnr.tcl", "cat innovus.log"),
        # formal
        ("jg -superlint top.sv", "cat superlint.log"),
        # lsf
        ("bsub -q short ./run.sh", "bjobs"),
        ("bjobs", "bpeek"),
        # coverage
        ("urg -dir simv.vdb", "cat urgReport/dashboard.html"),
    ],
)
def test_eda_workflow_next_steps(resolver, last_command, expected):
    steps = resolver.suggest(last_command, "/home/user/proj", limit=5)
    commands = [s.command for s in steps]
    assert expected in commands
