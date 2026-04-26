"""Extended slash command handlers (heavier dependencies).

Split across domain modules for readability; public names are re-exported
here so existing imports like ``from agentao.cli.commands_ext import
handle_crystallize_command`` continue to work unchanged.
"""

from .acp import (
    _handle_inline_interaction,
    handle_acp_command,
    run_acp_prompt_inline,
)
from .agents import _show_agents_dashboard, handle_agent_command
from .crystallize import (
    collect_crystallize_evidence,
    handle_crystallize_command,
    render_available_skills_summary,
    render_crystallize_context,
)
from .memory import show_memories

__all__ = [
    "collect_crystallize_evidence",
    "handle_crystallize_command",
    "render_available_skills_summary",
    "render_crystallize_context",
    "show_memories",
    "_show_agents_dashboard",
    "handle_agent_command",
    "_handle_inline_interaction",
    "handle_acp_command",
    "run_acp_prompt_inline",
]
