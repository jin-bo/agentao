"""Skill activation tool."""

from typing import Any, Dict

from .base import Tool


class ActivateSkillTool(Tool):
    """Tool for activating Claude skills."""

    def __init__(self, skill_manager=None):
        """Initialize skill tool.

        Args:
            skill_manager: SkillManager instance
        """
        self.skill_manager = skill_manager

    @property
    def name(self) -> str:
        return "activate_skill"

    @property
    def description(self) -> str:
        return "Activate a Claude skill for specialized tasks. Skills provide enhanced capabilities for specific domains like PDF handling, spreadsheets, presentations, etc."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to activate (e.g., 'pdf', 'xlsx', 'pptx', 'doc-coauthoring')",
                },
                "task_description": {
                    "type": "string",
                    "description": "Description of the task to perform with this skill",
                },
            },
            "required": ["skill_name", "task_description"],
        }

    def execute(self, skill_name: str, task_description: str) -> str:
        """Activate a skill."""
        if not self.skill_manager:
            return "Error: Skill manager not initialized"

        try:
            result = self.skill_manager.activate_skill(skill_name, task_description)
            return result
        except Exception as e:
            return f"Error activating skill: {str(e)}"
