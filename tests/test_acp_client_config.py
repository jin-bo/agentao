"""Tests for agentao.acp_client config models and loader."""

import json
from pathlib import Path

import pytest

from agentao.acp_client.config import load_acp_client_config
from agentao.acp_client.models import (
    AcpClientConfig,
    AcpConfigError,
    AcpServerConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SERVER: dict = {
    "command": "python",
    "args": ["-m", "my_server"],
    "env": {"KEY": "val"},
    "cwd": ".",
}


def _write_acp_config(root: Path, servers: dict) -> Path:
    cfg_dir = root / ".agentao"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "acp.json"
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return path


def _write_raw(root: Path, content: str) -> Path:
    cfg_dir = root / ".agentao"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "acp.json"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_config(self, tmp_path: Path) -> None:
        servers = {
            "my-server": {
                **VALID_SERVER,
                "autoStart": False,
                "startupTimeoutMs": 5000,
                "requestTimeoutMs": 30000,
                "capabilities": {"streaming": True},
                "description": "Test server",
            }
        }
        _write_acp_config(tmp_path, servers)

        cfg = load_acp_client_config(project_root=tmp_path)
        assert "my-server" in cfg.servers

        s = cfg.servers["my-server"]
        assert s.command == "python"
        assert s.args == ["-m", "my_server"]
        assert s.env == {"KEY": "val"}
        assert s.auto_start is False
        assert s.startup_timeout_ms == 5000
        assert s.request_timeout_ms == 30000
        assert s.capabilities == {"streaming": True}
        assert s.description == "Test server"

    def test_minimal_config_defaults(self, tmp_path: Path) -> None:
        _write_acp_config(tmp_path, {"srv": VALID_SERVER})

        cfg = load_acp_client_config(project_root=tmp_path)
        s = cfg.servers["srv"]
        assert s.auto_start is True
        assert s.startup_timeout_ms == 10_000
        assert s.request_timeout_ms == 60_000
        assert s.capabilities == {}
        assert s.description == ""

    def test_multiple_servers(self, tmp_path: Path) -> None:
        servers = {
            "alpha": VALID_SERVER,
            "beta": {**VALID_SERVER, "command": "node"},
        }
        _write_acp_config(tmp_path, servers)

        cfg = load_acp_client_config(project_root=tmp_path)
        assert set(cfg.servers.keys()) == {"alpha", "beta"}
        assert cfg.servers["beta"].command == "node"

    def test_empty_servers(self, tmp_path: Path) -> None:
        _write_acp_config(tmp_path, {})
        cfg = load_acp_client_config(project_root=tmp_path)
        assert cfg.servers == {}


# ---------------------------------------------------------------------------
# Missing config file
# ---------------------------------------------------------------------------


class TestMissingFile:
    def test_no_config_returns_empty(self, tmp_path: Path) -> None:
        cfg = load_acp_client_config(project_root=tmp_path)
        assert cfg.servers == {}

    def test_no_agentao_dir(self, tmp_path: Path) -> None:
        cfg = load_acp_client_config(project_root=tmp_path)
        assert isinstance(cfg, AcpClientConfig)
        assert cfg.servers == {}


# ---------------------------------------------------------------------------
# Invalid JSON
# ---------------------------------------------------------------------------


class TestInvalidJson:
    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, "not valid json {{{")
        with pytest.raises(AcpConfigError, match="invalid JSON"):
            load_acp_client_config(project_root=tmp_path)

    def test_error_includes_path(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, "{bad}")
        with pytest.raises(AcpConfigError, match="acp.json"):
            load_acp_client_config(project_root=tmp_path)


# ---------------------------------------------------------------------------
# Structural errors
# ---------------------------------------------------------------------------


class TestStructuralErrors:
    def test_top_level_not_dict(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, "[]")
        with pytest.raises(AcpConfigError, match="expected a JSON object"):
            load_acp_client_config(project_root=tmp_path)

    def test_servers_not_dict(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, json.dumps({"servers": []}))
        with pytest.raises(AcpConfigError, match="'servers' must be an object"):
            load_acp_client_config(project_root=tmp_path)

    def test_server_entry_not_dict(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, json.dumps({"servers": {"bad": "string"}}))
        with pytest.raises(AcpConfigError, match="server 'bad'"):
            load_acp_client_config(project_root=tmp_path)


# ---------------------------------------------------------------------------
# Missing / bad-type required fields
# ---------------------------------------------------------------------------


