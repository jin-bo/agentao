"""Tests for the update_goal injected tool (agentao/tools/goal.py)."""

from agentao.cli.goal_state import GoalState, GoalStatus
from agentao.tools.goal import UpdateGoalTool


def _tool(goal):
    calls = {"n": 0}

    def on_change():
        calls["n"] += 1

    return UpdateGoalTool(goal, on_change), calls


def test_schema():
    tool, _ = _tool(GoalState(objective="x"))
    assert tool.name == "update_goal"
    enum = tool.parameters["properties"]["status"]["enum"]
    assert enum == ["complete", "blocked"]
    assert tool.parameters["required"] == ["status"]


def test_is_read_only_so_it_survives_readonly_mode():
    # tool_planning denies non-read-only tools in read-only mode; update_goal is
    # a host-side status signal and must stay callable so analysis goals can end.
    tool, _ = _tool(GoalState(objective="x"))
    assert tool.is_read_only is True


def test_complete_marks_and_persists():
    goal = GoalState(objective="x")
    tool, calls = _tool(goal)
    out = tool.execute(status="complete")
    assert goal.status == GoalStatus.COMPLETE
    assert "complete" in out
    assert calls["n"] == 1


def test_blocked_marks_and_persists():
    goal = GoalState(objective="x")
    tool, calls = _tool(goal)
    out = tool.execute(status="blocked")
    assert goal.status == GoalStatus.BLOCKED
    assert "blocked" in out
    assert calls["n"] == 1


def test_guard_non_active_is_noop():
    goal = GoalState(objective="x", max_turns=1)
    goal.mark_limit_reached()
    tool, calls = _tool(goal)
    out = tool.execute(status="complete")
    # terminal limit_reached must not be overwritten by the agent
    assert goal.status == GoalStatus.LIMIT_REACHED
    assert "ignored" in out
    assert calls["n"] == 0


def test_guard_paused_is_noop():
    goal = GoalState(objective="x")
    goal.pause()
    tool, calls = _tool(goal)
    out = tool.execute(status="complete")
    assert goal.status == GoalStatus.PAUSED
    assert "ignored" in out
    assert calls["n"] == 0


def test_unknown_status_errors():
    goal = GoalState(objective="x")
    tool, calls = _tool(goal)
    out = tool.execute(status="bogus")
    assert "error" in out.lower()
    assert goal.is_active  # unchanged
    assert calls["n"] == 0
