"""Unit tests for autosuggest.paths — XDG path resolution."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_module_cache():
    """Reimport paths module fresh for each test to pick up env changes."""
    mod = "autosuggest.paths"
    if mod in sys.modules:
        del sys.modules[mod]
    yield
    if mod in sys.modules:
        del sys.modules[mod]


class TestLinuxPaths:
    @pytest.fixture(autouse=True)
    def _force_linux(self):
        with patch("autosuggest.paths.IS_WINDOWS", False):
            yield

    def test_db_path_xdg_data_home(self, tmp_path):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
            from autosuggest.paths import db_path
            result = db_path()
            assert result == tmp_path / "autosuggest" / "history.db"
            assert result.parent.exists()

    def test_db_path_default(self, tmp_path):
        with patch.dict(os.environ, {}, clear=True):
            with patch("autosuggest.paths.Path.home", return_value=tmp_path):
                from autosuggest.paths import db_path
                result = db_path()
                assert result == tmp_path / ".local" / "share" / "autosuggest" / "history.db"

    def test_socket_path_xdg_runtime(self, tmp_path):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(tmp_path)}):
            from autosuggest.paths import socket_path
            result = socket_path()
            assert result == str(tmp_path / "autosuggest.sock")

    def test_pid_path_xdg_runtime(self, tmp_path):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(tmp_path)}):
            from autosuggest.paths import pid_path
            result = pid_path()
            assert result == tmp_path / "autosuggest.pid"

    def test_workflows_path_user_override(self, tmp_path):
        config_dir = tmp_path / "autosuggest"
        config_dir.mkdir()
        user_wf = config_dir / "workflows.yaml"
        user_wf.write_text("workflows: []")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            from autosuggest.paths import workflows_path
            result = workflows_path()
            assert result == user_wf

    def test_workflows_path_fallback_to_bundled(self, tmp_path):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            from autosuggest.paths import workflows_path
            result = workflows_path()
            assert result.name == "workflows.yaml"
            assert "autosuggest" in str(result.parent)


class TestWindowsPaths:
    @pytest.fixture(autouse=True)
    def _force_windows(self):
        with patch("autosuggest.paths.IS_WINDOWS", True):
            yield

    def test_db_path_windows(self, tmp_path):
        with patch("autosuggest.paths.Path.home", return_value=tmp_path):
            from autosuggest.paths import db_path
            result = db_path()
            assert result == tmp_path / ".cli_autosuggest.db"

    def test_socket_path_windows_empty(self):
        from autosuggest.paths import socket_path
        assert socket_path() == ""

    def test_pid_path_windows(self, tmp_path):
        with patch("autosuggest.paths.Path.home", return_value=tmp_path):
            from autosuggest.paths import pid_path
            result = pid_path()
            assert result == tmp_path / ".cli_autosuggest.pid"
