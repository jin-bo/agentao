"""Handshake + session management for cached long-lived clients.

Provides :class:`ConnectionMixin` for :class:`ACPManager`. Owns the
``connect_server`` / ``ensure_connected`` entry points, the handshake
lock that serializes them against ephemeral-client setup, and the
session-parameter resolution used to decide whether a cached session
can be reused.
"""

from __future__ import annotations

import threading
from typing import Any, List, Optional

from ..client import (
    ACPClient,
    AcpClientError,
    AcpErrorCode,
    AcpServerNotFound,
    _fingerprint_mcp_servers,
)
from .helpers import logger


class ConnectionMixin:
    """Connection / handshake / session reuse logic for :class:`ACPManager`."""

    def _get_handshake_lock(self, name: str) -> "threading.RLock":
        with self._handshake_locks_meta:
            lock = self._handshake_locks.get(name)
            if lock is None:
                lock = threading.RLock()
                self._handshake_locks[name] = lock
            return lock

    def _client_for(self, name: str) -> Optional[ACPClient]:
        """Return the active client for a server (long-lived or ephemeral)."""
        client = self._clients.get(name)
        if client is not None:
            return client
        with self._ephemeral_lock:
            return self._ephemeral_clients.get(name)

    # ------------------------------------------------------------------
    # connect_server — full start → initialize → session/new flow
    # ------------------------------------------------------------------

    def connect_server(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
    ) -> ACPClient:
        """Start a server, perform ACP handshake, and create a session.

        This is the full ``start → initialize → session/new`` flow.
        If the process is already running, it reuses the existing handle.

        Args:
            name: Server name from config.
            cwd: Working directory for the ACP session.
            mcp_servers: MCP server configs for the session.
            timeout: Per-RPC timeout in seconds.

        Returns:
            The connected :class:`ACPClient`.

        Raises:
            KeyError: If *name* is not configured.
            RuntimeError: If subprocess fails to start.
            AcpRpcError / AcpClientError: If handshake fails.
        """
        # Serialize with concurrent ``prompt_once`` /
        # ``_open_ephemeral_client`` / ``ensure_connected`` calls via
        # the handshake lock — not the (fail-fast) turn lock. Without
        # this, an ephemeral handshake failure on one thread could
        # ``handle.stop()`` the subprocess this call is mid-initializing,
        # and two ``ACPClient`` readers could end up attached to the
        # same stdout. Using the turn lock here instead would be
        # incorrect: it is fail-fast, so direct embedder setup calls
        # would then make concurrent ``send_prompt`` /  ``prompt_once``
        # on other threads spuriously raise ``SERVER_BUSY`` even though
        # no turn is active.
        handshake_lock = self._get_handshake_lock(name)
        with handshake_lock:
            return self._connect_server_locked(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )

    def _connect_server_locked(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
    ) -> ACPClient:
        """``connect_server`` body — caller must hold the handshake lock."""
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # Fatal-state check must come before the cached-client short-circuit:
        # the server can be marked fatal *after* a client was cached (e.g. two
        # consecutive handshake failures), and returning a stale client in that
        # case would let callers bypass the recovery gate entirely.
        if self.is_fatal(name):
            raise AcpClientError(
                f"server '{name}' is in the fatal recovery state; "
                f"call restart_server('{name}') to clear it",
                code=AcpErrorCode.TRANSPORT_DISCONNECT,
                details={"server": name, "recovery": "fatal"},
            )

        # If a concurrent caller already installed a client while we
        # were waiting on the handshake lock, reuse theirs. Constructing
        # a second ``ACPClient`` on the same handle would start a
        # competing reader thread on one stdout stream and overwrite
        # ``self._clients[name]`` with a fresh session — breaking any
        # turn the first caller was about to run. This fires on the
        # ``send_prompt`` / ``send_prompt_nonblocking`` pre-warm path
        # where multiple threads can simultaneously observe "no cached
        # client" and race into ``connect_server``.
        #
        # Before returning the cached client, verify the subprocess is
        # still alive. ``_check_cached_client_alive`` evicts the stale
        # entry (or raises if fatal) when it is not, so we fall through
        # to the normal rebuild path below.
        existing = self._clients.get(name)
        if existing is not None:
            self._check_cached_client_alive(name)
            existing = self._clients.get(name)
            if existing is not None:
                eff_cwd, eff_mcp = self._effective_session_params(
                    name, cwd=cwd, mcp_servers=mcp_servers,
                )
                if self._session_matches(existing, cwd=eff_cwd, mcp_servers=eff_mcp):
                    return existing
                # Session params differ — re-session the alive client rather
                # than constructing a second ACPClient (which would race on
                # stdout). Guard against mid-turn re-sessioning: sending a new
                # session/new while session/prompt is in-flight mutates
                # session_id under the active turn and interleaves handshake
                # traffic with it.
                #
                # Use the server lock as the authoritative "turn active" signal.
                # _active_turns is set inside _run_turn_on_client, which is
                # called *after* the server lock is acquired, so there is a
                # window where the lock is held but _active_turns is empty.
                # blocking=False is required to prevent an ABBA deadlock:
                # send_prompt holds server_lock and waits for handshake_lock
                # (inside ensure_connected); acquiring server_lock here
                # (blocking) while holding handshake_lock would deadlock.
                _server_lock = self._get_server_lock(name)
                if not _server_lock.acquire(blocking=False):
                    raise AcpClientError(
                        f"server '{name}' has an active turn; "
                        f"connect_server cannot re-session a mid-turn client",
                        code=AcpErrorCode.SERVER_BUSY,
                        details={"server": name},
                    )
                _server_lock.release()
                try:
                    existing.create_session(
                        cwd=eff_cwd, mcp_servers=eff_mcp, timeout=timeout,
                    )
                except BaseException as exc:
                    if self._reclassify_as_handshake_fail(exc, name):
                        self._note_handshake_failure_and_maybe_fatal(name)
                    raise
                self._note_handshake_success(name)
                return existing

        # Refuse to attach a new ACPClient while a concurrent prompt_once
        # ephemeral client already owns the handle's stdout — two readers on
        # one pipe would drop or misroute ACP frames.
        with self._ephemeral_lock:
            has_ephemeral = name in self._ephemeral_clients
        if has_ephemeral:
            raise AcpClientError(
                f"server '{name}' has an in-flight ephemeral client; "
                f"connect_server cannot create a second reader on the same transport",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )

        # Track whether this call is starting the process from scratch.  If the
        # server was already running (e.g. pre-warmed via start_server() /
        # start_all()), a handshake failure must not tear it down — client.close()
        # already detaches our subscriber via unsubscribe_stdout(), which is
        # sufficient with the stdout-feeder design.
        _existing_proc = handle._proc
        _we_started = _existing_proc is None or _existing_proc.poll() is not None

        # Start process if not already running. Classify non-AcpClientError
        # failures (e.g. RuntimeError from the process handle) as
        # PROCESS_START_FAIL so embedders can distinguish a bad executable
        # from a rejected handshake.
        try:
            handle.start()
        except AcpClientError:
            raise
        except Exception as exc:
            raise AcpClientError(
                f"failed to start ACP server '{name}': {exc}",
                code=AcpErrorCode.PROCESS_START_FAIL,
                details={"server": name},
                cause=exc,
            ) from exc

        # Build notification callback that routes to inbox + user callback.
        def _on_notification(method: str, params: Any) -> None:
            self._route_notification(name, method, params)
            if self._notification_callback is not None:
                self._notification_callback(name, method, params)

        # Build server-request callback for permission/input requests.
        def _on_server_request(method: str, params: Any, request_id: Any) -> None:
            self._route_server_request(name, method, params, request_id)

        client = ACPClient(
            handle,
            notification_callback=_on_notification,
            server_request_callback=_on_server_request,
        )
        # Re-label initialize()/create_session() failures as HANDSHAKE_FAIL
        # so callers can separate protocol-level setup from ordinary RPC
        # errors on an established session. AcpRpcError keeps its numeric
        # ``code`` contract; we only tag the structured classification.
        # The sticky-fatal accounting lives here (not just in the
        # ``ensure_connected``/``prompt_once`` wrappers) so hosts that
        # call ``connect_server`` directly get the same "2 consecutive
        # handshakes ⇒ fatal" contract as every other entry point.
        try:
            client.start_reader()
            client.initialize(timeout=timeout)
            client.create_session(
                cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
        except BaseException as exc:
            # client.close() calls unsubscribe_stdout(), which detaches our
            # subscriber from the feeder thread — no stale reader remains.
            # Only stop the subprocess if we started it; a pre-warmed server
            # (started via start_server()/start_all()) must survive a transient
            # handshake failure so the next call can reuse the warm process.
            try:
                client.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error closing partially initialized client",
                    name, exc_info=True,
                )
            if _we_started:
                try:
                    handle.stop()
                except Exception:
                    logger.debug(
                        "acp[%s]: error stopping handle after handshake failure",
                        name, exc_info=True,
                    )
            if self._reclassify_as_handshake_fail(exc, name):
                self._note_handshake_failure_and_maybe_fatal(name)
            raise

        # A successful connect/handshake clears the handshake-failure
        # streak — otherwise an earlier single failure would linger and
        # combine with a later unrelated failure to trip the sticky-fatal
        # "consecutive handshake failures" rule across prewarmed hosts.
        self._note_handshake_success(name)
        self._clients[name] = client
        return client

    # ------------------------------------------------------------------
    # ensure_connected — reuse cached client / re-session / fall through
    # ------------------------------------------------------------------

    def ensure_connected(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
        _inside_turn: bool = False,
    ) -> ACPClient:
        """Return an existing client, or auto-connect / re-session as needed.

        Session reuse is conditional on matching per-call ``cwd`` and
        ``mcp_servers``. When either differs from the cached session, the
        old client is closed and a fresh session is created.

        Week 4 Issue 16: if the cached client's subprocess has died
        since the last call, classify the death via
        :func:`classify_process_death`. ``recoverable`` deaths (within
        the cap) trigger a lazy rebuild on this call; ``fatal`` deaths
        raise ``AcpClientError(code=TRANSPORT_DISCONNECT)`` and leave
        the server in the sticky fatal state (inspect via
        :meth:`is_fatal` / ``last_error``) until an explicit
        :meth:`restart_server` call clears it.

        Args:
            name: Server name from config.
            cwd: Working directory for the ACP session.
            mcp_servers: MCP server configs for the session.
            timeout: Per-RPC timeout.
            _inside_turn: Internal flag. Set to ``True`` when the caller
                already holds the per-server turn lock. Changes the
                behaviour when an in-flight ephemeral is detected: instead
                of raising ``SERVER_BUSY`` (correct when there is no turn
                lock to protect us), the ephemeral is displaced and its
                reader is closed so this thread can install its own
                long-lived client.

        Returns:
            The connected :class:`ACPClient`.
        """
        # Serialize with ``_open_ephemeral_client`` and concurrent
        # ``connect_server`` on other threads via the handshake lock —
        # the re-session branch below also calls
        # ``client.create_session`` which can race the ephemeral path's
        # ``handle.stop()``. Re-entrant: when this path falls through to
        # ``_connect_server_locked`` below, the same thread can acquire
        # the handshake lock again without self-deadlock.
        handshake_lock = self._get_handshake_lock(name)
        with handshake_lock:
            return self._ensure_connected_locked(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
                _inside_turn=_inside_turn,
            )

    def _ensure_connected_locked(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
        _inside_turn: bool = False,
    ) -> ACPClient:
        """``ensure_connected`` body — caller must hold the handshake lock."""
        # Refuse service while sticky-fatal AND classify+evict a dead
        # cached subprocess. Shared with ``prompt_once`` so the same
        # recovery contract applies whether the caller is the
        # long-lived session path or the one-shot path reusing an
        # existing long-lived client.
        self._check_cached_client_alive(name)

        effective_cwd, effective_mcp = self._effective_session_params(
            name, cwd=cwd, mcp_servers=mcp_servers,
        )
        client = self._clients.get(name)

        if client is not None:
            # Fast path: valid session + params match → reuse directly.
            if client.connection_info.session_id and self._session_matches(
                client, cwd=effective_cwd, mcp_servers=effective_mcp,
            ):
                return client
            # Either the params diverged (cwd / mcp_servers override) or
            # a prior ``session/new`` failed and cleared session_id.
            # Either way, reuse the existing transport and (re-)create
            # the session — building a second ACPClient on the same
            # handle would spawn a competing reader thread on the one
            # stdout stream. ``ACPClient.create_session`` clears its own
            # session metadata on failure, so the stale-reuse bug from
            # Codex P1 cannot fire on the next attempt.
            #
            # ``client.create_session`` can fail with exactly the same
            # HANDSHAKE_FAIL-eligible error shapes as the greenfield
            # ``connect_server`` path, so feed it through the same
            # classification + fatal-streak accounting. Without this,
            # a warmed server with repeated ``session/new`` failures
            # (e.g. cwd override the agent keeps rejecting) would
            # never trip the documented sticky-fatal contract.
            # Mirror the SERVER_BUSY guard from ``_connect_server_locked``:
            # a re-session on a live transport while another thread holds
            # the turn lock would inject ``session/new`` handshake traffic
            # into an in-flight prompt's stdout stream.  Use non-blocking
            # acquire to avoid an ABBA deadlock (turn lock → handshake lock
            # is the normal order; we already hold the handshake lock here).
            if not _inside_turn:
                _server_lock = self._get_server_lock(name)
                if not _server_lock.acquire(blocking=False):
                    raise AcpClientError(
                        f"server '{name}' has an active turn; "
                        f"ensure_connected cannot re-session a mid-turn client",
                        code=AcpErrorCode.SERVER_BUSY,
                        details={"server": name},
                    )
                _server_lock.release()
            try:
                client.create_session(
                    cwd=effective_cwd,
                    mcp_servers=effective_mcp,
                    timeout=timeout,
                )
            except BaseException as exc:
                if self._reclassify_as_handshake_fail(exc, name):
                    self._note_handshake_failure_and_maybe_fatal(name)
                raise
            # Successful re-session — treat the same as a successful
            # greenfield handshake and clear the streak so a prior
            # isolated failure cannot combine with a future one.
            self._note_handshake_success(name)
            return client
        # No cached long-lived client.  Before spawning a brand-new ACPClient
        # (which starts a competing reader thread on the handle's stdout),
        # check whether a concurrent ``prompt_once`` has already connected
        # this server via an ephemeral client.
        with self._ephemeral_lock:
            eph_client: Optional[ACPClient] = self._ephemeral_clients.get(name)
        if eph_client is not None:
            if not _inside_turn:
                # We do NOT hold the turn lock, so the ephemeral may belong
                # to an active turn.  Refuse rather than corrupt the session.
                raise AcpClientError(
                    f"server '{name}' has an in-flight ephemeral client; "
                    f"send_prompt is fail-fast",
                    code=AcpErrorCode.SERVER_BUSY,
                    details={"server": name},
                )
            # We hold the turn lock.  A concurrent ``prompt_once`` registered
            # this ephemeral but cannot acquire the turn lock (we own it), so
            # it will call ``_rollback_ephemeral_on_busy`` — which also needs
            # the handshake lock we currently hold.  Displace the ephemeral
            # here (under our handshake lock) so the reader stops before we
            # spawn our own, then fall through to the cold-start connect.
            # ``_rollback_ephemeral_on_busy`` is idempotent (identity check +
            # caught exceptions) so the concurrent rollback is harmless.
            with self._ephemeral_lock:
                if self._ephemeral_clients.get(name) is eph_client:
                    self._ephemeral_clients.pop(name, None)
            try:
                eph_client.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error displacing in-flight ephemeral",
                    name, exc_info=True,
                )
        # Forward to the locked connect body. The handshake RLock is already
        # held by the public ``ensure_connected`` wrapper; reentrance is allowed.
        return self._connect_server_locked(
            name, cwd=effective_cwd, mcp_servers=effective_mcp,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Session-parameter helpers
    # ------------------------------------------------------------------

    def _effective_session_params(
        self,
        name: str,
        *,
        cwd: Optional[str],
        mcp_servers: Optional[List[dict]],
    ) -> tuple[str, List[dict]]:
        """Resolve per-call ``cwd`` / ``mcp_servers`` to concrete defaults.

        ``ACPClient.create_session`` substitutes ``handle.config.cwd`` for
        a ``None`` cwd and ``[]`` for ``None`` mcp_servers, so callers that
        compare ``None`` to a stored value would incorrectly reuse a
        session that was previously mutated by an explicit override.
        Resolve up front so both the match check and the re-session call
        see the same effective values.
        """
        handle = self._handles[name]
        effective_cwd = cwd if cwd is not None else handle.config.cwd
        effective_mcp: List[dict] = (
            list(mcp_servers) if mcp_servers is not None else []
        )
        return effective_cwd, effective_mcp

    @staticmethod
    def _session_matches(
        client: ACPClient,
        *,
        cwd: str,
        mcp_servers: List[dict],
    ) -> bool:
        """Whether ``client``'s session was created with the requested params."""
        info = client.connection_info
        if info.session_cwd != cwd:
            return False
        if info.session_mcp_servers_fingerprint != _fingerprint_mcp_servers(
            mcp_servers
        ):
            return False
        return True
