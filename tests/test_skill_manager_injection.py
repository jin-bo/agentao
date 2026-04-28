"""Issue #11 — injected ``skill_manager`` skips auto-discovery.

When a host pre-builds its own :class:`SkillManager`, the agent must
use it verbatim without scanning project / bundled skill directories.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch


def test_injected_skill_manager_used_verbatim(tmp_path):
    sentinel = object()
    fake_manager = Mock()
    fake_manager.available_skills = {"sentinel": sentinel}
    fake_manager.active_skills = {}
    fake_manager.disabled_skills = set()
    fake_manager.get_active_skills = Mock(return_value={})

    with patch("agentao.agent.LLMClient") as mock_llm_cls, \
         patch("agentao.tooling.mcp_tools.load_mcp_config", return_value={}), \
         patch("agentao.tooling.mcp_tools.McpClientManager"), \
         patch("agentao.agent.SkillManager") as mock_default_cls:
        mock_llm_cls.return_value.logger = Mock()
        mock_llm_cls.return_value.model = "gpt-test"

        from agentao.agent import Agentao

        agent = Agentao(
            working_directory=tmp_path,
            skill_manager=fake_manager,
        )

    assert agent.skill_manager is fake_manager
    # The default constructor must NOT have been called when an
    # explicit instance was injected.
    mock_default_cls.assert_not_called()
