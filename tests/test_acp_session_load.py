"""Tests for ACP ``session/load`` (Issue 10).

Three layers:

1. **`ACPTransport.replay_history`** mapping unit tests — every persisted
   role / shape lands as the right ``session/update`` notification.
2. **`session_load.handle_session_load`** handler tests — param
   validation, missing-session error mapping, registry collision
   detection, session-not-found behavior, end-to-end factory wiring.
3. **End-to-end** ``session/load`` then ``session/prompt`` — drive the
   real :class:`AcpServer.run` loop, persist a fixture session via the
   real :func:`agentao.session.save_session`, load it through the wire,
   and confirm a follow-up prompt continues the same conversation.

Test doubles and server builders live in :mod:`tests.support.acp_agents`
and :mod:`tests.support.acp_server`. Note: ``FakeAgent`` here uses the
default ``track_messages=False``, so ``chat`` does not mutate
``messages`` — the load tests assert that ``messages`` equals the
hydrated history after a follow-up prompt.
"""

from __future__ import annotations

import io
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_load as acp_session_load
from agentao.acp import session_new as acp_session_new
from agentao.acp import session_prompt as acp_session_prompt
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_SESSION_LOAD,
    METHOD_SESSION_PROMPT,
    METHOD_SESSION_UPDATE,
    SERVER_NOT_INITIALIZED,
)
from agentao.acp.server import AcpServer, JsonRpcHandlerError
from agentao.acp.transport import (
    ACPTransport,
    _coerce_message_text,
    _strip_system_reminder_blocks,
)
from agentao.cancellation import CancellationToken
from agentao.session import save_session

