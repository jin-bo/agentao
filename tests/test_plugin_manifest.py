"""Tests for plugin manifest parsing and path safety validation."""

import json
import os
from pathlib import Path

import pytest

from agentao.plugins.manifest import PluginManifestParser
from agentao.plugins.models import (
    PluginCommandMetadata,
    PluginLoadError,
    PluginManifest,
    PluginWarning,
    PluginWarningSeverity,
)


@pytest.fixture
def parser():
    return PluginManifestParser()


@pytest.fixture
def tmp_plugin(tmp_path):
    """Create a temporary plugin directory with a plugin.json helper."""
    plugin_dir = tmp_path / "test-plugin"
    plugin_dir.mkdir()

    def write_manifest(data: dict):
        (plugin_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")

    return plugin_dir, write_manifest


# ======================================================================
# parse_file / parse_dict — basic
# ======================================================================


class TestParseMinimal:
    def test_minimal_valid(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        write({"name": "demo-plugin"})
        manifest, warnings, errors = parser.parse_file(plugin_dir)

        assert errors == []
        assert manifest.name == "demo-plugin"
        assert manifest.version is None
        assert manifest.unsupported_fields == {}

    def test_missing_plugin_json(self, parser, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        manifest, warnings, errors = parser.parse_file(empty_dir)
        assert errors == []
        assert len(warnings) == 1
        assert warnings[0].severity == PluginWarningSeverity.INFO
        assert "auto-discovery" in warnings[0].message.lower()
        assert manifest.name == "empty"
        # All component fields should be None (triggers auto-discovery)
        assert manifest.commands is None
        assert manifest.skills is None
        assert manifest.agents is None
        assert manifest.hooks is None
        assert manifest.mcp_servers is None

    def test_malformed_json(self, parser, tmp_plugin):
        plugin_dir, _ = tmp_plugin
        (plugin_dir / "plugin.json").write_text("{bad json", encoding="utf-8")
        manifest, warnings, errors = parser.parse_file(plugin_dir)
        assert len(errors) == 1
        assert "parse" in errors[0].message.lower()

    def test_json_not_object(self, parser, tmp_plugin):
        plugin_dir, _ = tmp_plugin
        (plugin_dir / "plugin.json").write_text('"just a string"', encoding="utf-8")
        _, _, errors = parser.parse_file(plugin_dir)
        assert len(errors) == 1
        assert "object" in errors[0].message.lower()


class TestNameValidation:
    def test_missing_name(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        write({"version": "1.0"})
        _, _, errors = parser.parse_file(plugin_dir)
        assert any("name" in e.message.lower() for e in errors)

    def test_empty_name(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        write({"name": "  "})
        _, _, errors = parser.parse_file(plugin_dir)
        assert any("empty" in e.message.lower() for e in errors)

    def test_name_with_spaces(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        write({"name": "bad name"})
        _, _, errors = parser.parse_file(plugin_dir)
        assert any("space" in e.message.lower() for e in errors)


# ======================================================================
# Full manifest
# ======================================================================

class TestFullManifest:
    def test_all_fields(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        write({
            "name": "code-review",
            "version": "0.3.0",
            "description": "Review helper",
            "author": {"name": "Test", "email": "t@t.com"},
            "homepage": "https://example.com",
            "repository": "https://github.com/test",
            "license": "MIT",
            "keywords": ["review", "code"],
            "dependencies": [
                "dep-a",
                {"name": "dep-b", "version": "^1.0"},
            ],
            "skills": ["./skills"],
            "commands": {
                "review": {
                    "source": "./commands/review.md",
                    "description": "Run review",
                    "argumentHint": "[scope]",
                }
            },
            "agents": ["./agents/reviewer.md"],
            "hooks": ["./hooks/hooks.json"],
            "mcpServers": "./.mcp.json",
        })
        manifest, warnings, errors = parser.parse_file(plugin_dir)
        assert errors == []
        assert manifest.name == "code-review"
        assert manifest.version == "0.3.0"
        assert manifest.author is not None
        assert manifest.author.email == "t@t.com"
        assert len(manifest.keywords) == 2
        assert len(manifest.dependencies) == 2
        assert manifest.dependencies[1].version == "^1.0"
        assert isinstance(manifest.commands, dict)
        assert "review" in manifest.commands
        assert manifest.commands["review"].argument_hint == "[scope]"
        assert manifest.skills == ["./skills"]
        assert manifest.agents == ["./agents/reviewer.md"]
        assert manifest.hooks == ["./hooks/hooks.json"]
        assert manifest.mcp_servers == "./.mcp.json"


# ======================================================================
# Unsupported fields
# ======================================================================

class TestUnsupportedFields:
    def test_known_unsupported_fields_warn(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        write({
            "name": "test-unsupported",
            "outputStyles": {"theme": "dark"},
            "lspServers": {},
            "settings": {"a": 1},
        })
        manifest, warnings, errors = parser.parse_file(plugin_dir)
        assert errors == []
        assert len(warnings) == 3
        assert "outputStyles" in manifest.unsupported_fields
        assert "lspServers" in manifest.unsupported_fields

    def test_unknown_field_info(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        write({"name": "test-unknown", "futureField": True})
        manifest, warnings, errors = parser.parse_file(plugin_dir)
        assert errors == []
        assert any("futureField" in w.message for w in warnings)
        assert "futureField" in manifest.unsupported_fields


# ======================================================================
# Commands parsing shapes
# ======================================================================

class TestCommandsParsing:
    def test_commands_string(self, parser):
        m, w, e = parser.parse_dict({"name": "t", "commands": "./cmds"}, plugin_root=Path("/tmp"))
        assert e == []
        assert m.commands == "./cmds"

    def test_commands_list(self, parser):
        m, w, e = parser.parse_dict({"name": "t", "commands": ["./a", "./b"]}, plugin_root=Path("/tmp"))
        assert e == []
        assert m.commands == ["./a", "./b"]

    def test_commands_dict(self, parser):
        m, w, e = parser.parse_dict(
            {"name": "t", "commands": {"do": {"source": "./do.md", "description": "D"}}},
            plugin_root=Path("/tmp"),
        )
        assert e == []
        assert isinstance(m.commands, dict)
        assert m.commands["do"].description == "D"

    def test_commands_invalid_shape(self, parser):
        m, w, e = parser.parse_dict({"name": "t", "commands": 42}, plugin_root=Path("/tmp"))
        assert any("commands" in err.message.lower() for err in e)


# ======================================================================
# Path safety
# ======================================================================

class TestPathSafety:
    def test_safe_relative_path(self, parser, tmp_plugin):
        plugin_dir, write = tmp_plugin
        (plugin_dir / "skills").mkdir()
        write({"name": "safe", "skills": "./skills"})
        manifest, _, _ = parser.parse_file(plugin_dir)
        issues = parser.validate_paths(manifest, plugin_root=plugin_dir)
        errors = [i for i in issues if isinstance(i, PluginLoadError)]
        assert errors == []

    def test_absolute_path_rejected(self, parser, tmp_plugin):
        plugin_dir, _ = tmp_plugin
        manifest = PluginManifest(name="bad", skills="/etc/passwd")
        issues = parser.validate_paths(manifest, plugin_root=plugin_dir)
        errors = [i for i in issues if isinstance(i, PluginLoadError)]
        assert len(errors) == 1
        assert "Absolute" in errors[0].message

    def test_traversal_rejected(self, parser, tmp_plugin):
        plugin_dir, _ = tmp_plugin
        manifest = PluginManifest(name="bad", skills="./../escape")
        issues = parser.validate_paths(manifest, plugin_root=plugin_dir)
        errors = [i for i in issues if isinstance(i, PluginLoadError)]
        assert len(errors) == 1
        assert ".." in errors[0].message

    def test_no_dot_slash_prefix_rejected(self, parser, tmp_plugin):
        plugin_dir, _ = tmp_plugin
        manifest = PluginManifest(name="bad", skills="skills")
        issues = parser.validate_paths(manifest, plugin_root=plugin_dir)
        errors = [i for i in issues if isinstance(i, PluginLoadError)]
        assert len(errors) == 1
        assert "'./' " in errors[0].message or "./" in errors[0].message

    @pytest.mark.skipif(os.name == "nt", reason="symlinks differ on Windows")
    def test_symlink_escape_rejected(self, parser, tmp_plugin):
        plugin_dir, _ = tmp_plugin
        # Create a symlink that points outside plugin root.
        escape_target = plugin_dir.parent / "escape-target"
        escape_target.mkdir()
        link = plugin_dir / "bad-link"
        link.symlink_to(escape_target)

        manifest = PluginManifest(name="bad", skills="./bad-link")
        issues = parser.validate_paths(manifest, plugin_root=plugin_dir)
        errors = [i for i in issues if isinstance(i, PluginLoadError)]
        assert len(errors) == 1
        assert "escapes" in errors[0].message.lower()

    def test_command_source_path_checked(self, parser, tmp_plugin):
        plugin_dir, _ = tmp_plugin
        manifest = PluginManifest(
            name="bad",
            commands={"x": PluginCommandMetadata(source="/etc/passwd")},
        )
        issues = parser.validate_paths(manifest, plugin_root=plugin_dir)
        errors = [i for i in issues if isinstance(i, PluginLoadError)]
        assert len(errors) == 1


# ======================================================================
# No-manifest auto-discovery
# ======================================================================


class TestNoManifestAutoDiscovery:
    """Tests for plugin directories without plugin.json."""

    def test_directory_name_as_plugin_name(self, parser, tmp_path):
        plugin_dir = tmp_path / "my-cool-plugin"
        plugin_dir.mkdir()
        manifest, warnings, errors = parser.parse_file(plugin_dir)
        assert errors == []
        assert manifest.name == "my-cool-plugin"

    def test_synthetic_manifest_fields_are_none(self, parser, tmp_path):
        plugin_dir = tmp_path / "bare-plugin"
        plugin_dir.mkdir()
        manifest, _, errors = parser.parse_file(plugin_dir)
        assert errors == []
        assert manifest.skills is None
        assert manifest.agents is None
        assert manifest.commands is None
        assert manifest.hooks is None
        assert manifest.mcp_servers is None
        assert manifest.version is None
        assert manifest.description is None
