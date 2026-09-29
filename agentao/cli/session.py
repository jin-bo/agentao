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

    # Keyed by the conversation id, so a launch-time ``--resume`` (which set
    # ``current_session_id`` to the loaded session's) adopts the id that
    # session's summaries were written under. See ``archive_session``.
    try:
        cli.agent.memory_manager.archive_session(cli.current_session_id)
    except Exception:
        pass

    # Begin a new replay instance if recording is enabled. No-op when
    # replay.enabled=false in .agentao/settings.json.
    try:
        cli.agent.reload_replay_config()
        cli.agent.start_replay(cli.current_session_id)
    except Exception:
        pass

    _begin_session_checkpoint(cli)
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
            supersedes=_checkpoint_file(cli),
        )
        cli.current_session_id = sid
        _record_checkpoint(cli, session_file)
        console.print(f"[dim]Session saved → {sid[:8]} ({session_file.name})[/dim]")
    except Exception:
        pass  # Non-critical


# ── Per-turn checkpoint ──────────────────────────────────────────────────
#
# ``on_session_end`` was the CLI's only save, so a closed terminal, a kill or
# a crash lost the whole conversation. ``checkpoint_session`` saves after
# every turn instead. It is **not** a session end: no ``SessionEnd`` hook, no
# replay close, no message. Each save replaces the previous file this process
# wrote for the same session (``supersedes``), so one session keeps one file
# however many turns it runs.
#
# State is ``(session_id, file, marker)``. ``marker`` says what history the
# file holds, so a save with nothing new is skipped — after ``/exit`` has
# already saved, and at a Ctrl-C on an idle prompt. It is reset whenever a
# session begins (``reset_session_checkpoint``), so a new or resumed session
# never replaces the file of the one before it.


def _history_marker(cli: AgentaoCLI) -> tuple:
    messages = cli.agent.messages
    return (id(messages), len(messages), id(messages[-1]) if messages else None)


def _checkpoint_file(cli: AgentaoCLI):
    state = getattr(cli, "_session_checkpoint", None)
    if state is None or state[0] != cli.current_session_id:
        return None
    return state[1]


def _record_checkpoint(cli: AgentaoCLI, session_file) -> None:
    cli._session_checkpoint = (cli.current_session_id, session_file, _history_marker(cli))


def reset_session_checkpoint(cli: AgentaoCLI, file=None) -> None:
    """Begin tracking a session whose current history is already on disk (or empty).

    ``file`` is the file that history was loaded from, for a resumed session:
    the next save replaces it (``supersedes``) instead of adding a second file
    for the same session, which every resume would otherwise do — and each
    extra file is one more that the 10-file rotation evicts another session
    for. ``save_session`` still removes it only if it records this session id.
    """
    cli._session_checkpoint = (cli.current_session_id, file, _history_marker(cli))


def _begin_session_checkpoint(cli: AgentaoCLI) -> None:
    """``on_session_start``'s reset, which keeps a launch-time ``--resume``'s file.

    ``resume_session(at_launch=True)`` records the loaded file before
    ``run_loop`` dispatches the session start; the same session id means that
    record is this session's, so only the marker is refreshed.
    """
    state = getattr(cli, "_session_checkpoint", None)
    keep = state[1] if state is not None and state[0] == cli.current_session_id else None
    reset_session_checkpoint(cli, keep)


def checkpoint_session(cli: AgentaoCLI) -> None:
    """Save the conversation if it changed since the last save. Never raises."""
    try:
        if not cli.agent.messages or cli.current_session_id is None:
            return
        state = getattr(cli, "_session_checkpoint", None)
        if (
            state is not None
            and state[0] == cli.current_session_id
            and state[2] == _history_marker(cli)
        ):
            return
        from ..embedding.sessions import persist_agent_session
        session_file, _sid = persist_agent_session(
            cli.agent,
            session_id=cli.current_session_id,
            project_root=cli.agent.working_directory,
            supersedes=_checkpoint_file(cli),
        )
        _record_checkpoint(cli, session_file)
    except Exception:
        try:
            cli.agent.llm.logger.warning("Session checkpoint failed", exc_info=True)
        except Exception:
            pass


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
