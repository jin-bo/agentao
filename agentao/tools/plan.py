"""Plan mode tools — model-callable draft management."""

from typing import Any, Dict

from .base import Tool


class PlanSaveTool(Tool):
    """Persist the current plan draft.  Returns a draft_id for plan_finalize."""

    def __init__(self, controller):
        super().__init__()
        self._controller = controller

    @property
    def name(self) -> str:
        return "plan_save"

    @property
    def description(self) -> str:
        return (
            "Persist the current plan draft to .agentao/plan.md. "
            "Returns a draft_id that must be passed to plan_finalize."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The complete plan document in Markdown.",
                },
            },
            "required": ["content"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def execute(self, **kwargs) -> str:
        content = kwargs.get("content", "")
        if not self._controller.session.is_active:
            return "Error: not in plan mode."
        if not content.strip():
            return "Error: content is empty."
        draft_id = self._controller.save_draft(content)
        return f"Draft saved. draft_id: {draft_id}"


class PlanFinalizeTool(Tool):
    """Mark a saved draft as ready for user approval."""

    def __init__(self, controller):
        super().__init__()
        self._controller = controller

    @property
    def name(self) -> str:
        return "plan_finalize"

    @property
    def description(self) -> str:
        return (
            "Mark the plan as ready for user approval. "
            "Pass the draft_id returned by your most recent plan_save call."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "description": "The draft_id returned by plan_save.",
                },
            },
            "required": ["draft_id"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def execute(self, **kwargs) -> str:
        draft_id = kwargs.get("draft_id", "")
        if not self._controller.session.is_active:
            return "Error: not in plan mode."
        try:
            self._controller.finalize(draft_id)
        except ValueError as e:
            return f"Error: {e}"
        return "Plan finalized. Waiting for user approval. Do not continue output."