class TestRequiredFieldValidation:
    def test_missing_single_field(self, tmp_path: Path) -> None:
        server = {k: v for k, v in VALID_SERVER.items() if k != "command"}
        _write_acp_config(tmp_path, {"srv": server})
        with pytest.raises(AcpConfigError, match="server 'srv'.*command"):
            load_acp_client_config(project_root=tmp_path)

    def test_missing_multiple_fields(self, tmp_path: Path) -> None:
        server = {"cwd": "."}
        _write_acp_config(tmp_path, {"srv": server})
        with pytest.raises(AcpConfigError, match="server 'srv'") as exc_info:
            load_acp_client_config(project_root=tmp_path)
        msg = str(exc_info.value)
        assert "command" in msg
        assert "args" in msg
        assert "env" in msg

    def test_bad_type_command(self, tmp_path: Path) -> None:
        server = {**VALID_SERVER, "command": 123}
        _write_acp_config(tmp_path, {"srv": server})
        with pytest.raises(AcpConfigError, match="'command' must be str"):
            load_acp_client_config(project_root=tmp_path)

    def test_bad_type_args(self, tmp_path: Path) -> None:
        server = {**VALID_SERVER, "args": "not-a-list"}
        _write_acp_config(tmp_path, {"srv": server})
        with pytest.raises(AcpConfigError, match="'args' must be list"):
            load_acp_client_config(project_root=tmp_path)

    def test_bad_type_env(self, tmp_path: Path) -> None:
        server = {**VALID_SERVER, "env": []}
        _write_acp_config(tmp_path, {"srv": server})
        with pytest.raises(AcpConfigError, match="'env' must be dict"):
            load_acp_client_config(project_root=tmp_path)


# ---------------------------------------------------------------------------
# cwd resolution
# ---------------------------------------------------------------------------


class TestCwdResolution:
    def test_relative_cwd_resolved(self, tmp_path: Path) -> None:
        server = {**VALID_SERVER, "cwd": "subdir"}
        _write_acp_config(tmp_path, {"srv": server})
        cfg = load_acp_client_config(project_root=tmp_path)
        expected = str((tmp_path / "subdir").resolve())
        assert cfg.servers["srv"].cwd == expected

    def test_dot_cwd_resolved(self, tmp_path: Path) -> None:
        _write_acp_config(tmp_path, {"srv": VALID_SERVER})
        cfg = load_acp_client_config(project_root=tmp_path)
        expected = str(tmp_path.resolve())
        assert cfg.servers["srv"].cwd == expected

    def test_absolute_cwd_unchanged(self, tmp_path: Path) -> None:
        server = {**VALID_SERVER, "cwd": "/absolute/path"}
        _write_acp_config(tmp_path, {"srv": server})
        cfg = load_acp_client_config(project_root=tmp_path)
        assert cfg.servers["srv"].cwd == "/absolute/path"


# ---------------------------------------------------------------------------
# camelCase mapping
# ---------------------------------------------------------------------------


class TestCamelCaseMapping:
    def test_camel_to_snake(self, tmp_path: Path) -> None:
        server = {
            **VALID_SERVER,
            "autoStart": False,
            "startupTimeoutMs": 1234,
            "requestTimeoutMs": 5678,
        }
        _write_acp_config(tmp_path, {"srv": server})
        cfg = load_acp_client_config(project_root=tmp_path)
        s = cfg.servers["srv"]
        assert s.auto_start is False
        assert s.startup_timeout_ms == 1234
        assert s.request_timeout_ms == 5678


# ---------------------------------------------------------------------------
# Capabilities metadata
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_capabilities_round_trip(self, tmp_path: Path) -> None:
        caps = {"streaming": True, "tools": ["search", "read"]}
        server = {**VALID_SERVER, "capabilities": caps}
        _write_acp_config(tmp_path, {"srv": server})
        cfg = load_acp_client_config(project_root=tmp_path)
        assert cfg.servers["srv"].capabilities == caps


# ---------------------------------------------------------------------------
# Explicit project_root vs default
# ---------------------------------------------------------------------------


class TestProjectRoot:
    def test_explicit_project_root(self, tmp_path: Path) -> None:
        _write_acp_config(tmp_path, {"srv": VALID_SERVER})
        cfg = load_acp_client_config(project_root=tmp_path)
        assert "srv" in cfg.servers

    def test_default_project_root_uses_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_acp_config(tmp_path, {"srv": VALID_SERVER})
        monkeypatch.chdir(tmp_path)
        cfg = load_acp_client_config()
        assert "srv" in cfg.servers
