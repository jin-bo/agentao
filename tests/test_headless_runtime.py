"""Smoke tests for the headless runtime contract (Week 1 + Week 2).

These tests pin the behaviours a headless embedder must be able to
rely on before the Week 3-4 policy and lifecycle work lands. The
surface being tested is:

- ``ACPManager.get_status()`` returns ``list[ServerStatus]`` — Week 1
- Single active turn per server; concurrent submit → ``SERVER_BUSY`` — Week 1
- ``cancel_turn`` leaves the server able to accept the next turn — Week 1
- Non-interactive auto-reject surfaces as ``AcpInteractionRequiredError``
  and does not poison subsequent turns — Week 1
- ``REQUEST_TIMEOUT`` leaves the manager usable for the next turn — Week 1
- Repeated ``send_prompt`` reuses the same session — Week 1
- Week 2: extended ``ServerStatus`` fields populate correctly
- Week 2: ``last_error_at`` is assigned at *store* time (inside
  ``_record_last_error``), not at raise time
- Week 2: ``readiness()`` classifies state + active-turn correctly
- Week 2: ``SERVER_BUSY`` and ``SERVER_NOT_FOUND`` are filtered out
  of the ``last_error`` store so they do not overwrite real failures

The mock ACP server is the one from ``test_acp_client_embedding``;
this file re-uses its fixture helper so the mock behaviour stays
authoritative in a single place.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from agentao.acp_client import (
    AcpClientConfig,
    AcpClientError,
    AcpConfigError,
    AcpErrorCode,
    AcpInteractionRequiredError,
    AcpRpcError,
    InteractionPolicy,
    ServerStatus,
    classify_process_death,
)
from agentao.acp_client.manager import AcpServerNotFound, ACPManager
from agentao.acp_client.models import ServerState

from .support.acp_client import make_interaction_mock_manager as _make_mgr


class TestStatusSnapshotShape:
    def test_get_status_returns_typed_list(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            snapshot = mgr.get_status()
            assert len(snapshot) == 1
            (s,) = snapshot
            assert isinstance(s, ServerStatus)
            assert s.server == "srv"
            assert isinstance(s.state, str)
            assert s.has_active_turn is False
        finally:
            mgr.stop_all()

    def test_has_active_turn_flips_during_turn(self, tmp_path: Path) -> None:
        """``has_active_turn`` must be derived from the manager's active
        turn slot, not from handle state — it must be True for the full
        lifetime of a non-interactive turn."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            observed_active = threading.Event()
            done = threading.Event()

            def _slow() -> None:
                try:
                    mgr.send_prompt("srv", "slow", interactive=False, timeout=10)
                finally:
                    done.set()

            worker = threading.Thread(target=_slow, daemon=True)
            worker.start()

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                snap = mgr.get_status()
                if snap and snap[0].has_active_turn:
                    observed_active.set()
                    break
                time.sleep(0.05)

            assert observed_active.is_set(), (
                "has_active_turn never became True during the slow turn"
            )

            mgr.cancel_turn("srv")
            done.wait(timeout=5)
            worker.join(timeout=5)

            snap = mgr.get_status()
            assert snap[0].has_active_turn is False
        finally:
            mgr.stop_all()


