"""Tests for AgentManager definition discovery and YAML parsing."""

from pathlib import Path
from unittest.mock import patch

import pytest

from agentao.agents.manager import AgentManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_agent(directory: Path, filename: str, frontmatter: dict, body: str = "You are an agent."):
    directory.mkdir(parents=True, exist_ok=True)
    fm_lines = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    content = f"---\n{fm_lines}\n---\n\n{body}\n"
    (directory / filename).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Built-in agents
# ---------------------------------------------------------------------------

def test_builtin_agents_loaded():
    m = AgentManager()
    agents = m.list_agents()
    # Both built-in definitions must be discovered
    assert "codebase-investigator" in agents
    assert "generalist" in agents


def test_builtin_agent_has_description():
    m = AgentManager()
    agents = m.list_agents()
    assert agents["generalist"]  # non-empty description


def test_builtin_agent_definition_fields():
    m = AgentManager()
    defn = m.definitions.get("generalist")
    assert defn is not None
    assert "name" in defn
    assert "max_turns" in defn
    assert "system_instructions" in defn


# ---------------------------------------------------------------------------
# Project-level agents
# ---------------------------------------------------------------------------

def test_project_agents_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "my-agent.md", {"name": "my-agent", "description": "Custom agent"})
    m = AgentManager()
    assert "my-agent" in m.list_agents()


def test_project_agent_overrides_builtin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "generalist.md",
                 {"name": "generalist", "description": "My custom generalist"},
                 body="Custom instructions.")
    m = AgentManager()
    assert m.list_agents()["generalist"] == "My custom generalist"


# ---------------------------------------------------------------------------
# YAML frontmatter parsing
# ---------------------------------------------------------------------------

def test_parse_frontmatter_full_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "full.md", {
        "name": "full-agent",
        "description": "Full metadata agent",
        "model": "openai/gpt-4o",
        "temperature": "0.5",
        "max_turns": "20",
        "tools": "read_file, glob",
    })
    m = AgentManager()
    defn = m.definitions["full-agent"]
    assert defn["description"] == "Full metadata agent"
    assert defn["model"] == "openai/gpt-4o"
    assert defn["temperature"] == pytest.approx(0.5)
    assert defn["max_turns"] == 20
    assert "read_file" in defn["tools"]
    assert "glob" in defn["tools"]


def test_parse_body_as_system_instructions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "agent.md",
                 {"name": "body-agent", "description": "Has body"},
                 body="You are a specialist. Follow these rules.")
    m = AgentManager()
    assert "specialist" in m.definitions["body-agent"]["system_instructions"]


def test_temperature_default_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "a.md", {"name": "no-temp", "description": "No temp"})
    m = AgentManager()
    assert m.definitions["no-temp"]["temperature"] is None


def test_model_default_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "a.md", {"name": "no-model", "description": "No model"})
    m = AgentManager()
    assert m.definitions["no-model"]["model"] is None


def test_max_turns_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "a.md", {"name": "default-turns", "description": "x"})
    m = AgentManager()
    assert m.definitions["default-turns"]["max_turns"] == 15


def test_tools_none_means_all(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "a.md", {"name": "all-tools", "description": "x"})
    m = AgentManager()
    assert m.definitions["all-tools"]["tools"] is None


def test_tools_string_parsed_as_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    _write_agent(agents_dir, "a.md", {"name": "limited", "description": "x", "tools": "read_file, glob, grep"})
    m = AgentManager()
    tools = m.definitions["limited"]["tools"]
    assert isinstance(tools, list)
    assert "read_file" in tools
    assert "grep" in tools


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def test_list_agents_returns_name_description_dict():
    m = AgentManager()
    result = m.list_agents()
    assert isinstance(result, dict)
    for name, desc in result.items():
        assert isinstance(name, str)
        assert isinstance(desc, str)


def test_get_agent_definition_returns_dict():
    m = AgentManager()
    defn = m.definitions.get("generalist")
    assert isinstance(defn, dict)
    assert defn["name"] == "generalist"


def test_get_agent_definition_missing_returns_none():
    m = AgentManager()
    assert m.definitions.get("totally_nonexistent_xyz") is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_malformed_yaml_skipped_gracefully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "bad.md").write_text("---\n: broken: yaml: [\n---\nbody\n", encoding="utf-8")
    _write_agent(agents_dir, "good.md", {"name": "good-agent", "description": "OK"})
    m = AgentManager()
    # good-agent loaded despite bad.md
    assert "good-agent" in m.definitions


def test_nonexistent_project_agents_dir_graceful(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # No .agentao/agents dir
    m = AgentManager()  # must not raise
    # Built-ins still loaded
    assert "generalist" in m.definitions


def test_missing_name_falls_back_to_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / ".agentao" / "agents"
    agents_dir.mkdir(parents=True)
    # No name in frontmatter
    (agents_dir / "my-agent.md").write_text(
        "---\ndescription: No name\n---\nBody.\n", encoding="utf-8"
    )
    m = AgentManager()
    assert "my-agent" in m.definitions