from .support.acp_agents import FakeAgent, make_factory
from .support.acp_server import make_initialized_server, make_server


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingServer:
    """Stand-in for :class:`AcpServer` that captures notifications.

    Used by the unit tests for ``ACPTransport.replay_history`` so we can
    inspect the produced notifications without spinning up a real server.
    """

    def __init__(self) -> None:
        self.notifications: List[Tuple[str, Dict[str, Any]]] = []

    def write_notification(self, method: str, params: Dict[str, Any]) -> None:
        # Round-trip through JSON to assert payloads are serializable.
        encoded = json.dumps(params, separators=(",", ":"))
        self.notifications.append((method, json.loads(encoded)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server():
    return make_server()


@pytest.fixture
def initialized_server():
    return make_initialized_server()


def _persist_session(
    cwd: Path, session_id: str, messages: List[Dict[str, Any]]
) -> None:
    """Save a session via the real persistence layer so the loader can find it."""
    save_session(
        messages=messages,
        model="test-model",
        active_skills=[],
        session_id=session_id,
        project_root=cwd,
    )


def _load_params(cwd: Path, session_id: str) -> Dict[str, Any]:
    return {
        "sessionId": session_id,
        "cwd": str(cwd),
        "mcpServers": [],
    }


class BlockingStdin:
    """Queue-backed stdin so a test can interleave reads with run()."""

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
    """Stdout double that lets a test poll for completed responses."""

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
# Part 1 — _coerce_message_text + _strip_system_reminder_blocks
# ===========================================================================


class TestHelpers:
    def test_coerce_string_passes_through(self):
        assert _coerce_message_text("hello") == "hello"

    def test_coerce_none_returns_empty(self):
        assert _coerce_message_text(None) == ""

    def test_coerce_list_of_text_parts(self):
        content = [
            {"type": "text", "text": "first "},
            {"type": "text", "text": "second"},
        ]
        assert _coerce_message_text(content) == "first second"

    def test_coerce_list_skips_non_text_parts(self):
        content = [
            {"type": "text", "text": "keep"},
            {"type": "image", "url": "data:..."},  # skipped
            {"type": "text", "text": " me"},
        ]
        assert _coerce_message_text(content) == "keep me"

    def test_coerce_other_falls_back_to_str(self):
        assert _coerce_message_text(42) == "42"

    def test_strip_system_reminder_removes_block(self):
        text = (
            "<system-reminder>\nDate: 2026-04-09\n</system-reminder>\nhello world"
        )
        assert _strip_system_reminder_blocks(text).strip() == "hello world"

    def test_strip_system_reminder_handles_multiple_blocks(self):
        text = (
            "<system-reminder>a</system-reminder>middle"
            "<system-reminder>b</system-reminder>tail"
        )
        assert _strip_system_reminder_blocks(text) == "middletail"

    def test_strip_system_reminder_no_op_on_clean_text(self):
        assert _strip_system_reminder_blocks("plain text") == "plain text"

    def test_strip_system_reminder_handles_empty(self):
        assert _strip_system_reminder_blocks("") == ""


# ===========================================================================
# Part 2 — ACPTransport.replay_history mapping
# ===========================================================================


class TestReplayHistoryMapping:
    def _transport(self) -> Tuple[ACPTransport, RecordingServer]:
        server = RecordingServer()
        return ACPTransport(server=server, session_id="sess_replay"), server

    def _last_update(self, server: RecordingServer) -> Dict[str, Any]:
        assert server.notifications, "expected at least one notification"
        method, params = server.notifications[-1]
        assert method == METHOD_SESSION_UPDATE
        assert params["sessionId"] == "sess_replay"
        return params["update"]

    # --- Skip rules ------------------------------------------------------

    def test_system_messages_are_skipped(self):
        t, server = self._transport()
        emitted = t.replay_history(
            [{"role": "system", "content": "system prompt blob"}]
        )
        assert emitted == 0
        assert server.notifications == []

    def test_unknown_role_is_skipped(self):
        t, server = self._transport()
        emitted = t.replay_history(
            [{"role": "developer", "content": "ignore me"}]
        )
        assert emitted == 0
        assert server.notifications == []

    def test_non_dict_entry_is_skipped(self):
        t, server = self._transport()
        emitted = t.replay_history(["not a dict", 42, None])
        assert emitted == 0
        assert server.notifications == []

    def test_empty_message_list_emits_nothing(self):
        t, server = self._transport()
        assert t.replay_history([]) == 0
        assert server.notifications == []

    # --- User messages ---------------------------------------------------

    def test_user_message_maps_to_user_message_chunk(self):
        t, server = self._transport()
        t.replay_history([{"role": "user", "content": "hello there"}])
        upd = self._last_update(server)
        assert upd == {
            "sessionUpdate": "user_message_chunk",
            "content": {"type": "text", "text": "hello there"},
        }

    def test_user_message_strips_system_reminder_blocks(self):
        t, server = self._transport()
        t.replay_history(
            [
                {
                    "role": "user",
                    "content": "<system-reminder>internal</system-reminder>real text",
                }
            ]
        )
        upd = self._last_update(server)
        assert upd["content"]["text"] == "real text"

    def test_user_message_that_is_only_system_reminder_emits_nothing(self):
        t, server = self._transport()
        emitted = t.replay_history(
            [
                {
                    "role": "user",
                    "content": "<system-reminder>only this</system-reminder>",
                }
            ]
        )
        assert emitted == 0
        assert server.notifications == []

    def test_user_message_with_list_content_flattens_text(self):
        t, server = self._transport()
        t.replay_history(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "part 1 "},
                        {"type": "text", "text": "part 2"},
                    ],
                }
            ]
        )
        upd = self._last_update(server)
        assert upd["content"]["text"] == "part 1 part 2"

    # --- Assistant messages (text only) ----------------------------------

    def test_assistant_text_maps_to_agent_message_chunk(self):
        t, server = self._transport()
        t.replay_history([{"role": "assistant", "content": "the answer"}])
        upd = self._last_update(server)
        assert upd == {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "the answer"},
        }

    def test_assistant_empty_content_no_tool_calls_emits_nothing(self):
        t, server = self._transport()
        emitted = t.replay_history([{"role": "assistant", "content": ""}])
        assert emitted == 0
        assert server.notifications == []

    # --- Assistant messages (tool calls) ---------------------------------

    def test_assistant_with_tool_call_emits_tool_call_completed(self):
        t, server = self._transport()
        t.replay_history(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_xyz",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"file_path": "/tmp/x.md"}',
                            },
                        }
                    ],
                }
            ]
        )
        upd = self._last_update(server)
        assert upd == {
            "sessionUpdate": "tool_call",
            "toolCallId": "call_xyz",
            "title": "read_file",
            "kind": "read",
            "status": "completed",
            "rawInput": {"file_path": "/tmp/x.md"},
        }

    def test_assistant_with_text_and_tool_calls_emits_both(self):
        t, server = self._transport()
        emitted = t.replay_history(
            [
                {
                    "role": "assistant",
                    "content": "let me check",
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "function": {
                                "name": "bash",
                                "arguments": '{"cmd": "ls"}',
                            },
                        }
                    ],
                }
            ]
        )
        assert emitted == 2
        # First: agent_message_chunk; second: tool_call
        first = server.notifications[0][1]["update"]
        second = server.notifications[1][1]["update"]
        assert first["sessionUpdate"] == "agent_message_chunk"
        assert first["content"]["text"] == "let me check"
        assert second["sessionUpdate"] == "tool_call"
        assert second["title"] == "bash"
        assert second["kind"] == "execute"

    def test_tool_call_with_invalid_json_arguments_falls_back(self):
        t, server = self._transport()
        t.replay_history(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_b",
                            "function": {
                                "name": "bash",
                                "arguments": "not json",
                            },
                        }
                    ],
                }
            ]
        )
        upd = self._last_update(server)
        assert upd["rawInput"] == {"_raw": "not json"}

    def test_tool_call_without_id_synthesizes_one(self):
        t, server = self._transport()
        t.replay_history(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                }
            ]
        )
        upd = self._last_update(server)
        assert upd["toolCallId"].startswith("replay_")

    def test_tool_call_unknown_tool_falls_back_to_other_kind(self):
        t, server = self._transport()
        t.replay_history(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "x",
                            "function": {
                                "name": "mcp_github_create_issue",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            ]
        )
        upd = self._last_update(server)
        assert upd["kind"] == "other"

    # --- Tool result messages --------------------------------------------

    def test_tool_message_maps_to_tool_call_update_completed(self):
        t, server = self._transport()
        t.replay_history(
            [
                {
                    "role": "tool",
                    "tool_call_id": "call_xyz",
                    "content": "file contents here",
                }
            ]
        )
        upd = self._last_update(server)
        assert upd == {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call_xyz",
            "status": "completed",
            "content": [
                {"type": "content", "content": {"type": "text", "text": "file contents here"}}
            ],
        }

    def test_tool_message_with_no_tool_call_id_is_skipped(self):
        t, server = self._transport()
        emitted = t.replay_history(
            [{"role": "tool", "content": "orphan"}]
        )
        assert emitted == 0
        assert server.notifications == []

    def test_tool_message_with_empty_content_omits_content_field(self):
        t, server = self._transport()
        t.replay_history(
            [{"role": "tool", "tool_call_id": "c1", "content": ""}]
        )
        upd = self._last_update(server)
        assert "content" not in upd
        assert upd["status"] == "completed"

    # --- Full conversation flow ------------------------------------------

    def test_full_conversation_replay_order_and_count(self):
        t, server = self._transport()
        emitted = t.replay_history(
            [
                {"role": "system", "content": "skipped"},
                {"role": "user", "content": "what time is it?"},
                {
                    "role": "assistant",
                    "content": "checking",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "bash", "arguments": '{"cmd": "date"}'},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "Thu Apr 9 12:34:56 PDT 2026",
                },
                {"role": "assistant", "content": "It is 12:34 PM."},
            ]
        )
        # Expected: user(1) + assistant_text(1) + tool_call(1) + tool_result(1) + assistant_text(1) = 5
        assert emitted == 5
        kinds = [n[1]["update"]["sessionUpdate"] for n in server.notifications]
        assert kinds == [
            "user_message_chunk",
            "agent_message_chunk",
            "tool_call",
            "tool_call_update",
            "agent_message_chunk",
        ]

    def test_replay_never_raises_on_corrupt_entry(self):
        """A single bad entry must not break replay of subsequent ones."""
        t, server = self._transport()
        emitted = t.replay_history(
            [
                {"role": "user", "content": "first"},
                # Tool call with broken type for tool_calls field — handler
                # logs and continues.
                {"role": "assistant", "tool_calls": "not a list"},
                {"role": "user", "content": "third"},
            ]
        )
        assert emitted == 2  # both users emitted; assistant produced 0
        kinds = [n[1]["update"]["sessionUpdate"] for n in server.notifications]
        assert kinds == ["user_message_chunk", "user_message_chunk"]

    def test_replay_history_with_no_server_returns_zero(self):
        t = ACPTransport(server=None, session_id="sess_x")
        assert t.replay_history([{"role": "user", "content": "x"}]) == 0


