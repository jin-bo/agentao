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

    # Begin a new replay instance if recording is enabled. No-op when
    # replay.enabled=false in .agentao/settings.json.
    try:
        cli.agent.reload_replay_config()
        cli.agent.start_replay(cli.current_session_id)
    except Exception:
        pass

    _dispatch_session_start_hooks(cli)


def on_session_end(cli: AgentaoCLI) -> None:
    """Hook called at the end of every session (before /clear, /new, or exit)."""
    _dispatch_session_end_hooks(cli)

    # Close the current replay instance before persisting the session.
    # The SESSION_REPLAY_PLAN reserves ``session_saved`` for an explicit
    # save entrypoint; the auto-save triggered by /clear / /new / exit
    # does NOT emit it.
    try:
        cli.agent.end_replay()
    except Exception:
        pass

    if not cli.agent.messages:
        return
    from ..embedding.sessions import save_session
    try:
        active_skills = list(cli.agent.skill_manager.get_active_skills().keys())
        session_file, sid = save_session(
            messages=cli.agent.messages,
            model=cli.agent.get_current_model(),
            active_skills=active_skills,
            session_id=cli.current_session_id,
            project_root=cli.agent.working_directory,
        )
        cli.current_session_id = sid
        console.print(f"[dim]Session saved → {sid[:8]} ({session_file.name})[/dim]")
    except Exception:
        pass  # Non-critical


def dispatch_plugin_session_start(agent, session_id: str) -> None:
    """Fire SessionStart plugin hooks for ``agent``. Best-effort.

    Both the interactive CLI and the ``agentao run`` pipeline use this
    so plugin hooks remain consistent across surfaces.
    """
    if not agent._plugin_hook_rules:
        return
    try:
        from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_session_start(
            session_id=session_id, cwd=cwd,
        )
        PluginHookDispatcher(cwd=cwd).dispatch_session_start(
            payload=payload, rules=agent._plugin_hook_rules,
        )
    except Exception:
        pass


def dispatch_plugin_session_end(agent, session_id: str) -> None:
    """Fire SessionEnd plugin hooks for ``agent``. Best-effort."""
    if not agent._plugin_hook_rules:
        return
    try:
        from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_session_end(
            session_id=session_id, cwd=cwd,
        )
        PluginHookDispatcher(cwd=cwd).dispatch_session_end(
            payload=payload, rules=agent._plugin_hook_rules,
        )
    except Exception:
        pass


def _dispatch_session_start_hooks(cli: AgentaoCLI) -> None:
    dispatch_plugin_session_start(cli.agent, cli.current_session_id)


def _dispatch_session_end_hooks(cli: AgentaoCLI) -> None:
    dispatch_plugin_session_end(cli.agent, cli.current_session_id)


def save_session_on_exit(cli: AgentaoCLI) -> None:
    """Internal helper; delegates to on_session_end()."""
    on_session_end(cli)
