"""Error classification, handshake-failure streaks, and sticky-fatal state.

Provides :class:`RecoveryMixin`, mixed into :class:`ACPManager`.  Owns
``_fatal_servers`` / ``_restart_counts`` / ``_handshake_fail_streak``
accounting and the ``_check_cached_client_alive`` contract that every
entry point leans on before reusing a cached client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..client import (
    AcpClientError,
    AcpErrorCode,
    AcpInteractionRequiredError,
    AcpRpcError,
    AcpServerNotFound,
)
from ..models import ServerState, classify_process_death
from .helpers import logger


class RecoveryMixin:
    """Week 4 recovery state + Week 2 last-error accounting."""

    # Error codes that are about caller-side concurrency / misuse rather
    # than server health. Recording them would overwrite a real failure
    # with noise every time the caller retries, so they are skipped.
    _NON_RECORDABLE_ERROR_CODES: frozenset = frozenset({
        AcpErrorCode.SERVER_BUSY,
        AcpErrorCode.SERVER_NOT_FOUND,
    })

    # ------------------------------------------------------------------
    # Week 2: last-error store (assignment-time timestamp)
    # ------------------------------------------------------------------

    def _record_last_error(self, name: str, exc: BaseException) -> None:
        """Store the most recent embedder-facing error for *name*.

        Timestamp is taken **inside this method** using
        ``datetime.now(timezone.utc)`` — i.e., at store-time, not at
        the time the error was raised. Consumers read it via
        :meth:`get_status` to judge staleness. See the state-vs-error
        contract in ``docs/features/headless-runtime.md``.
        """
        if name not in self._handles:
            return
        if isinstance(exc, AcpClientError) and exc.code in self._NON_RECORDABLE_ERROR_CODES:
            return
        message = str(exc) or type(exc).__name__
        now = datetime.now(timezone.utc)
        with self._last_errors_lock:
            self._last_errors[name] = (message, now)

    def reset_last_error(self, name: str) -> None:
        """Clear the recorded ``last_error`` / ``last_error_at`` for *name*.

        ``last_error`` is intentionally sticky across successful turns
        (so a consumer that polls once per minute still sees the failure
        context), so this explicit reset is the only way to drop a
        stored error short of a new error overwriting it.

        Also clears the handle-level fallback (``handle.info.last_error``
        / ``handle.info.last_error_at``). Startup and handshake failures
        populate the handle directly without going through
        ``_record_last_error``, and ``get_status()`` falls back to the
        handle value when the manager-side store is empty — so without
        clearing both, the "explicit reset" surface would leave the
        reported ``last_error`` unchanged for exactly the failure modes
        (``start_server`` / ``start_all`` crashes) callers are most
        likely to want to reset after remediation.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        with self._last_errors_lock:
            self._last_errors.pop(name, None)
        with handle._lock:
            handle.info.last_error = None
            handle.info.last_error_at = None

    # ------------------------------------------------------------------
    # Week 4: client / process death classification + recovery
    # ------------------------------------------------------------------

    def is_fatal(self, name: str) -> bool:
        """Return ``True`` if *name* is in the terminal fatal state.

        A server lands in the fatal state when the Week 4 classifier
        (``classify_process_death``) returns ``"fatal"`` — for example
        after OOM / SIGKILL, after consecutive handshake failures, or
        after exceeding ``max_recoverable_restarts``. The mark is
        sticky and **only** cleared by an explicit
        :meth:`restart_server` / :meth:`start_server` call.
        """
        if name not in self._handles:
            raise AcpServerNotFound(name)
        with self._recovery_lock:
            return name in self._fatal_servers

    def restart_count(self, name: str) -> int:
        """Return the current auto-recovery counter for *name*.

        Reset on the first successful turn after a recovery; bumped on
        every classified-recoverable rebuild. Used by embedders to
        detect flapping servers.
        """
        if name not in self._handles:
            raise AcpServerNotFound(name)
        with self._recovery_lock:
            return self._restart_counts.get(name, 0)

    def _classify_handle_death(
        self, name: str, *, during_active_turn: bool,
    ) -> str:
        """Run :func:`classify_process_death` against the handle's state.

        Pulls the live exit code / signal info off the handle and the
        running counters off the manager. Returns the string literal
        ``"recoverable"`` or ``"fatal"``.
        """
        handle = self._handles[name]
        proc = handle._proc
        exit_code: Optional[int] = None
        signaled = False
        if proc is not None:
            exit_code = proc.poll()
            if exit_code is not None and exit_code < 0:
                # Popen.poll on POSIX returns negative signal numbers
                # (e.g. -9 for SIGKILL); exit code 137 (128 + 9) on
                # some shells maps the same way.
                signaled = True
            elif exit_code in (137, 139, 143):
                signaled = True
        server_cfg = self._config.servers.get(name)
        cap = (
            server_cfg.max_recoverable_restarts
            if server_cfg is not None else 3
        )
        with self._recovery_lock:
            count = self._restart_counts.get(name, 0)
            streak = self._handshake_fail_streak.get(name, 0)
        return classify_process_death(
            exit_code=exit_code,
            signaled=signaled,
            during_active_turn=during_active_turn,
            restart_count=count,
            max_recoverable_restarts=cap,
            handshake_fail_streak=streak,
        )

    def _note_recovery_attempt(self, name: str) -> None:
        """Record one recoverable-death rebuild. Called inside the lock."""
        with self._recovery_lock:
            self._restart_counts[name] = self._restart_counts.get(name, 0) + 1

    def _note_successful_turn(self, name: str) -> None:
        """Reset recovery counters after a turn succeeds end-to-end."""
        with self._recovery_lock:
            self._restart_counts[name] = 0
            self._handshake_fail_streak[name] = 0

    def _note_handshake_failure(self, name: str) -> None:
        with self._recovery_lock:
            self._handshake_fail_streak[name] = (
                self._handshake_fail_streak.get(name, 0) + 1
            )

    def _note_handshake_success(self, name: str) -> None:
        """Clear the handshake-fail streak after a successful handshake.

        Called on both greenfield success (``connect_server``) and
        cached-client re-session success (``ensure_connected`` /
        ``prompt_once``). Without the cached-path call, a prior
        single failed ``session/new`` would leave the streak at 1
        indefinitely — the next unrelated handshake failure would
        then trip sticky-fatal despite not being a *consecutive*
        pair of failures.
        """
        with self._recovery_lock:
            self._handshake_fail_streak[name] = 0

    def _note_handshake_failure_and_maybe_fatal(self, name: str) -> None:
        """Single source of truth for the "2 consecutive handshakes ⇒ fatal" rule.

        ``connect_server``, the cached-client re-session branch in
        ``ensure_connected``, and ``prompt_once`` all reclassify
        handshake/session-setup exceptions as ``HANDSHAKE_FAIL``. Each
        of those entry points must also flip sticky-fatal on the second
        failure, otherwise a host choosing one API (e.g. the public
        ``connect_server``) silently opts out of the documented
        recovery contract. Increments the streak and fatal-marks in a
        single critical section so a racing clear cannot desync them.
        """
        with self._recovery_lock:
            streak = self._handshake_fail_streak.get(name, 0) + 1
            self._handshake_fail_streak[name] = streak
        if streak > 1:
            self._mark_fatal(name)

    def _reclassify_as_handshake_fail(
        self, exc: BaseException, name: str,
    ) -> bool:
        """Tag a handshake/session-setup failure.

        Mirrors the classification the three entry points used to
        duplicate inline. Returns ``True`` when *exc* belongs to the
        handshake-failure streak — callers pair a ``True`` return with
        :meth:`_note_handshake_failure_and_maybe_fatal`.

        ``AcpInteractionRequiredError`` is intentionally excluded: a
        permission cancel during setup is a user decision, not a
        handshake regression, and must not count toward the
        sticky-fatal streak.

        Mutation policy (delivers Appendix D §D.7 in full):

        * **Non-RPC** :class:`AcpClientError` with a setup-eligible
          ``code`` (``PROTOCOL_ERROR`` / ``TRANSPORT_DISCONNECT`` /
          ``REQUEST_TIMEOUT``): the original code is stashed in
          ``details["underlying_code"]`` **before** we reclassify
          ``exc.code`` to :attr:`AcpErrorCode.HANDSHAKE_FAIL`. That
          preserves the documented pattern embedders branch on
          (``case AcpErrorCode.HANDSHAKE_FAIL`` — see
          ``developer-guide/en/part-3/4-reverse-acp-call.md``) *and*
          lets §D.7's finer-classification example actually fire,
          because the underlying timeout/disconnect is still
          available on the exception.
        * :class:`AcpRpcError`: ``code`` (JSON-RPC int) and
          ``error_code`` (``PROTOCOL_ERROR``) are rigid public
          contract — never mutated. Only ``details["phase"]`` +
          ``details["server"]`` are stamped; embedders detect RPC
          handshake failures via ``isinstance(exc, AcpRpcError)`` +
          ``details["phase"] == "handshake"``.
        * An ``AcpClientError`` already tagged ``HANDSHAKE_FAIL``
          (e.g. a nested reclassification, or a direct raise from
          the client layer) still counts toward the streak and gets
          its ``details["phase"]`` / ``["server"]`` stamped.

        In every branch ``details["phase"] = "handshake"`` is the
        canonical cross-subclass signal.
        """
        if isinstance(exc, AcpInteractionRequiredError):
            return False
        if isinstance(exc, AcpRpcError):
            # RPC contract: leave ``code`` (int) and ``error_code``
            # (PROTOCOL_ERROR) alone. Phase signal lives in details.
            exc.details.setdefault("server", name)
            exc.details["phase"] = "handshake"
            return True
        if isinstance(exc, AcpClientError) and exc.code in (
            AcpErrorCode.PROTOCOL_ERROR,
            AcpErrorCode.TRANSPORT_DISCONNECT,
            AcpErrorCode.REQUEST_TIMEOUT,
        ):
            # Preserve the original classification so §D.7 readers
            # can still distinguish handshake-timeout from
            # handshake-disconnect, then flip the headline code to
            # the documented handshake bucket.
            exc.details.setdefault("underlying_code", exc.code)
            exc.code = AcpErrorCode.HANDSHAKE_FAIL
            exc.details.setdefault("server", name)
            exc.details["phase"] = "handshake"
            return True
        # Already an AcpClientError tagged HANDSHAKE_FAIL (e.g. a nested
        # reclassification) — still counts toward the streak.
        if (
            isinstance(exc, AcpClientError)
            and exc.code is AcpErrorCode.HANDSHAKE_FAIL
        ):
            exc.details.setdefault("server", name)
            exc.details["phase"] = "handshake"
            return True
        return False

    def _mark_fatal(self, name: str) -> None:
        with self._recovery_lock:
            self._fatal_servers.add(name)
        # Reflect the fatal classification in the handle snapshot so
        # readiness() / is_ready() / get_status() can't keep routing
        # work to a server the manager has already given up on. The
        # state is cleared when ``restart_server`` / ``start_server``
        # re-invokes handle.start() / handle.restart().
        handle = self._handles.get(name)
        if handle is not None:
            with handle._lock:
                handle._set_state(
                    ServerState.FAILED,
                    f"server '{name}' marked fatal by recovery classifier",
                )
                handle.info.pid = None

    def _clear_fatal(self, name: str) -> None:
        with self._recovery_lock:
            self._fatal_servers.discard(name)
            self._restart_counts[name] = 0
            self._handshake_fail_streak[name] = 0

    def _check_cached_client_alive(
        self, name: str, *, count_restart: bool = True
    ) -> None:
        """Classify a dead cached subprocess and evict the stale client.

        Shared precondition for every entry point that may reuse a
        long-lived client (``ensure_connected``, ``prompt_once``).
        When the cached client's subprocess has died, the server is
        either (a) already sticky-fatal — raise; (b) recoverably dead
        — bump the counter, close the stale client, return so the
        caller can rebuild; or (c) fresh-fatal — mark fatal and
        raise. A caller with no cached client is a no-op.

        Pass ``count_restart=False`` from health-poll paths (``get_status``,
        ``readiness``) that detect a dead process but do not trigger a
        rebuild themselves.  Charging the restart budget on a pure poll
        would cause the next real recovery to flip the server to
        sticky-fatal one crash earlier than intended.

        Always call *after* the fatal check; ``ensure_connected``
        does so first thing.
        """
        if self.is_fatal(name):
            raise AcpClientError(
                f"server '{name}' is in the fatal recovery state; "
                f"call restart_server('{name}') to clear it",
                code=AcpErrorCode.TRANSPORT_DISCONNECT,
                details={"server": name, "recovery": "fatal"},
            )
        client = self._clients.get(name)
        handle = self._handles.get(name)
        if handle is None:
            return
        if client is None:
            # No cached client (e.g. after prompt_once(stop_process=False)).
            # Still detect a dead process so get_status()/readiness() don't
            # report a stale READY state forever — hosts that gate submissions
            # on readiness() would otherwise route work to a dead server.
            proc = handle._proc
            if proc is not None and proc.poll() is not None:
                active_turn = self._get_active_turn(name) is not None
                classification = self._classify_handle_death(
                    name, during_active_turn=active_turn,
                )
                if classification == "fatal":
                    self._mark_fatal(name)
                    raise AcpClientError(
                        f"server '{name}' died and the classifier flagged "
                        f"the death as fatal (see handle exit code / "
                        f"signal); call restart_server to retry",
                        code=AcpErrorCode.TRANSPORT_DISCONNECT,
                        details={"server": name, "recovery": "fatal"},
                    )
                # Health-poll callers pass count_restart=False, so they
                # don't charge the budget.  Recovery-path callers pass
                # count_restart=True (the default) and must charge it so
                # max_recoverable_restarts is enforced for this workflow too.
                if count_restart:
                    self._note_recovery_attempt(name)
                with handle._lock:
                    handle._set_state(ServerState.CONFIGURED)
                    handle.info.pid = None
            return
        proc = handle._proc
        if proc is None or proc.poll() is None:
            return
        # If a turn slot is still installed for the server, the subprocess
        # died mid-turn. The Week 4 contract guarantees those deaths get
        # one rebuild attempt regardless of the idle-exit cap, so pass
        # ``during_active_turn`` through to the classifier.
        active_turn = self._get_active_turn(name) is not None
        classification = self._classify_handle_death(
            name, during_active_turn=active_turn,
        )
        stale = self._clients.pop(name, None)
        if stale is not None:
            try:
                stale.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error closing dead cached client",
                    name, exc_info=True,
                )
            # Reset the streak so a prior isolated handshake failure cannot
            # combine with a future one across an unrelated subprocess death.
            # Without this, "handshake failure → recoverable death → handshake
            # failure" incorrectly trips sticky-fatal after only one new failure.
            self._handshake_fail_streak[name] = 0
        if classification == "fatal":
            self._mark_fatal(name)
            raise AcpClientError(
                f"server '{name}' died and the classifier flagged "
                f"the death as fatal (see handle exit code / "
                f"signal); call restart_server to retry",
                code=AcpErrorCode.TRANSPORT_DISCONNECT,
                details={"server": name, "recovery": "fatal"},
            )
        if count_restart:
            self._note_recovery_attempt(name)
        # Reflect the eviction in the handle snapshot so get_status() and
        # readiness() don't return a stale READY state while the transport
        # awaits a transparent rebuild on the next prompt submission.
        with handle._lock:
            handle._set_state(ServerState.CONFIGURED)
            handle.info.pid = None
