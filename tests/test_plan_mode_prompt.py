"""Regression tests for plan mode system prompt constraints."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agentao.plan import PlanPhase

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _make_agent():
    with patch("agentao.agent.LLMClient") as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao

        return Agentao(working_directory=Path.cwd())


def _activate_plan(agent):
    """Set the shared PlanSession to ACTIVE (replaces old set_plan_mode)."""
    agent._plan_session.phase = PlanPhase.ACTIVE


def _turn_text(agent):
    """Everything the model is instructed with this turn, both messages.

    Since stage 0a the plan prompt rides the request-only volatile tail, not
    the system message. These tests are about the wording the model receives,
    so they read both; which message carries which block is asserted once, in
    ``test_system_prompt_sections.py``.
    """
    return agent._build_system_prompt() + agent._build_volatile_tail()


def test_plan_mode_prompt_contains_proposal_only_constraints():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "=== PLAN MODE ===" in prompt
    # One sentence now carries "reviewable change proposal" and the old
    # "your deliverable is a proposal document"; the prompt wraps inside it.
    assert "write a change\nproposal that the user can review" in prompt
    assert "Do NOT make changes, draft patches, or write" in prompt
    assert "proposal language only" in prompt
    assert "Hard Prohibitions" in prompt
    assert "Do not delegate to agents or sub-agents." in prompt


def test_plan_mode_prompt_still_allows_clarification_and_research():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "ask_user" in prompt
    assert "use tools only to research, inspect, and check facts" in prompt
    # Clarifying questions are a plan-mode rule, not only a core one.
    assert "a requirement\n   is ambiguous, or a design choice has several viable approaches" in prompt


def test_plan_mode_prompt_replaces_autonomous_completion_language():
    agent = _make_agent()

    normal_prompt = _turn_text(agent)
    assert "Work autonomously until the task is complete." in normal_prompt
    assert "Use a tool only when it materially improves correctness" in normal_prompt

    _activate_plan(agent)
    plan_prompt = _turn_text(agent)
    assert "Work autonomously until the task is complete." not in plan_prompt
    assert "Use a tool only when it materially improves correctness" not in plan_prompt
    assert "In plan mode, stop when the research and the proposal are complete." in plan_prompt
    assert "use tools only to research, inspect, and check facts" in plan_prompt


def test_plan_mode_prompt_includes_tool_protocol():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "plan_save" in prompt
    assert "plan_finalize" in prompt
    assert "draft_id" in prompt
    # Stop, and no additional text after a successful finalize.
    assert "After plan_finalize succeeds, stop. Do not write more text in that turn." in prompt


def test_plan_mode_prompt_excludes_agents_section():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "Available Agents" not in prompt


def test_plan_mode_prompt_requires_save_before_ending_turn():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "If a turn produces a new or changed plan, call plan_save(content) before" in prompt
    assert "A plan is complete only after plan_save and plan_finalize both succeed." in prompt


def test_plan_mode_prompt_handles_user_execute_intent():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "If the user says to execute" in prompt
    assert "plan_finalize on the latest draft_id" in prompt


def test_plan_mode_prompt_stale_draft_retry():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "stale draft_id" in prompt
    assert "call plan_save again" in prompt
    assert "call plan_finalize again with the new draft_id" in prompt


def test_plan_mode_prompt_prohibits_pseudo_code():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "no patches, diffs, pseudo-diffs, code\n  edits presented as plan steps" in prompt
    assert "line-by-line edit instructions" in prompt


def test_plan_mode_prompt_skill_boundary():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "Activate a skill only for read-only domain knowledge" in prompt


def test_plan_mode_prompt_tiered_sections():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "Small tasks" in prompt
    assert "Medium and large tasks" in prompt


def test_plan_mode_prompt_allows_only_its_own_persistence_tools():
    """No-writes and plan_save are reconciled in one sentence, not left to conflict."""
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "plan_save and plan_finalize are the only exceptions." in prompt


def test_plan_mode_override_does_not_reach_permissions_or_injection_boundary():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = _turn_text(agent)

    assert "These rules have priority over the core execution rules," in prompt
    assert "They do not override permission\nrestrictions or the Untrusted Input Boundary." in prompt


def test_plan_tools_hidden_outside_plan_mode():
    """plan_save and plan_finalize must not appear in tool list when inactive."""
    agent = _make_agent()
    from agentao.tools.base import Tool

    class _StubTool(Tool):
        def __init__(self, n):
            super().__init__()
            self._n = n
        @property
        def name(self): return self._n
        @property
        def description(self): return "stub"
        @property
        def parameters(self): return {"type": "object", "properties": {}}
        def execute(self, **kw): return ""

    agent.tools.register(_StubTool("plan_save"))
    agent.tools.register(_StubTool("plan_finalize"))

    agent._plan_session.phase = PlanPhase.INACTIVE
    visible = agent.tools.to_openai_format(plan_mode=agent._plan_mode)
    visible_names = {t["function"]["name"] for t in visible}
    assert "plan_save" not in visible_names
    assert "plan_finalize" not in visible_names

    agent._plan_session.phase = PlanPhase.ACTIVE
    visible_plan = agent.tools.to_openai_format(plan_mode=agent._plan_mode)
    visible_plan_names = {t["function"]["name"] for t in visible_plan}
    assert "plan_save" in visible_plan_names
    assert "plan_finalize" in visible_plan_names
