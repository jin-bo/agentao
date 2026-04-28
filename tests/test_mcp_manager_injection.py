"""Injected ``mcp_manager`` must register its tools on the agent."""

from __future__ import annotations

from unittest.mock import Mock, patch

from mcp.types import Tool as McpToolDef


def _make_tool_def(name: str) -> McpToolDef:
    return McpToolDef(
        name=name,
        description=f"fake tool {name}",
        inputSchema={"type": "object", "properties": {}, "required": []},
    )


def test_injected_mcp_manager_tools_are_registered(tmp_path):
    """Host-injected manager: tools must appear in ``agent.tools``."""
    fake_client = Mock()
    fake_client.is_trusted = True

    fake_manager = Mock()
    fake_manager.get_all_tools.return_value = [
        ("alpha", _make_tool_def("ping")),
        ("alpha", _make_tool_def("pong")),
    ]
    fake_manager.get_client.return_value = fake_client
    fake_manager.clients = {"alpha": fake_client}
    fake_manager.call_tool = Mock(return_value="ok")

    with patch("agentao.agent.LLMClient") as mock_llm_cls:
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-test"
        mock_llm_cls.return_value = mock_llm

        from agentao.agent import Agentao
        agent = Agentao(
            working_directory=tmp_path,
            mcp_manager=fake_manager,
        )

    assert "mcp_alpha_ping" in agent.tools.tools
    assert "mcp_alpha_pong" in agent.tools.tools
    # Host owns lifecycle: we stored the manager but never called connect.
    assert agent.mcp_manager is fake_manager
    fake_manager.connect_all.assert_not_called()
