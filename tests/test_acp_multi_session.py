"""Multi-session regression tests for the ACP server (Issue 13).

Issues 03–12 each verified that *one* session does the right thing in
isolation, but multi-session behavior — the property that two sessions
on the same server are independent state machines — was only implicitly
covered. This file pins down the cross-session invariants explicitly so
a future refactor that accidentally couples sessions (e.g. caching
permission grants on the server instead of the session, or letting the
turn lock leak across ids) breaks loudly.

The acceptance criterion from Issue 13 is *"Multi-session and
load/cancel behavior are covered"*. Concretely, this file groups its
tests around six invariants:

1. **Registry**: two sessions get unique ids, both retrievable via the
   manager, and ``len(manager) == 2``.
2. **Working directory & client capabilities**: each session sees its
   own ``cwd`` and a *snapshot* of capabilities, not a shared reference.
3. **Per-turn lock**: locking session A's ``turn_lock`` does **not**
   block ``session/prompt`` on session B, and the executor lets two
   prompts on different sessions run concurrently (the property Issue
   08's ThreadPoolExecutor exists to provide).
4. **Cancellation**: ``session/cancel`` for session A fires *only*
   session A's token; session B's mid-flight turn keeps running.
5. **Permission overrides**: an ``allow_always`` answer for session A
   does **not** short-circuit ``confirm_tool`` on session B — the
   override lives on :class:`AcpSessionState`, not on the transport or
   the server.
6. **Lifecycle**: closing one session leaves the others functional, and
   ``session/load`` can co-exist with sessions created by ``session/new``.

Plus a single end-to-end stdio subprocess test that brings up a real
``python -m agentao --acp --stdio`` process, opens two sessions in one
``initialize``-d connection, and asserts that both responses come back
with distinct ids.

All tests use the same ``FakeAgent`` pattern as the per-issue tests so
no LLM credentials are required.
"""

from __future__ import annotations

import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_cancel as acp_session_cancel
from agentao.acp import session_load as acp_session_load
from agentao.acp import session_new as acp_session_new
from agentao.acp import session_prompt as acp_session_prompt
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    METHOD_INITIALIZE,
    METHOD_SESSION_CANCEL,
    METHOD_SESSION_NEW,
    METHOD_SESSION_PROMPT,
)
from agentao.acp.server import AcpServer, _PendingRequest
from agentao.acp.transport import (
    ACPTransport,
    PERMISSION_ALLOW_ALWAYS,
    PERMISSION_REJECT_ALWAYS,
)
from agentao.cancellation import CancellationToken
from agentao.session import save_session


# ===========================================================================
# Test doubles
# ===========================================================================


