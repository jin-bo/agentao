"""Session lifecycle hooks for AgentaoCLI."""

from __future__ import annotations

import uuid as _uuid_mod
from typing import TYPE_CHECKING

from ..plugins.hooks.lifecycle import fire_session_end, fire_session_start
from ._globals import console
from .transport import print_hook_notice

if TYPE_CHECKING:
    from .app import AgentaoCLI


def on_session_start(cli: AgentaoCLI, *, source: str = "startup") -> None:
    """Hook called at the start of every session.

    ``source`` is the profile's ``SessionStart`` value and is **compared
    against a hook matcher** (`plugins/hooks/_dispatcher.py`), so it is not
    cosmetic: reporting ``startup`` for a `/clear` makes a ``clear`` rule dead
    and fires a ``startup`` rule that should not have run. See
    ``docs/design/session-lifecycle-source-vs-codex.md`` §6.1.
    """
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

    _dispatch_session_start_hooks(cli, source=source)


def on_session_end(cli: AgentaoCLI, *, reason: str = "other") -> None:
    """Hook called at the end of every session (before /clear, /new, or exit).

    ``reason`` is the profile's ``SessionEnd`` value and is matched the same way
    ``source`` is (see :func:`on_session_start`). ``other`` stays the default
    because it is upstream's own value for "none of the named causes", which is
    what a non-interactive `agentao run` genuinely ends with.
    """
    _dispatch_session_end_hooks(cli, reason=reason)

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


def dispatch_plugin_session_start(
    agent, session_id: str, *, source: str = "startup",
) -> list[str]:
    """Fire SessionStart plugin hooks for ``agent``. Best-effort.

    A thin CLI-facing alias for the surface-independent dispatch in
    ``plugins/hooks/lifecycle.py``, kept because the interactive CLI and
    ``agentao run`` both already call it by this name. The shared function is
    where ACP reaches the same behaviour without importing the CLI
    (``tests/test_import_layering.py`` rule 1).
    """
    return fire_session_start(agent, session_id, source=source)


def dispatch_plugin_session_end(
    agent, session_id: str, *, reason: str = "other",
) -> list[str]:
    """Fire SessionEnd plugin hooks for ``agent``. Returns the user notices.

    See :func:`dispatch_plugin_session_start` for why this is an alias. The
    returned notices are this event's **only** output channel — exit 2 on
    ``SessionEnd`` means stderr shown to the user — so a caller that drops the
    list removes the event's whole effect.
    """
    return fire_session_end(agent, session_id, reason=reason)


def _dispatch_session_start_hooks(cli: AgentaoCLI, *, source: str = "startup") -> None:
    for notice in dispatch_plugin_session_start(
        cli.agent, cli.current_session_id, source=source,
    ):
        print_hook_notice(notice)


def _dispatch_session_end_hooks(cli: AgentaoCLI, *, reason: str = "other") -> None:
    for notice in dispatch_plugin_session_end(
        cli.agent, cli.current_session_id, reason=reason,
    ):
        print_hook_notice(notice)
