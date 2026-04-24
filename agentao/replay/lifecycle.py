"""Replay lifecycle — start / end / reload routines.

Extracted from ``agentao/agent.py`` so the agent no longer owns the
adapter-swap dance. The ``Agentao`` class keeps thin facade methods
(``start_replay`` / ``end_replay`` / ``reload_replay_config``) since
the CLI, ACP session ops and the test suite all invoke these as agent
methods.

Behavioral contract preserved verbatim:

- ``start_replay`` is idempotent: calling again while a recorder is
  open returns the existing path unchanged.
- ``end_replay`` is idempotent: calling with no adapter/recorder is a
  no-op; calling twice does not double-record.
- Both functions mutate ``agent.transport`` and
  ``agent.tool_runner._transport`` to splice / unsplice the adapter —
  display and ACP behavior stay identical because the adapter forwards
  to the inner transport.
- Retention pruning runs best-effort after start and end.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .adapter import ReplayAdapter
from .config import ReplayConfig, load_replay_config
from .events import EventKind as _ReplayKind
from .recorder import ReplayRecorder
from .retention import ReplayRetentionPolicy

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


def start_replay(
    agent: "Agentao",
    session_id: Optional[str] = None,
) -> Optional[Path]:
    """Begin a new replay instance when ``replay.enabled=true``.

    Called by the CLI on session start and by ACP session/new and
    session/load after the session id is known. The call is
    idempotent — a second call while a recorder is already open is a
    no-op and returns the existing file path.

    Returns the replay file path when recording started, ``None``
    when recording is disabled or the recorder could not be created.
    """
    if session_id:
        agent._session_id = session_id
    if not agent._replay_config.enabled:
        return None
    if agent._replay_recorder is not None:
        return agent._replay_recorder.path
    sid = agent._session_id or ""
    if not sid:
        return None
    try:
        recorder = ReplayRecorder.create(
            session_id=sid,
            project_root=agent.working_directory,
            logger_=agent.llm.logger,
            capture_flags=dict(agent._replay_config.capture_flags),
        )
    except Exception as exc:
        agent.llm.logger.warning("replay: start failed: %s", exc)
        return None
    if agent._replay_config.deep_capture_enabled():
        # Deep-capture modes enlarge the replay file and may preserve
        # content that the default scanner can't fully redact (free-
        # form LLM messages, full tool results). Warn in the log so
        # the user sees it in agentao.log for audit purposes.
        on_flags = [
            k for k, v in agent._replay_config.capture_flags.items() if v
        ]
        agent.llm.logger.warning(
            "replay: deep-capture mode active (%s). File size and "
            "sensitivity may be higher than usual.",
            ", ".join(sorted(on_flags)),
        )
    agent._replay_recorder = recorder
    adapter = ReplayAdapter(agent.transport, recorder)
    agent._replay_adapter = adapter
    # Route every downstream emit/confirm through the adapter. The
    # adapter forwards to the original inner transport, so display
    # and ACP behavior remain unchanged.
    agent.transport = adapter
    try:
        agent.tool_runner._transport = adapter
    except Exception:
        pass
    recorder.record(
        _ReplayKind.SESSION_STARTED,
        payload={
            "session_id": sid,
            "cwd": str(agent.working_directory),
            "model": agent.llm.model,
        },
    )
    # Best-effort retention pass: new instance created.
    try:
        ReplayRetentionPolicy(
            max_instances=agent._replay_config.max_instances
        ).prune(agent.working_directory)
    except Exception:
        pass
    return recorder.path


def end_replay(agent: "Agentao") -> None:
    """Finalize the current replay instance, if any.

    Emits ``session_ended`` and closes the file. Safe to call more
    than once. Restores the original inner transport so a subsequent
    ``start_replay()`` cycle can attach a fresh adapter.
    """
    recorder = agent._replay_recorder
    adapter = agent._replay_adapter
    if recorder is None and adapter is None:
        return
    if recorder is not None:
        try:
            recorder.record(
                _ReplayKind.SESSION_ENDED,
                payload={"session_id": agent._session_id or ""},
            )
        except Exception:
            pass
        try:
            recorder.close()
        except Exception:
            pass
    # Detach adapter and restore the inner transport. Otherwise a
    # later ``start_replay()`` would wrap the adapter in a second
    # adapter and double-record every event.
    if adapter is not None:
        try:
            inner = adapter._inner
            if agent.transport is adapter:
                agent.transport = inner
            if agent.tool_runner._transport is adapter:
                agent.tool_runner._transport = inner
        except Exception:
            pass
    agent._replay_recorder = None
    agent._replay_adapter = None
    # Best-effort retention pass: instance ended.
    try:
        ReplayRetentionPolicy(
            max_instances=agent._replay_config.max_instances
        ).prune(agent.working_directory)
    except Exception:
        pass


def reload_replay_config(agent: "Agentao") -> ReplayConfig:
    """Re-read ``replay`` settings from disk.

    Called after ``/replay on`` / ``/replay off`` so a toggle takes
    effect on the next ``start_replay()`` without a CLI restart. The
    currently-open replay instance (if any) is intentionally left
    untouched — per spec, toggling only affects future instances.
    """
    try:
        agent._replay_config = load_replay_config(agent.working_directory)
    except Exception:
        agent._replay_config = ReplayConfig()
    return agent._replay_config
