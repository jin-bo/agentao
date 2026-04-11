"""Tests for Phase 2: plugin skills and commands resolution + registration."""

import json
import textwrap
from pathlib import Path

import pytest

from agentao.plugins.models import (
    LoadedPlugin,
    PluginCommandMetadata,
    PluginLoadError,
    PluginManifest,
    PluginSkillEntry,
)
from agentao.plugins.skills import (
    resolve_plugin_entries,
    validate_no_external_collisions,
)


# ======================================================================
# Helpers
# ======================================================================


def _make_skill_dir(root: Path, skill_name: str, description: str = "A skill") -> Path:
    """Create a minimal SKILL.md in a skill directory."""
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(f"""\
        ---
        name: {skill_name}
        description: {description}
        ---

        # {skill_name}

        Skill body content.
        """),
        encoding="utf-8",
    )
    return skill_dir


def _make_command_md(cmds_dir: Path, cmd_name: str, body: str = "Command body.") -> Path:
    cmds_dir.mkdir(parents=True, exist_ok=True)
    md_file = cmds_dir / f"{cmd_name}.md"
    md_file.write_text(body, encoding="utf-8")
    return md_file


def _loaded_plugin(
    tmp_path: Path,
    name: str = "test-plugin",
    *,
    skill_roots: list[Path] | None = None,
    manifest: PluginManifest | None = None,
) -> LoadedPlugin:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return LoadedPlugin(
        name=name,
        version="0.1.0",
        root_path=root,
        source="project",
        manifest=manifest or PluginManifest(name=name),
        skill_roots=skill_roots or [],
    )


# ======================================================================
# Skills resolution
# ======================================================================


class TestSkillResolution:
    def test_discovers_skill_md(self, tmp_path):
        skills_dir = tmp_path / "test-plugin" / "skills"
        _make_skill_dir(skills_dir, "my-skill", "Does things")

        plugin = _loaded_plugin(tmp_path, skill_roots=[skills_dir])
        entries, warnings, errors = resolve_plugin_entries(plugin)

        assert errors == []
        assert len(entries) == 1
        assert entries[0].runtime_name == "test-plugin:my-skill"
        assert entries[0].source_kind == "plugin-skill"
        assert entries[0].description == "Does things"
        assert entries[0].source_path == skills_dir / "my-skill" / "SKILL.md"
        assert "Skill body content." in entries[0].content

    def test_multiple_skills(self, tmp_path):
        skills_dir = tmp_path / "test-plugin" / "skills"
        _make_skill_dir(skills_dir, "alpha")
        _make_skill_dir(skills_dir, "beta")

        plugin = _loaded_plugin(tmp_path, skill_roots=[skills_dir])
        entries, _, errors = resolve_plugin_entries(plugin)
        assert errors == []
        assert len(entries) == 2
        names = {e.runtime_name for e in entries}
        assert names == {"test-plugin:alpha", "test-plugin:beta"}

    def test_missing_skills_dir_warns(self, tmp_path):
        missing = tmp_path / "test-plugin" / "nonexistent"
        plugin = _loaded_plugin(tmp_path, skill_roots=[missing])
        entries, warnings, errors = resolve_plugin_entries(plugin)
        assert entries == []
        assert len(warnings) == 1
        assert "does not exist" in warnings[0].message.lower()

    def test_dir_without_skill_md_skipped(self, tmp_path):
        skills_dir = tmp_path / "test-plugin" / "skills"
        empty_skill = skills_dir / "empty-skill"
        empty_skill.mkdir(parents=True)
        # No SKILL.md

        plugin = _loaded_plugin(tmp_path, skill_roots=[skills_dir])
        entries, _, _ = resolve_plugin_entries(plugin)
        assert entries == []


# ======================================================================
# Commands — file-based
# ======================================================================


