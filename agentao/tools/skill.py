"""Skill activation tool."""

from typing import Any, Dict

from .base import Tool


class ActivateSkillTool(Tool):
    """Tool for activating Agentao skills."""

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
    def is_read_only(self) -> bool:
        # Activation reads SKILL.md and changes only this session's active
        # set, so read-only mode lets it through; the skill's own
        # instructions are still gated call by call.
        return True

    @property
    def description(self) -> str:
        return "Activate a skill. A skill gives you instructions and files for one type of task."

    @property
    def parameters(self) -> Dict[str, Any]:
        skill_prop = {
            "type": "string",
            "description": "Name of the skill to activate",
        }
        # Dynamic enum constraint to prevent typos (similar to Gemini CLI).
        # Dropped when a Skills-enabled MCP server is connected: such a server
        # may serve a skill by URI that its listing omits, and the name
        # ``mcp:<server>:<uri>`` must then be accepted (docs/design/mcp-skills.md D2).
        if self.skill_manager:
            skill_names = list(self.skill_manager.list_available_skills())
            mcp = getattr(self.skill_manager, "mcp_skills", None)
            if mcp is not None and getattr(mcp, "servers", None):
                skill_prop["description"] = (
                    "Name of the skill to activate. A skill from an MCP server is "
                    "named mcp:<server>:<SKILL.md URI>."
                )
            elif skill_names:
                skill_prop["enum"] = skill_names
        return {
            "type": "object",
            "properties": {
                "skill_name": skill_prop,
                "task_description": {
                    "type": "string",
                    "description": "The task that you will do with this skill.",
                },
            },
            "required": ["skill_name", "task_description"],
        }

    def execute(self, skill_name: str, task_description: str) -> str:
        """Activate a skill."""
        if not self.skill_manager:
            return "Error: Skill manager not initialized"

        try:
            # The model's activation: an MCP skill loads only in the version
            # the user approved at the confirmation (docs/design/mcp-skills.md).
            activate = self.skill_manager.activate_skill
            if _takes(activate, "require_mcp_approval"):
                result = activate(skill_name, task_description, require_mcp_approval=True)
            else:
                # A host-injected manager written before the keyword existed;
                # it has no MCP skills to load.
                result = activate(skill_name, task_description)
            return result
        except Exception as e:
            return f"Error activating skill: {str(e)}"


def _takes(fn: Any, keyword: str) -> bool:
    import inspect

    try:
        return keyword in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
