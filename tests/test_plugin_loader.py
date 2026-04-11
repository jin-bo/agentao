"""Tests for plugin discovery, precedence, loading, and diagnostics."""

import json
from pathlib import Path

import pytest

from agentao.plugins.diagnostics import PluginDiagnostics, build_diagnostics
from agentao.plugins.manager import PluginManager
from agentao.plugins.models import LoadedPlugin, PluginLoadError, PluginWarning


def _write_plugin(plugin_dir: Path, manifest: dict):
    """Write a plugin.json into *plugin_dir*.

    *plugin_dir* is the plugin root — caller decides whether it's under
    ``local/``, a marketplace version dir, or anywhere else.
    """
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")


def _write_local_plugin(plugins_dir: Path, name: str, manifest: dict | None = None):
    """Convenience: write a plugin under ``{plugins_dir}/local/{name}/``."""
    plugin_dir = plugins_dir / "local" / name
    _write_plugin(plugin_dir, manifest or {"name": name})
    return plugin_dir


def _write_mp_plugin(plugins_dir: Path, marketplace: str, name: str, version: str, manifest: dict | None = None):
    """Convenience: write a plugin under ``{plugins_dir}/{marketplace}/{name}/{version}/``."""
    plugin_dir = plugins_dir / marketplace / name / version
    _write_plugin(plugin_dir, manifest or {"name": name})
    return plugin_dir


# ======================================================================
# Discovery
# ======================================================================


class TestDiscovery:
    def test_discovers_global_and_project(self, tmp_path):
        """Manual parser test — verifies manifest parsing across two dirs."""
        home = tmp_path / "home"
        cwd = tmp_path / "project"

        global_plugins = home / ".agentao" / "plugins"
        project_plugins = cwd / ".agentao" / "plugins"

        _write_local_plugin(global_plugins, "plug-a")
        _write_local_plugin(project_plugins, "plug-b")

        from agentao.plugins.manifest import PluginManifestParser
        from agentao.plugins.models import PluginCandidate

        parser = PluginManifestParser()
        candidates = []

        # Scan local/ in each plugins dir
        for source_label, plugins_dir in [("global", global_plugins), ("project", project_plugins)]:
            local_dir = plugins_dir / "local"
            if local_dir.is_dir():
                for child in sorted(local_dir.iterdir()):
                    m, w, e = parser.parse_file(child)
                    if not e:
                        candidates.append(PluginCandidate(
                            name=m.name, root_path=child, source=source_label,
                            source_rank=0 if source_label == "global" else 1,
                            manifest=m, marketplace="local",
                            qualified_name=f"{m.name}@local", warnings=w,
                        ))

        assert len(candidates) == 2
        names = {c.name for c in candidates}
        assert names == {"plug-a", "plug-b"}

    def test_inline_plugin(self, tmp_path):
        inline_dir = tmp_path / "my-plugin"
        _write_plugin(inline_dir, {"name": "inline-plug"})

        mgr = PluginManager(cwd=tmp_path, inline_dirs=[inline_dir])
        candidates = mgr.discover_candidates()
        inline = [c for c in candidates if c.source == "inline"]
        assert len(inline) == 1
        assert inline[0].name == "inline-plug"

    def test_missing_dir_no_error(self, tmp_path):
        mgr = PluginManager(cwd=tmp_path)
        # No .agentao/plugins exists — should be fine.
        candidates = mgr.discover_candidates()
        # May pick up global plugins if they exist, but no crash.
        assert isinstance(candidates, list)


# ======================================================================
# Precedence
# ======================================================================


