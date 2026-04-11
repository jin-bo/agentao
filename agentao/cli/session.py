"""Session lifecycle hooks for AgentaoCLI."""

from __future__ import annotations

import uuid as _uuid_mod
from typing import TYPE_CHECKING

from ._globals import console

if TYPE_CHECKING:
    from .app import AgentaoCLI


def on_session_start(cli: AgentaoCLI) -> None:
    """Hook called at the start of every session."""
    if cli.current_session_id is None:
        cli.current_session_id = str(_uuid_mod.uuid4())
    cli.agent._session_id = cli.current_session_id
    cli.agent.tool_runner._session_id = cli.current_session_id

    try:
        cli.agent.memory_manager.archive_session()
    except Exception:
        pass

    _dispatch_session_start_hooks(cli)


def on_session_end(cli: AgentaoCLI) -> None:
    """Hook called at the end of every session (before /clear, /new, or exit)."""
    _dispatch_session_end_hooks(cli)

    if not cli.agent.messages:
        return
    from ..session import save_session
    try:
        active_skills = list(cli.agent.skill_manager.get_active_skills().keys())
        session_file, sid = save_session(
            messages=cli.agent.messages,
            model=cli.agent.get_current_model(),
            active_skills=active_skills,
            session_id=cli.current_session_id,
        )
        cli.current_session_id = sid
        console.print(f"[dim]Session saved → {sid[:8]} ({session_file.name})[/dim]")
    except Exception:
        pass  # Non-critical


def _dispatch_session_start_hooks(cli: AgentaoCLI) -> None:
    """Fire SessionStart plugin hooks with the current (final) session ID."""
    if not cli.agent._plugin_hook_rules:
        return
    try:
        from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        _cwd = cli.agent.working_directory
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_session_start(
            session_id=cli.current_session_id, cwd=_cwd,
        )
        dispatcher = PluginHookDispatcher(cwd=_cwd)
        dispatcher.dispatch_session_start(
            payload=payload, rules=cli.agent._plugin_hook_rules,
        )
    except Exception:
        pass  # Best-effort


def _dispatch_session_end_hooks(cli: AgentaoCLI) -> None:
    """Fire SessionEnd plugin hooks with the current session ID."""
    if not cli.agent._plugin_hook_rules:
        return
    try:
        from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        _cwd = cli.agent.working_directory
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_session_end(
            session_id=cli.current_session_id, cwd=_cwd,
        )
        dispatcher = PluginHookDispatcher(cwd=_cwd)
        dispatcher.dispatch_session_end(
            payload=payload, rules=cli.agent._plugin_hook_rules,
        )
    except Exception:
        pass  # Best-effort


def save_session_on_exit(cli: AgentaoCLI) -> None:
    """Internal helper; delegates to on_session_end()."""
    on_session_end(cli)
