"""Embedded harness factory + host-side persistence for :class:`agentao.agent.Agentao`.

`build_from_environment()` captures every implicit env / dotenv / cwd /
``.agentao/*.json`` read that the agent constructor would otherwise
perform and routes them through explicit-injection kwargs. CLI and
ACP entrypoints go through this single surface so embedded hosts that
already have explicit config can construct :class:`Agentao` directly
without any of the env-touching side effects.

`sessions` holds the ``.agentao/sessions/*.json`` save/load/list/delete
helpers. ``project_root`` is required on every one of them — there is no
implicit ``Path.cwd()`` — and the top-level ``agentao.session`` shim that
used to supply that fallback was removed in 0.5.0.

`permission_loader` holds :func:`load_permission_rules`, the public
helper that reads ``<user_root>/permissions.json``. Hosts pass the
returned ``(rules, sources)`` to ``PermissionEngine(rules=..., loaded_sources=...)``
so the engine itself does no file I/O. Unlike every other config reader
in the tree it **fails closed**: a file that exists but cannot be honored
raises :class:`PermissionConfigError` rather than degrading to an empty
rule list. Hosts that must survive a broken policy file — a diagnostics
command, say — catch it; hosts that construct a session must not.
"""

from .factory import build_from_environment
from .permission_loader import PermissionConfigError, load_permission_rules
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
    "load_permission_rules",
    "PermissionConfigError",
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "delete_all_sessions",
    "strip_system_reminders",
    "format_session_time_local",
]
