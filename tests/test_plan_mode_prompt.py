"""Regression tests for plan mode system prompt constraints."""

from unittest.mock import Mock, patch

from agentao.plan import PlanPhase


def _make_agent():
    with patch("agentao.agent.LLMClient") as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao

        return Agentao()


def _activate_plan(agent):
    """Set the shared PlanSession to ACTIVE (replaces old set_plan_mode)."""
    agent._plan_session.phase = PlanPhase.ACTIVE


def test_plan_mode_prompt_contains_proposal_only_constraints():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "=== PLAN MODE ===" in prompt
    assert "reviewable" in prompt
    assert "change proposal" in prompt
    assert "proposal document" in prompt
    assert "proposal language only" in prompt
    assert "Hard Prohibitions" in prompt
    assert "Do not delegate execution to sub-agents." in prompt


def test_plan_mode_prompt_still_allows_clarification_and_research():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "ask_user" in prompt
    assert "read-only tools" in prompt


def test_plan_mode_prompt_replaces_autonomous_completion_language():
    agent = _make_agent()

    normal_prompt = agent._build_system_prompt()
    assert "Work autonomously until the task is fully resolved before yielding back to the user." in normal_prompt
    assert "Use tools proactively only when they materially improve correctness" in normal_prompt

    _activate_plan(agent)
    plan_prompt = agent._build_system_prompt()
    assert "Work autonomously until the task is fully resolved before yielding back to the user." not in plan_prompt
    assert "Use tools proactively only when they materially improve correctness" not in plan_prompt
    assert "In plan mode, stop after the research and proposal are complete." in plan_prompt
    assert "use tools only to research, inspect, and verify facts" in plan_prompt


def test_plan_mode_prompt_includes_tool_protocol():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "plan_save" in prompt
    assert "plan_finalize" in prompt
    assert "draft_id" in prompt
    # New wording: stop + no additional text
    assert "stop immediately" in prompt
    assert "Do not emit any text after plan_finalize" in prompt


def test_plan_mode_prompt_excludes_agents_section():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "Available Agents" not in prompt


def test_plan_mode_prompt_requires_save_before_ending_turn():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "must call plan_save" in prompt
    assert "not considered complete until it has been saved and finalized" in prompt


def test_plan_mode_prompt_handles_user_execute_intent():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "expresses intent to execute" in prompt
    assert "plan_finalize on the latest draft_id" in prompt


def test_plan_mode_prompt_stale_draft_retry():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "stale draft_id" in prompt
    assert "call plan_save again" in prompt


def test_plan_mode_prompt_prohibits_pseudo_code():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "patch-style" in prompt
    assert "diff-shaped" in prompt
    assert "step-by-step code edits" in prompt


def test_plan_mode_prompt_skill_boundary():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "Skills may be activated only for read-only" in prompt


def test_plan_mode_prompt_tiered_sections():
    agent = _make_agent()
    _activate_plan(agent)

    prompt = agent._build_system_prompt()

    assert "Small tasks" in prompt
    assert "Medium to large tasks" in prompt


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

    plan_tool_names = {"plan_save", "plan_finalize"}

    agent._plan_session.phase = PlanPhase.INACTIVE
    visible = [
        t for t in agent.tools.to_openai_format()
        if agent._plan_mode or t["function"]["name"] not in plan_tool_names
    ]
    visible_names = {t["function"]["name"] for t in visible}
    assert "plan_save" not in visible_names
    assert "plan_finalize" not in visible_names

    agent._plan_session.phase = PlanPhase.ACTIVE
    visible_plan = [
        t for t in agent.tools.to_openai_format()
        if agent._plan_mode or t["function"]["name"] not in plan_tool_names
    ]
    visible_plan_names = {t["function"]["name"] for t in visible_plan}
    assert "plan_save" in visible_plan_names
    assert "plan_finalize" in visible_plan_names
