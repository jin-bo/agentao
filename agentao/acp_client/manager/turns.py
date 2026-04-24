"""Turn execution: ``send_prompt`` / ``prompt_once`` / cancel / ephemerals.

Provides :class:`TurnsMixin` for :class:`ACPManager`, plus the
:class:`_TurnContext` dataclass consumed by the non-interactive
auto-rejection path in :class:`InteractionsMixin`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from ..client import (
    ACPClient,
    AcpClientError,
    AcpErrorCode,
    AcpInteractionRequiredError,
    AcpServerNotFound,
)
from ..models import (
    InteractionPolicy,
    PromptResult,
)
from ..process import ACPProcessHandle
from .helpers import logger


@dataclass
class _TurnContext:
    """Per-turn state tracked by :class:`ACPManager` for non-interactive calls.

    A single slot exists per named server (``ACPManager._active_turns``).
    Created when ``send_prompt`` starts a turn, removed when the turn
    completes. Non-interactive turns consult this context to auto-reject
    inbound server-initiated requests without exposing ``WAITING_FOR_USER``.

    ``effective_policy`` carries the resolved :class:`InteractionPolicy`
    for this turn (per-call override, else server default). The
    router reads it rather than the per-server config so per-call
    overrides land correctly on the running turn.
    """

    server: str
    interactive: bool
    interaction_error: Optional[AcpInteractionRequiredError] = None
    auto_replied_request_ids: Set[Any] = field(default_factory=set)
    cancelled: bool = False
    effective_policy: Optional[InteractionPolicy] = None


class TurnsMixin:
    """Single-active-turn contract + ephemeral-client prompt_once path."""

    # ------------------------------------------------------------------
    # Per-server lock / turn slot helpers
    # ------------------------------------------------------------------

    def _get_server_lock(self, name: str) -> threading.Lock:
        with self._server_locks_meta:
            lock = self._server_locks.get(name)
            if lock is None:
                lock = threading.Lock()
                self._server_locks[name] = lock
            return lock

    def _install_turn(self, name: str, ctx: _TurnContext) -> None:
        with self._active_turns_lock:
            self._active_turns[name] = ctx

    def _clear_turn(self, name: str) -> None:
        with self._active_turns_lock:
            self._active_turns.pop(name, None)

    def _get_active_turn(self, name: str) -> Optional[_TurnContext]:
        with self._active_turns_lock:
            return self._active_turns.get(name)

    # ------------------------------------------------------------------
    # send_prompt (blocking) — Issue 04
    # ------------------------------------------------------------------

    def send_prompt(
        self,
        name: str,
        text: str,
        *,
        timeout: Optional[float] = None,
        interactive: bool = True,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        interaction_policy: Any = None,
    ) -> Dict[str, Any]:
        """Send a prompt to a server, auto-starting if necessary.

        Args:
            name: Server name.
            text: Plain-text user message.
            timeout: Seconds to wait for the turn (RPC only; the
                per-server lock is acquired in fail-fast mode so callers
                never block waiting for another turn to finish).
            interactive: When ``True`` (default), the CLI interaction
                pipeline handles ``session/request_permission`` and
                ``_agentao.cn/ask_user`` via ``WAITING_FOR_USER``.  When
                ``False``, such requests are auto-rejected and the turn
                ultimately raises :class:`AcpInteractionRequiredError`.
            cwd: Per-call working directory.  Forwarded to
                :meth:`ensure_connected`; if it differs from the cached
                session's ``session_cwd``, a fresh session is created.
            mcp_servers: Per-call MCP server list.  Same session-reuse
                semantics as ``cwd``.
            interaction_policy: Per-call override of the non-interactive
                interaction policy. Accepts :class:`InteractionPolicy`
                or the string ``"reject_all"`` / ``"accept_all"``.
                ``None`` falls back to the server's configured
                ``nonInteractivePolicy``. Only consulted when
                ``interactive=False``.

        Returns:
            The ``session/prompt`` result dict.

        Raises:
            AcpClientError(code=SERVER_BUSY): Another turn is already
                active for this server (single-active-turn contract —
                "no queueing"; see ``docs/features/headless-runtime.md``
                §2). Callers should back off and retry, not block a
                worker thread behind a slow / stuck turn.
            AcpInteractionRequiredError: Non-interactive turn that the
                server tried to interrupt for user input.
            AcpClientError: Timeout or transport failure.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # ``interaction_policy`` is documented as only applying to
        # non-interactive turns, so skip resolution (including the
        # string/type validation inside ``_resolve_interaction_policy``)
        # when ``interactive=True``. Callers that pass a shared options
        # object regardless of mode should not be rejected for supplying
        # a value that would be ignored anyway.
        resolved_policy: Optional[InteractionPolicy] = None
        if not interactive:
            resolved_policy = self._resolve_interaction_policy(
                name, interaction_policy,
            )

        # Pre-warm a fresh connect OUTSIDE the fail-fast turn lock —
        # that way a pending handshake never holds the turn slot and
        # concurrent prompt callers only observe ``SERVER_BUSY`` when a
        # turn is *actually* running. Fresh connect is safe to do
        # outside the turn lock because it installs a brand-new client;
        # it cannot mutate a client another thread's turn is already
        # using. Re-session on an existing cached client IS unsafe
        # outside the turn lock (it would overwrite the cached
        # ``session_id`` mid-turn), so we defer that to after
        # ``lock.acquire`` via ``ensure_connected``.
        #
        # Both checks — no long-lived client *and* no ephemeral client
        # — run under the handshake lock so a concurrent ``prompt_once``
        # ephemeral in flight cannot slip past us and cause
        # ``_connect_server_locked`` to spawn a second ``ACPClient`` +
        # reader on the same handle's stdout.
        try:
            with self._get_handshake_lock(name):
                with self._ephemeral_lock:
                    has_ephemeral = name in self._ephemeral_clients
                if self._clients.get(name) is None and not has_ephemeral:
                    self._connect_server_locked(
                        name, cwd=cwd, mcp_servers=mcp_servers,
                        timeout=timeout,
                    )
        except Exception as exc:
            self._record_last_error(name, exc)
            raise

        # Fail-fast concurrency: mirror ``prompt_once``'s non-blocking
        # acquire. The Week 1 contract is "single active turn per server,
        # no queueing"; blocking here would hang a worker thread behind
        # a slow or stuck turn instead of letting the caller retry or
        # route elsewhere.
        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            raise AcpClientError(
                f"server '{name}' has an active turn; send_prompt is fail-fast",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )
        try:
            try:
                # Inside the turn lock now — ``ensure_connected`` can
                # safely re-session the cached client if ``cwd`` /
                # ``mcp_servers`` diverged without corrupting a
                # concurrent turn (there can't be one — we hold the
                # lock). The fast path returns immediately.
                client = self.ensure_connected(
                    name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
                    _inside_turn=True,
                )
                result = self._run_turn_on_client(
                    name, client, text,
                    timeout=timeout,
                    interactive=interactive,
                    policy=resolved_policy,
                )
                self._note_successful_turn(name)
                return result
            except Exception as exc:
                self._record_last_error(name, exc)
                raise
        finally:
            lock.release()

    def _run_turn_on_client(
        self,
        name: str,
        client: ACPClient,
        text: str,
        *,
        timeout: Optional[float],
        interactive: bool,
        policy: Optional[InteractionPolicy],
    ) -> Dict[str, Any]:
        """Common turn runner used by both ``send_prompt`` and ``prompt_once``."""
        if interactive:
            ctx = _TurnContext(
                server=name, interactive=True, effective_policy=policy,
            )
            self._install_turn(name, ctx)
            try:
                return client.send_prompt(text, timeout=timeout)
            finally:
                self._clear_turn(name)
        return self._run_non_interactive_turn(
            name, client, text, timeout=timeout, policy=policy,
        )

    def _run_non_interactive_turn(
        self,
        name: str,
        client: ACPClient,
        text: str,
        *,
        timeout: Optional[float],
        policy: Optional[InteractionPolicy],
    ) -> Dict[str, Any]:
        """Run one non-interactive ``session/prompt`` turn.

        Auto-rejection of inbound ``session/request_permission`` and
        ``_agentao.cn/ask_user`` happens in :meth:`_route_server_request`
        by consulting the installed :class:`_TurnContext`.
        """
        ctx = _TurnContext(
            server=name, interactive=False, effective_policy=policy,
        )
        self._install_turn(name, ctx)

        if timeout is None:
            timeout = client._handle.config.request_timeout_ms / 1000.0

        try:
            rid, slot = client.send_prompt_nonblocking(text)
        except AcpClientError:
            self._clear_turn(name)
            raise

        try:
            if not slot.event.wait(timeout=timeout):
                # Timeout: cancel the turn and raise REQUEST_TIMEOUT.
                # discard_pending_slot is idempotent and raise-free, so
                # a single slow/hung turn cannot poison subsequent calls
                # even if cancel_prompt fails midway (e.g. broken pipe).
                try:
                    client.cancel_prompt(rid)
                except Exception:
                    logger.debug(
                        "acp[%s]: cancel_prompt after timeout raised", name,
                        exc_info=True,
                    )
                finally:
                    client.discard_pending_slot(rid)
                raise AcpClientError(
                    f"timeout waiting for session/prompt response (id={rid})",
                    code=AcpErrorCode.REQUEST_TIMEOUT,
                    details={
                        "server": name,
                        "method": "session/prompt",
                        "request_id": rid,
                        "timeout": timeout,
                    },
                )

            # Collect result — may raise AcpRpcError / transport error.
            result = client.finish_prompt(rid, slot)

            # Cancel wins over a latched interaction error.
            if ctx.cancelled:
                return result
            if ctx.interaction_error is not None:
                raise ctx.interaction_error
            return result
        finally:
            self._clear_turn(name)

    # ------------------------------------------------------------------
    # send_prompt_nonblocking / finish / cancel
    # ------------------------------------------------------------------

    def send_prompt_nonblocking(
        self,
        name: str,
        text: str,
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
    ) -> tuple:
        """Send a prompt without blocking.  Returns ``(client, rid, slot)``.

        The caller polls ``slot.event`` and must finalize the turn via
        :meth:`finish_prompt_nonblocking` or :meth:`cancel_prompt_nonblocking`
        — **not** the raw ``ACPClient`` helpers — so the per-server lock
        and ``_active_turns`` slot are released. An interactive
        :class:`_TurnContext` is installed for the duration of the turn so
        concurrent ``send_prompt``/``prompt_once`` calls honor the same
        single-active-turn contract.

        Blocks while acquiring the per-server lock (parity with
        :meth:`send_prompt`). On any failure before returning, the lock
        and turn slot are rolled back.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # Fresh connect OUTSIDE turn lock — see ``send_prompt`` for the
        # rationale. Re-session on an existing cached client is deferred
        # to after ``lock.acquire`` so it cannot corrupt another thread's
        # turn. The handshake-locked check against ``_ephemeral_clients``
        # prevents spawning a second ``ACPClient`` onto a handle that a
        # concurrent ``prompt_once`` ephemeral is already using.
        try:
            with self._get_handshake_lock(name):
                with self._ephemeral_lock:
                    has_ephemeral = name in self._ephemeral_clients
                if self._clients.get(name) is None and not has_ephemeral:
                    self._connect_server_locked(
                        name, cwd=cwd, mcp_servers=mcp_servers,
                        timeout=timeout,
                    )
        except Exception as exc:
            self._record_last_error(name, exc)
            raise

        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            raise AcpClientError(
                f"server '{name}' already has an active turn; send_prompt_nonblocking is fail-fast",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )
        turn_installed = False
        try:
            client = self.ensure_connected(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
                _inside_turn=True,
            )
            ctx = _TurnContext(server=name, interactive=True)
            self._install_turn(name, ctx)
            turn_installed = True
            rid, slot = client.send_prompt_nonblocking(text)
            return client, rid, slot
        except BaseException as exc:
            if turn_installed:
                self._clear_turn(name)
            lock.release()
            if isinstance(exc, Exception):
                self._record_last_error(name, exc)
            raise

    def finish_prompt_nonblocking(
        self,
        name: str,
        client: ACPClient,
        rid: int,
        slot: Any,
    ) -> Dict[str, Any]:
        """Finalize a :meth:`send_prompt_nonblocking` turn on success.

        Releases the per-server lock and clears the turn slot in a
        ``finally``, so callers never leak serialization state even if
        ``finish_prompt`` raises.

        Failures propagate after being recorded in the ``last_error``
        snapshot so ``get_status()`` reports them; successful turns
        reset the recovery counters via ``_note_successful_turn`` for
        parity with ``send_prompt`` / ``prompt_once``.
        """
        try:
            try:
                result = client.finish_prompt(rid, slot)
            except Exception as exc:
                self._record_last_error(name, exc)
                raise
            self._note_successful_turn(name)
            return result
        finally:
            self._clear_turn(name)
            try:
                self._get_server_lock(name).release()
            except RuntimeError:
                # Lock already released (e.g., by a prior cancel path).
                logger.debug(
                    "acp[%s]: server lock was not held on finish", name,
                )

    def cancel_prompt_nonblocking(
        self,
        name: str,
        client: ACPClient,
        rid: int,
    ) -> None:
        """Abort a :meth:`send_prompt_nonblocking` turn.

        Guarantees the client-side pending slot is cleared (via
        :meth:`ACPClient.discard_pending_slot`) and the per-server lock
        is released, even if ``cancel_prompt`` fails mid-transport.
        """
        try:
            try:
                client.cancel_prompt(rid)
            except Exception:
                logger.debug(
                    "acp[%s]: cancel_prompt(nonblocking) raised",
                    name, exc_info=True,
                )
            finally:
                client.discard_pending_slot(rid)
        finally:
            self._clear_turn(name)
            try:
                self._get_server_lock(name).release()
            except RuntimeError:
                logger.debug(
                    "acp[%s]: server lock was not held on cancel", name,
                )

    # ------------------------------------------------------------------
    # prompt_once — one-shot turn with ephemeral client
    # ------------------------------------------------------------------

    def prompt_once(
        self,
        name: str,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
        interactive: bool = False,
        stop_process: bool = True,
        interaction_policy: Any = None,
    ) -> PromptResult:
        """Run one ACP prompt turn with deterministic cleanup.

        Intended for daemon / workflow runtimes that want a single
        request/response lifecycle rather than a reusable session.

        Concurrency contract (v1):

        * Acquires the per-server lock in **fail-fast** mode; if another
          turn is already active for this server, raises
          ``AcpClientError(code=SERVER_BUSY)``.
        * If no long-lived client exists for ``name``, builds an
          ephemeral client that is **not** registered in
          ``self._clients`` and does not appear in ``get_status()``.
          On exit the ephemeral client is closed.
        * ``stop_process=True`` (default) stops the server subprocess
          on exit, but only when no long-lived client exists for this
          name (otherwise the subprocess is shared and must survive).
        * If a long-lived client already exists for ``name``, it is
          reused; in that case the process is never stopped by this
          call regardless of ``stop_process``.

        Args:
            name: Server name.
            prompt: Plain-text user message.
            cwd: Per-call working directory.
            mcp_servers: Per-call MCP server configs.
            timeout: Seconds to wait for the turn (RPC only).
            interactive: Default ``False``; see :meth:`send_prompt`.
            stop_process: Stop the subprocess on exit when this call
                owns an ephemeral client.
            interaction_policy: Per-call override of the non-interactive
                interaction policy. Accepts :class:`InteractionPolicy`
                or the string ``"reject_all"`` / ``"accept_all"``.
                ``None`` falls back to the server's configured
                ``nonInteractivePolicy``. Only consulted when
                ``interactive=False`` (the default for ``prompt_once``).

        Returns:
            A :class:`PromptResult` with ``stop_reason``, raw payload,
            session id, and effective ``cwd``.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # ``interaction_policy`` is documented as only applying to
        # non-interactive turns, so skip resolution when
        # ``interactive=True``. Parity with ``send_prompt``.
        resolved_policy: Optional[InteractionPolicy] = None
        if not interactive:
            resolved_policy = self._resolve_interaction_policy(
                name, interaction_policy,
            )

        # Week 4 recovery contract also applies to the ``prompt_once``
        # reuse path: if the cached long-lived client's subprocess
        # died since the last turn, the caller deserves the same
        # classification + rebuild (or fatal raise) as ``send_prompt``
        # gets via ``ensure_connected``. This runs before any lock so
        # a fatal-state server refuses fast without claiming anything.
        try:
            self._check_cached_client_alive(name)
        except Exception as _exc:
            self._record_last_error(name, _exc)
            raise

        effective_cwd, effective_mcp = self._effective_session_params(
            name, cwd=cwd, mcp_servers=mcp_servers,
        )

        # Ephemeral setup is safe to do OUTSIDE the turn lock — an
        # ephemeral client is specific to this call and not visible to
        # any concurrent turn. Re-sessioning a CACHED client is *not*
        # safe outside the lock (it would overwrite shared session
        # state under another running turn), so we defer the
        # cached-client branch to inside the lock.
        #
        # The check-and-create runs under the handshake lock so a
        # concurrent ``send_prompt`` pre-warm or another ``prompt_once``
        # on another thread cannot race our check and cause two
        # ``ACPClient`` readers to attach to the same handle's stdout.
        #
        # If another thread's ``prompt_once`` already has an ephemeral
        # in flight, we cannot safely spawn a second one (duplicate
        # reader) and cannot borrow theirs (single-use per call). Fail
        # fast with ``SERVER_BUSY`` — ``_record_last_error`` filters it
        # so this doesn't clobber any real failure in the store.
        #
        # process_was_running is sampled inside the handshake lock so
        # that a concurrent start_server() / start_all() cannot start the
        # subprocess in the window between sampling and the lock, which
        # would leave process_was_running=False for a process we did not
        # start and cause the cleanup paths to stop a shared daemon.
        client: Optional[ACPClient] = None
        ephemeral_created = False
        process_was_running = True  # safe default: don't stop anything
        try:
            with self._get_handshake_lock(name):
                process_was_running = (
                    handle._proc is not None and handle._proc.poll() is None
                )
                with self._ephemeral_lock:
                    has_ephemeral = name in self._ephemeral_clients
                if has_ephemeral:
                    raise AcpClientError(
                        f"server '{name}' has an active turn; "
                        f"prompt_once is fail-fast",
                        code=AcpErrorCode.SERVER_BUSY,
                        details={"server": name},
                    )
                if self._clients.get(name) is None:
                    client = self._open_ephemeral_client_locked(
                        name,
                        cwd=effective_cwd,
                        mcp_servers=effective_mcp,
                        timeout=timeout,
                    )
                    ephemeral_created = True
                # Else: a long-lived client already exists (possibly
                # installed by a concurrent pre-warm). Leave ``client``
                # as None; the cached-client branch below (inside the
                # turn lock) handles fast-path reuse / re-session.
        except Exception as exc:
            self._record_last_error(name, exc)
            raise

        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            # Roll back the ephemeral setup we just did — leaving the
            # reader thread attached to the handle's stdout would break
            # the *next* caller's handshake exactly like the stale-
            # reader bug in ``connect_server`` / ``_open_ephemeral_client``.
            # Closing the client and stopping the handle (when we own
            # it) EOFs the reader cleanly. If the ephemeral was the
            # only user of a pre-existing subprocess we leave that
            # subprocess alone on the rare SERVER_BUSY race — the
            # cached-client path on the next attempt will take over.
            if ephemeral_created and client is not None:
                self._rollback_ephemeral_on_busy(
                    name, client, handle, process_was_running,
                )
            raise AcpClientError(
                f"server '{name}' has an active turn; prompt_once is fail-fast",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )

        try:
            try:
                if not ephemeral_created:
                    # Cached-client path. We hold the turn lock now, so
                    # a re-session on ``existing`` cannot corrupt any
                    # concurrent turn.
                    existing = self._clients.get(name)
                    if existing is None:
                        # Rare: cache was evicted (restart_server etc.)
                        # between the pre-check and here. Fall through
                        # to a full ``ensure_connected`` inside the
                        # turn lock.
                        client = self.ensure_connected(
                            name,
                            cwd=effective_cwd,
                            mcp_servers=effective_mcp,
                            timeout=timeout,
                            _inside_turn=True,
                        )
                    else:
                        # Re-check liveness now that we hold the turn lock:
                        # the process may have died in the window between the
                        # early pre-check and here.
                        self._check_cached_client_alive(name)
                        existing = self._clients.get(name)
                        if existing is None:
                            # Died since the pre-check; rebuild.
                            client = self.ensure_connected(
                                name,
                                cwd=effective_cwd,
                                mcp_servers=effective_mcp,
                                timeout=timeout,
                                _inside_turn=True,
                            )
                        elif existing.connection_info.session_id and self._session_matches(
                            existing, cwd=effective_cwd, mcp_servers=effective_mcp,
                        ):
                            client = existing
                        else:
                            # Re-session the cached client — handshake lock
                            # still serializes against concurrent
                            # connect_server / ephemeral setup on other
                            # threads.
                            with self._get_handshake_lock(name):
                                try:
                                    existing.create_session(
                                        cwd=effective_cwd,
                                        mcp_servers=effective_mcp,
                                        timeout=timeout,
                                    )
                                except BaseException as exc:
                                    if self._reclassify_as_handshake_fail(exc, name):
                                        self._note_handshake_failure_and_maybe_fatal(name)
                                    raise
                                self._note_handshake_success(name)
                            client = existing
                raw = self._run_turn_on_client(
                    name, client, prompt,
                    timeout=timeout,
                    interactive=interactive,
                    policy=resolved_policy,
                )
                self._note_successful_turn(name)
                return PromptResult(
                    stop_reason=raw.get("stopReason", "") if isinstance(raw, dict) else "",
                    raw=raw if isinstance(raw, dict) else {},
                    session_id=client.connection_info.session_id,
                    cwd=client.connection_info.session_cwd,
                )
            except Exception as exc:
                self._record_last_error(name, exc)
                raise
        finally:
            if ephemeral_created and client is not None:
                # prompt_once is always one-shot — never promote the
                # ephemeral client to the long-lived cache, regardless of
                # stop_process or process_was_running.  Close the client to
                # stop its reader thread cleanly; a subsequent connect or
                # prompt_once call on the same server will start a fresh
                # reader.  Only stop the subprocess when stop_process=True
                # AND this call was the one that started it.
                keep_process_alive = not stop_process or process_was_running
                with self._get_handshake_lock(name):
                    with self._ephemeral_lock:
                        if self._ephemeral_clients.get(name) is client:
                            self._ephemeral_clients.pop(name, None)
                    try:
                        client.close()
                    except Exception:
                        logger.debug(
                            "acp[%s]: error closing ephemeral client",
                            name, exc_info=True,
                        )
                    if not keep_process_alive and name not in self._clients:
                        try:
                            handle.stop()
                        except Exception:
                            logger.debug(
                                "acp[%s]: error stopping handle",
                                name, exc_info=True,
                                )
            lock.release()

    def _rollback_ephemeral_on_busy(
        self,
        name: str,
        client: ACPClient,
        handle: ACPProcessHandle,
        process_was_running: bool = False,
    ) -> None:
        """Tear down an ephemeral created before we lost the fail-fast
        turn-lock race in :meth:`prompt_once`.

        Mirrors the cleanup ``_open_ephemeral_client`` does on a
        handshake failure: ``client.close()`` + ``handle.stop()`` so the
        reader thread unblocks on stdout EOF and doesn't leak onto the
        next caller's transport. ``handle.stop()`` is guarded by
        ``name not in self._clients`` AND ``not process_was_running``
        because a long-lived client may have been installed while we were
        handshaking, and a pre-existing subprocess should never be torn
        down by a call that did not start it.

        Runs under the handshake lock so a concurrent
        ``send_prompt`` pre-warm / ``prompt_once`` check on another
        thread can never observe the brief window where the ephemeral
        has been popped but the subprocess is still running — that
        window would let them attach a fresh reader to a stdout that
        is about to EOF.
        """
        with self._get_handshake_lock(name):
            try:
                client.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error closing ephemeral during SERVER_BUSY rollback",
                    name, exc_info=True,
                )
            with self._ephemeral_lock:
                if self._ephemeral_clients.get(name) is client:
                    self._ephemeral_clients.pop(name, None)
            if name not in self._clients and not process_was_running:
                try:
                    handle.stop()
                except Exception:
                    logger.debug(
                        "acp[%s]: error stopping handle during SERVER_BUSY rollback",
                        name, exc_info=True,
                    )

    def _open_ephemeral_client(
        self,
        name: str,
        *,
        cwd: Optional[str],
        mcp_servers: Optional[List[dict]],
        timeout: Optional[float],
    ) -> ACPClient:
        """Build an ACPClient for a single :meth:`prompt_once` call.

        The client is stored in :attr:`_ephemeral_clients` only for the
        duration of the call so the notification / server-request
        callbacks can still resolve a client by server name.

        Handshake lock is held across the whole ``handle.start`` →
        ``initialize`` → ``create_session`` sequence (and the failure
        teardown) so a concurrent ``connect_server`` /
        ``ensure_connected`` on another thread cannot race with
        ``handle.stop()`` below.
        """
        handshake_lock = self._get_handshake_lock(name)
        handshake_lock.acquire()
        try:
            return self._open_ephemeral_client_locked(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
        finally:
            handshake_lock.release()

    def _open_ephemeral_client_locked(
        self,
        name: str,
        *,
        cwd: Optional[str],
        mcp_servers: Optional[List[dict]],
        timeout: Optional[float],
    ) -> ACPClient:
        """``_open_ephemeral_client`` body — caller holds handshake lock."""
        handle = self._handles[name]
        _existing_proc = handle._proc
        _we_started = _existing_proc is None or _existing_proc.poll() is not None
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

        def _on_notification(method: str, params: Any) -> None:
            self._route_notification(name, method, params)
            if self._notification_callback is not None:
                self._notification_callback(name, method, params)

        def _on_server_request(method: str, params: Any, request_id: Any) -> None:
            self._route_server_request(name, method, params, request_id)

        client = ACPClient(
            handle,
            notification_callback=_on_notification,
            server_request_callback=_on_server_request,
        )
        with self._ephemeral_lock:
            self._ephemeral_clients[name] = client
        try:
            client.start_reader()
            client.initialize(timeout=timeout)
            client.create_session(
                cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
        except BaseException as exc:
            # Shared classification + sticky-fatal accounting — same
            # behaviour as the long-lived ``connect_server`` and
            # cached-client ``ensure_connected`` paths so the
            # "2 consecutive handshakes ⇒ fatal" contract holds
            # whichever API the embedder chose.
            if self._reclassify_as_handshake_fail(exc, name):
                self._note_handshake_failure_and_maybe_fatal(name)
            with self._ephemeral_lock:
                if self._ephemeral_clients.get(name) is client:
                    self._ephemeral_clients.pop(name, None)
            try:
                client.close()
            except Exception:
                pass
            # client.close() calls unsubscribe_stdout() which detaches our
            # subscriber — no stale reader remains on the feeder.  Only stop
            # the subprocess if we started it; a pre-warmed server must survive
            # a bad one-shot call (e.g. invalid per-call session params) so
            # subsequent turns can continue on the existing process.
            if _we_started:
                try:
                    handle.stop()
                except Exception:
                    logger.debug(
                        "acp[%s]: stop handle after ephemeral setup failure raised",
                        name, exc_info=True,
                    )
            raise
        # Successful greenfield handshake in the ephemeral/``prompt_once``
        # path — clear the streak for the same reason the long-lived
        # ``connect_server`` and cached-client re-session paths do.
        # Without this, a prior isolated handshake failure would linger
        # at streak=1 and combine with a future unrelated handshake
        # failure into an incorrect sticky-fatal flip.
        self._note_handshake_success(name)
        return client

    # ------------------------------------------------------------------
    # cancel_turn
    # ------------------------------------------------------------------

    def cancel_turn(self, name: str) -> None:
        """Cancel the active turn on a server, if any.

        Sets the cancellation flag on the active turn context (if any)
        so that a latched non-interactive interaction error is suppressed
        in favor of the cancellation outcome. Sends ``session/cancel`` as
        a notification — does not wait for the per-server lock, so it is
        safe to call while a turn is in flight.

        No-op if the server has no client.
        """
        ctx = self._get_active_turn(name)
        if ctx is not None:
            ctx.cancelled = True
        client = self._client_for(name)
        if client is not None:
            client.cancel_active_turn()
