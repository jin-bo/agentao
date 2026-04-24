"""Process-lifecycle operations (start / stop / restart).

Provides :class:`LifecycleMixin` for :class:`ACPManager`.  Owns the
coarse ``start_all`` / ``stop_all`` bulk operations plus the per-server
``start_server`` / ``stop_server`` / ``restart_server`` surface used by
the CLI and operator recovery paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..client import AcpServerNotFound
from ..config import load_acp_client_config
from ..models import ServerState
from .helpers import logger


class LifecycleMixin:
    """Start / stop / restart primitives for :class:`ACPManager`."""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_project(cls, project_root: Optional[Path] = None) -> "LifecycleMixin":
        """Load ``acp.json`` and build a manager with handles for every server.

        Args:
            project_root: Forwarded to :func:`load_acp_client_config`.
        """
        config = load_acp_client_config(project_root=project_root)
        return cls(config)

    # ------------------------------------------------------------------
    # Bulk lifecycle
    # ------------------------------------------------------------------

    def start_all(self, *, only_auto: bool = True) -> None:
        """Start server subprocesses.

        Args:
            only_auto: If ``True`` (default), only start servers whose
                ``auto_start`` config flag is set.
        """
        for name, handle in self._handles.items():
            if only_auto and not handle.config.auto_start:
                logger.debug("acp: skipping '%s' (autoStart=false)", name)
                continue
            try:
                with self._get_handshake_lock(name):
                    proc = handle._proc
                    proc_alive = proc is not None and proc.poll() is None
                    if handle.info.state is ServerState.FAILED and proc_alive:
                        # Live-fatal: subprocess is alive but stuck in FAILED.
                        # handle.start() is a no-op here, so force a full
                        # restart — mirrors the start_server() recovery path.
                        self._evict_cached_client(name, "start_all/live-fatal")
                        handle.restart()
                        self._clear_fatal(name)
                    else:
                        did_launch = not proc_alive
                        if did_launch:
                            self._evict_cached_client(name, "start_all/dead-proc")
                        handle.start()
                        if did_launch:
                            self._clear_fatal(name)
            except RuntimeError as exc:
                logger.error("acp: %s", exc)

    def stop_all(self) -> None:
        """Stop all clients and server subprocesses."""
        for name, client in self._clients.items():
            try:
                client.close()
            except Exception as exc:
                logger.error("acp: error closing client '%s': %s", name, exc)
        self._clients.clear()

        for name, client in list(self._ephemeral_clients.items()):
            try:
                client.close()
            except Exception as exc:
                logger.error(
                    "acp: error closing ephemeral client '%s': %s", name, exc
                )
        self._ephemeral_clients.clear()

        for handle in self._handles.values():
            try:
                handle.stop()
            except Exception as exc:
                logger.error(
                    "acp: error stopping '%s': %s", handle.name, exc
                )

    # ------------------------------------------------------------------
    # Per-server lifecycle
    # ------------------------------------------------------------------

    def _evict_cached_client(self, name: str, reason: str) -> None:
        """Close and drop any cached client tied to a soon-to-be-replaced proc.

        Required on every explicit recovery path that spawns a new
        subprocess (``start_server`` restart branch, ``restart_server``,
        ``start_server`` on a dead proc). Otherwise ``_clients[name]``
        keeps pointing at the old stdio transport and the next
        ``send_prompt`` reuses a session bound to the dead process
        before ``_check_cached_client_alive`` has had a chance to
        notice, producing ``TRANSPORT_DISCONNECT`` instead of recovery.
        """
        client = self._clients.pop(name, None)
        if client is None:
            return
        try:
            client.close()
        except Exception:
            logger.debug(
                "acp[%s]: error closing cached client (%s)",
                name, reason, exc_info=True,
            )

    def start_server(self, name: str) -> None:
        """Start a single server by name.

        Explicit start always clears the Week 4 fatal state and
        recovery counters — operators use ``start_server`` /
        ``restart_server`` to acknowledge a fatal-state server is
        ready to be retried.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        with self._get_handshake_lock(name):
            # If the subprocess is still alive but the handle state was
            # forced to FAILED by ``_mark_fatal`` (e.g. repeated handshake
            # failures with a live process), a plain ``handle.start()`` is
            # a no-op and leaves readiness()/get_status() stuck reporting
            # "failed". Force a full restart in that case so callers using
            # the documented ``start_server`` recovery path always observe
            # recovery succeed.
            proc = handle._proc
            proc_alive = proc is not None and proc.poll() is None
            if handle.info.state is ServerState.FAILED and proc_alive:
                self._evict_cached_client(name, "start_server/restart")
                handle.restart()
            else:
                # If there is no live subprocess, any cached client is
                # pinned to a dead transport — evict before ``handle.start``
                # spawns the replacement. On the idempotent "already alive"
                # path there is nothing to evict.
                if not proc_alive:
                    self._evict_cached_client(name, "start_server/dead-proc")
                handle.start()
            self._clear_fatal(name)

    def stop_server(self, name: str) -> None:
        """Stop a single server by name.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        client = self._clients.pop(name, None)
        if client is not None:
            client.close()
        handle.stop()

    def restart_server(self, name: str) -> None:
        """Restart a single server by name.

        Explicit restart clears the Week 4 fatal state and recovery
        counters — this is the operator-action escape hatch out of a
        fatal classification.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        with self._get_handshake_lock(name):
            self._evict_cached_client(name, "restart_server")
            with self._ephemeral_lock:
                stale = self._ephemeral_clients.pop(name, None)
            if stale is not None:
                try:
                    stale.close()
                except Exception:
                    pass
            handle.restart()
            self._clear_fatal(name)
