"""Tests for the ACP session registry and per-session lifecycle (Issue 03).

These drive :class:`AcpSessionManager` and :class:`AcpSessionState`
directly with a fake ``Agentao``-shaped stub so we don't need real API
credentials or an LLM client. The one integration test at the bottom
verifies the ``AcpServer.run()`` finally-block shutdown hook.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest

from agentao.acp.models import AcpSessionState
from agentao.acp.server import AcpServer
from agentao.acp.session_manager import (
    AcpSessionManager,
    DuplicateSessionError,
    SessionNotFoundError,
)
from agentao.cancellation import CancellationToken


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeAgent:
    """Stand-in for ``Agentao`` — just records that ``close`` was called.

    Real ``Agentao`` construction requires provider credentials and pulls
    the full LLM/tool stack. The registry only touches ``.close()`` on
    the runtime, so a one-method double is enough.
    """

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class ExplodingAgent(FakeAgent):
    """Fake agent whose ``close`` raises, to test shutdown robustness."""

    def close(self) -> None:
        self.close_calls += 1
        raise RuntimeError("simulated MCP disconnect failure")


def _state(
    session_id: str = "s1",
    agent: FakeAgent | None = None,
    cwd: Path | None = None,
    cancel_token: CancellationToken | None = None,
    client_capabilities: dict | None = None,
) -> AcpSessionState:
    return AcpSessionState(
        session_id=session_id,
        agent=agent if agent is not None else FakeAgent(),
        cwd=cwd or Path("/tmp"),
        client_capabilities=client_capabilities if client_capabilities is not None else {},
        cancel_token=cancel_token,
    )


# ---------------------------------------------------------------------------
# AcpSessionState.close
# ---------------------------------------------------------------------------

def test_close_disconnects_agent_and_cancels_token():
    agent = FakeAgent()
    token = CancellationToken()
    state = _state(agent=agent, cancel_token=token)

    state.close()

    assert state.closed is True
    assert agent.close_calls == 1
    assert token.is_cancelled is True
    assert token.reason == "session-closed"


def test_close_is_idempotent():
    agent = FakeAgent()
    state = _state(agent=agent)

    state.close()
    state.close()
    state.close()

    # Only the first call reaches the inner work; subsequent calls short-
    # circuit on the ``closed`` flag.
    assert agent.close_calls == 1


def test_close_with_no_agent_or_token_is_noop():
    state = AcpSessionState(session_id="empty")

    state.close()  # must not raise

    assert state.closed is True


def test_close_swallows_agent_close_exceptions():
    """A broken ``agent.close()`` must not prevent the state from closing —
    one bad session cannot poison shutdown for its siblings."""
    agent = ExplodingAgent()
    state = _state(agent=agent)

    state.close()  # must not raise

    assert state.closed is True
    assert agent.close_calls == 1


# ---------------------------------------------------------------------------
# AcpSessionManager — happy path
# ---------------------------------------------------------------------------

def test_create_and_get():
    manager = AcpSessionManager()
    state = _state("abc")

    manager.create(state)

    assert manager.get("abc") is state
    assert "abc" in manager
    assert len(manager) == 1


def test_get_missing_returns_none():
    manager = AcpSessionManager()
    assert manager.get("nope") is None
    assert "nope" not in manager


def test_require_returns_state_when_present():
    manager = AcpSessionManager()
    state = _state("abc")
    manager.create(state)

    assert manager.require("abc") is state


def test_require_raises_session_not_found():
    manager = AcpSessionManager()

    with pytest.raises(SessionNotFoundError) as exc_info:
        manager.require("ghost")

    assert exc_info.value.session_id == "ghost"
    assert "ghost" in str(exc_info.value)


def test_delete_removes_and_closes_session():
    manager = AcpSessionManager()
    agent = FakeAgent()
    manager.create(_state("abc", agent=agent))

    manager.delete("abc")

    assert "abc" not in manager
    assert len(manager) == 0
    assert agent.close_calls == 1


def test_delete_missing_raises_session_not_found():
    manager = AcpSessionManager()

    with pytest.raises(SessionNotFoundError):
        manager.delete("ghost")


# ---------------------------------------------------------------------------
# Duplicate protection
# ---------------------------------------------------------------------------

def test_create_duplicate_raises():
    manager = AcpSessionManager()
    manager.create(_state("abc"))

    with pytest.raises(DuplicateSessionError) as exc_info:
        manager.create(_state("abc"))

    assert exc_info.value.session_id == "abc"
    # Original session still present and untouched.
    assert len(manager) == 1


def test_create_duplicate_does_not_replace_existing():
    """After a DuplicateSessionError the original state must still be the
    one returned by ``get`` — silently replacing would be a stealth bug."""
    manager = AcpSessionManager()
    original = _state("abc")
    manager.create(original)

    with pytest.raises(DuplicateSessionError):
        manager.create(_state("abc"))

    assert manager.get("abc") is original


# ---------------------------------------------------------------------------
# Multi-session coexistence
# ---------------------------------------------------------------------------

def test_multiple_sessions_coexist_independently():
    manager = AcpSessionManager()
    manager.create(_state("alpha"))
    manager.create(_state("beta"))
    manager.create(_state("gamma"))

    assert len(manager) == 3
    assert set(manager.session_ids()) == {"alpha", "beta", "gamma"}
    assert manager.get("alpha").session_id == "alpha"
    assert manager.get("beta").session_id == "beta"
    assert manager.get("gamma").session_id == "gamma"


def test_delete_one_leaves_others_untouched():
    manager = AcpSessionManager()
    agent_a = FakeAgent()
    agent_b = FakeAgent()
    manager.create(_state("alpha", agent=agent_a))
    manager.create(_state("beta", agent=agent_b))

    manager.delete("alpha")

    assert "alpha" not in manager
    assert "beta" in manager
    assert agent_a.close_calls == 1
    assert agent_b.close_calls == 0


# ---------------------------------------------------------------------------
# close_all — shutdown semantics
# ---------------------------------------------------------------------------

def test_close_all_closes_every_session_and_clears_registry():
    manager = AcpSessionManager()
    agents = [FakeAgent() for _ in range(3)]
    for i, agent in enumerate(agents):
        manager.create(_state(f"s{i}", agent=agent))

    manager.close_all()

    assert len(manager) == 0
    for agent in agents:
        assert agent.close_calls == 1


def test_close_all_is_idempotent():
    manager = AcpSessionManager()
    agent = FakeAgent()
    manager.create(_state("abc", agent=agent))

    manager.close_all()
    manager.close_all()
    manager.close_all()

    assert agent.close_calls == 1  # state.close is itself idempotent
    assert len(manager) == 0


def test_close_all_continues_through_failing_sessions():
    """One session's broken ``close`` must not prevent siblings from
    tearing down. This is the whole point of try/except in close_all."""
    manager = AcpSessionManager()
    good_a = FakeAgent()
    bad = ExplodingAgent()
    good_b = FakeAgent()
    manager.create(_state("good-a", agent=good_a))
    manager.create(_state("bad", agent=bad))
    manager.create(_state("good-b", agent=good_b))

    manager.close_all()  # must not raise

    assert good_a.close_calls == 1
    assert bad.close_calls == 1
    assert good_b.close_calls == 1
    assert len(manager) == 0


def test_close_all_on_empty_registry_is_noop():
    manager = AcpSessionManager()
    manager.close_all()  # must not raise
    assert len(manager) == 0


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------

def test_concurrent_create_and_get_do_not_crash():
    """Not a formal race test — just exercises the lock path under load.
    If the registry were unsynchronized, dict mutations during iteration
    would flag here on PyPy or under heavy contention."""
    manager = AcpSessionManager()
    errors: list[Exception] = []

    def create_many(prefix: str) -> None:
        try:
            for i in range(200):
                manager.create(_state(f"{prefix}-{i}"))
        except Exception as e:
            errors.append(e)

    def read_many() -> None:
        try:
            for _ in range(500):
                _ = manager.session_ids()
                _ = len(manager)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=create_many, args=("A",)),
        threading.Thread(target=create_many, args=("B",)),
        threading.Thread(target=read_many),
        threading.Thread(target=read_many),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(manager) == 400


# ---------------------------------------------------------------------------
# AcpServer integration — shutdown hook fires on EOF
# ---------------------------------------------------------------------------

def test_server_run_closes_all_sessions_on_eof():
    """AcpServer.run() must call sessions.close_all() in its finally block
    so stdio EOF tears down every session-owned Agentao instance."""
    stdin = io.StringIO("")  # empty → immediate EOF
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    agent_a = FakeAgent()
    agent_b = FakeAgent()
    server.sessions.create(_state("a", agent=agent_a))
    server.sessions.create(_state("b", agent=agent_b))

    server.run()

    assert agent_a.close_calls == 1
    assert agent_b.close_calls == 1
    assert len(server.sessions) == 0


def test_server_run_closes_all_sessions_even_if_read_loop_raises():
    """The finally clause runs on exception paths too. Simulate a
    read-side exception and confirm cleanup still happens."""

    class ExplodingStdin:
        def readline(self) -> str:
            raise RuntimeError("simulated stdin failure")

    stdout = io.StringIO()
    server = AcpServer(stdin=ExplodingStdin(), stdout=stdout)

    agent = FakeAgent()
    server.sessions.create(_state("a", agent=agent))

    # AcpServer catches the readline exception internally and returns;
    # the finally block must still fire close_all.
    server.run()

    assert agent.close_calls == 1
    assert len(server.sessions) == 0


def test_server_run_shutdown_hook_does_not_raise_on_session_close_failure():
    """A broken session close during shutdown must not surface as an
    exception from ``run()`` — close_all swallows per-session errors."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    server.sessions.create(_state("bad", agent=ExplodingAgent()))

    server.run()  # must not raise

    assert len(server.sessions) == 0
