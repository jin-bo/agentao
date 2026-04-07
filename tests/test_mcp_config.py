"""Tests for MCP configuration loading and environment variable expansion."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agentao.mcp.config import (
    expand_env_vars,
    _expand_config_env,
    _load_json_file,
    load_mcp_config,
    save_mcp_config,
)


# ---------------------------------------------------------------------------
# expand_env_vars
# ---------------------------------------------------------------------------

def test_expand_dollar_syntax(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "abc123")
    assert expand_env_vars("Bearer $MY_TOKEN") == "Bearer abc123"


def test_expand_brace_syntax(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "xyz")
    assert expand_env_vars("Bearer ${MY_TOKEN}") == "Bearer xyz"


def test_expand_missing_var_returns_empty(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert expand_env_vars("$MISSING_VAR") == ""


def test_expand_no_vars():
    assert expand_env_vars("plain string") == "plain string"


def test_expand_multiple_vars(monkeypatch):
    monkeypatch.setenv("HOST", "localhost")
    monkeypatch.setenv("PORT", "8080")
    assert expand_env_vars("$HOST:$PORT") == "localhost:8080"


# ---------------------------------------------------------------------------
# _expand_config_env
# ---------------------------------------------------------------------------

def test_expand_env_dict(monkeypatch):
    monkeypatch.setenv("SECRET", "s3cr3t")
    config = {"env": {"API_KEY": "$SECRET"}}
    result = _expand_config_env(config)
    assert result["env"]["API_KEY"] == "s3cr3t"


def test_expand_headers(monkeypatch):
    monkeypatch.setenv("TOKEN", "tok")
    config = {"headers": {"Authorization": "Bearer $TOKEN"}}
    result = _expand_config_env(config)
    assert result["headers"]["Authorization"] == "Bearer tok"


def test_expand_list_args(monkeypatch):
    monkeypatch.setenv("PKG", "@scope/pkg")
    config = {"args": ["npx", "-y", "$PKG"]}
    result = _expand_config_env(config)
    assert result["args"] == ["npx", "-y", "@scope/pkg"]


def test_expand_non_string_values_unchanged():
    config = {"timeout": 30, "trust": True}
    result = _expand_config_env(config)
    assert result["timeout"] == 30
    assert result["trust"] is True


def test_expand_config_preserves_other_keys():
    config = {"command": "npx", "timeout": 60}
    result = _expand_config_env(config)
    assert result["command"] == "npx"
    assert result["timeout"] == 60


# ---------------------------------------------------------------------------
# _load_json_file
# ---------------------------------------------------------------------------

def test_load_json_file_valid(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"key": "value"}), encoding="utf-8")
    assert _load_json_file(f) == {"key": "value"}


def test_load_json_file_missing(tmp_path):
    assert _load_json_file(tmp_path / "nonexistent.json") == {}


def test_load_json_file_invalid_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not json {{{", encoding="utf-8")
    assert _load_json_file(f) == {}


def test_load_json_file_empty_file(tmp_path):
    f = tmp_path / "empty.json"
    f.write_text("", encoding="utf-8")
    assert _load_json_file(f) == {}


# ---------------------------------------------------------------------------
# load_mcp_config
# ---------------------------------------------------------------------------

def _write_mcp(path: Path, servers: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_load_mcp_config_global_only(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_mcp(tmp_path / "home" / ".agentao" / "mcp.json", {
        "global-server": {"command": "npx", "args": []}
    })
    result = load_mcp_config()
    assert "global-server" in result


def test_load_mcp_config_project_only(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_mcp(tmp_path / ".agentao" / "mcp.json", {
        "project-server": {"command": "python", "args": ["-m", "server"]}
    })
    result = load_mcp_config()
    assert "project-server" in result


def test_load_mcp_config_project_overrides_global(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_mcp(tmp_path / "home" / ".agentao" / "mcp.json", {
        "shared": {"command": "global-cmd", "args": []}
    })
    _write_mcp(tmp_path / ".agentao" / "mcp.json", {
        "shared": {"command": "project-cmd", "args": []}
    })
    result = load_mcp_config()
    assert result["shared"]["command"] == "project-cmd"


def test_load_mcp_config_merged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_mcp(tmp_path / "home" / ".agentao" / "mcp.json", {
        "global-svc": {"command": "ga", "args": []}
    })
    _write_mcp(tmp_path / ".agentao" / "mcp.json", {
        "project-svc": {"command": "pa", "args": []}
    })
    result = load_mcp_config()
    assert "global-svc" in result
    assert "project-svc" in result


def test_load_mcp_config_env_vars_expanded(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_KEY", "secret")
    _write_mcp(tmp_path / ".agentao" / "mcp.json", {
        "svc": {"headers": {"Authorization": "Bearer $API_KEY"}}
    })
    result = load_mcp_config()
    assert result["svc"]["headers"]["Authorization"] == "Bearer secret"


def test_load_mcp_config_no_files_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    assert load_mcp_config() == {}


# ---------------------------------------------------------------------------
# save_mcp_config
# ---------------------------------------------------------------------------

def test_save_mcp_config_project(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    servers = {"my-server": {"command": "cmd", "args": []}}
    path = save_mcp_config(servers, global_config=False)
    saved = json.loads(path.read_text())
    assert saved["mcpServers"] == servers


def test_save_mcp_config_global(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    servers = {"global-svc": {"url": "https://example.com/sse"}}
    path = save_mcp_config(servers, global_config=True)
    assert "home" in str(path)
    saved = json.loads(path.read_text())
    assert "global-svc" in saved["mcpServers"]


def test_save_mcp_config_preserves_other_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    # Write existing file with extra key
    cfg_dir = tmp_path / ".agentao"
    cfg_dir.mkdir()
    (cfg_dir / "mcp.json").write_text(
        json.dumps({"otherKey": "preserved", "mcpServers": {}}), encoding="utf-8"
    )
    save_mcp_config({"new-svc": {}}, global_config=False)
    saved = json.loads((cfg_dir / "mcp.json").read_text())
    assert saved["otherKey"] == "preserved"
    assert "new-svc" in saved["mcpServers"]