class TestPrecedence:
    def test_project_overrides_global(self, tmp_path):
        from agentao.plugins.models import PluginCandidate, PluginManifest

        global_c = PluginCandidate(
            name="shared",
            root_path=tmp_path / "global" / "shared",
            source="global",
            source_rank=0,
            manifest=PluginManifest(name="shared", version="1.0"),
        )
        project_c = PluginCandidate(
            name="shared",
            root_path=tmp_path / "project" / "shared",
            source="project",
            source_rank=1,
            manifest=PluginManifest(name="shared", version="2.0"),
        )

        mgr = PluginManager(cwd=tmp_path)
        resolved = mgr.resolve_precedence([global_c, project_c])
        assert len(resolved) == 1
        assert resolved[0].manifest.version == "2.0"
        assert resolved[0].source == "project"

    def test_inline_overrides_project(self, tmp_path):
        from agentao.plugins.models import PluginCandidate, PluginManifest

        project_c = PluginCandidate(
            name="shared",
            root_path=tmp_path / "project" / "shared",
            source="project",
            source_rank=1,
            manifest=PluginManifest(name="shared", version="1.0"),
        )
        inline_c = PluginCandidate(
            name="shared",
            root_path=tmp_path / "inline" / "shared",
            source="inline",
            source_rank=2,
            manifest=PluginManifest(name="shared", version="3.0"),
        )

        mgr = PluginManager(cwd=tmp_path)
        resolved = mgr.resolve_precedence([project_c, inline_c])
        assert len(resolved) == 1
        assert resolved[0].manifest.version == "3.0"

    def test_different_names_both_kept(self, tmp_path):
        from agentao.plugins.models import PluginCandidate, PluginManifest

        a = PluginCandidate(
            name="plug-a", root_path=tmp_path / "a", source="global",
            source_rank=0, manifest=PluginManifest(name="plug-a"),
        )
        b = PluginCandidate(
            name="plug-b", root_path=tmp_path / "b", source="project",
            source_rank=1, manifest=PluginManifest(name="plug-b"),
        )

        mgr = PluginManager(cwd=tmp_path)
        resolved = mgr.resolve_precedence([a, b])
        assert len(resolved) == 2


# ======================================================================
# Disable rules
# ======================================================================


class TestDisableRules:
    def test_disabled_plugin_filtered(self, tmp_path):
        from agentao.plugins.models import PluginCandidate, PluginManifest

        cwd = tmp_path / "project"
        config_dir = cwd / ".agentao"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins_config.json").write_text(
            json.dumps({"bad-plug": {"disabled": True}}), encoding="utf-8",
        )

        c = PluginCandidate(
            name="bad-plug", root_path=tmp_path / "x", source="project",
            source_rank=1, manifest=PluginManifest(name="bad-plug"),
        )

        mgr = PluginManager(cwd=cwd)
        result = mgr.filter_disabled([c])
        assert result == []

    def test_not_disabled_passes(self, tmp_path):
        from agentao.plugins.models import PluginCandidate, PluginManifest

        c = PluginCandidate(
            name="good", root_path=tmp_path / "x", source="project",
            source_rank=1, manifest=PluginManifest(name="good"),
        )

        mgr = PluginManager(cwd=tmp_path)
        result = mgr.filter_disabled([c])
        assert len(result) == 1

    def test_disable_with_false_value(self, tmp_path):
        from agentao.plugins.models import PluginCandidate, PluginManifest

        cwd = tmp_path / "project"
        config_dir = cwd / ".agentao"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins_config.json").write_text(
            json.dumps({"off-plug": False}), encoding="utf-8",
        )

        c = PluginCandidate(
            name="off-plug", root_path=tmp_path / "x", source="project",
            source_rank=1, manifest=PluginManifest(name="off-plug"),
        )

        mgr = PluginManager(cwd=cwd)
        result = mgr.filter_disabled([c])
        assert result == []


# ======================================================================
# LoadedPlugin assembly
# ======================================================================


