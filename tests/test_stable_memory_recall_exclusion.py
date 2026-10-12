"""A stable memory the block's budget left out can still be recalled.

``<memory-stable>`` shows the stable entries that fit ``STABLE_BLOCK_MAX_CHARS``.
Dynamic recall in the volatile tail leaves out what that block shows. It used
to leave out every stable *candidate* instead, so an entry the budget dropped
reached the model through neither block.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agentao.memory.models import SaveMemoryRequest

pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _make_agent():
    with patch("agentao.agent.LLMClient") as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao
        return Agentao(working_directory=Path.cwd())


def _seed_constraints(agent, n=15):
    for i in range(n):
        agent.memory_manager.upsert(SaveMemoryRequest(
            key=f"rule_{i:02d}",
            value=f"zephyr{i:02d}quartz constraint " + "x" * 220,
            type="constraint",
        ))


def test_a_constraint_the_budget_dropped_is_recalled_in_the_tail():
    agent = _make_agent()
    _seed_constraints(agent)

    prompt = agent._build_system_prompt()
    dropped = [i for i in range(15) if f"zephyr{i:02d}quartz" not in prompt]
    assert dropped, "the 15 constraints must overflow the stable block's budget"
    probe = f"zephyr{dropped[0]:02d}quartz"

    agent._last_user_message = f"what does {probe} say?"
    tail = agent._build_volatile_tail()

    assert "<memory-context>" in tail
    assert probe in tail


def test_a_constraint_the_block_shows_is_not_recalled_again():
    agent = _make_agent()
    _seed_constraints(agent)

    prompt = agent._build_system_prompt()
    shown = [i for i in range(15) if f"zephyr{i:02d}quartz" in prompt]
    assert shown
    probe = f"zephyr{shown[-1]:02d}quartz"

    agent._last_user_message = f"what does {probe} say?"
    tail = agent._build_volatile_tail()

    assert probe not in tail