# ===========================================================================
# Part 3 — handle_session_load: param validation
# ===========================================================================


class TestSessionLoadParamValidation:
    def test_load_before_initialize_raises_server_not_initialized(self, server):
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_session_load.handle_session_load(
                server, {"sessionId": "x", "cwd": "/tmp", "mcpServers": []}
            )
        assert exc.value.code == SERVER_NOT_INITIALIZED

    def test_params_must_be_dict(self, initialized_server):
        with pytest.raises(TypeError, match="JSON object"):
            acp_session_load.handle_session_load(initialized_server, [])

    def test_missing_session_id_raises(self, initialized_server, tmp_path):
        with pytest.raises(TypeError, match="sessionId must be a non-empty string"):
            acp_session_load.handle_session_load(
                initialized_server,
                {"cwd": str(tmp_path), "mcpServers": []},
            )

    def test_empty_session_id_raises(self, initialized_server, tmp_path):
        with pytest.raises(TypeError, match="sessionId must be a non-empty string"):
            acp_session_load.handle_session_load(
                initialized_server,
                {"sessionId": "", "cwd": str(tmp_path), "mcpServers": []},
            )

    def test_missing_cwd_raises(self, initialized_server):
        with pytest.raises(TypeError, match="cwd must be a string"):
            acp_session_load.handle_session_load(
                initialized_server,
                {"sessionId": "x", "mcpServers": []},
            )

    def test_relative_cwd_raises(self, initialized_server):
        with pytest.raises(TypeError, match="absolute path"):
            acp_session_load.handle_session_load(
                initialized_server,
                {"sessionId": "x", "cwd": "relative", "mcpServers": []},
            )

    def test_missing_mcp_servers_raises(self, initialized_server, tmp_path):
        with pytest.raises(TypeError, match="mcpServers must be a JSON array"):
            acp_session_load.handle_session_load(
                initialized_server,
                {"sessionId": "x", "cwd": str(tmp_path)},
            )


