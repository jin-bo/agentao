"""Embedded harness factory + host-side persistence for :class:`agentao.agent.Agentao`.

`build_from_environment()` captures every implicit env / dotenv / cwd /
``.agentao/*.json`` read that the agent constructor would otherwise
perform and routes them through explicit-injection kwargs. CLI and
ACP entrypoints go through this single surface so embedded hosts that
already have explicit config can construct :class:`Agentao` directly
without any of the env-touching side effects.

`sessions` (formerly the top-level :mod:`agentao.session`) holds the
``.agentao/sessions/*.json`` save/load/list/delete helpers. The legacy
import path remains as a deprecation shim until 0.5.0; new code should
``from agentao.embedding.sessions import save_session, ...`` and pass
``project_root`` explicitly.
"""

from .factory import build_from_environment
from .sessions import (
    delete_all_sessions,
    delete_session,
    format_session_time_local,
    list_sessions,
    load_session,
    save_session,
    strip_system_reminders,
)

__all__ = [
    "build_from_environment",
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "delete_all_sessions",
    "strip_system_reminders",
    "format_session_time_local",
]
