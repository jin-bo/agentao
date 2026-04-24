"""The :class:`ACPManager` god-class composed from focused mixins.

``ACPManager`` is the single entry point for the CLI and agent layers to
start, stop, query, and communicate with project-local ACP servers.
The class body here is deliberately small: it wires the shared state
(handles, clients, locks, recovery counters) that every mixin reads off
``self`` and inherits behaviour from the mixins in :mod:`lifecycle`,
:mod:`connection`, :mod:`turns`, :mod:`interactions`, :mod:`status`, and
:mod:`recovery`.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..client import ACPClient
from ..inbox import Inbox
from ..interaction import InteractionRegistry
from ..models import AcpClientConfig
from ..process import ACPProcessHandle
from .connection import ConnectionMixin
from .interactions import InteractionsMixin
from .lifecycle import LifecycleMixin
from .recovery import RecoveryMixin
from .status import StatusMixin
from .turns import TurnsMixin, _TurnContext


class ACPManager(
    LifecycleMixin,
    ConnectionMixin,
    TurnsMixin,
    InteractionsMixin,
    StatusMixin,
    RecoveryMixin,
):
    """Registry that owns one :class:`ACPProcessHandle` per configured server.

    Typical lifecycle::

        mgr = ACPManager.from_project()   # load config, create handles
        mgr.start_all()                   # launch subprocesses
        ...                               # CLI / agent work
        mgr.stop_all()                    # clean up on exit
    """

    def __init__(
        self,
        config: AcpClientConfig,
        *,
        notification_callback: Optional[Callable[[str, str, Any], None]] = None,
    ) -> None:
        self._config = config
        self._handles: Dict[str, ACPProcessHandle] = {}
        self._clients: Dict[str, ACPClient] = {}
        self._notification_callback = notification_callback
        self.inbox = Inbox()
        self.interactions = InteractionRegistry()

        # Per-server turn-bearing serialization. Acquired around the
        # synchronous send_prompt / prompt_once / cancel_turn entrypoints;
        # never held across async MCP loop internals (lock is a plain
        # threading.Lock, not an asyncio.Lock). Fail-fast contract:
        # callers use ``acquire(blocking=False)`` and surface
        # ``SERVER_BUSY`` instead of queueing.
        self._server_locks: Dict[str, threading.Lock] = {}
        self._server_locks_meta = threading.Lock()

        # Per-server handshake-bearing serialization. Distinct from the
        # turn lock so a direct ``connect_server`` / ``ensure_connected``
        # call cannot make a concurrent ``send_prompt`` / ``prompt_once``
        # spuriously raise ``SERVER_BUSY``: the turn lock is fail-fast,
        # but handshake setup is not turn activity. Re-entrant so
        # ``ensure_connected`` → ``connect_server`` on the same thread
        # doesn't self-deadlock.
        self._handshake_locks: Dict[str, "threading.RLock"] = {}
        self._handshake_locks_meta = threading.Lock()

        # Single active turn slot per named server.
        self._active_turns: Dict[str, _TurnContext] = {}
        self._active_turns_lock = threading.Lock()

        # Ephemeral clients created by ``prompt_once``. They do NOT appear
        # in ``self._clients`` or ``get_status()``; the separate map only
        # exists so callback routing (notifications, server requests) can
        # still find the active client for a given server name.
        self._ephemeral_clients: Dict[str, ACPClient] = {}
        self._ephemeral_lock = threading.Lock()

        # Week 2 status-snapshot diagnostics. ``_last_errors`` carries the
        # most recent human-readable error + its *store-time* timestamp,
        # set inside ``_record_last_error`` (not at raise time).
        # ``_config_warnings`` is a per-server deprecation surface that
        # Week 3 legacy-config handling will populate; today it is
        # plumbed through to ``ServerStatus`` as an empty list so
        # embedders can start depending on the field shape.
        self._last_errors: Dict[str, Tuple[str, datetime]] = {}
        self._last_errors_lock = threading.Lock()
        self._config_warnings: Dict[str, List[str]] = {
            name: [] for name in config.servers
        }

        # Week 4 Issue 16 — recovery state. ``_fatal_servers`` holds
        # servers whose last classified death was terminal; entries are
        # cleared only by an explicit ``restart_server(name)`` or
        # ``start_server(name)``. ``_restart_counts`` tracks the number
        # of consecutive auto-recoveries since the last successful turn
        # and bounds recovery via ``max_recoverable_restarts`` on the
        # server config. ``_handshake_fail_streak`` is bumped on each
        # handshake failure seen inside ``connect_server`` and reset on
        # success; two in a row flips the classification to fatal.
        self._fatal_servers: Set[str] = set()
        self._restart_counts: Dict[str, int] = {
            name: 0 for name in config.servers
        }
        self._handshake_fail_streak: Dict[str, int] = {
            name: 0 for name in config.servers
        }
        self._recovery_lock = threading.Lock()

        for name, server_cfg in config.servers.items():
            self._handles[name] = ACPProcessHandle(name, server_cfg)
