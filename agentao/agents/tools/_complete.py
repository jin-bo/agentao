"""``CompleteTaskTool`` and the ``TaskComplete`` exception it raises.

Sub-agents call ``complete_task(result=...)`` to return their final
answer. The tool's ``execute`` raises a ``TaskComplete`` exception
which the wrapper's chat loop catches as a normal terminal signal —
this is the only Tool that uses control-flow-by-exception, kept here
so the convention stays in one place.
"""

from __future__ import annotations

from typing import Any, Dict

from ...tools.base import Tool


class TaskComplete(Exception):
    """Raised by CompleteTaskTool to signal sub-agent task completion."""

    def __init__(self, result: str):
        self.result = result


class CompleteTaskTool(Tool):
    """Tool that sub-agents call to return their result."""

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return (
            "Call this tool when you have completed the assigned task. "
            "Pass the final result as a string. You MUST call this tool to finish."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The final result of the completed task",
                }
            },
            "required": ["result"],
        }

    def execute(self, result: str) -> str:
        raise TaskComplete(result)
