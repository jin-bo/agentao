"""Tests for ACPTransport — Issue 07.

Verifies that Agentao runtime :class:`AgentEvent` values are translated
to well-shaped ACP ``session/update`` notifications, that the transport
never raises, that the session id is stamped on every notification, and
that thread-safe writes reach the server's write path.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from agentao.acp.protocol import METHOD_SESSION_UPDATE
from agentao.acp.server import AcpServer
from agentao.acp.transport import ACPTransport, _json_safe, _tool_kind
from agentao.transport.events import AgentEvent, EventType


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class RecordingServer:
    """Stand-in for :class:`AcpServer` that captures notification calls."""

    def __init__(self) -> None:
        self.notifications: List[Tuple[str, Dict[str, Any]]] = []

    def write_notification(self, method: str, params: Dict[str, Any]) -> None:
        # Round-trip through json to guarantee JSON-safety of payloads —
        # if any value is not serializable the test fails loudly.
        encoded = json.dumps(params, separators=(",", ":"))
        self.notifications.append((method, json.loads(encoded)))


@pytest.fixture
def transport():
    server = RecordingServer()
    return ACPTransport(server=server, session_id="sess_test"), server


def _last_update(server: RecordingServer) -> Dict[str, Any]:
    assert server.notifications, "expected at least one notification"
    method, params = server.notifications[-1]
    assert method == METHOD_SESSION_UPDATE
    assert params["sessionId"] == "sess_test"
    return params["update"]


# ---------------------------------------------------------------------------
# Silent events
# ---------------------------------------------------------------------------

def test_turn_start_emits_no_notification(transport):
    t, server = transport
    t.emit(AgentEvent(EventType.TURN_START, {}))
    assert server.notifications == []


def test_tool_confirmation_emits_no_notification(transport):
    """TOOL_CONFIRMATION is Issue 08's concern — transport must drop it."""
    t, server = transport
    t.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {"tool": "bash", "args": {}}))
    assert server.notifications == []


# ---------------------------------------------------------------------------
# LLM_TEXT → agent_message_chunk
# ---------------------------------------------------------------------------

def test_llm_text_maps_to_agent_message_chunk(transport):
    t, server = transport
    t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "Hello"}))
    upd = _last_update(server)
    assert upd == {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Hello"},
    }


def test_llm_text_missing_chunk_emits_empty_string(transport):
    t, server = transport
    t.emit(AgentEvent(EventType.LLM_TEXT, {}))
    upd = _last_update(server)
    assert upd["content"]["text"] == ""


def test_multiple_llm_text_chunks_emit_multiple_notifications(transport):
    t, server = transport
    for chunk in ["Hello", ", ", "world"]:
        t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": chunk}))
    assert len(server.notifications) == 3
    texts = [n[1]["update"]["content"]["text"] for n in server.notifications]
    assert texts == ["Hello", ", ", "world"]


# ---------------------------------------------------------------------------
# THINKING → agent_thought_chunk
# ---------------------------------------------------------------------------

def test_thinking_maps_to_agent_thought_chunk(transport):
    t, server = transport
    t.emit(AgentEvent(EventType.THINKING, {"text": "Let me check..."}))
    upd = _last_update(server)
    assert upd == {
        "sessionUpdate": "agent_thought_chunk",
        "content": {"type": "text", "text": "Let me check..."},
    }


# ---------------------------------------------------------------------------
# TOOL_START → tool_call
# ---------------------------------------------------------------------------

def test_tool_start_maps_to_tool_call(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_START,
            {
                "tool": "read_file",
                "call_id": "uuid-1",
                "args": {"file_path": "/tmp/x"},
            },
        )
    )
    upd = _last_update(server)
    assert upd == {
        "sessionUpdate": "tool_call",
        "toolCallId": "uuid-1",
        "title": "read_file",
        "kind": "read",
        "status": "pending",
        "rawInput": {"file_path": "/tmp/x"},
    }


def test_tool_start_unknown_tool_falls_back_to_other(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_START,
            {"tool": "mcp_github_create_issue", "call_id": "u", "args": {}},
        )
    )
    assert _last_update(server)["kind"] == "other"


def test_tool_start_coerces_path_args_to_string(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_START,
            {
                "tool": "read_file",
                "call_id": "u",
                "args": {"file_path": Path("/tmp/a")},
            },
        )
    )
    upd = _last_update(server)
    assert upd["rawInput"] == {"file_path": "/tmp/a"}


# ---------------------------------------------------------------------------
# TOOL_OUTPUT → tool_call_update (incremental)
# ---------------------------------------------------------------------------

