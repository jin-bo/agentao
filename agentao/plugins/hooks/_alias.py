"""Bidirectional Agentao ↔ Claude tool-name alias mapping.

Plugin hook payloads use Claude-compatible tool names so a hook script
written against Claude Code (``Read``, ``Bash``, ``Edit``) can run
under Agentao without modification.
"""

from __future__ import annotations


# Agentao tool name -> Claude-compatible alias
_TOOL_ALIASES: dict[str, str] = {
    "read_file": "Read",
    "write_file": "Write",
    "replace": "Edit",
    "run_shell_command": "Bash",
    "glob": "Glob",
    "search_file_content": "Grep",
    "web_fetch": "WebFetch",
    "web_search": "WebSearch",
    "list_directory": "LS",
    "save_memory": "SaveMemory",
    "ask_user": "AskUser",
    "todo_write": "TodoWrite",
    "plan_save": "PlanSave",
    "plan_finalize": "PlanFinalize",
    "activate_skill": "ActivateSkill",
}


class ToolAliasResolver:
    """Bidirectional mapping between Agentao tool names and Claude aliases."""

    def __init__(self, extra: dict[str, str] | None = None) -> None:
        self._to_claude = dict(_TOOL_ALIASES)
        if extra:
            self._to_claude.update(extra)
        self._to_agentao = {v: k for k, v in self._to_claude.items()}

    def to_claude_name(self, agentao_name: str) -> str:
        return self._to_claude.get(agentao_name, agentao_name)

    def to_agentao_name(self, claude_name: str) -> str:
        return self._to_agentao.get(claude_name, claude_name)
