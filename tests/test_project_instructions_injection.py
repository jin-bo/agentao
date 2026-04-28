"""Issue #11 — injected ``project_instructions`` skips the AGENTAO.md disk read."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch


def test_injected_project_instructions_used_verbatim(tmp_path):
    with patch("agentao.agent.LLMClient") as mock_llm_cls, \
         patch("agentao.tooling.mcp_tools.load_mcp_config", return_value={}), \
         patch("agentao.tooling.mcp_tools.McpClientManager"), \
         patch("agentao.agent.load_project_instructions") as mock_load:
        mock_llm_cls.return_value.logger = Mock()
        mock_llm_cls.return_value.model = "gpt-test"

        from agentao.agent import Agentao

        agent = Agentao(
            working_directory=tmp_path,
            project_instructions="HOST OVERRIDE",
        )

    assert agent.project_instructions == "HOST OVERRIDE"
    # The disk-reading helper must NOT have been called when an
    # explicit instructions string was injected.
    mock_load.assert_not_called()


def test_no_injection_falls_back_to_disk_read(tmp_path):
    """When no override is supplied, the agent loads AGENTAO.md from disk."""
    with patch("agentao.agent.LLMClient") as mock_llm_cls, \
         patch("agentao.tooling.mcp_tools.load_mcp_config", return_value={}), \
         patch("agentao.tooling.mcp_tools.McpClientManager"), \
         patch("agentao.agent.load_project_instructions", return_value="from disk") as mock_load:
        mock_llm_cls.return_value.logger = Mock()
        mock_llm_cls.return_value.model = "gpt-test"

        from agentao.agent import Agentao

        agent = Agentao(working_directory=tmp_path)

    assert agent.project_instructions == "from disk"
    mock_load.assert_called_once()
