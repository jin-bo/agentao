"""TodoWrite tool for session-scoped task tracking."""

from typing import Any, Dict, List

from .base import Tool


class TodoWriteTool(Tool):
    """Tool for maintaining a session-scoped task checklist.

    The LLM calls this to track progress on multi-step tasks.
    State is in-memory only — cleared when the session ends or /clear is run.
    """

    def __init__(self):
        self.todos: List[Dict[str, str]] = []

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def description(self) -> str:
        return (
            "Update the task checklist for the current session. "
            "Use this at the start of multi-step tasks to create a plan, "
            "and update statuses (pending → in_progress → completed) as you work. "
            "Always pass the COMPLETE list — this replaces the previous list entirely. "
            "Do NOT use this for trivial single-step requests."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "The complete updated todo list (replaces current list)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Task description",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Current status of the task",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        }

    def execute(self, todos: list) -> str:
        """Replace the current todo list with the provided list."""
        self.todos = [
            {"content": t["content"], "status": t["status"]}
            for t in todos
        ]
        pending = sum(1 for t in self.todos if t["status"] == "pending")
        in_progress = sum(1 for t in self.todos if t["status"] == "in_progress")
        completed = sum(1 for t in self.todos if t["status"] == "completed")
        n = len(self.todos)
        return (
            f"Todo list updated: {n} task(s) "
            f"({completed} completed, {in_progress} in progress, {pending} pending)"
        )

    def get_todos(self) -> List[Dict[str, str]]:
        """Return current todos."""
        return self.todos

    def clear(self) -> None:
        """Clear all todos (called on /clear)."""
        self.todos = []
