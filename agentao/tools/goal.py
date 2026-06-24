"""The ``update_goal`` injected tool — the agent's only write into goal state.

Injected via :meth:`Agentao.add_tool` for the duration of
``run_goal_continuation`` (NOT registered in ``agent.py::_register_tools()``).
The agent may set only ``complete`` or ``blocked``, and only while the goal is
``active``: after a terminal/paused status the call is a no-op that returns an
error result to the model — keeping the terminal ``limit_reached`` state
immutable except by user action. See
``docs/design/codex-goal-mechanism-review.md`` §E.1.

This module deliberately imports nothing from ``agentao.cli``: it talks to the
goal object through a small duck-typed surface (``is_active`` /
``mark_complete`` / ``mark_blocked`` / ``status_label``) so the tool layer
never depends on the CLI layer.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .base import Tool


class UpdateGoalTool(Tool):
    """Lets the agent mark the active long-task goal complete or blocked."""

    def __init__(self, goal: Any, on_change: Optional[Callable[[], None]] = None):
        super().__init__()
        self._goal = goal
        self._on_change = on_change or (lambda: None)

    @property
    def name(self) -> str:
        return "update_goal"

    @property
    def description(self) -> str:
        return (
            "Update the status of the current long-task goal. Call with "
            "status='complete' ONLY when the objective is fully achieved, or "
            "status='blocked' when you genuinely cannot make further progress "
            "without the user (missing credentials, an ambiguous decision that is "
            "the user's to make). Do NOT mark complete merely because a budget is "
            "nearly exhausted. You cannot pause, resume, or re-budget the goal — "
            "those are the user's controls."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["complete", "blocked"],
                    "description": (
                        "complete = objective fully achieved; "
                        "blocked = cannot proceed without the user"
                    ),
                },
            },
            "required": ["status"],
        }

    @property
    def is_read_only(self) -> bool:
        # update_goal is a host-side *status signal* — it mutates only the goal
        # record (.agentao/goal.json), never the user's files / shell / network.
        # Marking it read-only keeps it callable in read-only mode (tool_planning
        # denies non-read-only tools there), so an analysis-only goal can mark
        # itself complete/blocked instead of running to its budget.
        return True

    def execute(self, status: str) -> str:
        # Active-only guard: terminal/paused goals are immutable by the agent.
        if not self._goal.is_active:
            return (
                f"update_goal ignored: the goal is '{self._goal.status_label()}', "
                "not active, so the agent cannot change its status."
            )
        if status == "complete":
            self._goal.mark_complete()
        elif status == "blocked":
            self._goal.mark_blocked()
        else:
            return (
                f"update_goal error: unknown status {status!r}; "
                "use 'complete' or 'blocked'."
            )
        self._on_change()
        return f"Goal marked '{self._goal.status_label()}'."
