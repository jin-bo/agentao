"""Authentication state and the login reservation.

Provides :class:`AuthMixin` for :class:`ACPManager`. The manager does not run
logins — a ``terminal`` method needs the user's terminal, which only the host
has — but it owns the three things a host needs around one:

- the ``authMethods`` each server advertised, kept per server so they survive
  the handshake teardown that follows an ``auth_required``;
- the client capabilities to advertise, which declare Terminal Auth only when
  the host said it can run a terminal login (``terminal_auth=True``);
- :meth:`reserve_for_login`, which keeps turns off a server while its login,
  restart and reconnect run.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from ..auth import terminal_auth_client_capabilities
from ..client import ACPClient, AcpClientError, AcpErrorCode, AcpServerNotFound


class AuthMixin:
    """Per-server ``authMethods`` + login reservation for :class:`ACPManager`."""

    def _client_capabilities(self) -> Dict[str, Any]:
        """``clientCapabilities`` for ``initialize``."""
        if self._terminal_auth:
            return terminal_auth_client_capabilities()
        return {}

    def _initialize_client(self, name: str, client: ACPClient, *, timeout: Any) -> None:
        """Run ``initialize`` on *client* and record its ``authMethods``.

        Recorded before ``session/new`` runs, so a session refused with
        ``auth_required`` still leaves the methods behind for the host.
        """
        client.initialize(timeout=timeout, client_capabilities=self._client_capabilities())
        with self._auth_lock:
            self._auth_methods[name] = list(client.connection_info.auth_methods)

    def auth_methods(self, name: str) -> List[Dict[str, Any]]:
        """The ``authMethods`` *name* advertised at its last ``initialize``.

        Empty before the first handshake, and when the agent advertised none.
        Advertised methods depend on the declared client capabilities: an
        agent may list a ``terminal`` method only to a client that declared
        Terminal Auth.

        Raises:
            AcpServerNotFound: If *name* is not configured.
        """
        if name not in self._handles:
            raise AcpServerNotFound(name)
        with self._auth_lock:
            return [dict(m) for m in self._auth_methods.get(name, [])]

    def needs_login(self, name: str) -> bool:
        """Whether *name*'s last session setup answered ``auth_required``.

        Stays ``True`` across restarts until a session opens, so a host can
        show "needs login" rather than a bare ``failed`` / ``stopped``.

        Raises:
            AcpServerNotFound: If *name* is not configured.
        """
        if name not in self._handles:
            raise AcpServerNotFound(name)
        with self._auth_lock:
            return name in self._needs_login

    @property
    def terminal_auth(self) -> bool:
        """Whether this manager declares Terminal Auth to its servers."""
        return self._terminal_auth

    # ------------------------------------------------------------------
    # Login reservation
    # ------------------------------------------------------------------

    def _refuse_if_reserved_for_login(self, name: str) -> None:
        """Raise ``SERVER_BUSY`` if another thread holds *name* for a login.

        Called at each public entry point (fail fast) and again inside each
        handshake-locked body — the authoritative check, since a reservation
        is installed under the same handshake lock.
        """
        with self._auth_lock:
            owner = self._login_reservations.get(name)
        if owner is not None and owner != threading.get_ident():
            raise AcpClientError(
                f"server '{name}' is reserved for login; retry after it completes",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name, "reason": "login"},
            )

    @contextmanager
    def reserve_for_login(self, name: str) -> Iterator[None]:
        """Keep turns and other connects off *name* while a login runs.

        Raises ``SERVER_BUSY`` when *name* has an active turn (or is already
        reserved). While held, ``send_prompt`` / ``send_prompt_nonblocking``
        / ``prompt_once`` / ``connect_server`` / ``ensure_connected`` from
        other threads raise ``SERVER_BUSY``; the reserving thread itself
        keeps using :meth:`restart_server` and :meth:`connect_server` to
        bring the server back after the login. Other servers are unaffected.

        Raises:
            AcpServerNotFound: If *name* is not configured.
            AcpClientError(code=SERVER_BUSY): A turn or another login holds it.
        """
        if name not in self._handles:
            raise AcpServerNotFound(name)
        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            raise AcpClientError(
                f"server '{name}' has an active turn; log in after it finishes",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )
        try:
            # Install under the handshake lock: a connect already inside its
            # handshake finishes first, and every handshake body rechecks the
            # reservation once it holds that lock, so none can start after.
            with self._get_handshake_lock(name):
                with self._auth_lock:
                    if name in self._login_reservations:
                        raise AcpClientError(
                            f"server '{name}' is reserved for login; retry after it completes",
                            code=AcpErrorCode.SERVER_BUSY,
                            details={"server": name, "reason": "login"},
                        )
                    self._login_reservations[name] = threading.get_ident()
            try:
                yield
            finally:
                with self._auth_lock:
                    self._login_reservations.pop(name, None)
        finally:
            lock.release()
