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
    from ..embedding.sessions import persist_agent_session
    try:
        session_file, sid = persist_agent_session(
            cli.agent,
            session_id=cli.current_session_id,
            project_root=cli.agent.working_directory,
        )
        cli.current_session_id = sid
        console.print(f"[dim]Session saved → {sid[:8]} ({session_file.name})[/dim]")
    except Exception:
        pass  # Non-critical


def _apply_lifecycle_result(agent, result, *, print_notices: bool) -> list[str]:
    """Route a lifecycle result's channels. Returns the user notices.

    Two channels, two destinations, and neither is the log. ``model_contexts``
    is rendered exactly as the ``UserPromptSubmit`` attachment path renders it,
    so a hook's context reads the same wherever it came from.
    """
    notices = list(getattr(result, "user_notices", []) or [])
    for ctx in getattr(result, "model_contexts", []) or []:
        try:
            agent.add_message("user", f"[hook_additional_context] context: {ctx}")
        except Exception:
            pass
    if print_notices:
        for notice in notices:
            console.print(f"[yellow]⚠ {notice}[/yellow]")
    return notices


def dispatch_plugin_session_start(agent, session_id: str) -> list[str]:
    """Fire SessionStart plugin hooks for ``agent``. Best-effort.

    Both the interactive CLI and the ``agentao run`` pipeline use this
    so plugin hooks remain consistent across surfaces.
    """
    if not agent._plugin_hook_rules:
        return []
    try:
        from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_session_start(
            session_id=session_id, cwd=cwd,
        )
        result = PluginHookDispatcher(cwd=cwd).dispatch_session_start(
            payload=payload, rules=agent._plugin_hook_rules,
        )
        # Consuming the return value is the whole fix: it existed and was
        # discarded inside a bare ``except: pass``, so nothing downstream could
        # have routed a notice even if the dispatcher had produced one.
        return _apply_lifecycle_result(agent, result, print_notices=False)
    except Exception:
        pass
    return []


def dispatch_plugin_session_end(agent, session_id: str) -> list[str]:
    """Fire SessionEnd plugin hooks for ``agent``. Returns the user notices.

    The JSON half of this event is genuinely conformant — the reference gives it
    no decision control and discards its output — but **exit 2 is a separate
    channel**, and on ``SessionEnd`` it means *stderr shown to the user*. That
    had no sink at all: the return value was thrown away here, and `agentao run`
    emitted its whole output before the dispatch even ran.
    """
    if not agent._plugin_hook_rules:
        return []
    try:
        from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_session_end(
            session_id=session_id, cwd=cwd,
        )
        result = PluginHookDispatcher(cwd=cwd).dispatch_session_end(
            payload=payload, rules=agent._plugin_hook_rules,
        )
        # No model channel here: the event's JSON output is discarded, so only
        # the exit-2 user notice survives.
        return list(result.user_notices)
    except Exception:
        pass
    return []


def _dispatch_session_start_hooks(cli: AgentaoCLI) -> None:
    for notice in dispatch_plugin_session_start(cli.agent, cli.current_session_id):
        console.print(f"[yellow]⚠ {notice}[/yellow]")


def _dispatch_session_end_hooks(cli: AgentaoCLI) -> None:
    for notice in dispatch_plugin_session_end(cli.agent, cli.current_session_id):
        console.print(f"[yellow]⚠ {notice}[/yellow]")


def save_session_on_exit(cli: AgentaoCLI) -> None:
    """Internal helper; delegates to on_session_end()."""
    on_session_end(cli)
