"""Regression tests for memory injection into system prompt (agentao/agent.py)."""

import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch


@contextmanager
def _agent_with_temp_memory():
    """Yield an Agentao instance whose memory manager uses isolated temp dirs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_proj = Path(tmpdir) / ".agentao"
        tmp_global = Path(tmpdir) / "global"
        with patch("agentao.agent.LLMClient") as mock_llm_cls, \
             patch("agentao.agent.load_mcp_config", return_value=[]), \
             patch("agentao.agent.McpClientManager"):
            mock_llm = Mock()
            mock_llm.logger = Mock()
            mock_llm.model = "test-model"
            mock_llm_cls.return_value = mock_llm

            from agentao.agent import Agentao
            from agentao.memory import MemoryManager
            from agentao.tools.memory import SaveMemoryTool

            agent = Agentao()
            # Override manager with isolated temp dirs
            agent.memory_manager = MemoryManager(
                project_root=tmp_proj, global_root=tmp_global
            )
            agent.memory_tool = SaveMemoryTool(memory_manager=agent.memory_manager)
            yield agent


def test_memories_injected_into_system_prompt():
    with _agent_with_temp_memory() as agent:
        agent.memory_tool.execute(key="project", value="agentao regression", tags=[])
        agent.memory_tool.execute(key="owner", value="jin-bo", tags=[])
        prompt = agent._build_system_prompt()
        assert "<memory-stable>" in prompt
        assert "project" in prompt
        assert "agentao regression" in prompt
        assert "owner" in prompt
        assert "jin-bo" in prompt
        assert "</memory-stable>" in prompt


def test_no_memories_no_section_in_prompt():
    with _agent_with_temp_memory() as agent:
        prompt = agent._build_system_prompt()
        assert "<memory-stable>" not in prompt


def test_memory_tag_appears_in_prompt():
    with _agent_with_temp_memory() as agent:
        agent.memory_tool.execute(key="lang", value="Python", tags=["tech", "stack"])
        prompt = agent._build_system_prompt()
        assert "tech" in prompt
        assert "stack" in prompt


def test_new_memory_reflected_on_next_prompt_build():
    with _agent_with_temp_memory() as agent:
        prompt_before = agent._build_system_prompt()
        assert "<memory-stable>" not in prompt_before

        agent.memory_tool.execute(key="status", value="active", tags=[])
        prompt_after = agent._build_system_prompt()
        assert "status" in prompt_after
        assert "active" in prompt_after


def test_memory_content_is_xml_escaped():
    with _agent_with_temp_memory() as agent:
        agent.memory_tool.execute(
            key="dangerous", value='value with <script> & "quotes"', tags=[]
        )
        prompt = agent._build_system_prompt()
        assert "&lt;script&gt;" in prompt
        assert "&amp;" in prompt