class TestCommandsFileBased:
    def test_default_commands_dir(self, tmp_path):
        """When manifest.commands is None, scan <root>/commands/*.md."""
        root = tmp_path / "cmd-plugin"
        root.mkdir()
        _make_command_md(root / "commands", "deploy", "# Deploy\n\nDeploy the app.")

        plugin = LoadedPlugin(
            name="cmd-plugin",
            version=None,
            root_path=root,
            source="project",
            manifest=PluginManifest(name="cmd-plugin"),
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert errors == []
        assert len(entries) == 1
        assert entries[0].runtime_name == "cmd-plugin:deploy"
        assert entries[0].source_kind == "plugin-command"

    def test_commands_string_path(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        cmds = root / "my-cmds"
        _make_command_md(cmds, "check", "Check stuff.")

        manifest = PluginManifest(name="plug", commands="./my-cmds")
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert errors == []
        assert len(entries) == 1
        assert entries[0].runtime_name == "plug:check"

    def test_commands_list_paths(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        _make_command_md(root / "cmds-a", "a", "A")
        _make_command_md(root / "cmds-b", "b", "B")

        manifest = PluginManifest(name="plug", commands=["./cmds-a", "./cmds-b"])
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert errors == []
        names = {e.runtime_name for e in entries}
        assert names == {"plug:a", "plug:b"}


# ======================================================================
# Commands — manifest mapping
# ======================================================================


class TestCommandsMappingFormat:
    def test_source_file(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        cmd_file = root / "commands" / "review.md"
        cmd_file.parent.mkdir(parents=True)
        cmd_file.write_text("# Review\n\nDo a review.", encoding="utf-8")

        manifest = PluginManifest(
            name="plug",
            commands={
                "review": PluginCommandMetadata(
                    source="./commands/review.md",
                    description="Run code review",
                    argument_hint="[scope]",
                ),
            },
        )
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert errors == []
        assert len(entries) == 1
        assert entries[0].runtime_name == "plug:review"
        assert entries[0].description == "Run code review"
        assert entries[0].argument_hint == "[scope]"
        assert "Do a review" in entries[0].content

    def test_inline_content(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()

        manifest = PluginManifest(
            name="plug",
            commands={
                "release-check": PluginCommandMetadata(
                    content="# Release Check\n\nValidate release readiness.",
                    description="Check release",
                ),
            },
        )
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert errors == []
        assert len(entries) == 1
        assert entries[0].runtime_name == "plug:release-check"
        assert entries[0].content == "# Release Check\n\nValidate release readiness."
        assert entries[0].source_path is None

    def test_no_source_or_content_is_error(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()

        manifest = PluginManifest(
            name="plug",
            commands={
                "broken": PluginCommandMetadata(description="Missing content"),
            },
        )
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert len(errors) == 1
        assert "broken" in errors[0].message


# ======================================================================
# Namespacing
# ======================================================================


class TestNamespacing:
    def test_runtime_names_are_namespaced(self, tmp_path):
        skills_dir = tmp_path / "my-plug" / "skills"
        _make_skill_dir(skills_dir, "linter")
        cmds_dir = tmp_path / "my-plug" / "commands"
        _make_command_md(cmds_dir, "fix")

        manifest = PluginManifest(name="my-plug")
        plugin = LoadedPlugin(
            name="my-plug", version=None,
            root_path=tmp_path / "my-plug",
            source="project", manifest=manifest,
            skill_roots=[skills_dir],
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert errors == []
        names = {e.runtime_name for e in entries}
        assert all("my-plug:" in n for n in names)


# ======================================================================
# Collision detection
# ======================================================================


class TestCollisionDetection:
    def test_internal_collision_is_fatal(self, tmp_path):
        """A skill and command with the same runtime_name within one plugin."""
        root = tmp_path / "plug"
        root.mkdir()

        # Create a skill named "review"
        skills_dir = root / "skills"
        _make_skill_dir(skills_dir, "review")

        # Create a command also named "review"
        cmds_dir = root / "commands"
        _make_command_md(cmds_dir, "review", "Review command.")

        manifest = PluginManifest(name="plug")
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
            skill_roots=[skills_dir],
        )
        entries, _, errors = resolve_plugin_entries(plugin)
        assert len(errors) == 1
        assert "duplicate" in errors[0].message.lower() or "collide" in errors[0].message.lower()

    def test_external_collision_detected(self):
        entry = PluginSkillEntry(
            runtime_name="existing-skill",
            plugin_name="test",
            source_kind="plugin-skill",
        )
        errors = validate_no_external_collisions(
            "test", [entry], {"existing-skill", "other-skill"}
        )
        assert len(errors) == 1
        assert "collides" in errors[0].message.lower()

    def test_no_external_collision(self):
        entry = PluginSkillEntry(
            runtime_name="test:new-skill",
            plugin_name="test",
            source_kind="plugin-skill",
        )
        errors = validate_no_external_collisions(
            "test", [entry], {"other-skill"}
        )
        assert errors == []


# ======================================================================
# SkillManager integration
# ======================================================================


class TestSkillManagerRegistration:
    def test_register_plugin_skills(self, tmp_path):
        from agentao.skills.manager import SkillManager

        mgr = SkillManager(skills_dir=str(tmp_path / "empty"))

        entries = [
            PluginSkillEntry(
                runtime_name="demo:linter",
                plugin_name="demo",
                source_kind="plugin-skill",
                description="Runs the linter",
                content="# Linter\n\nRun linting.",
            ),
            PluginSkillEntry(
                runtime_name="demo:deploy",
                plugin_name="demo",
                source_kind="plugin-command",
                description="Deploy app",
                content="# Deploy\n\nDeploy the app.",
            ),
        ]

        errors = mgr.register_plugin_skills(entries)
        assert errors == []
        assert "demo:linter" in mgr.available_skills
        assert "demo:deploy" in mgr.available_skills
        assert mgr.available_skills["demo:linter"]["plugin_name"] == "demo"
        assert mgr.available_skills["demo:linter"]["source_kind"] == "plugin-skill"

    def test_register_collision_with_builtin(self, tmp_path):
        from agentao.skills.manager import SkillManager

        # Create a "native" skill first.
        native_skill = tmp_path / "native-skills" / "my-skill"
        native_skill.mkdir(parents=True)
        (native_skill / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: native\n---\n# My Skill\n",
            encoding="utf-8",
        )

        mgr = SkillManager(skills_dir=str(tmp_path / "native-skills"))
        assert "my-skill" in mgr.available_skills

        # Try to register a plugin entry with the same name.
        entry = PluginSkillEntry(
            runtime_name="my-skill",
            plugin_name="demo",
            source_kind="plugin-skill",
        )
        errors = mgr.register_plugin_skills([entry])
        assert len(errors) == 1
        assert "collides" in errors[0].message.lower()

    def test_get_skill_content_inline(self, tmp_path):
        from agentao.skills.manager import SkillManager

        mgr = SkillManager(skills_dir=str(tmp_path / "empty"))
        entry = PluginSkillEntry(
            runtime_name="demo:inline-cmd",
            plugin_name="demo",
            source_kind="plugin-command",
            content="# Inline\n\nInline content here.",
        )
        mgr.register_plugin_skills([entry])

        content = mgr.get_skill_content("demo:inline-cmd")
        assert content == "# Inline\n\nInline content here."

    def test_plugin_skills_appear_in_listing(self, tmp_path):
        from agentao.skills.manager import SkillManager

        mgr = SkillManager(skills_dir=str(tmp_path / "empty"))
        entry = PluginSkillEntry(
            runtime_name="demo:check",
            plugin_name="demo",
            source_kind="plugin-command",
            content="Check things.",
        )
        mgr.register_plugin_skills([entry])

        assert "demo:check" in mgr.list_available_skills()
        assert "demo:check" in mgr.list_all_skills()