class TestConcurrencyContract:
    """Single server → single active turn → second request raises
    ``SERVER_BUSY``. No implicit queueing."""

    def test_concurrent_submit_raises_server_busy(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            started = threading.Event()
            finished = threading.Event()

            def _slow() -> None:
                started.set()
                try:
                    mgr.send_prompt(
                        "srv", "slow", interactive=False, timeout=10,
                    )
                finally:
                    finished.set()

            worker = threading.Thread(target=_slow, daemon=True)
            worker.start()
            started.wait(timeout=2)
            time.sleep(0.2)

            with pytest.raises(AcpClientError) as exc_info:
                mgr.prompt_once("srv", "hello", timeout=2)
            assert exc_info.value.code is AcpErrorCode.SERVER_BUSY

            mgr.cancel_turn("srv")
            finished.wait(timeout=10)
            worker.join(timeout=10)
        finally:
            mgr.stop_all()


class TestCancelThenContinue:
    def test_cancel_then_next_turn_succeeds(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            done = threading.Event()
            box: dict = {}

            def _slow() -> None:
                try:
                    box["result"] = mgr.send_prompt(
                        "srv", "slow", interactive=False, timeout=10,
                    )
                except BaseException as exc:  # pragma: no cover
                    box["error"] = exc
                finally:
                    done.set()

            worker = threading.Thread(target=_slow, daemon=True)
            worker.start()

            # Wait until the turn is visible as active.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if any(s.has_active_turn for s in mgr.get_status()):
                    break
                time.sleep(0.05)

            mgr.cancel_turn("srv")
            done.wait(timeout=5)
            worker.join(timeout=5)
            assert "error" not in box
            assert box["result"]["stopReason"] == "cancelled"

            # Manager must be able to accept another turn on the same server.
            follow = mgr.send_prompt(
                "srv", "hello", interactive=False, timeout=5,
            )
            assert follow["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()


class TestNonInteractiveRejectDoesNotPoisonNextTurn:
    def test_reject_then_next_turn_succeeds(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()

            with pytest.raises(AcpInteractionRequiredError) as exc_info:
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5,
                )
            assert exc_info.value.code == AcpErrorCode.INTERACTION_REQUIRED

            # Turn slot must be clear after the auto-reject lands.
            with mgr._active_turns_lock:
                assert mgr._active_turns == {}

            follow = mgr.send_prompt(
                "srv", "hello", interactive=False, timeout=5,
            )
            assert follow["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()


class TestTimeoutRecovery:
    def test_timeout_raises_and_lets_next_turn_run(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            # The mock "slow" handler runs up to 5s; a 0.3s timeout
            # guarantees a REQUEST_TIMEOUT on the current turn.
            with pytest.raises(AcpClientError) as exc_info:
                mgr.send_prompt(
                    "srv", "slow", interactive=False, timeout=0.3,
                )
            assert exc_info.value.code is AcpErrorCode.REQUEST_TIMEOUT

            # Turn slot released, server lock released.
            with mgr._active_turns_lock:
                assert mgr._active_turns == {}

            follow = mgr.send_prompt(
                "srv", "hello", interactive=False, timeout=5,
            )
            assert follow["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()


class TestSessionReuse:
    def test_repeated_send_prompt_reuses_session(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            sid1 = mgr._clients["srv"].connection_info.session_id
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            sid2 = mgr._clients["srv"].connection_info.session_id
            assert sid1 == sid2
            # Handle returns to READY between turns.
            assert mgr.get_handle("srv").state == ServerState.READY
        finally:
            mgr.stop_all()


# ---------------------------------------------------------------------------
# Week 2 — extended diagnostics surface
# ---------------------------------------------------------------------------


class TestExtendedSnapshotShape:
    """Week 2 fields exist, have the right types, and start empty."""

    def test_initial_snapshot_has_week2_fields(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            (s,) = mgr.get_status()
            assert isinstance(s, ServerStatus)
            assert s.active_session_id is None
            assert s.last_error is None
            assert s.last_error_at is None
            assert s.inbox_pending == 0
            assert s.interaction_pending == 0
            assert s.config_warnings == []
        finally:
            mgr.stop_all()

    def test_active_session_id_populates_after_turn(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            (s,) = mgr.get_status()
            assert s.active_session_id is not None
            assert s.active_session_id == (
                mgr._clients["srv"].connection_info.session_id
            )
        finally:
            mgr.stop_all()


class TestLastErrorStore:
    """Errors raised from public entry points land in ``last_error`` /
    ``last_error_at``; fail-fast concurrency codes do not."""

    def test_timeout_records_last_error(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpClientError) as exc_info:
                mgr.send_prompt(
                    "srv", "slow", interactive=False, timeout=0.3,
                )
            assert exc_info.value.code is AcpErrorCode.REQUEST_TIMEOUT

            (s,) = mgr.get_status()
            assert s.last_error is not None
            assert "timeout" in s.last_error.lower()
            assert isinstance(s.last_error_at, datetime)
            assert s.last_error_at.tzinfo is timezone.utc
            # Store-time must be within a recent window of real wall time.
            delta = datetime.now(timezone.utc) - s.last_error_at
            assert timedelta(0) <= delta < timedelta(seconds=30)
        finally:
            mgr.stop_all()

    def test_interaction_required_records_last_error(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5,
                )

            (s,) = mgr.get_status()
            assert s.last_error is not None
            assert s.last_error_at is not None
        finally:
            mgr.stop_all()

    def test_last_error_persists_across_successful_turns(
        self, tmp_path: Path
    ) -> None:
        """``last_error`` is sticky — a later success does not clear it.

        Consumers are expected to combine ``state`` with
        ``last_error_at`` staleness to decide whether the recorded
        error is still relevant.
        """
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5,
                )
            (s_err,) = mgr.get_status()
            assert s_err.last_error is not None
            stored_at = s_err.last_error_at

            # Follow-up success must not clear the recorded error.
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            (s_ok,) = mgr.get_status()
            assert s_ok.last_error == s_err.last_error
            assert s_ok.last_error_at == stored_at
            # State returns to READY even though last_error is still set.
            assert s_ok.state == ServerState.READY.value
        finally:
            mgr.stop_all()

    def test_reset_last_error_clears_store(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpClientError):
                mgr.send_prompt(
                    "srv", "slow", interactive=False, timeout=0.3,
                )
            (s,) = mgr.get_status()
            assert s.last_error is not None

            mgr.reset_last_error("srv")
            (s_after,) = mgr.get_status()
            assert s_after.last_error is None
            assert s_after.last_error_at is None
        finally:
            mgr.stop_all()

    def test_reset_last_error_unknown_server_raises(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(AcpServerNotFound):
                mgr.reset_last_error("does-not-exist")
        finally:
            mgr.stop_all()

    def test_reset_last_error_clears_handle_level_fallback(
        self, tmp_path: Path,
    ) -> None:
        """Startup / handshake failures populate ``handle.info.last_error``
        directly without going through ``_record_last_error``; ``get_status``
        falls back to that value when the manager-side store is empty.
        ``reset_last_error`` must clear both sides, otherwise the
        documented reset surface silently does nothing for the failure
        modes (``start_server`` / ``start_all`` crashes) callers are
        most likely to reset after remediation."""
        mgr = _make_mgr(tmp_path)
        try:
            handle = mgr.get_handle("srv")
            assert handle is not None
            # Simulate a startup/handshake failure that only touched
            # ``handle.info.last_error`` (the manager store is empty).
            handle.info.last_error = "synthetic startup failure"

            (s_before,) = mgr.get_status()
            assert s_before.last_error == "synthetic startup failure"

            mgr.reset_last_error("srv")

            (s_after,) = mgr.get_status()
            assert s_after.last_error is None
            assert s_after.last_error_at is None
            assert handle.info.last_error is None
        finally:
            mgr.stop_all()

    def test_server_busy_does_not_overwrite_real_error(
        self, tmp_path: Path
    ) -> None:
        """``SERVER_BUSY`` on ``prompt_once`` is a caller-side signal.

        If a real failure is already stored, retrying after a busy
        signal must not wipe the original error — otherwise every
        polling retry would erase the diagnostic the host actually
        needs.
        """
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            # Seed a real recorded error.
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5,
                )
            (s_before,) = mgr.get_status()
            baseline = s_before.last_error

            # Trigger SERVER_BUSY manually by running a slow turn in a
            # worker thread, then firing a prompt_once against the same
            # server. prompt_once's non-blocking lock acquire fails
            # immediately with SERVER_BUSY.
            done = threading.Event()

            def _slow() -> None:
                try:
                    mgr.send_prompt(
                        "srv", "slow", interactive=False, timeout=10,
                    )
                finally:
                    done.set()

            worker = threading.Thread(target=_slow, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if any(s.has_active_turn for s in mgr.get_status()):
                    break
                time.sleep(0.05)

            with pytest.raises(AcpClientError) as exc_info:
                mgr.prompt_once("srv", "hello", timeout=2)
            assert exc_info.value.code is AcpErrorCode.SERVER_BUSY

            (s_after,) = mgr.get_status()
            assert s_after.last_error == baseline

            mgr.cancel_turn("srv")
            done.wait(timeout=10)
            worker.join(timeout=10)
        finally:
            mgr.stop_all()

    def test_last_error_at_uses_store_time_clock(
        self, tmp_path: Path
    ) -> None:
        """``last_error_at`` is assigned using the clock **inside**
        ``_record_last_error``, not from a value captured earlier.

        Proof: patch ``datetime`` in the manager module so that
        ``datetime.now(timezone.utc)`` returns a fixed instant for the
        duration of the recording call. If the timestamp were taken at
        raise time and merely passed into the store, this patch could
        not intercept it.
        """
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            fixed = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

            class _FixedDatetime(datetime):
                @classmethod
                def now(cls, tz=None):  # type: ignore[override]
                    return fixed if tz is timezone.utc else datetime.now(tz)

            with mock.patch(
                "agentao.acp_client.manager.recovery.datetime", _FixedDatetime
            ):
                with pytest.raises(AcpClientError):
                    mgr.send_prompt(
                        "srv", "slow", interactive=False, timeout=0.3,
                    )

            (s,) = mgr.get_status()
            assert s.last_error_at == fixed
        finally:
            mgr.stop_all()


class TestReadinessClassifier:
    """``readiness()`` collapses ``state`` × ``has_active_turn`` into
    a stable four-valued classification."""

    def test_unconfigured_server_raises(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(AcpServerNotFound):
                mgr.readiness("nope")
        finally:
            mgr.stop_all()

    def test_ready_after_start(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            assert mgr.readiness("srv") == "ready"
            assert mgr.is_ready("srv")
        finally:
            mgr.stop_all()

    def test_busy_during_active_turn(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            done = threading.Event()

            def _slow() -> None:
                try:
                    mgr.send_prompt(
                        "srv", "slow", interactive=False, timeout=10,
                    )
                finally:
                    done.set()

            worker = threading.Thread(target=_slow, daemon=True)
            worker.start()

            deadline = time.monotonic() + 3.0
            saw_busy = False
            while time.monotonic() < deadline:
                if mgr.readiness("srv") == "busy":
                    saw_busy = True
                    break
                time.sleep(0.05)
            assert saw_busy, "readiness never became busy during a slow turn"

            mgr.cancel_turn("srv")
            done.wait(timeout=10)
            worker.join(timeout=10)
        finally:
            mgr.stop_all()

    def test_not_ready_before_start(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            assert mgr.readiness("srv") == "not_ready"
            assert not mgr.is_ready("srv")
        finally:
            mgr.stop_all()

    def test_idle_crashed_server_not_reported_ready(
        self, tmp_path: Path,
    ) -> None:
        """A server that crashes between turns keeps ``state == READY``
        until the next prompt triggers ``_check_cached_client_alive``.
        Without the idle-crash guard, ``readiness()`` would lie and
        tell a polling host it is safe to submit — the host would then
        hit recovery / TRANSPORT_DISCONNECT immediately. The guard
        must downgrade the classification to ``not_ready``."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            assert mgr.readiness("srv") == "ready"

            handle = mgr.get_handle("srv")
            assert handle is not None
            proc = handle._proc
            assert proc is not None
            proc.kill()
            proc.wait(timeout=5)

            # ``state`` is still ``READY`` — no prompt has run since
            # the crash — but ``readiness()`` must notice the dead pid.
            # SIGKILL is classified as fatal → "failed"; a non-signal
            # exit would return "not_ready". Either way the guard must
            # never return "ready" for a dead process.
            assert handle.info.state is ServerState.READY
            assert mgr.readiness("srv") != "ready"
            assert not mgr.is_ready("srv")
        finally:
            mgr.stop_all()


# ---------------------------------------------------------------------------
# Week 3 — interaction policy model
# ---------------------------------------------------------------------------


def _make_mgr_with_policy(tmp_path: Path, policy_mode: str) -> ACPManager:
    """Variant of ``_make_mgr`` that sets a specific server default
    ``nonInteractivePolicy`` as an :class:`InteractionPolicy` dataclass,
    bypassing ``from_dict`` (that path is exercised separately in the
    legacy-config tests)."""
    return _make_mgr(
        tmp_path,
        non_interactive_policy=InteractionPolicy(mode=policy_mode),
    )


class TestInteractionPolicyDefault:
    """Server default applies when no per-call override is given."""

    def test_default_reject_all_raises_on_permission(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5,
                )
        finally:
            mgr.stop_all()

    def test_accept_all_default_resolves_permission(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr_with_policy(tmp_path, "accept_all")
        try:
            mgr.start_all()
            result = mgr.send_prompt(
                "srv", "permission", interactive=False, timeout=5,
            )
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()


class TestInteractionPolicyOverride:
    """Per-call override wins over server default."""

    def test_per_call_accept_overrides_reject_default(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)  # server default = reject_all
        try:
            mgr.start_all()
            result = mgr.send_prompt(
                "srv", "permission", interactive=False, timeout=5,
                interaction_policy="accept_all",
            )
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()

    def test_per_call_reject_overrides_accept_default(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr_with_policy(tmp_path, "accept_all")
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5,
                    interaction_policy="reject_all",
                )
        finally:
            mgr.stop_all()

    def test_per_call_accepts_interaction_policy_dataclass(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            result = mgr.send_prompt(
                "srv", "permission", interactive=False, timeout=5,
                interaction_policy=InteractionPolicy(mode="accept_all"),
            )
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()

    def test_invalid_override_string_raises(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(ValueError):
                mgr.send_prompt(
                    "srv", "hello", interactive=False, timeout=5,
                    interaction_policy="yolo",
                )
        finally:
            mgr.stop_all()

    def test_invalid_override_type_raises(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(TypeError):
                mgr.send_prompt(
                    "srv", "hello", interactive=False, timeout=5,
                    interaction_policy=42,
                )
        finally:
            mgr.stop_all()

    def test_prompt_once_accepts_interaction_policy(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            result = mgr.prompt_once(
                "srv", "permission", timeout=5,
                interaction_policy="accept_all",
            )
            assert result.stop_reason == "end_turn"
        finally:
            mgr.stop_all()


class TestLegacyConfigRejection:
    """Legacy string form of ``nonInteractivePolicy`` is broken at
    config *load* time (Issue 12). Migration is documented, not silent."""

    def test_legacy_string_config_raises_at_from_dict(
        self, tmp_path: Path,
    ) -> None:
        raw = {
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                    "nonInteractivePolicy": "reject_all",
                },
            },
        }
        with pytest.raises(AcpConfigError) as exc_info:
            AcpClientConfig.from_dict(raw, project_root=tmp_path)
        # Migration guidance must point to the new shape.
        msg = str(exc_info.value)
        assert "nonInteractivePolicy" in msg
        assert "{\"mode\":" in msg or '"mode"' in msg
        assert "reject_all" in msg
        # Explicit migration pointer.
        assert "e-migration" in msg

    def test_legacy_string_config_from_file_raises(
        self, tmp_path: Path,
    ) -> None:
        """Loading through ``load_acp_client_config`` — the path the
        real CLI uses — must also fail, not just ``from_dict``."""
        import json
        from agentao.acp_client import load_acp_client_config

        acp_dir = tmp_path / ".agentao"
        acp_dir.mkdir()
        acp_path = acp_dir / "acp.json"
        acp_path.write_text(json.dumps({
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                    "nonInteractivePolicy": "accept_all",
                },
            },
        }))
        with pytest.raises(AcpConfigError) as exc_info:
            load_acp_client_config(project_root=tmp_path)
        assert "nonInteractivePolicy" in str(exc_info.value)

    def test_new_structured_shape_is_accepted(
        self, tmp_path: Path,
    ) -> None:
        raw = {
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                    "nonInteractivePolicy": {"mode": "accept_all"},
                },
            },
        }
        cfg = AcpClientConfig.from_dict(raw, project_root=tmp_path)
        assert isinstance(
            cfg.servers["srv"].non_interactive_policy, InteractionPolicy,
        )
        assert cfg.servers["srv"].non_interactive_policy.mode == "accept_all"

    def test_invalid_mode_in_structured_shape_raises(
        self, tmp_path: Path,
    ) -> None:
        raw = {
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                    "nonInteractivePolicy": {"mode": "yolo"},
                },
            },
        }
        with pytest.raises(AcpConfigError):
            AcpClientConfig.from_dict(raw, project_root=tmp_path)


# ---------------------------------------------------------------------------
# Week 4 — client/process death classification + recovery
# ---------------------------------------------------------------------------


class TestClassifyProcessDeath:
    """Pure classifier — the decision matrix lives here as runnable truth."""

    def test_signaled_is_fatal(self) -> None:
        assert classify_process_death(
            exit_code=-9, signaled=True,
            during_active_turn=False,
            restart_count=0, max_recoverable_restarts=3,
        ) == "fatal"

    def test_oom_like_exit_code_is_fatal(self) -> None:
        assert classify_process_death(
            exit_code=137, signaled=True,
            during_active_turn=False,
            restart_count=0, max_recoverable_restarts=3,
        ) == "fatal"

    def test_handshake_fail_streak_is_fatal(self) -> None:
        assert classify_process_death(
            exit_code=1, signaled=False,
            during_active_turn=False,
            restart_count=0, max_recoverable_restarts=3,
            handshake_fail_streak=2,
        ) == "fatal"

    def test_idle_clean_exit_is_recoverable(self) -> None:
        assert classify_process_death(
            exit_code=0, signaled=False,
            during_active_turn=False,
            restart_count=0, max_recoverable_restarts=3,
        ) == "recoverable"

    def test_active_turn_death_is_recoverable_even_above_cap(self) -> None:
        # Active-turn death bypasses the cap so the next call can try
        # once to recover. The cap still bounds *idle* death respawns.
        assert classify_process_death(
            exit_code=1, signaled=False,
            during_active_turn=True,
            restart_count=99, max_recoverable_restarts=3,
        ) == "recoverable"

    def test_idle_nonzero_within_cap_is_recoverable(self) -> None:
        assert classify_process_death(
            exit_code=1, signaled=False,
            during_active_turn=False,
            restart_count=2, max_recoverable_restarts=3,
        ) == "recoverable"

    def test_idle_nonzero_beyond_cap_is_fatal(self) -> None:
        assert classify_process_death(
            exit_code=1, signaled=False,
            during_active_turn=False,
            restart_count=3, max_recoverable_restarts=3,
        ) == "fatal"

    def test_stdio_eof_without_exit_is_recoverable(self) -> None:
        assert classify_process_death(
            exit_code=None, signaled=False,
            during_active_turn=False,
            restart_count=0, max_recoverable_restarts=3,
        ) == "recoverable"


class TestRecoveryState:
    """Manager-side plumbing of the classifier."""

    def test_is_fatal_defaults_false(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            assert mgr.is_fatal("srv") is False
            assert mgr.restart_count("srv") == 0
        finally:
            mgr.stop_all()

    def test_is_fatal_unknown_server_raises(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(AcpServerNotFound):
                mgr.is_fatal("nope")
            with pytest.raises(AcpServerNotFound):
                mgr.restart_count("nope")
        finally:
            mgr.stop_all()

    def test_restart_server_clears_fatal_mark(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr._mark_fatal("srv")
            assert mgr.is_fatal("srv")
            # ``restart_server`` is the operator-action escape hatch.
            mgr.restart_server("srv")
            assert mgr.is_fatal("srv") is False
            assert mgr.restart_count("srv") == 0
        finally:
            mgr.stop_all()

    def test_restart_server_evicts_cached_client(
        self, tmp_path: Path,
    ) -> None:
        """``restart_server`` must close and drop the cached client so
        the next ``send_prompt`` rebuilds against the fresh subprocess.
        Without the eviction, ``_clients[name]`` keeps pointing at the
        old stdio transport and the first post-restart prompt reuses
        a dead session before ``_check_cached_client_alive`` notices,
        surfacing ``TRANSPORT_DISCONNECT`` where recovery was promised.
        """
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            cached_before = mgr.get_client("srv")
            assert cached_before is not None

            mgr.restart_server("srv")

            # The stale client must be gone. A fresh prompt then
            # rebuilds against the replacement subprocess and should
            # succeed (i.e. no TRANSPORT_DISCONNECT).
            assert mgr.get_client("srv") is None
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            cached_after = mgr.get_client("srv")
            assert cached_after is not None
            assert cached_after is not cached_before
        finally:
            mgr.stop_all()

    def test_start_server_evicts_client_on_failed_alive_restart(
        self, tmp_path: Path,
    ) -> None:
        """When ``start_server`` forces a ``handle.restart()`` because
        the handle was marked FAILED while the subprocess was still
        alive (the ``_mark_fatal`` → live-proc case), the cached client
        is pinned to the soon-to-be-killed subprocess and must be
        evicted before the replacement spawns. Otherwise the next
        prompt reuses a dead transport."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            cached_before = mgr.get_client("srv")
            assert cached_before is not None

            # Force the FAILED-state + live-proc restart branch.
            handle = mgr.get_handle("srv")
            assert handle is not None
            with handle._lock:
                handle._set_state(ServerState.FAILED, "synthetic fatal")

            mgr.start_server("srv")

            assert mgr.get_client("srv") is None
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            cached_after = mgr.get_client("srv")
            assert cached_after is not None
            assert cached_after is not cached_before
        finally:
            mgr.stop_all()

    def test_fatal_server_refuses_ensure_connected(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr._mark_fatal("srv")
            with pytest.raises(AcpClientError) as exc_info:
                mgr.send_prompt(
                    "srv", "hello", interactive=False, timeout=5,
                )
            assert exc_info.value.code is AcpErrorCode.TRANSPORT_DISCONNECT
            assert "fatal" in str(exc_info.value).lower()
        finally:
            mgr.stop_all()

    def test_successful_turn_resets_restart_count(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            # Simulate a prior recoverable restart.
            mgr._note_recovery_attempt("srv")
            mgr._note_recovery_attempt("srv")
            assert mgr.restart_count("srv") == 2

            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            assert mgr.restart_count("srv") == 0
        finally:
            mgr.stop_all()

    def test_connect_server_handshake_failures_trip_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hosts that call ``connect_server`` directly must hit the
        same "2 consecutive handshake failures ⇒ sticky fatal" contract
        as ``send_prompt`` / ``prompt_once``. Without handshake
        accounting inside ``connect_server`` itself, callers could
        retry this public API forever and never flip to fatal."""
        from agentao.acp_client.client import ACPClient

        def _boom(self, *a, **kw):
            raise AcpClientError(
                "synthetic handshake failure",
                code=AcpErrorCode.HANDSHAKE_FAIL,
            )

        monkeypatch.setattr(ACPClient, "initialize", _boom)
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(AcpClientError) as exc1:
                mgr.connect_server("srv", timeout=5)
            assert exc1.value.code is AcpErrorCode.HANDSHAKE_FAIL
            assert mgr.is_fatal("srv") is False

            # Second consecutive handshake failure on the same public
            # entry point must flip sticky-fatal.
            with pytest.raises(AcpClientError) as exc2:
                mgr.connect_server("srv", timeout=5)
            assert exc2.value.code is AcpErrorCode.HANDSHAKE_FAIL
            assert mgr.is_fatal("srv") is True
        finally:
            mgr.stop_all()

    def test_handshake_reclassification_respects_rpc_error_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reclassification must not violate either :class:`AcpRpcError`
        invariant: ``code`` stays the raw JSON-RPC numeric wire int,
        and ``error_code`` stays :attr:`AcpErrorCode.PROTOCOL_ERROR`.
        The handshake-phase signal lives in ``details["phase"]``
        (per Appendix D.3) instead, so embedders can distinguish
        handshake-phase RPC failures from steady-state RPC failures
        without any attribute mutation on the exception."""
        from agentao.acp_client.client import ACPClient

        def _boom(self, *a, **kw):
            raise AcpRpcError(rpc_code=-32603, rpc_message="boom")

        monkeypatch.setattr(ACPClient, "initialize", _boom)
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(AcpRpcError) as exc_info:
                mgr.connect_server("srv", timeout=5)
            err = exc_info.value
            # `code` stays the raw JSON-RPC wire int.
            assert isinstance(err.code, int)
            assert err.code == -32603
            assert err.rpc_code == -32603
            # `error_code` stays PROTOCOL_ERROR — the AcpRpcError
            # structured-category invariant holds across
            # reclassification.
            assert err.error_code is AcpErrorCode.PROTOCOL_ERROR
            # Handshake-phase signal lives in details, so embedders
            # who want "was this a handshake failure?" can branch on
            # it uniformly across RPC and non-RPC subclasses.
            assert err.details.get("phase") == "handshake"
            assert err.details.get("server") == "srv"
        finally:
            mgr.stop_all()

    def test_handshake_reclassification_preserves_non_rpc_underlying_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-RPC handshake reclassification must deliver *both*
        documented guarantees simultaneously:

        1. The headline ``code`` flips to ``HANDSHAKE_FAIL`` so the
           ``case AcpErrorCode.HANDSHAKE_FAIL:`` pattern taught in
           part-3/3.4 and elsewhere keeps working.
        2. The underlying classification (``REQUEST_TIMEOUT`` here) is
           preserved in ``details["underlying_code"]`` so Appendix D
           §D.7's finer-detail example can actually fire — otherwise
           "was the handshake failure a timeout vs. a disconnect?"
           becomes unanswerable inside a handshake branch.
        """
        from agentao.acp_client.client import ACPClient

        def _boom(self, *a, **kw):
            raise AcpClientError(
                "synthetic handshake timeout",
                code=AcpErrorCode.REQUEST_TIMEOUT,
            )

        monkeypatch.setattr(ACPClient, "initialize", _boom)
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(AcpClientError) as exc_info:
                mgr.connect_server("srv", timeout=5)
            err = exc_info.value
            # Headline code flipped to HANDSHAKE_FAIL — old
            # `case HANDSHAKE_FAIL` callers still match.
            assert err.code is AcpErrorCode.HANDSHAKE_FAIL
            # Underlying classification preserved — §D.7 finer-detail
            # embedders can still distinguish timeout vs. disconnect.
            assert (
                err.details.get("underlying_code")
                is AcpErrorCode.REQUEST_TIMEOUT
            )
            assert err.details.get("phase") == "handshake"
            assert err.details.get("server") == "srv"
        finally:
            mgr.stop_all()

    def test_ensure_connected_resession_failures_trip_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a cached client has to re-run ``session/new`` (e.g.
        because a prior attempt cleared ``session_id``), repeated
        failures must feed the handshake-fail streak. Otherwise a
        warmed server with a persistently failing ``session/new``
        bypasses the documented sticky-fatal recovery path."""
        from agentao.acp_client.client import ACPClient

        mgr = _make_mgr(tmp_path)
        try:
            # Warm a cached client via a successful real session.
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            client = mgr.get_client("srv")
            assert client is not None

            # Force subsequent ``create_session`` calls to fail, and
            # clear the cached session_id so ensure_connected takes
            # the re-session branch instead of the fast-path.
            client.connection_info.session_id = None

            def _boom(self, *a, **kw):
                # Mimic ACPClient.create_session's side-effect on
                # failure so the cached client stays in the retry
                # branch for the next attempt.
                self.connection_info.session_id = None
                raise AcpClientError(
                    "synthetic session/new failure",
                    code=AcpErrorCode.HANDSHAKE_FAIL,
                )

            monkeypatch.setattr(ACPClient, "create_session", _boom)

            with pytest.raises(AcpClientError) as exc1:
                mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            assert exc1.value.code is AcpErrorCode.HANDSHAKE_FAIL
            assert mgr.is_fatal("srv") is False

            # Second consecutive failure on the cached-client re-session
            # branch must flip sticky-fatal.
            with pytest.raises(AcpClientError) as exc2:
                mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            assert exc2.value.code is AcpErrorCode.HANDSHAKE_FAIL
            assert mgr.is_fatal("srv") is True
        finally:
            mgr.stop_all()

    def test_successful_greenfield_prompt_once_clears_handshake_streak(
        self, tmp_path: Path,
    ) -> None:
        """A successful greenfield ``prompt_once`` (no cached client,
        goes through ``_open_ephemeral_client``) must clear the
        handshake-fail streak, same as ``connect_server`` and the
        cached-client re-session paths. Without the reset, a prior
        isolated handshake failure stays at streak=1 and a future
        unrelated handshake failure would wrongly trip sticky-fatal."""
        mgr = _make_mgr(tmp_path)
        try:
            # Simulate one prior isolated handshake failure so the
            # streak is sitting at 1 before we run the greenfield
            # ``prompt_once`` path.
            mgr._note_handshake_failure("srv")
            assert mgr._handshake_fail_streak.get("srv", 0) == 1

            # No cached client; no call to start_all(). prompt_once
            # owns the full start → initialize → session/new → turn
            # → teardown lifecycle via _open_ephemeral_client.
            result = mgr.prompt_once("srv", "hello", timeout=5)
            assert result.stop_reason  # turn actually ran

            # Greenfield success must have reset the streak.
            assert mgr._handshake_fail_streak.get("srv", 0) == 0
            assert mgr.is_fatal("srv") is False
        finally:
            mgr.stop_all()

    def test_successful_resession_clears_handshake_streak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A successful cached-client ``create_session()`` must clear
        the handshake-fail streak, mirroring the greenfield
        ``connect_server`` path. Without this reset, a single earlier
        failed ``session/new`` leaves the streak at 1 forever — a
        future unrelated handshake failure would then flip sticky-fatal
        despite the two failures not being *consecutive*. This
        regression specifically exercises the
        ``ensure_connected`` / ``prompt_once`` re-session path, where
        the fix has to live."""
        from agentao.acp_client.client import ACPClient

        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            # Warm a cached client with a successful real session.
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            client = mgr.get_client("srv")
            assert client is not None

            # Simulate one prior failed re-session (streak = 1) and
            # force session_id to be cleared so the next call takes
            # the cached-client re-session branch.
            mgr._note_handshake_failure("srv")
            assert mgr._handshake_fail_streak.get("srv", 0) == 1
            client.connection_info.session_id = None

            # The next send_prompt triggers re-session (original
            # create_session, which will succeed against the mock).
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            assert mgr._handshake_fail_streak.get("srv", 0) == 0

            # Prove the sticky-fatal no longer fires on the next
            # single failure — would have if the streak had leaked.
            def _boom(self, *a, **kw):
                self.connection_info.session_id = None
                raise AcpClientError(
                    "synthetic late failure",
                    code=AcpErrorCode.HANDSHAKE_FAIL,
                )

            monkeypatch.setattr(ACPClient, "create_session", _boom)
            # Re-trigger the re-session branch.
            client = mgr.get_client("srv")
            assert client is not None
            client.connection_info.session_id = None
            with pytest.raises(AcpClientError):
                mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            # One failure after a reset must NOT be sticky-fatal.
            assert mgr.is_fatal("srv") is False
        finally:
            mgr.stop_all()

    def test_prompt_once_cached_client_resession_goes_through_classifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``prompt_once`` reuses a cached long-lived client, and when
        ``session_id`` has been cleared it must rerun ``session/new``
        via :meth:`_reclassify_as_handshake_fail` + the sticky-fatal
        streak — same as ``ensure_connected``. Previously the
        cached-client re-session branch called ``create_session``
        directly and failures bypassed the handshake classification
        (no ``details["phase"]``, no ``underlying_code``, no fatal
        accounting)."""
        from agentao.acp_client.client import ACPClient

        mgr = _make_mgr(tmp_path)
        try:
            # Warm a cached long-lived client via a successful real
            # session, then clear session_id so prompt_once takes the
            # re-session branch on the next call.
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            client = mgr.get_client("srv")
            assert client is not None
            client.connection_info.session_id = None

            def _boom(self, *a, **kw):
                self.connection_info.session_id = None
                raise AcpClientError(
                    "synthetic session/new failure during prompt_once",
                    code=AcpErrorCode.REQUEST_TIMEOUT,
                )

            monkeypatch.setattr(ACPClient, "create_session", _boom)

            with pytest.raises(AcpClientError) as exc1:
                mgr.prompt_once("srv", "hello", timeout=5)
            err1 = exc1.value
            # Classification + underlying preservation reached the
            # cached-client prompt_once path.
            assert err1.code is AcpErrorCode.HANDSHAKE_FAIL
            assert err1.details.get("phase") == "handshake"
            assert (
                err1.details.get("underlying_code")
                is AcpErrorCode.REQUEST_TIMEOUT
            )
            assert mgr.is_fatal("srv") is False

            # Second consecutive failure on the same branch must flip
            # sticky-fatal.
            with pytest.raises(AcpClientError):
                mgr.prompt_once("srv", "hello", timeout=5)
            assert mgr.is_fatal("srv") is True
        finally:
            mgr.stop_all()


class TestMaxRecoverableRestartsConfig:
    """Config parsing accepts the new camelCase key."""

    def test_default_is_three(self, tmp_path: Path) -> None:
        raw = {
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                },
            },
        }
        cfg = AcpClientConfig.from_dict(raw, project_root=tmp_path)
        assert cfg.servers["srv"].max_recoverable_restarts == 3

    def test_override_is_accepted(self, tmp_path: Path) -> None:
        raw = {
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                    "maxRecoverableRestarts": 10,
                },
            },
        }
        cfg = AcpClientConfig.from_dict(raw, project_root=tmp_path)
        assert cfg.servers["srv"].max_recoverable_restarts == 10

    def test_negative_is_rejected(self, tmp_path: Path) -> None:
        raw = {
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                    "maxRecoverableRestarts": -1,
                },
            },
        }
        with pytest.raises(AcpConfigError):
            AcpClientConfig.from_dict(raw, project_root=tmp_path)

    def test_non_integer_is_rejected(self, tmp_path: Path) -> None:
        raw = {
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {},
                    "cwd": str(tmp_path),
                    "maxRecoverableRestarts": "three",
                },
            },
        }
        with pytest.raises(AcpConfigError):
            AcpClientConfig.from_dict(raw, project_root=tmp_path)


# ---------------------------------------------------------------------------
# Week 4 — daemon-style regression suite (Issue 17)
# ---------------------------------------------------------------------------


class TestDaemonRegression:
    """End-to-end scenarios the Week 4 headless runtime must uphold."""

    def test_long_session_reuse_stays_ready(self, tmp_path: Path) -> None:
        """Multiple back-to-back turns on the same server reuse the
        session and return to READY between turns."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            first_session: str | None = None
            for _ in range(5):
                mgr.send_prompt(
                    "srv", "hello", interactive=False, timeout=5,
                )
                sid = mgr._clients["srv"].connection_info.session_id
                if first_session is None:
                    first_session = sid
                assert sid == first_session
                assert mgr.get_handle("srv").state == ServerState.READY
            assert mgr.restart_count("srv") == 0
        finally:
            mgr.stop_all()

    def test_reject_then_continue(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5,
                )
            result = mgr.send_prompt(
                "srv", "hello", interactive=False, timeout=5,
            )
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()

    def test_cancel_then_continue(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            done = threading.Event()

            def _slow() -> None:
                try:
                    mgr.send_prompt(
                        "srv", "slow", interactive=False, timeout=10,
                    )
                finally:
                    done.set()

            worker = threading.Thread(target=_slow, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if any(s.has_active_turn for s in mgr.get_status()):
                    break
                time.sleep(0.05)

            mgr.cancel_turn("srv")
            done.wait(timeout=5)
            worker.join(timeout=5)

            result = mgr.send_prompt(
                "srv", "hello", interactive=False, timeout=5,
            )
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()

    def test_timeout_then_continue(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpClientError) as exc_info:
                mgr.send_prompt(
                    "srv", "slow", interactive=False, timeout=0.3,
                )
            assert exc_info.value.code is AcpErrorCode.REQUEST_TIMEOUT
            result = mgr.send_prompt(
                "srv", "hello", interactive=False, timeout=5,
            )
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()

    def test_prompt_once_recovers_dead_long_lived_client(
        self, tmp_path: Path,
    ) -> None:
        """``prompt_once`` must honor the Week 4 recovery contract
        even when a long-lived client is cached. Previously this
        path reused the dead client directly and bypassed
        classification; after the P1 fix it routes through
        ``_check_cached_client_alive`` like ``ensure_connected``.
        """
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            # Establish a long-lived client via send_prompt.
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            handle = mgr.get_handle("srv")
            proc = handle._proc
            assert proc is not None
            # Kill the subprocess out from under the cached client.
            proc.terminate()
            proc.wait(timeout=5)

            # The next prompt_once must either rebuild (recoverable)
            # or raise TRANSPORT_DISCONNECT + mark fatal (signal
            # path) — never reuse the dead client silently.
            try:
                result = mgr.prompt_once("srv", "hello", timeout=10)
                assert result.stop_reason == "end_turn"
                assert mgr.is_fatal("srv") is False
            except AcpClientError as exc:
                assert exc.code is AcpErrorCode.TRANSPORT_DISCONNECT
                assert mgr.is_fatal("srv") is True
        finally:
            mgr.stop_all()

    def test_process_death_surfaces_last_error_and_rebuilds(
        self, tmp_path: Path,
    ) -> None:
        """Kill the subprocess while idle, then submit a new turn.
        The classifier must say ``recoverable`` (exit code 0), the
        manager must rebuild a fresh client, and the new turn must
        succeed. ``last_error`` is the prior error (from the detected
        transport loss at rebuild time, if any); it may be None if the
        death was clean."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", interactive=False, timeout=5)
            client = mgr._clients["srv"]

            # Kill the subprocess underfoot while idle.
            handle = mgr.get_handle("srv")
            proc = handle._proc
            assert proc is not None
            proc.terminate()
            proc.wait(timeout=5)

            # Next turn: ensure_connected must classify this as
            # recoverable (negative signal-terminated exit may or may
            # not apply depending on platform; tolerate either).
            # Success case = fatal OR a successful rebuild.
            try:
                mgr.send_prompt(
                    "srv", "hello", interactive=False, timeout=10,
                )
                assert mgr.restart_count("srv") == 0
                assert mgr.is_fatal("srv") is False
            except AcpClientError as exc:
                # Signal-terminated path: classifier returns fatal.
                assert exc.code is AcpErrorCode.TRANSPORT_DISCONNECT
                assert mgr.is_fatal("srv") is True
        finally:
            mgr.stop_all()
