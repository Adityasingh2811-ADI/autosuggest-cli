"""Tests for the persistent shell environment used by the REPL."""

import shutil

import pytest

from autosuggest.shell_session import CommandRunner, _parse_env0

has_bash = shutil.which("bash") is not None
has_tcsh = shutil.which("tcsh") is not None


def test_parse_env0_handles_newlines_in_values():
    data = b"A=1\x00B=line1\nline2\x00MALFORMED\x00C=3\x00"
    env = _parse_env0(data)
    assert env["A"] == "1"
    assert env["B"] == "line1\nline2"
    assert env["C"] == "3"
    assert "MALFORMED" not in env


@pytest.mark.skipif(not has_bash, reason="bash not available")
class TestBashBackend:
    def test_persistent_env_carries_across_commands(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="bash")
        assert runner.persistent
        assert runner.backend == "bash"
        assert runner.run("export FOO=bar") == 0
        assert runner.run('test "$FOO" = bar') == 0

    def test_cd_persists_across_commands(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="bash")
        assert runner.run("cd sub") == 0
        assert runner.cwd == str(sub)
        assert runner.run("test -d .") == 0

    def test_exit_status_is_propagated(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="bash")
        assert runner.run("true") == 0
        assert runner.run("exit 7") == 7

    def test_rcfile_environment_is_sourced_once(self, tmp_path):
        rc = tmp_path / "rc"
        rc.write_text("export ADI_TOOL=loaded\n")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=rc, force_backend="bash")
        assert runner.persistent
        assert runner.run('test "$ADI_TOOL" = loaded') == 0


@pytest.mark.skipif(not has_tcsh, reason="tcsh not available")
class TestTcshBackend:
    def test_setenv_persists(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="tcsh")
        assert runner.persistent
        assert runner.backend == "tcsh"
        assert runner.run("setenv FOO bar") == 0
        assert runner.run('test "$FOO" = bar') == 0

    def test_cd_persists(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="tcsh")
        assert runner.run("cd sub") == 0
        assert runner.cwd == str(sub)

    def test_exit_status_propagated(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="tcsh")
        assert runner.run("true") == 0
        assert runner.run("exit 7") == 7

    def test_set_path_persists(self, tmp_path):
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="tcsh")
        assert runner.run("set path = ($path /tmp/tcsh_test_path_unit)") == 0
        assert runner.run('echo "$PATH" | grep -q /tmp/tcsh_test_path_unit') == 0

    def test_source_csh_script(self, tmp_path):
        setup = tmp_path / "setup.csh"
        setup.write_text("setenv SOURCED_CSH yes\n")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="tcsh")
        assert runner.run(f"source {setup}") == 0
        assert runner.run('test "$SOURCED_CSH" = yes') == 0

    def test_pinit_ee_sandbox(self, tmp_path):
        """Test that pinit (a tcsh source script) works natively."""
        from pathlib import Path
        pinit_script = Path("/cad/adi/apps/adi/adv/release6/script/gpms/pinit")
        if not pinit_script.exists():
            pytest.skip("pinit script not available on this host")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing", force_backend="tcsh")
        rc = runner.run("source /cad/adi/apps/adi/adv/release6/script/gpms/pinit ee_sandbox")
        assert rc == 0
        assert runner.run('test "$GPMS_PROJECT" = ee_sandbox') == 0

    def test_tcsh_rcfile_sourced(self, tmp_path):
        rc = tmp_path / "rc.csh"
        rc.write_text("setenv MY_TCSH_RC loaded_ok\n")
        runner = CommandRunner(start_cwd=str(tmp_path), rcfile=rc, force_backend="tcsh")
        assert runner.persistent
        assert runner.run('test "$MY_TCSH_RC" = loaded_ok') == 0