class FakeAgent:
    """Minimal Agentao replacement.

    Records ``chat`` calls and exposes a ``messages`` list so tests can
    assert that conversation state stays per-session. ``side_effect``
    runs *inside* ``chat`` before returning, used to fire cancel tokens
    or coordinate barriers in concurrency tests.
    """

    def __init__(
        self,
        reply: str = "ok",
        *,
        side_effect: Optional[Callable[[CancellationToken], None]] = None,
    ) -> None:
        self.reply = reply
        self.side_effect = side_effect
        self.messages: List[Dict[str, Any]] = []
        self.chat_calls: List[Tuple[str, CancellationToken]] = []
        self.close_calls = 0

    def chat(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> str:
        assert cancellation_token is not None, "handler must always pass a token"
        self.chat_calls.append((user_message, cancellation_token))
        # Mirror real Agentao: append the user message to the running history
        # so the multi-session message-isolation test can observe it.
        self.messages.append({"role": "user", "content": user_message})
        if self.side_effect is not None:
            self.side_effect(cancellation_token)
        # If the side effect cancelled, return a sentinel like the real
        # agent would; the handler reads ``token.is_cancelled`` for the
        # stopReason, so the literal value here is just for completeness.
        if cancellation_token.is_cancelled:
            return "[Cancelled: acp]"
        self.messages.append({"role": "assistant", "content": self.reply})
        return self.reply

    def close(self) -> None:
        self.close_calls += 1


class StallingFakeAgent:
    """Fake agent whose ``chat`` blocks until either the token is
    cancelled or a release event fires.

    Used by the cancellation-isolation and concurrency tests so a turn
    is observably *in flight* while another session does work.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.observed_cancellation = False
        self.chat_calls = 0
        self.close_calls = 0

    def chat(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> str:
        self.chat_calls += 1
        assert cancellation_token is not None
        self.entered.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if cancellation_token.is_cancelled:
                self.observed_cancellation = True
                return "[Cancelled: acp]"
            if self.release.is_set():
                return "released"
            time.sleep(0.005)
        return "timeout"

    def close(self) -> None:
        self.close_calls += 1


def make_factory(agent: Any) -> Callable[..., Any]:
    """Return an agent factory that always yields the given fake."""
    def factory(**kwargs: Any) -> Any:
        return agent
    return factory


def make_round_robin_factory(agents: List[Any]) -> Callable[..., Any]:
    """Return a factory that yields each agent in ``agents`` in turn.

    Used by tests that build N sessions in one ``initialized_server``
    so each session gets its own fake without monkeypatching.
    """
    iterator = iter(agents)

    def factory(**kwargs: Any) -> Any:
        try:
            return next(iterator)
        except StopIteration as e:
            raise AssertionError("agent factory exhausted") from e

    return factory


# ===========================================================================
# I/O test doubles (shared with the prompt/cancel/load test files)
# ===========================================================================


class BlockingStdin:
    """Queue-backed stdin so a driver thread can control EOF timing."""

    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._closed = False

    def push_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        self._q.put(line)

    def push_eof(self) -> None:
        self._q.put(None)

    def readline(self) -> str:
        if self._closed:
            return ""
        item = self._q.get()
        if item is None:
            self._closed = True
            return ""
        return item


class CapturingStdout:
    """Stdout double that lets a driver thread poll for completed lines."""

    def __init__(self) -> None:
        self._buf = io.StringIO()
        self._lock = threading.Lock()

    def write(self, data: str) -> int:
        with self._lock:
            return self._buf.write(data)

    def flush(self) -> None:
        with self._lock:
            self._buf.flush()

    def getvalue(self) -> str:
        with self._lock:
            return self._buf.getvalue()


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def server():
    return AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())


@pytest.fixture
def initialized_server(server):
    acp_initialize.handle_initialize(
        server,
        {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {
                "fs": {"readTextFile": True, "writeTextFile": True},
                "terminal": True,
            },
            "clientInfo": {"name": "multi-session-test", "version": "0.0.1"},
        },
    )
    return server


def _new_session(
    server: AcpServer,
    cwd: Path,
    agent: Any,
) -> str:
    """Create one session via the real ``session/new`` handler.

    Returns the new session id. Sessions created via this helper are
    bound to ``agent`` and ``cwd`` independently of any other call —
    nothing about this function shares state across invocations.
    """
    result = acp_session_new.handle_session_new(
        server,
        {"cwd": str(cwd), "mcpServers": []},
        agent_factory=make_factory(agent),
    )
    return result["sessionId"]


@pytest.fixture
def two_sessions(initialized_server, tmp_path):
    """Create two ACP sessions on the same server with two distinct fakes.

    Each session has its own ``cwd`` (under ``tmp_path``) so the test
    can verify cwd isolation as well. Returns
    ``(server, (sid_a, agent_a), (sid_b, agent_b))``.
    """
    cwd_a = tmp_path / "alpha"
    cwd_b = tmp_path / "beta"
    cwd_a.mkdir()
    cwd_b.mkdir()

    agent_a = FakeAgent(reply="alpha-reply")
    agent_b = FakeAgent(reply="beta-reply")

    sid_a = _new_session(initialized_server, cwd_a, agent_a)
    sid_b = _new_session(initialized_server, cwd_b, agent_b)

    return initialized_server, (sid_a, agent_a, cwd_a), (sid_b, agent_b, cwd_b)


def _prompt_params(sid: str, text: str) -> dict:
    return {
        "sessionId": sid,
        "prompt": [{"type": "text", "text": text}],
    }


# ===========================================================================
# Part 1 — Registry isolation
# ===========================================================================


class TestRegistryIsolation:
    def test_two_sessions_get_unique_ids(self, two_sessions):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        assert sid_a != sid_b
        assert sid_a.startswith("sess_")
        assert sid_b.startswith("sess_")

    def test_both_sessions_registered_in_manager(self, two_sessions):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        assert sid_a in server.sessions
        assert sid_b in server.sessions
        assert len(server.sessions) == 2

    def test_session_ids_snapshot_returns_both(self, two_sessions):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        ids = set(server.sessions.session_ids())
        assert ids == {sid_a, sid_b}

    def test_each_session_lookup_returns_distinct_state(self, two_sessions):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        state_a = server.sessions.require(sid_a)
        state_b = server.sessions.require(sid_b)
        assert state_a is not state_b
        assert state_a.session_id == sid_a
        assert state_b.session_id == sid_b

    def test_each_session_has_its_own_turn_lock_instance(self, two_sessions):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        state_a = server.sessions.require(sid_a)
        state_b = server.sessions.require(sid_b)
        assert state_a.turn_lock is not state_b.turn_lock

    def test_each_session_has_its_own_permission_overrides_dict(
        self, two_sessions
    ):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        state_a = server.sessions.require(sid_a)
        state_b = server.sessions.require(sid_b)
        assert state_a.permission_overrides is not state_b.permission_overrides

    def test_ten_concurrent_sessions_all_unique(self, initialized_server, tmp_path):
        """Pile in ten sessions and confirm none collide or share state."""
        ids: List[str] = []
        for i in range(10):
            cwd = tmp_path / f"s{i}"
            cwd.mkdir()
            sid = _new_session(initialized_server, cwd, FakeAgent())
            ids.append(sid)

        assert len(set(ids)) == 10
        assert len(initialized_server.sessions) == 10
        # Lookups all succeed with distinct cwds.
        for i, sid in enumerate(ids):
            state = initialized_server.sessions.require(sid)
            assert state.cwd == tmp_path / f"s{i}"


# ===========================================================================
# Part 2 — Working directory and capability snapshot isolation
# ===========================================================================


class TestCwdAndCapabilitiesIsolation:
    def test_each_session_records_its_own_cwd(self, two_sessions):
        server, (sid_a, _, cwd_a), (sid_b, _, cwd_b) = two_sessions
        state_a = server.sessions.require(sid_a)
        state_b = server.sessions.require(sid_b)
        assert state_a.cwd == cwd_a
        assert state_b.cwd == cwd_b
        assert state_a.cwd != state_b.cwd

    def test_each_session_has_its_own_capabilities_snapshot(self, two_sessions):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        state_a = server.sessions.require(sid_a)
        state_b = server.sessions.require(sid_b)
        # Snapshots are equal (same handshake) but not the same object,
        # so a mutation on one cannot leak to the other.
        assert state_a.client_capabilities == state_b.client_capabilities
        assert state_a.client_capabilities is not state_b.client_capabilities

    def test_mutating_one_capabilities_does_not_affect_the_other(
        self, two_sessions
    ):
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        state_a = server.sessions.require(sid_a)
        state_b = server.sessions.require(sid_b)
        state_a.client_capabilities["terminal"] = False
        state_a.client_capabilities["custom"] = "alpha-only"
        # B is unchanged.
        assert state_b.client_capabilities["terminal"] is True
        assert "custom" not in state_b.client_capabilities


# ===========================================================================
# Part 3 — Per-turn lock and concurrent prompt isolation
# ===========================================================================


class TestTurnLockIsolation:
    def test_locking_one_session_does_not_block_prompt_on_another(
        self, two_sessions
    ):
        """The turn_lock is per-session. Hold A's lock and verify B's
        prompt still runs to completion synchronously."""
        server, (sid_a, _, _), (sid_b, agent_b, _) = two_sessions
        state_a = server.sessions.require(sid_a)
        # Pretend a turn is mid-flight on A by grabbing its lock.
        assert state_a.turn_lock.acquire(blocking=False)
        try:
            result = acp_session_prompt.handle_session_prompt(
                server, _prompt_params(sid_b, "for B only")
            )
            assert result == {"stopReason": "end_turn"}
            assert agent_b.chat_calls[0][0] == "for B only"
        finally:
            state_a.turn_lock.release()

    def test_concurrent_prompts_on_different_sessions_run_in_parallel(
        self, initialized_server, tmp_path
    ):
        """Issue 08's ThreadPoolExecutor lets two prompts on different
        sessions execute concurrently. Build two stalling agents that
        block until both have entered ``chat``, then release them. If
        the executor were single-threaded the second ``chat`` would
        never enter and the test would time out."""
        cwd_a = tmp_path / "p1"
        cwd_b = tmp_path / "p2"
        cwd_a.mkdir()
        cwd_b.mkdir()

        agent_a = StallingFakeAgent()
        agent_b = StallingFakeAgent()
        sid_a = _new_session(initialized_server, cwd_a, agent_a)
        sid_b = _new_session(initialized_server, cwd_b, agent_b)
        acp_session_prompt.register(initialized_server)

        line_a = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_PROMPT,
                "params": _prompt_params(sid_a, "alpha"),
            }
        )
        line_b = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": METHOD_SESSION_PROMPT,
                "params": _prompt_params(sid_b, "beta"),
            }
        )

        stdin = BlockingStdin()
        stdout = CapturingStdout()
        initialized_server._in = stdin  # type: ignore[assignment]
        initialized_server._out = stdout  # type: ignore[assignment]

        def driver() -> None:
            stdin.push_line(line_a)
            stdin.push_line(line_b)
            # Wait for *both* agents to actually be inside chat — that's
            # the proof of concurrent dispatch.
            assert agent_a.entered.wait(5.0), "agent A never entered chat"
            assert agent_b.entered.wait(5.0), "agent B never entered chat"
            # Release both and let the workers finish.
            agent_a.release.set()
            agent_b.release.set()
            # Brief grace so workers complete the write before EOF.
            time.sleep(0.1)
            stdin.push_eof()

        t = threading.Thread(target=driver, daemon=True)
        t.start()
        try:
            initialized_server.run()
        finally:
            t.join(timeout=5.0)

        # Both prompts entered chat (the *concurrent* assertion above).
        assert agent_a.chat_calls == 1
        assert agent_b.chat_calls == 1

        # Both responses landed on stdout, with their original ids.
        lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]
        ids = {p["id"] for p in parsed if "id" in p}
        assert {1, 2} <= ids


# ===========================================================================
# Part 4 — Cancellation isolation
# ===========================================================================


class TestCancellationIsolation:
    def test_cancel_on_one_session_does_not_touch_other_sessions_token(
        self, two_sessions
    ):
        """Bind tokens to both sessions, cancel A, verify B's token is
        untouched. Pure unit-level — no chat() at all."""
        server, (sid_a, _, _), (sid_b, _, _) = two_sessions
        state_a = server.sessions.require(sid_a)
        state_b = server.sessions.require(sid_b)
        token_a = CancellationToken()
        token_b = CancellationToken()
        state_a.cancel_token = token_a
        state_b.cancel_token = token_b

        acp_session_cancel.handle_session_cancel(
            server, {"sessionId": sid_a}
        )

        assert token_a.is_cancelled is True
        assert token_b.is_cancelled is False

    def test_cancel_in_flight_only_affects_addressed_session(
        self, initialized_server, tmp_path
    ):
        """End-to-end with two stalling agents: cancel A while both are
        in flight, then release B. A's prompt returns ``cancelled``, B's
        returns ``end_turn`` with the released reply."""
        cwd_a = tmp_path / "x1"
        cwd_b = tmp_path / "x2"
        cwd_a.mkdir()
        cwd_b.mkdir()

        agent_a = StallingFakeAgent()
        agent_b = StallingFakeAgent()
        sid_a = _new_session(initialized_server, cwd_a, agent_a)
        sid_b = _new_session(initialized_server, cwd_b, agent_b)
        acp_session_prompt.register(initialized_server)
        acp_session_cancel.register(initialized_server)

        prompt_a = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_PROMPT,
                "params": _prompt_params(sid_a, "stall A"),
            }
        )
        prompt_b = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": METHOD_SESSION_PROMPT,
                "params": _prompt_params(sid_b, "stall B"),
            }
        )
        cancel_a = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": METHOD_SESSION_CANCEL,
                "params": {"sessionId": sid_a},
            }
        )

        stdin = BlockingStdin()
        stdout = CapturingStdout()
        initialized_server._in = stdin  # type: ignore[assignment]
        initialized_server._out = stdout  # type: ignore[assignment]

        def driver() -> None:
            stdin.push_line(prompt_a)
            stdin.push_line(prompt_b)
            assert agent_a.entered.wait(5.0)
            assert agent_b.entered.wait(5.0)
            # Cancel only A. B keeps polling.
            stdin.push_line(cancel_a)
            # Give A's worker a moment to observe the cancel.
            time.sleep(0.1)
            assert agent_a.observed_cancellation is True
            assert agent_b.observed_cancellation is False
            # Now let B finish gracefully.
            agent_b.release.set()
            time.sleep(0.1)
            stdin.push_eof()

        t = threading.Thread(target=driver, daemon=True)
        t.start()
        try:
            initialized_server.run()
        finally:
            t.join(timeout=5.0)

        # B was untouched: never observed cancel, ran to completion.
        assert agent_a.observed_cancellation is True
        assert agent_b.observed_cancellation is False

        # Stop reasons reflect the per-session outcome.
        lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]
        resp_a = next(p for p in parsed if p.get("id") == 1)
        resp_b = next(p for p in parsed if p.get("id") == 2)
        assert resp_a["result"] == {"stopReason": "cancelled"}
        assert resp_b["result"] == {"stopReason": "end_turn"}


# ===========================================================================
# Part 5 — Permission override isolation
# ===========================================================================


class _PermissionGrantingServer:
    """Stand-in for AcpServer that always responds with the same
    permission outcome on ``call``.

    Used to drive ``ACPTransport.confirm_tool`` without spinning up the
    full read-loop, while still exercising the per-session
    ``permission_overrides`` write path. The ``sessions`` registry is
    a real :class:`AcpSessionManager` so the transport's session lookup
    behaves identically to production.
    """

    def __init__(self, option_id: str) -> None:
        from agentao.acp.session_manager import AcpSessionManager
        self.sessions = AcpSessionManager()
        self.option_id = option_id
        self.calls: List[Tuple[str, dict]] = []

    def call(self, method: str, params: dict) -> _PendingRequest:
        self.calls.append((method, params))
        pending = _PendingRequest(f"srv_{len(self.calls)}")
        pending.result = {
            "outcome": {"outcome": "selected", "optionId": self.option_id}
        }
        pending.event.set()
        return pending


class TestPermissionOverrideIsolation:
    def test_allow_always_on_session_a_does_not_grant_session_b(self):
        """Two transports bound to the same server but different
        sessions. After A answers ``allow_always`` for ``bash``, B's
        next ``confirm_tool('bash', ...)`` must still hit the wire."""
        srv = _PermissionGrantingServer(option_id=PERMISSION_ALLOW_ALWAYS)
        state_a = AcpSessionState(session_id="sess_a")
        state_b = AcpSessionState(session_id="sess_b")
        srv.sessions.create(state_a)
        srv.sessions.create(state_b)

        transport_a = ACPTransport(server=srv, session_id="sess_a")
        transport_b = ACPTransport(server=srv, session_id="sess_b")

        # A says allow_always for bash → recorded on A only.
        assert transport_a.confirm_tool("bash", "", {}) is True
        assert state_a.permission_overrides == {"bash": True}
        assert state_b.permission_overrides == {}

        # B's first confirm for bash MUST hit the wire — it has no
        # remembered grant.
        before = len(srv.calls)
        assert transport_b.confirm_tool("bash", "", {}) is True
        after = len(srv.calls)
        assert after == before + 1, "session B's confirm short-circuited unexpectedly"
        assert state_b.permission_overrides == {"bash": True}

    def test_reject_always_on_session_a_does_not_block_session_b(self):
        srv = _PermissionGrantingServer(option_id=PERMISSION_REJECT_ALWAYS)
        state_a = AcpSessionState(session_id="sess_a")
        state_b = AcpSessionState(session_id="sess_b")
        srv.sessions.create(state_a)
        srv.sessions.create(state_b)

        transport_a = ACPTransport(server=srv, session_id="sess_a")
        transport_b = ACPTransport(server=srv, session_id="sess_b")

        assert transport_a.confirm_tool("bash", "", {}) is False
        assert state_a.permission_overrides == {"bash": False}
        # B is unaffected — its overrides dict is still empty.
        assert state_b.permission_overrides == {}

        # B independently asks and is also rejected (because the canned
        # response is reject_always), but the *path* is independent —
        # the transport made a fresh call, not a short-circuit.
        before = len(srv.calls)
        assert transport_b.confirm_tool("bash", "", {}) is False
        assert len(srv.calls) == before + 1
        assert state_b.permission_overrides == {"bash": False}

    def test_two_sessions_can_make_opposite_decisions_for_same_tool(self):
        """Build two servers (one allow_always, one reject_always) and
        run two sessions through them. Verify the per-session dicts hold
        opposite values for the same tool name. This is the strongest
        form of "no shared state" — the same key resolves to different
        booleans depending on which session you ask."""
        # We can't use one server because the canned outcome is fixed,
        # so use two RecordingServers and assert per-session storage.
        srv_allow = _PermissionGrantingServer(option_id=PERMISSION_ALLOW_ALWAYS)
        srv_reject = _PermissionGrantingServer(option_id=PERMISSION_REJECT_ALWAYS)

        state_allow = AcpSessionState(session_id="sess_allow")
        state_reject = AcpSessionState(session_id="sess_reject")
        srv_allow.sessions.create(state_allow)
        srv_reject.sessions.create(state_reject)

        ACPTransport(server=srv_allow, session_id="sess_allow").confirm_tool(
            "bash", "", {}
        )
        ACPTransport(server=srv_reject, session_id="sess_reject").confirm_tool(
            "bash", "", {}
        )

        assert state_allow.permission_overrides == {"bash": True}
        assert state_reject.permission_overrides == {"bash": False}


# ===========================================================================
# Part 6 — Per-session message history isolation
# ===========================================================================


class TestMessageHistoryIsolation:
    def test_chat_on_one_session_does_not_touch_others_messages(
        self, two_sessions
    ):
        """A turn on session A appends to agent A's history only —
        agent B's ``messages`` list stays untouched. This is the
        property that justifies binding one ``Agentao`` runtime per
        session in :func:`session_new.handle_session_new`."""
        server, (sid_a, agent_a, _), (sid_b, agent_b, _) = two_sessions

        acp_session_prompt.handle_session_prompt(
            server, _prompt_params(sid_a, "msg-only-for-A")
        )

        # A's messages have the user+assistant pair from the chat call.
        assert any(
            m["role"] == "user" and m["content"] == "msg-only-for-A"
            for m in agent_a.messages
        )
        # B never saw it.
        assert agent_b.messages == []
        assert agent_b.chat_calls == []

    def test_interleaved_chats_keep_per_session_histories_distinct(
        self, two_sessions
    ):
        """Alternate prompts between A and B and confirm neither
        history sees the other's user content."""
        server, (sid_a, agent_a, _), (sid_b, agent_b, _) = two_sessions

        acp_session_prompt.handle_session_prompt(
            server, _prompt_params(sid_a, "A1")
        )
        acp_session_prompt.handle_session_prompt(
            server, _prompt_params(sid_b, "B1")
        )
        acp_session_prompt.handle_session_prompt(
            server, _prompt_params(sid_a, "A2")
        )

        # A's user-content list has just A1 and A2.
        a_user = [m["content"] for m in agent_a.messages if m["role"] == "user"]
        b_user = [m["content"] for m in agent_b.messages if m["role"] == "user"]
        assert a_user == ["A1", "A2"]
        assert b_user == ["B1"]


# ===========================================================================
# Part 7 — Lifecycle: closing one session leaves the others alone
# ===========================================================================


class TestSessionLifecycleIsolation:
    def test_deleting_one_session_leaves_other_running(self, two_sessions):
        """Explicit ``manager.delete`` on session A closes A's runtime
        but session B's prompts still work."""
        server, (sid_a, agent_a, _), (sid_b, agent_b, _) = two_sessions

        server.sessions.delete(sid_a)
        assert sid_a not in server.sessions
        assert sid_b in server.sessions
        assert agent_a.close_calls == 1
        assert agent_b.close_calls == 0

        # Session B still answers prompts normally.
        result = acp_session_prompt.handle_session_prompt(
            server, _prompt_params(sid_b, "still alive?")
        )
        assert result == {"stopReason": "end_turn"}

    def test_close_all_tears_down_every_session_exactly_once(
        self, two_sessions
    ):
        server, (sid_a, agent_a, _), (sid_b, agent_b, _) = two_sessions
        server.sessions.close_all()
        # Both runtimes were closed, both have empty registry slots.
        assert len(server.sessions) == 0
        assert agent_a.close_calls == 1
        assert agent_b.close_calls == 1

        # Idempotent: a second close_all is harmless.
        server.sessions.close_all()
        assert agent_a.close_calls == 1
        assert agent_b.close_calls == 1

    def test_close_all_when_one_close_raises_still_closes_others(
        self, initialized_server, tmp_path
    ):
        """A misbehaving session must not block sibling teardown."""
        class ExplodingAgent(FakeAgent):
            def close(self) -> None:
                self.close_calls += 1
                raise RuntimeError("simulated MCP teardown failure")

        cwd_a = tmp_path / "boom"
        cwd_b = tmp_path / "fine"
        cwd_a.mkdir()
        cwd_b.mkdir()

        bad = ExplodingAgent()
        good = FakeAgent()
        _new_session(initialized_server, cwd_a, bad)
        _new_session(initialized_server, cwd_b, good)

        # Should not raise.
        initialized_server.sessions.close_all()
        assert bad.close_calls == 1
        assert good.close_calls == 1
        assert len(initialized_server.sessions) == 0


# ===========================================================================
# Part 8 — session/load coexists with session/new sessions
# ===========================================================================


class TestSessionLoadCoexistence:
    def test_loaded_session_lives_alongside_new_sessions(
        self, initialized_server, tmp_path
    ):
        """Open two sessions via ``session/new``, then ``session/load``
        a third by id. The registry should hold all three independently
        and ``session/prompt`` on each should hit only that session's
        agent."""
        cwd_a = tmp_path / "newA"
        cwd_b = tmp_path / "newB"
        cwd_load = tmp_path / "loaded"
        cwd_a.mkdir()
        cwd_b.mkdir()
        cwd_load.mkdir()

        agent_a = FakeAgent(reply="A")
        agent_b = FakeAgent(reply="B")
        sid_a = _new_session(initialized_server, cwd_a, agent_a)
        sid_b = _new_session(initialized_server, cwd_b, agent_b)

        # Persist a session under cwd_load that we'll load by id.
        loaded_sid = "sess_persisted_xyz"
        save_session(
            messages=[{"role": "user", "content": "previously"}],
            model="test-model",
            active_skills=[],
            session_id=loaded_sid,
            project_root=cwd_load,
        )

        agent_loaded = FakeAgent(reply="L")
        acp_session_load.handle_session_load(
            initialized_server,
            {
                "sessionId": loaded_sid,
                "cwd": str(cwd_load),
                "mcpServers": [],
            },
            agent_factory=make_factory(agent_loaded),
        )

        # All three are alive in the registry.
        assert len(initialized_server.sessions) == 3
        for sid in (sid_a, sid_b, loaded_sid):
            assert sid in initialized_server.sessions

        # The loaded session's agent had its history hydrated.
        assert any(
            m["role"] == "user" and m["content"] == "previously"
            for m in agent_loaded.messages
        )

        # Routing is per-session: prompting the loaded one only fires its agent.
        acp_session_prompt.handle_session_prompt(
            initialized_server, _prompt_params(loaded_sid, "follow-up")
        )
        assert agent_loaded.chat_calls[-1][0] == "follow-up"
        assert agent_a.chat_calls == []
        assert agent_b.chat_calls == []

    def test_load_then_cancel_only_affects_loaded_session(
        self, initialized_server, tmp_path
    ):
        """Cancel-by-id reaches the loaded session and not the others."""
        cwd_new = tmp_path / "n"
        cwd_load = tmp_path / "l"
        cwd_new.mkdir()
        cwd_load.mkdir()

        agent_new = FakeAgent()
        sid_new = _new_session(initialized_server, cwd_new, agent_new)

        loaded_sid = "sess_persisted_for_cancel"
        save_session(
            messages=[],
            model="test-model",
            active_skills=[],
            session_id=loaded_sid,
            project_root=cwd_load,
        )
        agent_loaded = FakeAgent()
        acp_session_load.handle_session_load(
            initialized_server,
            {
                "sessionId": loaded_sid,
                "cwd": str(cwd_load),
                "mcpServers": [],
            },
            agent_factory=make_factory(agent_loaded),
        )

        # Bind tokens to both and cancel only the loaded one.
        state_new = initialized_server.sessions.require(sid_new)
        state_loaded = initialized_server.sessions.require(loaded_sid)
        token_new = CancellationToken()
        token_loaded = CancellationToken()
        state_new.cancel_token = token_new
        state_loaded.cancel_token = token_loaded

        acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": loaded_sid}
        )

        assert token_loaded.is_cancelled is True
        assert token_new.is_cancelled is False