# ===========================================================================
# Part 4 — handle_session_load: missing-session error
# ===========================================================================


class TestSessionLoadMissingSession:
    def test_missing_sessions_directory_raises_invalid_request(
        self, initialized_server, tmp_path
    ):
        # Empty tmp_path → no .agentao/sessions directory at all.
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_session_load.handle_session_load(
                initialized_server, _load_params(tmp_path, "any-id")
            )
        assert exc.value.code == INVALID_REQUEST

    def test_missing_specific_session_id_raises_invalid_request(
        self, initialized_server, tmp_path
    ):
        # Persist some other session so the directory exists but our id is absent.
        _persist_session(
            tmp_path, "real-session", [{"role": "user", "content": "hi"}]
        )
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_session_load.handle_session_load(
                initialized_server,
                _load_params(tmp_path, "ghost-session"),
            )
        assert exc.value.code == INVALID_REQUEST
        assert "not found" in exc.value.message.lower()


# ===========================================================================
# Part 5 — handle_session_load: registry collision
# ===========================================================================


class TestSessionLoadRegistryCollision:
    def test_load_for_already_active_session_raises(
        self, initialized_server, tmp_path
    ):
        # Pre-register a session under the id we will try to load.
        state = AcpSessionState(session_id="already-live")
        initialized_server.sessions.create(state)

        # Persist a session on disk so the loader could find data — the
        # collision check should fire BEFORE we touch the disk.
        _persist_session(
            tmp_path, "already-live", [{"role": "user", "content": "hi"}]
        )

        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_session_load.handle_session_load(
                initialized_server,
                _load_params(tmp_path, "already-live"),
                agent_factory=make_factory(FakeAgent()),
            )
        assert exc.value.code == INVALID_REQUEST
        assert "already active" in exc.value.message.lower()


# ===========================================================================
# Part 6 — handle_session_load: happy path with FakeAgent
# ===========================================================================


