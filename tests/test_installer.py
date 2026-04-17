"""Tests for the universal installer — validates structure and helpers."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import install  # noqa: E402 — import intentionally follows sys.path mutation above


class TestPlatformDetection:
    """Installer platform helpers should work on any OS."""

    def test_platform_name(self):
        name = install.platform_name()
        assert name in ("macOS", "Windows", "Linux")

    def test_home_returns_path(self):
        assert install.home().is_dir()

    def test_find_python_returns_something(self):
        python = install.find_python()
        # Should find at least the Python running the tests
        assert python is not None or sys.version_info < (3, 11)

    def test_find_resolve_api_path_type(self):
        result = install.find_resolve_api_path()
        assert result is None or isinstance(result, str)


class TestMCPClients:
    """Validate MCP client definitions."""

    def test_at_least_10_clients_defined(self):
        assert len(install.MCP_CLIENTS) >= 10

    def test_all_clients_have_required_fields(self):
        for client in install.MCP_CLIENTS:
            assert "id" in client, f"Client missing 'id': {client}"
            assert "name" in client, f"Client missing 'name': {client}"
            assert "get_path" in client, f"Client missing 'get_path': {client}"
            assert "config_key" in client, f"Client missing 'config_key': {client}"
            assert callable(client["get_path"]), f"get_path not callable: {client['id']}"

    def test_client_ids_are_unique(self):
        ids = [c["id"] for c in install.MCP_CLIENTS]
        assert len(ids) == len(set(ids)), f"Duplicate client IDs: {ids}"

    def test_known_clients_present(self):
        ids = {c["id"] for c in install.MCP_CLIENTS}
        expected = {"claude-desktop", "claude-code", "cursor", "vscode", "windsurf", "zed"}
        missing = expected - ids
        assert not missing, f"Missing expected clients: {missing}"


class TestConfigBuilders:
    """Config builders should produce valid JSON structures."""

    def test_standard_entry(self):
        entry = install.build_server_entry("/usr/bin/python3", "/path/to/project")
        assert entry["command"] == "/usr/bin/python3"
        assert "-m" in entry["args"]
        assert "celavii_resolve" in entry["args"]

    def test_zed_entry(self):
        entry = install.build_zed_entry("/usr/bin/python3", "/path/to/project")
        assert "command" in entry
        assert "settings" in entry
        assert entry["command"]["path"] == "/usr/bin/python3"

    def test_entries_are_json_serializable(self):
        entry = install.build_server_entry("/usr/bin/python3", "/path/to/project")
        json.dumps(entry)  # Should not raise

        zed = install.build_zed_entry("/usr/bin/python3", "/path/to/project")
        json.dumps(zed)  # Should not raise


class TestFileIO:
    """JSON read/write helpers."""

    def test_read_nonexistent_returns_empty(self, tmp_path):
        result = install.read_json(tmp_path / "nonexistent.json")
        assert result == {}

    def test_read_invalid_json_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{")
        result = install.read_json(bad)
        assert result == {}

    def test_read_valid_json(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_text('{"key": "value"}')
        result = install.read_json(good)
        assert result == {"key": "value"}

    def test_write_creates_file(self, tmp_path):
        target = tmp_path / "sub" / "config.json"
        success = install.write_json(target, {"test": True})
        assert success
        assert target.is_file()
        assert json.loads(target.read_text()) == {"test": True}

    def test_write_creates_backup(self, tmp_path):
        target = tmp_path / "config.json"
        target.write_text('{"old": true}')
        install.write_json(target, {"new": True})
        backup = target.with_suffix(".json.backup")
        assert backup.is_file()
        assert json.loads(backup.read_text()) == {"old": True}

    def test_dry_run_does_not_write(self, tmp_path, capsys):
        target = tmp_path / "config.json"
        install.write_json(target, {"test": True}, dry_run=True)
        assert not target.is_file()
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out


class TestProjectFiles:
    """Verify essential project files exist."""

    def test_pyproject_toml_exists(self):
        assert (PROJECT_ROOT / "pyproject.toml").is_file()

    def test_claude_md_exists(self):
        assert (PROJECT_ROOT / "CLAUDE.md").is_file()

    def test_mcp_json_exists(self):
        assert (PROJECT_ROOT / ".mcp.json").is_file()

    def test_env_example_exists(self):
        assert (PROJECT_ROOT / ".env.example").is_file()

    def test_gitignore_exists(self):
        assert (PROJECT_ROOT / ".gitignore").is_file()

    def test_readme_has_content(self):
        readme = (PROJECT_ROOT / "README.md").read_text()
        assert "Celavii-Resolve" in readme
        assert "tools" in readme