def test_tool_output_maps_to_tool_call_update_in_progress(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_OUTPUT,
            {"tool": "bash", "call_id": "u", "chunk": "line 1\n"},
        )
    )
    upd = _last_update(server)
    assert upd == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "u",
        "status": "in_progress",
        "content": [
            {"type": "content", "content": {"type": "text", "text": "line 1\n"}}
        ],
    }


# ---------------------------------------------------------------------------
# TOOL_COMPLETE → tool_call_update (terminal)
# ---------------------------------------------------------------------------

def test_tool_complete_ok_maps_to_completed(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_COMPLETE,
            {
                "tool": "read_file",
                "call_id": "u",
                "status": "ok",
                "duration_ms": 42,
                "error": None,
            },
        )
    )
    upd = _last_update(server)
    assert upd == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "u",
        "status": "completed",
    }


def test_tool_complete_error_maps_to_failed(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_COMPLETE,
            {"tool": "bash", "call_id": "u", "status": "error", "error": "boom"},
        )
    )
    upd = _last_update(server)
    assert upd["status"] == "failed"
    assert upd["content"] == [
        {"type": "content", "content": {"type": "text", "text": "Error: boom"}}
    ]


def test_tool_complete_cancelled_maps_to_failed(transport):
    """ACP tool_call_update has no 'cancelled' status — map to failed."""
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_COMPLETE,
            {"tool": "bash", "call_id": "u", "status": "cancelled"},
        )
    )
    assert _last_update(server)["status"] == "failed"


# ---------------------------------------------------------------------------
# AGENT_START / AGENT_END → agent_thought_chunk markers
# ---------------------------------------------------------------------------

def test_agent_start_emits_thought_marker(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.AGENT_START,
            {"agent": "codebase-investigator", "task": "find bugs", "max_turns": 10},
        )
    )
    upd = _last_update(server)
    assert upd["sessionUpdate"] == "agent_thought_chunk"
    text = upd["content"]["text"]
    assert "sub-agent started" in text
    assert "codebase-investigator" in text
    assert "find bugs" in text


def test_agent_end_emits_thought_marker_with_turns(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.AGENT_END,
            {
                "agent": "codebase-investigator",
                "state": "completed",
                "turns": 3,
                "tool_calls": 5,
                "tokens": 1200,
                "duration_ms": 8000,
                "error": None,
            },
        )
    )
    upd = _last_update(server)
    assert upd["sessionUpdate"] == "agent_thought_chunk"
    text = upd["content"]["text"]
    assert "sub-agent finished" in text
    assert "codebase-investigator" in text
    assert "completed" in text
    assert "3 turns" in text


# ---------------------------------------------------------------------------
# ERROR → agent_message_chunk
# ---------------------------------------------------------------------------

def test_error_event_emits_visible_message(transport):
    t, server = transport
    t.emit(
        AgentEvent(
            EventType.ERROR,
            {"message": "tool crashed", "detail": "KeyError: 'x'"},
        )
    )
    upd = _last_update(server)
    assert upd["sessionUpdate"] == "agent_message_chunk"
    assert "Error: tool crashed" in upd["content"]["text"]
    assert "KeyError" in upd["content"]["text"]


# ---------------------------------------------------------------------------
# Never-raise contract
# ---------------------------------------------------------------------------

class BrokenServer:
    """Server whose write_notification always raises."""

    def write_notification(self, method: str, params: Dict[str, Any]) -> None:
        raise RuntimeError("simulated write failure")


def test_emit_never_raises_when_server_write_fails():
    t = ACPTransport(server=BrokenServer(), session_id="sess_x")
    # Must not propagate the RuntimeError — transport failures cannot
    # crash a turn in progress.
    t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))


def test_emit_never_raises_on_unknown_event_type():
    server = RecordingServer()
    t = ACPTransport(server=server, session_id="sess_x")

    class FakeEvent:
        type = "bogus_type"
        data: Dict[str, Any] = {}

    # Has ``type`` and ``data`` but type is not a real EventType — should
    # fall through to the "no mapping" branch and NOT raise.
    t.emit(FakeEvent())  # type: ignore[arg-type]
    assert server.notifications == []


# ---------------------------------------------------------------------------
# _tool_kind / _json_safe helpers
# ---------------------------------------------------------------------------

def test_tool_kind_known_mappings():
    assert _tool_kind("read_file") == "read"
    assert _tool_kind("write_file") == "edit"
    assert _tool_kind("run_shell_command") == "execute"
    assert _tool_kind("web_fetch") == "fetch"
    assert _tool_kind("find_files") == "search"


def test_tool_kind_unknown_falls_back_to_other():
    assert _tool_kind("some_mcp_tool") == "other"
    assert _tool_kind("") == "other"