class TestSessionLoadHappyPath:
    def test_load_persists_messages_into_agent_and_replays(
        self, initialized_server, tmp_path
    ):
        # Save a multi-message session to disk.
        sid = "11111111-1111-1111-1111-111111111111"
        history = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "pong"},
        ]
        _persist_session(tmp_path, sid, history)

        fake = FakeAgent()
        result = acp_session_load.handle_session_load(
            initialized_server,
            _load_params(tmp_path, sid),
            agent_factory=make_factory(fake),
        )

        # Response is empty per spec.
        assert result == {}

        # Agent.messages was hydrated with the loaded history.
        assert fake.messages == history

        # Session is registered.
        state = initialized_server.sessions.require(sid)
        assert state.agent is fake
        assert state.cwd == tmp_path.resolve()

        # Replay landed on stdout via the server's write_notification.
        # We use the recording fixture for the unit-level mapping tests;
        # here we verify only that *some* notifications were written and
        # the registry is consistent.
        out = initialized_server._out.getvalue()
        # Two non-system messages → at least 2 notifications.
        assert out.count(METHOD_SESSION_UPDATE) >= 2

    def test_load_assigns_session_id_to_agent(
        self, initialized_server, tmp_path
    ):
        """Same binding contract as ``session/new``: the agent's
        ``_session_id`` must reflect the persisted ACP session id so
        harness lifecycle events are filterable from the host side."""
        sid = "44444444-4444-4444-4444-444444444444"
        history = [{"role": "user", "content": "hi"}]
        _persist_session(tmp_path, sid, history)

        fake = FakeAgent()
        acp_session_load.handle_session_load(
            initialized_server,
            _load_params(tmp_path, sid),
            agent_factory=make_factory(fake),
        )
        assert fake._session_id == sid

    def test_load_agent_factory_failure_cleans_up(
        self, initialized_server, tmp_path
    ):
        sid = "22222222-2222-2222-2222-222222222222"
        _persist_session(tmp_path, sid, [{"role": "user", "content": "x"}])

        def failing_factory(**kwargs: Any) -> FakeAgent:
            raise RuntimeError("factory boom")

        with pytest.raises(RuntimeError, match="factory boom"):
            acp_session_load.handle_session_load(
                initialized_server,
                _load_params(tmp_path, sid),
                agent_factory=failing_factory,
            )
        # No partial session leaked into the registry.
        assert sid not in initialized_server.sessions

    def test_loaded_session_can_continue_with_new_prompt(
        self, initialized_server, tmp_path
    ):
        """Acceptance criterion #3: loaded session can receive new prompts."""
        sid = "33333333-3333-3333-3333-333333333333"
        history = [
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": "first answer"},
        ]
        _persist_session(tmp_path, sid, history)

        fake = FakeAgent(reply="continued answer")
        acp_session_load.handle_session_load(
            initialized_server,
            _load_params(tmp_path, sid),
            agent_factory=make_factory(fake),
        )

        # Now drive a follow-up prompt — the session must be findable
        # AND the agent must have its history pre-loaded.
        result = acp_session_prompt.handle_session_prompt(
            initialized_server,
            {"sessionId": sid, "prompt": [{"type": "text", "text": "second turn"}]},
        )
        assert result == {"stopReason": "end_turn"}
        assert len(fake.chat_calls) == 1
        # Crucially, the agent already had two pre-loaded messages BEFORE
        # the chat call. session_prompt's chat() pushed a new user message
        # but our FakeAgent doesn't add it; we just verify the hydration.
        assert fake.messages == history


# ===========================================================================
# Part 7 — Registration / dispatcher wire
# ===========================================================================


