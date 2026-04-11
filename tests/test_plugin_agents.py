"""Tests for Phase 3: plugin agent resolution and registration."""

import textwrap
from pathlib import Path

import pytest

from agentao.plugins.agents import (
    resolve_plugin_agents,
    validate_no_external_collisions,
)
from agentao.plugins.models import (
    LoadedPlugin,
    PluginAgentDefinition,
    PluginManifest,
)


# ======================================================================
# Helpers
# ======================================================================


def _make_agent_md(
    agents_dir: Path,
    agent_name: str,
    description: str = "An agent",
    body: str = "Agent instructions here.",
) -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    md_file = agents_dir / f"{agent_name}.md"
    md_file.write_text(
        textwrap.dedent(f"""\
        ---
        name: {agent_name}
        description: {description}
        max_turns: 10
        ---

        {body}
        """),
        encoding="utf-8",
    )
    return md_file


def _loaded_plugin(
    tmp_path: Path,
    name: str = "test-plugin",
    *,
    agent_paths: list[Path] | None = None,
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
        agent_paths=agent_paths or [],
    )


# ======================================================================
# Agent resolution — default directory
# ======================================================================


class TestDefaultAgentsDir:
    def test_discovers_agents_from_default_dir(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        _make_agent_md(root / "agents", "reviewer", "Code reviewer")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
        )
        defs, warnings, errors = resolve_plugin_agents(plugin)

        assert errors == []
        assert len(defs) == 1
        assert defs[0].runtime_name == "plug:reviewer"
        assert defs[0].plugin_name == "plug"
        assert defs[0].description == "Code reviewer"
        assert defs[0].source_path == root / "agents" / "reviewer.md"

    def test_no_default_dir_is_fine(self, tmp_path):
        plugin = _loaded_plugin(tmp_path)
        defs, warnings, errors = resolve_plugin_agents(plugin)
        assert defs == []
        assert errors == []

    def test_multiple_agents(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        _make_agent_md(root / "agents", "alpha")
        _make_agent_md(root / "agents", "beta")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
        )
        defs, _, errors = resolve_plugin_agents(plugin)
        assert errors == []
        assert len(defs) == 2
        names = {d.runtime_name for d in defs}
        assert names == {"plug:alpha", "plug:beta"}


# ======================================================================
# Agent resolution — manifest paths
# ======================================================================


class TestManifestAgentPaths:
    def test_single_file_path(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        md = _make_agent_md(root / "custom-agents", "scanner", "Security scanner")

        manifest = PluginManifest(name="plug", agents=["./custom-agents/scanner.md"])
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
            agent_paths=[md],
        )
        defs, _, errors = resolve_plugin_agents(plugin)
        assert errors == []
        assert len(defs) == 1
        assert defs[0].runtime_name == "plug:scanner"

    def test_directory_path(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        agents_dir = root / "my-agents"
        _make_agent_md(agents_dir, "helper")

        manifest = PluginManifest(name="plug", agents=["./my-agents"])
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
            agent_paths=[agents_dir],
        )
        defs, _, errors = resolve_plugin_agents(plugin)
        assert errors == []
        assert len(defs) == 1
        assert defs[0].runtime_name == "plug:helper"

    def test_missing_path_warns(self, tmp_path):
        root = tmp_path / "plug"
        root.mkdir()
        missing = root / "nonexistent.md"

        manifest = PluginManifest(name="plug", agents=["./nonexistent.md"])
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
            agent_paths=[missing],
        )
        defs, warnings, errors = resolve_plugin_agents(plugin)
        assert defs == []
        assert len(warnings) == 1
        assert "not found" in warnings[0].message.lower() or "not a .md" in warnings[0].message.lower()


# ======================================================================
# Malformed agent isolation
# ======================================================================


class TestMalformedAgent:
    def test_malformed_agent_skipped_with_warning(self, tmp_path):
        root = tmp_path / "plug"
        agents_dir = root / "agents"
        agents_dir.mkdir(parents=True)

        # Good agent
        _make_agent_md(agents_dir, "good", "Works fine")

        # Malformed: binary content that can't be parsed
        bad_file = agents_dir / "bad.md"
        bad_file.write_bytes(b"\x80\x81\x82\x83")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
        )
        defs, warnings, errors = resolve_plugin_agents(plugin)

        # Good agent still loaded
        assert len(defs) == 1
        assert defs[0].runtime_name == "plug:good"
        # Bad agent produced a warning
        assert len(warnings) == 1
        assert "bad" in warnings[0].message.lower()

    def test_agent_without_frontmatter(self, tmp_path):
        root = tmp_path / "plug"
        agents_dir = root / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "plain.md").write_text("# Just a heading\n\nNo frontmatter.", encoding="utf-8")

        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="plug"),
        )
        defs, _, errors = resolve_plugin_agents(plugin)
        assert errors == []
        assert len(defs) == 1
        assert defs[0].runtime_name == "plug:plain"
        # Falls back to stem name


