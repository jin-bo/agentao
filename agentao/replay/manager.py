"""Replay lifecycle owner — moved out of :class:`agentao.agent.Agentao`.

A :class:`ReplayManager` owns everything the previous four agent
attributes used to: ``_replay_recorder``, ``_replay_adapter``,
``_host_replay_sink``, ``_replay_config``. The factory layer
(``embedding/``) creates one and assigns ``agent.replay_manager``;
old agent facade methods (``start_replay`` / ``end_replay`` /
``reload_replay_config``) delegate here and are scheduled for removal
in 0.5.0.

State machine (idempotent at every edge):

* ``start(session_id)`` — no-op when ``config.enabled`` is false or a
  recorder is already open. Creates the recorder, splices the
  :class:`ReplayAdapter` in front of the bound transport, attaches a
  :class:`HostReplaySink` to the host event stream, listens for
  ``TURN_BEGIN`` / ``TURN_END`` events on the transport so per-turn
  state stays inside the manager.
* ``end()`` — flushes ``SESSION_ENDED``, closes the recorder, restores
  the inner transport, detaches the host sink, and unsubscribes the
  turn listener. Safe to call repeatedly.
* ``reload_config()`` — re-reads ``replay`` from
  ``.agentao/settings.json``. Toggles take effect on the next
  ``start()``; the currently-open instance is intentionally untouched.

The agent object is passed to :meth:`__init__` so the manager can
mutate ``agent.transport`` / ``agent.tool_runner._transport`` while
splicing the adapter, the same dance the old ``replay/lifecycle.py``
module did before this refactor.
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
        try:
            agent.tool_runner._transport = adapter
        except Exception:
            pass
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
        # TURN_BEGIN / TURN_END events the runtime emits on
        # ``agent.transport`` (now the adapter) get translated into
        # ``adapter.begin_turn`` / ``end_turn`` by the adapter's mirror
        # path — see ``replay/adapter.py``. No separate listener is
        # needed; the splice itself routes them.
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
            try:
                inner = adapter._inner
                if agent.transport is adapter:
                    agent.transport = inner
                if agent.tool_runner._transport is adapter:
                    agent.tool_runner._transport = inner
            except Exception:
                pass
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