class TestRegistration:
    def test_register_populates_handler_dict(self, initialized_server):
        acp_session_load.register(
            initialized_server, agent_factory=make_factory(FakeAgent())
        )
        assert METHOD_SESSION_LOAD in initialized_server._handlers

    def test_end_to_end_wire_returns_empty_result(self, initialized_server, tmp_path):
        sid = "44444444-4444-4444-4444-444444444444"
        _persist_session(
            tmp_path,
            sid,
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        )
        fake = FakeAgent()
        acp_session_load.register(
            initialized_server, agent_factory=make_factory(fake)
        )

        request_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": METHOD_SESSION_LOAD,
                "params": _load_params(tmp_path, sid),
            }
        )
        initialized_server._in = io.StringIO(request_line + "\n")
        initialized_server._out = io.StringIO()
        initialized_server.run()

        # Find the load response — it shares stdout with replay notifications.
        lines = [
            ln for ln in initialized_server._out.getvalue().splitlines() if ln.strip()
        ]
        parsed = [json.loads(ln) for ln in lines]
        load_resp = next((p for p in parsed if p.get("id") == 99), None)
        assert load_resp is not None
        assert load_resp["result"] == {}

        # Replay notifications were emitted.
        replay_msgs = [
            p for p in parsed if p.get("method") == METHOD_SESSION_UPDATE
        ]
        # 2 non-system messages → 2 notifications.
        assert len(replay_msgs) == 2
        kinds = [m["params"]["update"]["sessionUpdate"] for m in replay_msgs]
        assert "user_message_chunk" in kinds
        assert "agent_message_chunk" in kinds

    def test_end_to_end_load_then_prompt_continues_session(
        self, initialized_server, tmp_path
    ):
        """Full wire integration: load (over wire) → prompt (over wire).

        Race-safe sequencing: ACP clients are expected to wait for the
        load response before sending the next prompt. We model that
        contract precisely with a :class:`BlockingStdin` and a driver
        thread that polls stdout for the load response, then pushes the
        prompt line, then EOF. This is also what an ACP IDE client does.
        """
        sid = "55555555-5555-5555-5555-555555555555"
        _persist_session(
            tmp_path,
            sid,
            [
                {"role": "user", "content": "earlier"},
                {"role": "assistant", "content": "earlier reply"},
            ],
        )
        fake = FakeAgent(reply="follow-up reply")
        acp_session_load.register(
            initialized_server, agent_factory=make_factory(fake)
        )
        acp_session_prompt.register(initialized_server)

        load_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_LOAD,
                "params": _load_params(tmp_path, sid),
            }
        )
        prompt_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": METHOD_SESSION_PROMPT,
                "params": {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": "next"}],
                },
            }
        )

        stdin = BlockingStdin()
        stdout = CapturingStdout()
        initialized_server._in = stdin  # type: ignore[assignment]
        initialized_server._out = stdout  # type: ignore[assignment]

        def driver() -> None:
            stdin.push_line(load_line)
            # Wait until the load response (id=1) appears in stdout
            # before pushing the prompt — this is the explicit
            # request/await contract that real clients honor.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                for ln in stdout.getvalue().splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        msg = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == 1 and "result" in msg:
                        stdin.push_line(prompt_line)
                        # Now wait for the prompt response too, then EOF.
                        deadline2 = time.monotonic() + 5.0
                        while time.monotonic() < deadline2:
                            for ln2 in stdout.getvalue().splitlines():
                                try:
                                    m2 = json.loads(ln2.strip())
                                except (json.JSONDecodeError, ValueError):
                                    continue
                                if m2.get("id") == 2 and (
                                    "result" in m2 or "error" in m2
                                ):
                                    stdin.push_eof()
                                    return
                            time.sleep(0.01)
                        stdin.push_eof()
                        return
                time.sleep(0.01)
            stdin.push_eof()

        t = threading.Thread(target=driver, daemon=True)
        t.start()
        try:
            initialized_server.run()
        finally:
            t.join(timeout=5.0)

        lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]

        load_resp = next((p for p in parsed if p.get("id") == 1), None)
        prompt_resp = next((p for p in parsed if p.get("id") == 2), None)

        assert load_resp is not None and load_resp["result"] == {}
        assert prompt_resp is not None
        assert prompt_resp["result"]["stopReason"] == "end_turn"

        # Agent received the follow-up call with the right text.
        assert len(fake.chat_calls) == 1
        assert fake.chat_calls[0][0] == "next"


# ===========================================================================
# Part 8 — initialize advertises loadSession capability
# ===========================================================================


def test_initialize_still_advertises_load_session_capability():
    """Issue 02 advertised loadSession=True before the handler existed.
    Issue 10 backs that promise — verify the capability is still True
    so the contract is consistent."""
    from agentao.acp.initialize import AGENT_CAPABILITIES
    assert AGENT_CAPABILITIES.get("loadSession") is True