# ===========================================================================
# Part 9 — Stdio subprocess: two sessions in one connection
# ===========================================================================


def _agentao_repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="subprocess stdio framing is platform-specific; covered on Linux/macOS",
)
class TestStdioMultiSessionSubprocess:
    """Spawn a real ``python -m agentao --acp --stdio`` and verify that
    two sessions can coexist over the same connection.

    This is the only test in the file that crosses a process boundary.
    Mirrors the smoke tests in ``test_acp_cli_entrypoint.py`` so the
    failure mode (missing dependency, broken module entry, etc.) is
    diagnosed in only one place.
    """

    def _spawn_acp(self) -> subprocess.Popen:
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        # session/new constructs a real Agentao runtime which checks for
        # credentials during LLMClient init.
        env.setdefault("OPENAI_API_KEY", "test-dummy-key")
        env.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")
        env.setdefault("OPENAI_MODEL", "gpt-5.4")
        return subprocess.Popen(
            [sys.executable, "-m", "agentao", "--acp", "--stdio"],
            cwd=str(_agentao_repo_root()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )

    def test_two_sessions_get_distinct_ids_in_one_connection(self, tmp_path):
        cwd_a = tmp_path / "ssA"
        cwd_b = tmp_path / "ssB"
        cwd_a.mkdir()
        cwd_b.mkdir()

        init = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_INITIALIZE,
                "params": {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {},
                },
            }
        )
        new_a = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": METHOD_SESSION_NEW,
                "params": {"cwd": str(cwd_a), "mcpServers": []},
            }
        )
        new_b = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": METHOD_SESSION_NEW,
                "params": {"cwd": str(cwd_b), "mcpServers": []},
            }
        )

        proc = self._spawn_acp()
        try:
            stdout, stderr = proc.communicate(
                input=init + "\n" + new_a + "\n" + new_b + "\n",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            pytest.fail(
                f"agentao --acp --stdio timed out\n"
                f"stdout={stdout}\nstderr={stderr}"
            )

        # Every line on stdout must be a valid JSON-RPC envelope.
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]
        for msg in parsed:
            assert msg["jsonrpc"] == "2.0"

        # Pluck out the three responses by their original ids.
        init_resp = next((m for m in parsed if m.get("id") == 1), None)
        a_resp = next((m for m in parsed if m.get("id") == 2), None)
        b_resp = next((m for m in parsed if m.get("id") == 3), None)
        assert init_resp is not None and "result" in init_resp
        assert a_resp is not None and "result" in a_resp, (
            f"session/new for cwd_a missing\nstderr={stderr}"
        )
        assert b_resp is not None and "result" in b_resp, (
            f"session/new for cwd_b missing\nstderr={stderr}"
        )

        sid_a = a_resp["result"]["sessionId"]
        sid_b = b_resp["result"]["sessionId"]
        assert sid_a.startswith("sess_")
        assert sid_b.startswith("sess_")
        assert sid_a != sid_b, "two sessions in one connection collided on id"

        # Process exited cleanly because we closed stdin (EOF after our lines).
        assert proc.returncode == 0, (
            f"agentao exited with code {proc.returncode}\nstderr={stderr}"
        )
