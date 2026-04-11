"""Tests for Phase 4: plugin MCP server resolution and merge."""

import json
from pathlib import Path

import pytest

from agentao.plugins.mcp import (
    McpMergeResult,
    merge_plugin_mcp_servers,
    resolve_plugin_mcp_servers,
)
from agentao.plugins.models import LoadedPlugin, PluginManifest


# ======================================================================
# Helpers
# ======================================================================


def _plugin(
    tmp_path: Path,
    name: str,
    *,
    mcp_servers: dict | None = None,
    manifest_mcp: str | dict | None = None,
) -> LoadedPlugin:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return LoadedPlugin(
        name=name,
        version=None,
        root_path=root,
        source="project",
        manifest=PluginManifest(name=name, mcp_servers=manifest_mcp),
        mcp_servers=mcp_servers or {},
    )


# ======================================================================
# resolve_plugin_mcp_servers
# ======================================================================


class TestResolvePluginMcp:
    def test_inline_mcp_servers(self, tmp_path):
        """manifest mcpServers as inline object — already resolved in plugin.mcp_servers."""
        servers = {
            "localdocs": {"command": "npx", "args": ["-y", "server-fs", "./docs"]},
        }
        plugin = _plugin(tmp_path, "plug", mcp_servers=servers, manifest_mcp=servers)

        resolved, warnings = resolve_plugin_mcp_servers(plugin)
        assert warnings == []
        assert "localdocs" in resolved
        assert resolved["localdocs"]["command"] == "npx"

    def test_default_mcp_json(self, tmp_path):
        """When manifest.mcp_servers is None, reads .mcp.json from plugin root."""
        root = tmp_path / "plug"
        root.mkdir(parents=True)
        mcp_data = {
            "mcpServers": {
                "myserver": {"command": "node", "args": ["server.js"]},
            }
        }
        (root / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
            mcp_servers={},
        )
        resolved, warnings = resolve_plugin_mcp_servers(plugin)
        assert warnings == []
        assert "myserver" in resolved

    def test_default_mcp_json_flat_format(self, tmp_path):
        """Flat dict (no mcpServers wrapper) also works."""
        root = tmp_path / "plug"
        root.mkdir(parents=True)
        mcp_data = {"flat-server": {"command": "echo", "args": ["hi"]}}
        (root / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
            mcp_servers={},
        )
        resolved, _ = resolve_plugin_mcp_servers(plugin)
        assert "flat-server" in resolved

    def test_no_mcp_at_all(self, tmp_path):
        plugin = _plugin(tmp_path, "bare")
        resolved, warnings = resolve_plugin_mcp_servers(plugin)
        assert resolved == {}
        assert warnings == []

    def test_malformed_mcp_json_warns(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir(parents=True)
        (root / ".mcp.json").write_text("{bad json", encoding="utf-8")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
            mcp_servers={},
        )
        resolved, warnings = resolve_plugin_mcp_servers(plugin)
        assert resolved == {}
        assert len(warnings) == 1
        assert "parse" in warnings[0].message.lower() or "could not" in warnings[0].message.lower()


# ======================================================================
# merge_plugin_mcp_servers
# ======================================================================


class TestMergePluginMcp:
    def test_basic_merge(self, tmp_path):
        base = {"existing": {"command": "base-cmd"}}
        plug = _plugin(tmp_path, "plug", mcp_servers={
            "new-server": {"command": "plug-cmd"},
        })

        result = merge_plugin_mcp_servers(base, [plug])
        assert result.errors == []
        assert "existing" in result.servers
        assert "new-server" in result.servers

    def test_multiple_plugins_merge(self, tmp_path):
        base: dict = {}
        p1 = _plugin(tmp_path, "p1", mcp_servers={"s1": {"command": "c1"}})
        p2 = _plugin(tmp_path, "p2", mcp_servers={"s2": {"command": "c2"}})

        result = merge_plugin_mcp_servers(base, [p1, p2])
        assert result.errors == []
        assert set(result.servers.keys()) == {"s1", "s2"}

    def test_collision_with_base(self, tmp_path):
        base = {"shared": {"command": "base-cmd"}}
        plug = _plugin(tmp_path, "plug", mcp_servers={
            "shared": {"command": "plug-cmd"},
        })

        result = merge_plugin_mcp_servers(base, [plug])
        assert len(result.errors) == 1
        assert "shared" in result.errors[0].message
        assert "base" in result.errors[0].message
        # Base version preserved, plugin version rejected.
        assert result.servers["shared"]["command"] == "base-cmd"

    def test_collision_between_plugins(self, tmp_path):
        base: dict = {}
        p1 = _plugin(tmp_path, "p1", mcp_servers={"dup": {"command": "c1"}})
        p2 = _plugin(tmp_path, "p2", mcp_servers={"dup": {"command": "c2"}})

        result = merge_plugin_mcp_servers(base, [p1, p2])
        assert len(result.errors) == 1
        assert "dup" in result.errors[0].message
        # First plugin's version kept; second rejected.
        assert result.servers["dup"]["command"] == "c1"

    def test_collision_rejects_whole_plugin(self, tmp_path):
        """If a plugin has any collision, ALL its servers are rejected."""
        base = {"existing": {"command": "base"}}
        plug = _plugin(tmp_path, "plug", mcp_servers={
            "existing": {"command": "bad"},  # collision
            "unique": {"command": "also-rejected"},
        })

        result = merge_plugin_mcp_servers(base, [plug])
        assert len(result.errors) == 1
        assert "unique" not in result.servers  # atomically rejected
        assert result.servers["existing"]["command"] == "base"

    def test_no_plugins(self, tmp_path):
        base = {"a": {"command": "x"}}
        result = merge_plugin_mcp_servers(base, [])
        assert result.servers == base
        assert result.errors == []

    def test_plugin_without_mcp(self, tmp_path):
        base = {"a": {"command": "x"}}
        plug = _plugin(tmp_path, "bare")
        result = merge_plugin_mcp_servers(base, [plug])
        assert result.servers == base
        assert result.errors == []


# ======================================================================
# End-to-end with default .mcp.json
# ======================================================================


class TestEndToEnd:
    def test_default_mcp_json_merged(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir(parents=True)
        mcp_data = {
            "mcpServers": {
                "plugin-srv": {"command": "node", "args": ["srv.js"]},
            }
        }
        (root / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
            mcp_servers={},
        )

        base = {"base-srv": {"command": "base"}}
        result = merge_plugin_mcp_servers(base, [plugin])
        assert result.errors == []
        assert "base-srv" in result.servers
        assert "plugin-srv" in result.servers
