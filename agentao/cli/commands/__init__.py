"""Slash command handlers for AgentaoCLI (core set).

Re-exports the public surface that ``cli/input_loop.py`` and
``cli/entrypoints.py`` already import from ``agentao.cli.commands``.
Topic-grouped handlers live in submodules; this package is just the
compat facade — the import sites at the call layer stay unchanged.
"""

from __future__ import annotations

from .context import handle_context_command
from .mcp import handle_mcp_command
from .permission import handle_permission_command, handle_sandbox_command
from .planning import handle_plan_command, handle_todos_command
from .provider import (
    handle_model_command,
    handle_provider_command,
    handle_temperature_command,
)
from .sessions import handle_sessions_command, resume_session
from .tools_intro import handle_tools_command

__all__ = [
    "handle_context_command",
    "handle_mcp_command",
    "handle_model_command",
    "handle_permission_command",
    "handle_plan_command",
    "handle_provider_command",
    "handle_sandbox_command",
    "handle_sessions_command",
    "handle_temperature_command",
    "handle_todos_command",
    "handle_tools_command",
    "resume_session",
]
