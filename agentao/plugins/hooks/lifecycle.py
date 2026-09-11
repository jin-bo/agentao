"""Surface-independent ``SessionStart`` / ``SessionEnd`` dispatch.

These two events have three consumers — the interactive CLI, ``agentao run``,
and the ACP server — and exactly one of them owns a terminal. The dispatch
itself, and the routing of a lifecycle result's two channels, is the same
everywhere; only the *destination of the user notices* differs. So the work
lives here and each surface decides what to do with the returned list: the CLI
prints, ``agentao run`` folds them into its result warnings, ACP sends them as
``session/update`` notifications.

It lives under ``plugins/hooks`` rather than in ``cli/`` because ACP may not
import the CLI (``tests/test_import_layering.py`` rule 1), and because the only
thing these functions know about is the hook subsystem plus an ``agent``.

Both functions are **best-effort by contract**: a lifecycle event must never be
able to stop a session from starting or a connection from closing, so every
failure is swallowed and reported as "no notices".
"""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger(__name__)

__all__ = ["fire_session_start", "fire_session_end", "apply_lifecycle_result"]


def apply_lifecycle_result(agent: Any, result: Any) -> List[str]:
    """Route a lifecycle result's two channels. Returns the user notices.

    ``model_contexts`` is injected into history exactly as the
    ``UserPromptSubmit`` attachment path renders it, so a hook's context reads
    the same wherever it came from. The notices are *returned*, not printed:
    this module has no terminal.
    """
    notices = list(getattr(result, "user_notices", []) or [])
    for ctx in getattr(result, "model_contexts", []) or []:
        try:
            agent.add_message("user", f"[hook_additional_context] context: {ctx}")
        except Exception:
            logger.exception("hooks: failed to inject lifecycle context")
    return notices


def fire_session_start(
    agent: Any, session_id: str, *, source: str = "startup",
) -> List[str]:
    """Fire ``SessionStart`` hooks for ``agent``. Returns the user notices.

    ``source`` is compared against a profile rule's matcher, so it is not
    cosmetic — see ``docs/design/session-lifecycle-source-vs-codex.md``.

    **Call this after history is restored and before the session can take a
    turn.** Injected context is appended to `agent.messages`, so a caller that
    fires it first and restores history afterwards discards it; a caller that
    publishes the session first can have the context land mid-turn instead of
    in front of it.
    """
    if not getattr(agent, "_plugin_hook_rules", None):
        return []
    try:
        from . import ClaudeHookPayloadAdapter, PluginHookDispatcher
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_session_start(
            session_id=session_id, cwd=cwd, source=source,
        )
        result = PluginHookDispatcher(cwd=cwd).dispatch_session_start(
            payload=payload, rules=agent._plugin_hook_rules,
        )
        return apply_lifecycle_result(agent, result)
    except Exception:
        logger.exception("hooks: SessionStart dispatch failed")
    return []


def fire_session_end(
    agent: Any, session_id: str, *, reason: str = "other",
) -> List[str]:
    """Fire ``SessionEnd`` hooks for ``agent``. Returns the user notices.

    The JSON half of this event is inert by contract — the reference gives it no
    decision control and discards its output — but **exit 2 is a separate
    channel**, and on ``SessionEnd`` it means *stderr shown to the user*. That
    is the entire reason this returns anything, and the reason a caller that
    drops the list has silently removed the event's only output.
    """
    if not getattr(agent, "_plugin_hook_rules", None):
        return []
    try:
        from . import ClaudeHookPayloadAdapter, PluginHookDispatcher
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_session_end(
            session_id=session_id, cwd=cwd, reason=reason,
        )
        result = PluginHookDispatcher(cwd=cwd).dispatch_session_end(
            payload=payload, rules=agent._plugin_hook_rules,
        )
        # No model channel here: the event's JSON output is discarded, so only
        # the exit-2 user notice survives.
        return list(getattr(result, "user_notices", []) or [])
    except Exception:
        logger.exception("hooks: SessionEnd dispatch failed")
    return []
