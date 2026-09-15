"""``ToolRegistry`` records where each tool came from (SUB-02, #256).

A sub-agent decides by that origin which of its parent's tools it gets, so the
origin has to be right at every registration site, and a replacement has to
overwrite it: a host tool built from a built-in's own class is still a host
tool. Class identity was the stand-in before, and it swapped such a
replacement back for the built-in.
"""

from __future__ import annotations

from functools import partial
from unittest.mock import patch

import pytest

from agentao.agents.bg_store import BackgroundTaskStore
from agentao.embedding import build_from_environment
from agentao.tooling.registry import BUILTIN_TOOL_NAMES
from agentao.tools import ReadFileTool
from agentao.tools.base import TOOL_ORIGINS, ToolRegistry

from tests.support.stdio_mcp_server import stdio_server
from tests.support.tools import NamedTool, make_dummy_agent


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


# ── the registry ────────────────────────────────────────────────────────────


def test_a_tool_registered_without_an_origin_is_a_host_tool():
    registry = ToolRegistry()
    registry.register(NamedTool("lookup"))

    assert registry.origin("lookup") == "host"


def test_an_unknown_origin_is_rejected_and_registers_nothing():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="origin"):
        registry.register(NamedTool("lookup"), origin="plugin")

    assert "lookup" not in registry.tools
    assert TOOL_ORIGINS == {"builtin", "host", "mcp", "agent", "plan"}


def test_a_replacement_of_the_same_class_overwrites_the_origin():
    registry = ToolRegistry()
    registry.register(ReadFileTool(), origin="builtin")
    registry.register(ReadFileTool(), replace=True)

    assert registry.origin("read_file") == "host"


def test_a_tool_written_past_register_reads_as_host():
    """Whatever was registered under the name before, its origin does not carry
    over to a tool put in ``tools`` directly."""
    registry = ToolRegistry()
    registry.register(ReadFileTool(), origin="builtin")
    registry.tools["read_file"] = ReadFileTool()

    assert registry.origin("read_file") == "host"


def test_unregister_forgets_the_origin():
    registry = ToolRegistry()
    builtin = ReadFileTool()
    registry.register(builtin, origin="builtin")
    registry.unregister("read_file")
    registry.tools["read_file"] = builtin

    assert registry.origin("read_file") == "host"


def test_the_origin_of_an_unregistered_name_raises_key_error():
    with pytest.raises(KeyError):
        ToolRegistry().origin("read_file")


# ── every registration site ─────────────────────────────────────────────────


def test_each_registration_site_records_its_own_origin(tmp_path):
    agent = make_dummy_agent(
        tmp_path,
        enable_builtin_agents=True,
        bg_store=BackgroundTaskStore(persistence_dir=None),
        extra_tools=[NamedTool("extra_lookup"), ReadFileTool()],
    )
    try:
        agent.add_tool(NamedTool("added_lookup"))
        agent.add_tool(NamedTool("write_file"), replace=True)
        agent.tools.register(NamedTool("registered_lookup"))
        agent.tools.register(ReadFileTool(), replace=True)
        origins = {name: agent.tools.origin(name) for name in agent.tools.tools}
    finally:
        agent.close()

    host = {"extra_lookup", "added_lookup", "registered_lookup", "read_file", "write_file"}
    assert {n for n, o in origins.items() if o == "host"} == host
    assert {n for n, o in origins.items() if o == "agent"} == {
        "agent_codebase_investigator", "agent_generalist",
    }
    # ``check_background_agent`` is registered a second time by the agent-tool
    # pass; it is still a built-in there.
    assert origins["check_background_agent"] == "builtin"
    assert {n for n, o in origins.items() if o == "builtin"} == (
        set(origins) & BUILTIN_TOOL_NAMES
    ) - host


def test_mcp_tools_are_recorded_as_mcp(tmp_path):
    config, _ = stdio_server(tmp_path)
    agent = make_dummy_agent(tmp_path, extra_mcp_servers={"probe": config})
    try:
        mcp_names = [n for n in agent.tools.tools if n.startswith("mcp_")]
        origins = {agent.tools.origin(n) for n in mcp_names}
    finally:
        agent.close()

    assert mcp_names
    assert origins == {"mcp"}


def test_the_clis_plan_tools_are_recorded_as_plan(tmp_path):
    with patch("agentao.cli.app.safe_load_dotenv"), \
            patch("agentao.cli.subcommands._load_and_register_plugins"):
        from agentao.cli import AgentaoCLI

        cli = AgentaoCLI(
            agent_factory=partial(build_from_environment, working_directory=tmp_path),
        )
    try:
        assert cli.agent.tools.origin("plan_save") == "plan"
        assert cli.agent.tools.origin("plan_finalize") == "plan"
    finally:
        cli.agent.close()
