"""``SessionStart`` / ``SessionEnd`` for the ACP surface.

The dispatch itself is surface-independent and lives in
``agentao/plugins/hooks/lifecycle.py`` — ACP may not import the CLI
(``tests/test_import_layering.py`` rule 1), and the CLI's own helpers are now
aliases for the same functions. What is ACP-specific is *where the two channels
go*: injected context lands in the session's own history, and the exit-2 user
notices ride a ``session/update``.

**Delivery is best-effort, and on ``session/new`` it is weaker than that.** The
notice is written before the response that tells the client which sessionId it
just created, so a strict client may drop it as referring to an unknown session.
That is accepted rather than worked around: the event's substantive channel is
the context injected into history, which is unaffected, and buffering a
diagnostic until a turn that may never come trades a dropped message for a
message that never arrives.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..plugins.hooks.lifecycle import fire_session_end, fire_session_start
from ._transport_helpers import write_user_notice

logger = logging.getLogger(__name__)

__all__ = ["session_start_publisher", "make_notifier", "fire_end_for_state"]


def make_notifier(server: Any, session_id: str) -> Callable[[str], None]:
    """A one-argument notice sender bound to this session.

    Stored on :class:`AcpSessionState` so :meth:`AcpSessionState.close` can
    deliver ``SessionEnd`` notices without ``models.py`` importing the server —
    which would be a cycle, the server module already importing the models.
    """
    def _notify(text: str) -> None:
        write_user_notice(server, session_id, text)
    return _notify


def session_start_publisher(
    server: Any, agent: Any, session_id: str, *, source: str,
) -> Callable[[Any], None]:
    """A ``before_publish`` callback that fires ``SessionStart``.

    Handed to :meth:`AcpSessionManager.create`, which runs it under the
    registration lock **after** the duplicate check and **before** the session
    becomes reachable. See that method for why both halves matter; the short
    version is that a rejected registration must run no user commands, and a
    client that supplies its own id can pipeline a prompt behind the load.

    Called only after history has been restored, because injected context is
    appended to ``agent.messages`` and would otherwise be discarded by the
    restore or land behind it.
    """
    def _before_publish(state: Any) -> None:
        state.notify_user = make_notifier(server, session_id)
        for notice in fire_session_start(agent, session_id, source=source):
            state.notify_user(notice)
    return _before_publish


def fire_end_for_state(state: Any) -> None:
    """Fire ``SessionEnd`` for a session being closed. Never raises.

    Called from :meth:`AcpSessionState.close` behind its idempotence guard, so
    a double close dispatches once. ``reason`` is ``other``: ACP has no named
    upstream cause — a connection ending is not a prompt-input exit, a clear, or
    a resume — and ``other`` is upstream's own value for exactly that.
    """
    agent = getattr(state, "agent", None)
    if agent is None:
        return
    # No try/except around this call: ``fire_session_end`` already traps
    # everything and returns ``[]``, so a handler here would be unreachable and
    # would advertise a failure mode that does not exist. Dispatch failures are
    # logged by ``plugins/hooks/lifecycle.py``, which is where they happen.
    notices = fire_session_end(agent, state.session_id, reason="other")
    notify = getattr(state, "notify_user", None)
    if not callable(notify):
        return
    for notice in notices:
        notify(notice)