def test_json_safe_passes_native_values_through():
    assert _json_safe(None) is None
    assert _json_safe(True) is True
    assert _json_safe(42) == 42
    assert _json_safe(3.14) == 3.14
    assert _json_safe("hello") == "hello"


def test_json_safe_coerces_path():
    assert _json_safe(Path("/tmp/a")) == "/tmp/a"


def test_json_safe_recurses_into_dicts_and_lists():
    value = {
        "files": [Path("/a"), Path("/b")],
        "nested": {"p": Path("/c")},
    }
    assert _json_safe(value) == {
        "files": ["/a", "/b"],
        "nested": {"p": "/c"},
    }


def test_json_safe_coerces_set_to_sorted_list():
    # Sets aren't JSON-native; they become lists.
    out = _json_safe({1, 2, 3})
    assert isinstance(out, list)
    assert sorted(out) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Session id stamping
# ---------------------------------------------------------------------------

def test_session_id_stamped_on_every_notification():
    server = RecordingServer()
    t = ACPTransport(server=server, session_id="sess_abc")
    t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))
    t.emit(AgentEvent(EventType.THINKING, {"text": "thinking"}))
    t.emit(
        AgentEvent(
            EventType.TOOL_START,
            {"tool": "read_file", "call_id": "u", "args": {}},
        )
    )
    assert len(server.notifications) == 3
    for _, params in server.notifications:
        assert params["sessionId"] == "sess_abc"


# ---------------------------------------------------------------------------
# End-to-end wire: transport writes actual NDJSON to stdout
# ---------------------------------------------------------------------------

def test_end_to_end_notifications_hit_stdout():
    """Drive the real AcpServer.write_notification path and verify the
    NDJSON that lands on stdout matches ACP's session/update envelope."""
    stdout = io.StringIO()
    server = AcpServer(stdin=io.StringIO(""), stdout=stdout)
    t = ACPTransport(server=server, session_id="sess_e2e")

    t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "line1"}))
    t.emit(
        AgentEvent(
            EventType.TOOL_START,
            {"tool": "read_file", "call_id": "c1", "args": {"file_path": "/a"}},
        )
    )
    t.emit(
        AgentEvent(
            EventType.TOOL_COMPLETE,
            {"tool": "read_file", "call_id": "c1", "status": "ok"},
        )
    )

    # Parse the NDJSON stream on stdout.
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]

    for msg in parsed:
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == METHOD_SESSION_UPDATE
        assert msg["params"]["sessionId"] == "sess_e2e"
        # Notifications have no id.
        assert "id" not in msg

    assert parsed[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert parsed[1]["params"]["update"]["sessionUpdate"] == "tool_call"
    assert parsed[2]["params"]["update"]["sessionUpdate"] == "tool_call_update"
    assert parsed[2]["params"]["update"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------

def test_concurrent_emits_dont_interleave_on_stdout():
    """Multiple threads emitting simultaneously must produce valid NDJSON.

    The server's write lock guarantees that individual JSON lines are
    not interleaved byte-by-byte — each line is still parseable.
    """
    stdout = io.StringIO()
    server = AcpServer(stdin=io.StringIO(""), stdout=stdout)
    t = ACPTransport(server=server, session_id="sess_threads")

    def worker(i: int) -> None:
        for j in range(50):
            t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": f"t{i}-{j}"}))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 5 * 50
    # Every line must be valid JSON with the expected envelope.
    for ln in lines:
        msg = json.loads(ln)
        assert msg["method"] == METHOD_SESSION_UPDATE
        assert msg["params"]["sessionId"] == "sess_threads"
        assert msg["params"]["update"]["sessionUpdate"] == "agent_message_chunk"


# ---------------------------------------------------------------------------
# Backward compatibility: the Issue 06 no-op test must still pass
# ---------------------------------------------------------------------------

def test_emit_accepts_all_event_types_without_raising():
    """Smoke test: every real EventType value must go through emit()
    without raising, even with empty payloads."""
    server = RecordingServer()
    t = ACPTransport(server=server, session_id="sess_smoke")
    for etype in EventType:
        t.emit(AgentEvent(etype, {}))
    # TURN_START and TOOL_CONFIRMATION are the only silent ones.
    emitted_types = {
        n[1]["update"]["sessionUpdate"] for n in server.notifications
    }
    # Should have at least agent_message_chunk, agent_thought_chunk,
    # tool_call, and tool_call_update variants present.
    assert "agent_message_chunk" in emitted_types
    assert "agent_thought_chunk" in emitted_types
    assert "tool_call" in emitted_types
    assert "tool_call_update" in emitted_types
