"""Issue #17 — MCP server discovery routes through an injected MCPRegistry.

A swappable MCPRegistry means embedded hosts can register MCP servers
programmatically (plugin systems, dynamic discovery, remote
registries) without writing to ``.agentao/mcp.json``. The tests below
confirm wire-up: a custom registry's ``list_servers()`` is the single
source the agent consults, and a registry that returns an empty dict
(or is explicitly ``None``) skips file discovery entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

from agentao.capabilities import (
    FileBackedMCPRegistry,
    InMemoryMCPRegistry,
    MCPRegistry,
)
from agentao.mcp.config import McpServerConfig


@pytest.fixture
def stub_llm_env(monkeypatch):
    """Provide stub credentials so LLMClient construction succeeds."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")
    return None


# ---------------------------------------------------------------------------
# Default impls
# ---------------------------------------------------------------------------


class TestFileBackedMCPRegistry:
    def test_returns_empty_when_no_files(self, tmp_path):
        reg = FileBackedMCPRegistry(project_root=tmp_path)
        assert reg.list_servers() == {}

    def test_reads_project_file(self, tmp_path):
        cfg_dir = tmp_path / ".agentao"
        cfg_dir.mkdir()
        (cfg_dir / "mcp.json").write_text(
            '{"mcpServers": {"alpha": {"command": "echo", "args": []}}}'
        )
        reg = FileBackedMCPRegistry(project_root=tmp_path)
        servers = reg.list_servers()
        assert "alpha" in servers
        assert servers["alpha"]["command"] == "echo"

    def test_user_wins_on_name_collision(self, tmp_path):
        """User-scope wins on collision; the project entry is ignored.

        Locks in the security invariant: a checked-in
        ``.agentao/mcp.json`` cannot silently redirect a known server
        name (e.g. ``github``) to a different transport or endpoint.
        """
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "mcp.json").write_text(
            '{"mcpServers": {"shared": {"command": "user-bin"}}}'
        )
        proj_dir = tmp_path / "proj" / ".agentao"
        proj_dir.mkdir(parents=True)
        (proj_dir / "mcp.json").write_text(
            '{"mcpServers": {"shared": {"command": "proj-bin"}}}'
        )
        reg = FileBackedMCPRegistry(
            project_root=tmp_path / "proj",
            user_root=user_dir,
        )
        assert reg.list_servers()["shared"]["command"] == "user-bin"

    def test_re_reads_disk_on_each_call(self, tmp_path):
        cfg_dir = tmp_path / ".agentao"
        cfg_dir.mkdir()
        cfg_path = cfg_dir / "mcp.json"
        cfg_path.write_text('{"mcpServers": {"v1": {"command": "x"}}}')
        reg = FileBackedMCPRegistry(project_root=tmp_path)
        assert "v1" in reg.list_servers()
        cfg_path.write_text('{"mcpServers": {"v2": {"command": "y"}}}')
        servers = reg.list_servers()
        assert "v2" in servers
        assert "v1" not in servers


class TestInMemoryMCPRegistry:
    def test_returns_empty_for_none(self):
        reg = InMemoryMCPRegistry(None)
        assert reg.list_servers() == {}

    def test_returns_supplied_servers(self):
        reg = InMemoryMCPRegistry({"alpha": {"command": "x"}})
        servers = reg.list_servers()
        assert "alpha" in servers

    def test_caller_mutation_does_not_leak_back(self):
        """``list_servers()`` returns a fresh dict so caller-side
        mutation can't corrupt the registry's inner state."""
        reg = InMemoryMCPRegistry({"alpha": {"command": "x", "args": []}})
        view = reg.list_servers()
        view["alpha"]["command"] = "tampered"
        view["beta"] = {"command": "new"}
        # Re-read returns the original.
        fresh = reg.list_servers()
        assert fresh["alpha"]["command"] == "x"
        assert "beta" not in fresh

    def test_constructor_input_is_isolated(self):
        """A subsequent mutation to the dict the caller passed in must
        not leak into the registry."""
        source: Dict[str, McpServerConfig] = {
            "alpha": {"command": "x", "args": []}
        }
        reg = InMemoryMCPRegistry(source)
        source["alpha"]["command"] = "tampered"
        source["beta"] = {"command": "new"}
        snapshot = reg.list_servers()
        assert snapshot["alpha"]["command"] == "x"
        assert "beta" not in snapshot