# ======================================================================
# Namespacing
# ======================================================================


class TestNamespacing:
    def test_runtime_names_namespaced(self, tmp_path):
        root = tmp_path / "my-plug"
        root.mkdir()
        _make_agent_md(root / "agents", "reviewer")

        plugin = LoadedPlugin(
            name="my-plug", version=None, root_path=root,
            source="project", manifest=PluginManifest(name="my-plug"),
        )
        defs, _, _ = resolve_plugin_agents(plugin)
        assert all(d.runtime_name.startswith("my-plug:") for d in defs)


# ======================================================================
# Collision detection
# ======================================================================


class TestCollisionDetection:
    def test_internal_collision_is_fatal(self, tmp_path):
        root = tmp_path / "plug"
        agents_dir = root / "agents"
        _make_agent_md(agents_dir, "dup")

        # Create a second agents dir with a same-named agent
        agents_dir2 = root / "agents2"
        _make_agent_md(agents_dir2, "dup")

        manifest = PluginManifest(name="plug", agents=["./agents", "./agents2"])
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=root,
            source="project", manifest=manifest,
            agent_paths=[agents_dir, agents_dir2],
        )
        defs, _, errors = resolve_plugin_agents(plugin)
        assert len(errors) == 1
        assert "duplicate" in errors[0].message.lower()

    def test_external_collision_detected(self):
        defn = PluginAgentDefinition(
            runtime_name="existing-agent",
            plugin_name="test",
            source_path=Path("/tmp/a.md"),
            raw_markdown="# Agent",
        )
        errors = validate_no_external_collisions(
            "test", [defn], {"existing-agent", "other-agent"}
        )
        assert len(errors) == 1
        assert "collides" in errors[0].message.lower()

    def test_no_external_collision(self):
        defn = PluginAgentDefinition(
            runtime_name="test:new-agent",
            plugin_name="test",
            source_path=Path("/tmp/a.md"),
            raw_markdown="# Agent",
        )
        errors = validate_no_external_collisions(
            "test", [defn], {"other-agent"}
        )
        assert errors == []


# ======================================================================
# AgentManager integration
# ======================================================================


class TestAgentManagerRegistration:
    def test_register_plugin_agents(self, tmp_path):
        from agentao.agents.manager import AgentManager

        mgr = AgentManager()
        initial_count = len(mgr.definitions)

        defn = PluginAgentDefinition(
            runtime_name="demo:reviewer",
            plugin_name="demo",
            source_path=tmp_path / "reviewer.md",
            raw_markdown=textwrap.dedent("""\
                ---
                name: reviewer
                description: Reviews code
                max_turns: 5
                ---

                Review the code carefully.
            """),
            description="Reviews code",
        )
        errors = mgr.register_plugin_agents([defn])
        assert errors == []
        assert "demo:reviewer" in mgr.definitions
        assert mgr.definitions["demo:reviewer"]["description"] == "Reviews code"
        assert mgr.definitions["demo:reviewer"]["max_turns"] == 5
        assert mgr.definitions["demo:reviewer"]["plugin_name"] == "demo"
        assert len(mgr.definitions) == initial_count + 1

    def test_collision_with_builtin(self):
        from agentao.agents.manager import AgentManager

        mgr = AgentManager()
        if not mgr.definitions:
            pytest.skip("No built-in agents to test collision against")

        existing_name = next(iter(mgr.definitions))
        defn = PluginAgentDefinition(
            runtime_name=existing_name,
            plugin_name="demo",
            source_path=Path("/tmp/x.md"),
            raw_markdown="---\nname: x\n---\nBody",
        )
        errors = mgr.register_plugin_agents([defn])
        assert len(errors) == 1
        assert "collides" in errors[0].message.lower()

    def test_plugin_agents_appear_in_listing(self, tmp_path):
        from agentao.agents.manager import AgentManager

        mgr = AgentManager()
        defn = PluginAgentDefinition(
            runtime_name="demo:helper",
            plugin_name="demo",
            source_path=tmp_path / "helper.md",
            raw_markdown="---\nname: helper\ndescription: Helps\n---\nHelp instructions.",
            description="Helps",
        )
        mgr.register_plugin_agents([defn])
        listing = mgr.list_agents()
        assert "demo:helper" in listing
        assert listing["demo:helper"] == "Helps"