class TestLoadPlugin:
    def test_loads_minimal_plugin(self, tmp_path):
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_local_plugin(plugins_dir, "demo", {"name": "demo", "version": "0.1.0"})

        mgr = PluginManager(cwd=tmp_path / "project")
        candidates = mgr.discover_candidates()
        project_candidates = [c for c in candidates if c.source == "project"]
        assert len(project_candidates) == 1
        loaded = mgr.load_plugin(project_candidates[0])
        assert loaded.name == "demo"
        assert loaded.version == "0.1.0"
        assert loaded.source == "project"
        assert loaded.marketplace == "local"
        assert loaded.qualified_name == "demo@local"

    def test_loads_with_skill_paths(self, tmp_path):
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        plugin_dir = _write_local_plugin(plugins_dir, "demo", {"name": "demo", "skills": ["./skills"]})
        (plugin_dir / "skills").mkdir()

        mgr = PluginManager(cwd=tmp_path / "project")
        candidates = mgr.discover_candidates()
        demo = next(c for c in candidates if c.name == "demo")
        loaded = mgr.load_plugin(demo)
        assert len(loaded.skill_roots) == 1

    def test_broken_plugin_does_not_block_others(self, tmp_path):
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_local_plugin(plugins_dir, "good", {"name": "good", "version": "1.0"})
        # Bad plugin: invalid JSON in local/
        bad_dir = plugins_dir / "local" / "bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "plugin.json").write_text("{bad", encoding="utf-8")

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        assert any(p.name == "good" for p in loaded)
        assert len(mgr.get_errors()) >= 1

    def test_path_safety_rejects_plugin(self, tmp_path):
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_local_plugin(plugins_dir, "unsafe", {"name": "unsafe", "skills": "/etc/passwd"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        assert not any(p.name == "unsafe" for p in loaded)
        assert len(mgr.get_errors()) >= 1


# ======================================================================
# Full pipeline
# ======================================================================


class TestFullPipeline:
    def test_end_to_end(self, tmp_path):
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_local_plugin(plugins_dir, "alpha", {"name": "alpha", "version": "1.0"})
        _write_local_plugin(plugins_dir, "beta", {"name": "beta", "version": "2.0"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        names = {p.name for p in loaded}
        assert {"alpha", "beta"}.issubset(names)

        # list_plugins returns same result
        assert mgr.list_plugins() == loaded


# ======================================================================
# Diagnostics
# ======================================================================


class TestDiagnostics:
    def test_build_diagnostics(self, tmp_path):
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_local_plugin(plugins_dir, "ok")
        bad_dir = plugins_dir / "local" / "bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "plugin.json").write_text("{bad", encoding="utf-8")

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()

        diag = build_diagnostics(loaded, mgr.get_warnings(), mgr.get_errors())
        assert diag.plugin_count >= 1
        assert diag.has_errors
        assert "plugin(s) loaded" in diag.summary()
        assert "error" in diag.summary().lower()

        report = diag.format_report()
        assert "ok" in report
        assert "bad" in report.lower()

    def test_empty_diagnostics(self):
        diag = PluginDiagnostics()
        assert diag.plugin_count == 0
        assert not diag.has_errors
        assert "0 plugin(s)" in diag.summary()


# ======================================================================
# No-manifest loading (plugin.json optional)
# ======================================================================


class TestNoManifestLoading:
    """Integration tests: plugin dirs without plugin.json."""

    def test_no_manifest_local_plugin_discovered(self, tmp_path):
        """A local/ directory without plugin.json should still produce a candidate."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        bare = plugins_dir / "local" / "bare-plugin"
        bare.mkdir(parents=True)

        mgr = PluginManager(cwd=tmp_path / "project")
        candidates = mgr.discover_candidates()
        assert any(c.name == "bare-plugin" for c in candidates)

    def test_no_manifest_loads_successfully(self, tmp_path):
        """Plugin without plugin.json loads through the full pipeline."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        bare = plugins_dir / "local" / "bare-plugin"
        bare.mkdir(parents=True)

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        assert any(p.name == "bare-plugin" for p in loaded)

    def test_no_manifest_skills_autodiscovery(self, tmp_path):
        """skills/ directory auto-discovered when no plugin.json."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        bare = plugins_dir / "local" / "bare-plugin"
        skill_dir = bare / "skills" / "greet"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: greet\ndescription: A greeting skill\n---\nHello!",
            encoding="utf-8",
        )

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "bare-plugin")
        from agentao.plugins.skills import resolve_plugin_entries
        entries, _, _ = resolve_plugin_entries(plugin)
        assert any("greet" in e.runtime_name for e in entries)

    def test_no_manifest_hooks_autodiscovery(self, tmp_path):
        """hooks/hooks.json auto-discovered when no plugin.json."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        bare = plugins_dir / "local" / "bare-plugin"
        bare.mkdir(parents=True)
        hooks_dir = bare / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hooks.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [{"type": "command", "command": "echo hi"}]}}),
            encoding="utf-8",
        )

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "bare-plugin")
        assert len(plugin.hook_specs) == 1

    def test_no_manifest_commands_autodiscovery(self, tmp_path):
        """commands/ auto-discovered via skills.py fallback."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        bare = plugins_dir / "local" / "bare-plugin"
        cmds_dir = bare / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "hello.md").write_text(
            "---\ndescription: Say hello\n---\nHello!",
            encoding="utf-8",
        )

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "bare-plugin")
        from agentao.plugins.skills import resolve_plugin_entries
        entries, _, _ = resolve_plugin_entries(plugin)
        assert any("hello" in e.runtime_name for e in entries)

    def test_no_manifest_inline_plugin(self, tmp_path):
        """Inline --plugin-dir also works without plugin.json."""
        inline_dir = tmp_path / "my-plugin"
        inline_dir.mkdir()

        mgr = PluginManager(cwd=tmp_path, inline_dirs=[inline_dir])
        loaded = mgr.load_plugins()
        assert any(p.name == "my-plugin" for p in loaded)


# ======================================================================
# Marketplace discovery
# ======================================================================


class TestMarketplaceDiscovery:
    """Tests for marketplace-based plugin directory organisation."""

    def test_discovers_marketplace_plugins(self, tmp_path):
        """Three-level marketplace structure is discovered."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_mp_plugin(plugins_dir, "official", "tool-a", "1.0.0",
                         {"name": "tool-a", "version": "1.0.0"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "tool-a")
        assert plugin.marketplace == "official"
        assert plugin.qualified_name == "tool-a@official"
        assert plugin.version == "1.0.0"

    def test_discovers_local_plugins(self, tmp_path):
        """Two-level local/ structure is discovered."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_local_plugin(plugins_dir, "my-tool", {"name": "my-tool", "version": "0.1"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "my-tool")
        assert plugin.marketplace == "local"
        assert plugin.qualified_name == "my-tool@local"

    def test_picks_latest_version(self, tmp_path):
        """When multiple version dirs exist, the latest (by semver) is selected."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_mp_plugin(plugins_dir, "mp", "tool", "1.0.0", {"name": "tool", "version": "1.0.0"})
        _write_mp_plugin(plugins_dir, "mp", "tool", "2.0.0", {"name": "tool", "version": "2.0.0"})
        _write_mp_plugin(plugins_dir, "mp", "tool", "1.5.0", {"name": "tool", "version": "1.5.0"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "tool")
        assert plugin.version == "2.0.0"

    def test_picks_latest_version_double_digits(self, tmp_path):
        """Semantic comparison: 10.0.0 > 2.0.0 (not lexical)."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_mp_plugin(plugins_dir, "mp", "tool", "2.0.0", {"name": "tool", "version": "2.0.0"})
        _write_mp_plugin(plugins_dir, "mp", "tool", "10.0.0", {"name": "tool", "version": "10.0.0"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "tool")
        assert plugin.version == "10.0.0"

    def test_same_name_different_marketplace_single_winner(self, tmp_path):
        """Same plugin name from different marketplaces — only one wins.

        The runtime keys everything by bare plugin name, so allowing
        multiple same-named plugins causes skill/agent collisions.
        The highest-priority candidate wins and a warning is emitted.
        """
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_mp_plugin(plugins_dir, "mp-a", "shared", "1.0",
                         {"name": "shared", "version": "1.0"})
        _write_mp_plugin(plugins_dir, "mp-b", "shared", "2.0",
                         {"name": "shared", "version": "2.0"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        shared_plugins = [p for p in loaded if p.name == "shared"]
        # Only one winner — the other is suppressed with a warning.
        assert len(shared_plugins) == 1
        warnings = mgr.get_warnings()
        override_warnings = [w for w in warnings if "overrides" in w.message and "shared" in w.message]
        assert len(override_warnings) == 1

    def test_inline_overrides_marketplace_same_name(self, tmp_path):
        """Inline --plugin-dir overrides a marketplace plugin with the same name."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_mp_plugin(plugins_dir, "official", "tool", "1.0",
                         {"name": "tool", "version": "1.0"})
        inline_dir = tmp_path / "dev-tool"
        _write_plugin(inline_dir, {"name": "tool", "version": "dev"})

        mgr = PluginManager(cwd=tmp_path / "project", inline_dirs=[inline_dir])
        loaded = mgr.load_plugins()
        tool = next(p for p in loaded if p.name == "tool")
        assert tool.version == "dev"
        assert tool.source == "inline"

    def test_qualified_name_format(self, tmp_path):
        """qualified_name follows 'name@marketplace' format."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_mp_plugin(plugins_dir, "openai-codex", "codex", "1.0.2",
                         {"name": "codex", "version": "1.0.2"})

        mgr = PluginManager(cwd=tmp_path / "project")
        candidates = mgr.discover_candidates()
        c = next(c for c in candidates if c.name == "codex")
        assert c.qualified_name == "codex@openai-codex"
        assert c.marketplace == "openai-codex"

    def test_inline_plugin_no_marketplace(self, tmp_path):
        """Inline plugins have marketplace=None."""
        inline_dir = tmp_path / "my-plugin"
        _write_plugin(inline_dir, {"name": "my-plugin"})

        mgr = PluginManager(cwd=tmp_path, inline_dirs=[inline_dir])
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.name == "my-plugin")
        assert plugin.marketplace is None
        assert plugin.qualified_name is None

    def test_disable_by_qualified_name(self, tmp_path):
        """plugins_config.json can disable by qualified_name."""
        from agentao.plugins.models import PluginCandidate, PluginManifest

        cwd = tmp_path / "project"
        config_dir = cwd / ".agentao"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins_config.json").write_text(
            json.dumps({"tool@mp-a": {"disabled": True}}), encoding="utf-8",
        )

        c = PluginCandidate(
            name="tool", root_path=tmp_path / "x", source="project",
            source_rank=1, manifest=PluginManifest(name="tool"),
            marketplace="mp-a", qualified_name="tool@mp-a",
        )

        mgr = PluginManager(cwd=cwd)
        result = mgr.filter_disabled([c])
        assert result == []

    def test_diagnostics_shows_marketplace(self, tmp_path):
        """format_report() includes marketplace label."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_mp_plugin(plugins_dir, "official", "tool", "1.0",
                         {"name": "tool", "version": "1.0"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        diag = build_diagnostics(loaded, mgr.get_warnings(), mgr.get_errors())
        report = diag.format_report()
        assert "[official]" in report

    def test_hidden_dirs_skipped(self, tmp_path):
        """Directories starting with '.' are ignored."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        hidden = plugins_dir / ".hidden-mp" / "tool" / "1.0"
        hidden.mkdir(parents=True)
        (hidden / "plugin.json").write_text(
            json.dumps({"name": "tool"}), encoding="utf-8",
        )

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        assert not any(p.name == "tool" for p in loaded)

    def test_mixed_local_and_marketplace(self, tmp_path):
        """local/ and marketplace plugins load together."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        _write_local_plugin(plugins_dir, "local-tool", {"name": "local-tool"})
        _write_mp_plugin(plugins_dir, "official", "mp-tool", "1.0",
                         {"name": "mp-tool"})

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        names = {p.name for p in loaded}
        assert {"local-tool", "mp-tool"}.issubset(names)

    def test_no_manifest_marketplace_uses_plugin_folder_name(self, tmp_path):
        """No-manifest marketplace plugin uses the plugin folder name, not version dir."""
        plugins_dir = tmp_path / "project" / ".agentao" / "plugins"
        version_dir = plugins_dir / "official" / "my-tool" / "1.0.0"
        version_dir.mkdir(parents=True)
        # No plugin.json — auto-discovery should use "my-tool" not "1.0.0"

        mgr = PluginManager(cwd=tmp_path / "project")
        loaded = mgr.load_plugins()
        plugin = next(p for p in loaded if p.marketplace == "official")
        assert plugin.name == "my-tool"
        assert plugin.qualified_name == "my-tool@official"