# ---------------------------------------------------------------------------
# init_mcp routing — agent consults the injected registry, not disk
# ---------------------------------------------------------------------------


class _RecordingRegistry:
    """A custom MCPRegistry that records every ``list_servers()`` call."""

    def __init__(self, servers: Dict[str, McpServerConfig]) -> None:
        self.servers = servers
        self.calls: List[str] = []

    def list_servers(self) -> Dict[str, McpServerConfig]:
        self.calls.append("list_servers")
        return dict(self.servers)


def _assert_protocol_compatible(reg: _RecordingRegistry) -> MCPRegistry:
    return reg  # static-type witness


def test_init_mcp_calls_injected_registry(stub_llm_env, tmp_path):
    """``init_mcp`` queries the agent's ``_mcp_registry`` rather than
    falling back to the file source."""
    fake = _RecordingRegistry({})

    from agentao.agent import Agentao

    with patch("agentao.tooling.mcp_tools.load_mcp_config") as file_loader:
        agent = Agentao(working_directory=tmp_path, mcp_registry=fake)
        try:
            assert fake.calls == ["list_servers"]
            file_loader.assert_not_called()
        finally:
            agent.close()


def test_init_mcp_falls_back_to_file_when_no_registry(stub_llm_env, tmp_path):
    """The bare-construction path (no factory, no registry) still
    consults the on-disk MCP config — the M5 cut keeps that fallback so
    ``Agentao(working_directory=...)`` outside the factory continues to
    work for repos that already set up ``.agentao/mcp.json``."""
    from agentao.agent import Agentao

    with patch(
        "agentao.tooling.mcp_tools.load_mcp_config",
        return_value={},
    ) as file_loader:
        agent = Agentao(working_directory=tmp_path)
        try:
            file_loader.assert_called_once()
        finally:
            agent.close()


def test_factory_default_uses_file_backed_registry(stub_llm_env, tmp_path):
    """``build_from_environment`` injects ``FileBackedMCPRegistry`` so
    the CLI/ACP path keeps the legacy file-load behavior."""
    from agentao.embedding import build_from_environment

    agent = build_from_environment(working_directory=tmp_path)
    try:
        assert isinstance(agent._mcp_registry, FileBackedMCPRegistry)
    finally:
        agent.close()


def test_factory_accepts_custom_registry(stub_llm_env, tmp_path):
    """``build_from_environment(mcp_registry=...)`` overrides the
    file-backed default. The agent consults the custom registry on
    init, never touching disk."""
    fake = _RecordingRegistry({"custom": {"command": "echo", "args": ["hi"]}})

    from agentao.embedding import build_from_environment

    # Patch the file loader so an accidental disk read trips an assert.
    with patch(
        "agentao.tooling.mcp_tools.load_mcp_config",
        side_effect=AssertionError("file source should be skipped"),
    ):
        agent = build_from_environment(
            working_directory=tmp_path,
            mcp_registry=fake,
        )
        try:
            assert agent._mcp_registry is fake
            assert fake.calls == ["list_servers"]
        finally:
            agent.close()


def test_mcp_registry_and_mcp_manager_mutually_exclusive(stub_llm_env, tmp_path):
    """Passing both a pre-built ``mcp_manager`` and a ``mcp_registry``
    is a programmer error — ``mcp_registry`` is the config source for
    construction, ``mcp_manager`` is the construction outcome itself."""
    from agentao.agent import Agentao
    from agentao.mcp import McpClientManager

    with pytest.raises(ValueError, match="mcp_manager.*mcp_registry"):
        Agentao(
            working_directory=tmp_path,
            mcp_manager=McpClientManager({}),
            mcp_registry=InMemoryMCPRegistry(),
        )
