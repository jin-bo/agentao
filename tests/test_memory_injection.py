"""Regression tests for memory injection into system prompt (agentao/agent.py)."""

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from agentao.tools.memory import SaveMemoryTool


@contextmanager
def _agent_with_temp_memory():
    """Yield an Agentao instance whose memory tool is backed by a temp file.

    Patches SaveMemoryTool inside agentao.agent so the agent never touches
    `.agentao/memory.json` in the repo root — not even during __init__.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"memories": []}, f)
        tmp = f.name
    try:
        memory_tool = SaveMemoryTool(memory_file=tmp)
        with patch("agentao.agent.LLMClient") as mock_llm_cls, \
             patch("agentao.agent.load_mcp_config", return_value=[]), \
             patch("agentao.agent.McpClientManager"), \
             patch("agentao.agent.SaveMemoryTool", return_value=memory_tool):
            mock_llm = Mock()
            mock_llm.logger = Mock()
            mock_llm.model = "test-model"
            mock_llm_cls.return_value = mock_llm

            from agentao.agent import Agentao
            agent = Agentao()
            yield agent
    finally:
        Path(tmp).unlink(missing_ok=True)


def test_memories_injected_into_system_prompt():
    with _agent_with_temp_memory() as agent:
        agent.memory_tool.execute(key="project", value="agentao regression", tags=[])
        agent.memory_tool.execute(key="owner", value="jin-bo", tags=[])
        prompt = agent._build_system_prompt()
        assert "=== Memories ===" in prompt
        assert "project" in prompt
        assert "agentao regression" in prompt
        assert "owner" in prompt
        assert "jin-bo" in prompt


def test_no_memories_no_section_in_prompt():
    with _agent_with_temp_memory() as agent:
        prompt = agent._build_system_prompt()
        assert "=== Memories ===" not in prompt


def test_memory_tag_appears_in_prompt():
    with _agent_with_temp_memory() as agent:
        agent.memory_tool.execute(key="lang", value="Python", tags=["tech", "stack"])
        prompt = agent._build_system_prompt()
        assert "tech" in prompt
        assert "stack" in prompt


def test_new_memory_reflected_on_next_prompt_build():
    with _agent_with_temp_memory() as agent:
        prompt_before = agent._build_system_prompt()
        assert "=== Memories ===" not in prompt_before

        agent.memory_tool.execute(key="status", value="active", tags=[])
        prompt_after = agent._build_system_prompt()
        assert "status" in prompt_after
        assert "active" in prompt_after
