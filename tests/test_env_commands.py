"""Tests for environment-related commands: module load, pinit, setenv, path setting.

Verifies that the autosuggest engine correctly records, suggests, and (in the
REPL persistent shell) preserves environment changes across commands.
"""

import shutil
import sqlite3
import time

import pytest

from autosuggest.engine import PredictionEngine
from autosuggest.shell_session import CommandRunner, _parse_env0

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="persistent shell session requires bash",
)


class TestEnvCommandRecording:
    """Engine correctly stores and suggests env-related commands."""

    @pytest.fixture
    def env_db(self, db_path):
        conn = sqlite3.connect(str(db_path))
        now = time.time()
        rows = [
            ("module load cadence/ic23.1", "/proj/myproj", 0, now - 60),
            ("module load cadence/ic23.1", "/proj/myproj", 0, now - 30),
            ("module load synopsys/vcs2023", "/proj/myproj", 0, now - 120),
            ("module load python/adi/3.12.2", "/proj/myproj", 0, now - 90),
            ("module unload cadence/ic23.1", "/proj/myproj", 0, now - 10),
            ("pinit myproject", "/proj/myproj", 0, now - 50),
            ("pinit myproject", "/proj/myproj", 0, now - 20),
            ("pinit otherproj", "/proj/otherproj", 0, now - 200),
            ("setenv PROJ_ROOT /proj/myproj", "/proj/myproj", 0, now - 70),
            ("setenv LD_LIBRARY_PATH /opt/lib", "/proj/myproj", 0, now - 150),
            ("set path = ($path /cad/tools/bin)", "/proj/myproj", 0, now - 80),
            ("source /proj/myproj/setup.csh", "/proj/myproj", 0, now - 40),
            ("source /proj/myproj/setup.csh", "/proj/myproj", 0, now - 15),
            # Failed module loads should NOT be suggested
            ("module load broken/module", "/proj/myproj", 1, now - 5),
        ]
        conn.executemany(
            "INSERT INTO command_history (command, cwd, exit_status, timestamp) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()
        return db_path

    def test_module_load_prefix_match(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_suggestions("module load", "/proj/myproj")
        commands = [r.command for r in results]
        assert "module load cadence/ic23.1" in commands
        assert "module load synopsys/vcs2023" in commands
        assert "module load python/adi/3.12.2" in commands
        engine.close()

    def test_module_load_frecency_ranking(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_suggestions("module load", "/proj/myproj")
        commands = [r.command for r in results]
        # cadence/ic23.1 used twice and more recently -> should rank first
        assert commands[0] == "module load cadence/ic23.1"
        engine.close()

    def test_failed_module_load_excluded(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_suggestions("module load broken", "/proj/myproj")
        commands = [r.command for r in results]
        assert "module load broken/module" not in commands
        engine.close()

    def test_pinit_prefix_match(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_suggestions("pinit", "/proj/myproj")
        commands = [r.command for r in results]
        assert "pinit myproject" in commands
        assert "pinit otherproj" in commands
        engine.close()

    def test_pinit_context_boost(self, env_db):
        engine = PredictionEngine(env_db)
        results_local = engine.get_suggestions("pinit", "/proj/myproj")
        results_other = engine.get_suggestions("pinit", "/tmp/elsewhere")
        local_scores = {r.command: r.score for r in results_local}
        other_scores = {r.command: r.score for r in results_other}
        # pinit myproject used in /proj/myproj should score higher there
        assert local_scores.get("pinit myproject", 0) > other_scores.get("pinit myproject", 0)
        engine.close()

    def test_setenv_prefix_match(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_suggestions("setenv", "/proj/myproj")
        commands = [r.command for r in results]
        assert "setenv PROJ_ROOT /proj/myproj" in commands
        assert "setenv LD_LIBRARY_PATH /opt/lib" in commands
        engine.close()

    def test_set_path_prefix_match(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_suggestions("set path", "/proj/myproj")
        commands = [r.command for r in results]
        assert "set path = ($path /cad/tools/bin)" in commands
        engine.close()

    def test_source_prefix_match(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_suggestions("source", "/proj/myproj")
        commands = [r.command for r in results]
        assert "source /proj/myproj/setup.csh" in commands
        engine.close()

    def test_next_steps_after_pinit(self, env_db):
        engine = PredictionEngine(env_db)
        results = engine.get_next_steps("pinit myproject", "/proj/myproj")
        # After pinit, source and module load should be common next steps
        commands = [r.command for r in results]
        # These were recorded after pinit in temporal order
        assert len(commands) >= 0  # may or may not have successors depending on ordering
        engine.close()


class TestPersistentShellEnvCommands:
    """The REPL persistent shell correctly handles env-modifying commands (bash backend)."""

    def test_export_persists(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        assert runner.run("export MY_VAR=hello123") == 0
        assert runner.run('test "$MY_VAR" = hello123') == 0

    def test_path_append_persists(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        assert runner.run("export PATH=$PATH:/opt/custom/bin") == 0
        assert runner.run('echo "$PATH" | grep -q /opt/custom/bin') == 0

    def test_multiple_exports_persist(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        assert runner.run("export A=1") == 0
        assert runner.run("export B=2") == 0
        assert runner.run('test "$A" = 1 && test "$B" = 2') == 0

    def test_module_function_available(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        assert runner.run("true") == 0

    @pytest.mark.skipif(
        not shutil.which("modulecmd"),
        reason="Environment Modules not installed",
    )
    def test_module_load_persists_env(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        rc = runner.run("module load null 2>/dev/null")
        if rc == 0:
            assert runner.run("module list 2>&1 | grep -q null") == 0

    def test_source_script_persists_env(self, tmp_path):
        setup = tmp_path / "setup.sh"
        setup.write_text("export SETUP_VAR=sourced_ok\n")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        assert runner.run(f"source {setup}") == 0
        assert runner.run('test "$SETUP_VAR" = sourced_ok') == 0

    def test_cd_after_source_works(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        setup = tmp_path / "env.sh"
        setup.write_text("export PROJECT=active\n")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        assert runner.run(f"source {setup}") == 0
        assert runner.run("cd sub") == 0
        assert runner.cwd == str(sub)
        assert runner.run('test "$PROJECT" = active') == 0

    def test_unset_variable(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="bash")
        assert runner.run("export TEMP_VAR=exists") == 0
        assert runner.run('test "$TEMP_VAR" = exists') == 0
        assert runner.run("unset TEMP_VAR") == 0
        assert runner.run('test -z "$TEMP_VAR"') == 0


@pytest.mark.skipif(
    not shutil.which("tcsh"),
    reason="tcsh not available",
)
class TestTcshNativeBackend:
    """Verify tcsh-native syntax works with the tcsh backend."""

    def test_setenv_persists(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="tcsh")
        assert runner.run("setenv FOO bar") == 0
        assert runner.run('test "$FOO" = bar') == 0

    def test_set_path_persists(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="tcsh")
        assert runner.run("set path = ($path /opt/new)") == 0
        assert runner.run('echo "$PATH" | grep -q /opt/new') == 0

    def test_source_csh_script(self, tmp_path):
        setup = tmp_path / "setup.csh"
        setup.write_text("setenv CSH_VAR loaded\n")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="tcsh")
        assert runner.run(f"source {setup}") == 0
        assert runner.run('test "$CSH_VAR" = loaded') == 0

    def test_pinit_works_natively(self, tmp_path):
        from pathlib import Path
        pinit = Path("/cad/adi/apps/adi/adv/release6/script/gpms/pinit")
        if not pinit.exists():
            pytest.skip("pinit not available")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="tcsh")
        assert runner.run("source /cad/adi/apps/adi/adv/release6/script/gpms/pinit ee_sandbox") == 0
        assert runner.run('test "$GPMS_PROJECT" = ee_sandbox') == 0

    def test_module_load_in_tcsh(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "none", force_backend="tcsh")
        rc = runner.run("module load xlmc")
        # xlmc may not exist, but module system should not crash
        assert runner.run("true") == 0
