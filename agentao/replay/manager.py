"""Replay lifecycle owner.

A :class:`ReplayManager` owns the recorder, adapter, host event sink,
and config for one agent. The factory layer (``embedding/``) creates
one and assigns it to ``agent.replay_manager``.

State machine (idempotent at every edge):

* ``start(session_id)`` — no-op when ``config.enabled`` is false or a
  recorder is already open. Creates the recorder, splices the
  :class:`ReplayAdapter` in front of the bound transport, attaches a
  :class:`HostReplaySink` to the host event stream.
* ``end()`` — flushes ``SESSION_ENDED``, closes the recorder, restores
  the inner transport, detaches the host sink. Safe to call repeatedly.
* ``reload_config()`` — re-reads ``replay`` from
  ``.agentao/settings.json``. Toggles take effect on the next
  ``start()``; the currently-open instance is intentionally untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .adapter import ReplayAdapter
from .config import ReplayConfig, load_replay_config
from .events import EventKind as _ReplayKind
from .recorder import ReplayRecorder
from .retention import ReplayRetentionPolicy

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


class ReplayManager:
    """Owns recorder / adapter / host sink / config for a single agent."""

    def __init__(
        self,
        agent: "Agentao",
        config: Optional[ReplayConfig] = None,
    ) -> None:
        self._agent = agent
        self._config: ReplayConfig = config if config is not None else ReplayConfig()
        self._recorder: Optional[ReplayRecorder] = None
        self._adapter: Optional[ReplayAdapter] = None
        self._host_replay_sink: Optional[Any] = None

    # -- public accessors --------------------------------------------------

    @property
    def config(self) -> ReplayConfig:
        return self._config

    @property
    def recorder(self) -> Optional[ReplayRecorder]:
        return self._recorder

    @property
    def adapter(self) -> Optional[ReplayAdapter]:
        return self._adapter

    @property
    def host_replay_sink(self) -> Optional[Any]:
        return self._host_replay_sink

    # -- lifecycle ---------------------------------------------------------

    def start(self, session_id: Optional[str] = None) -> Optional[Path]:
        """Begin a new replay instance when ``config.enabled`` is true.

        Idempotent: a second call while a recorder is already open
        returns the existing path. Returns ``None`` when recording is
        disabled or the recorder couldn't be created.
        """
        agent = self._agent
        if session_id:
            agent._session_id = session_id
        if not self._config.enabled:
            return None
        if self._recorder is not None:
            return self._recorder.path
        sid = agent._session_id or ""
        if not sid:
            return None
        try:
            recorder = ReplayRecorder.create(
                session_id=sid,
                project_root=agent.working_directory,
                logger_=agent.llm.logger,
                capture_flags=dict(self._config.capture_flags),
            )
        except Exception as exc:
            agent.llm.logger.warning("replay: start failed: %s", exc)
            return None
        if self._config.deep_capture_enabled():
            on_flags = [k for k, v in self._config.capture_flags.items() if v]
            agent.llm.logger.warning(
                "replay: deep-capture mode active (%s). File size and "
                "sensitivity may be higher than usual.",
                ", ".join(sorted(on_flags)),
            )
        self._recorder = recorder
        adapter = ReplayAdapter(agent.transport, recorder)
        self._adapter = adapter
        agent.transport = adapter
        agent.tool_runner._transport = adapter
        # Bridge the public host EventStream into the same recorder so
        # tool_lifecycle / subagent_lifecycle / permission_decision events
        # land in one audit artifact. ``end()`` detaches.
        try:
            from ..host.replay_projection import HostReplaySink
            self._host_replay_sink = HostReplaySink(
                recorder, stream=agent._host_events,
            )
        except Exception as exc:
            agent.llm.logger.warning(
                "replay: host sink attach failed: %s", exc,
            )
            self._host_replay_sink = None
        # TURN_BEGIN / TURN_END events the runtime emits on the
        # transport are translated into recorder turn writes by
        # ``ReplayAdapter._mirror`` — the splice above is what routes
        # them, no separate listener is needed.
        recorder.record(
            _ReplayKind.SESSION_STARTED,
            payload={
                "session_id": sid,
                "cwd": str(agent.working_directory),
                "model": agent.llm.model,
            },
        )
        try:
            ReplayRetentionPolicy(
                max_instances=self._config.max_instances
            ).prune(agent.working_directory)
        except Exception:
            pass
        return recorder.path

    def end(self) -> None:
        """Finalize the current replay instance, if any. Safe to call twice."""
        agent = self._agent
        recorder = self._recorder
        adapter = self._adapter
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
        if adapter is not None:
            inner = adapter.inner
            if agent.transport is adapter:
                agent.transport = inner
            if agent.tool_runner._transport is adapter:
                agent.tool_runner._transport = inner
        sink = self._host_replay_sink
        if sink is not None:
            try:
                sink.detach()
            except Exception:
                pass
            self._host_replay_sink = None
        self._recorder = None
        self._adapter = None
        try:
            ReplayRetentionPolicy(
                max_instances=self._config.max_instances
            ).prune(agent.working_directory)
        except Exception:
            pass

    def reload_config(self) -> ReplayConfig:
        """Re-read ``replay`` settings from disk.

        Toggles only affect future ``start()`` calls — the currently
        open instance (if any) is intentionally left untouched.
        """
        try:
            self._config = load_replay_config(self._agent.working_directory)
        except Exception:
            self._config = ReplayConfig()
        return self._config


__all__ = ["ReplayManager"]
