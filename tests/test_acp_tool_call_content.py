"""ACP ``tool_call`` content is a collection you replace, not a stream.

The normative ACP v1 schema source says a ``tool_call_update``'s
collections are overwritten rather than extended
(``agentclientprotocol/agent-client-protocol@bf6d1ec``,
``agent-client-protocol-schema/src/v1/tool_call.rs:167,252,285``).
agentao used to map each streamed ``TOOL_OUTPUT`` chunk to an update
carrying that chunk as the whole collection, so a conformant client kept
only the last one — and a failing command replaced even that with the
bare ``Error: …`` line.

These tests pin the accumulate-and-restate behaviour and both of its
bounds (a flush threshold and a size cap), which exist so restating the
collection does not become quadratic in the output size.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from agentao.acp._tool_call_content import (
    FLUSH_CHARS,
    HEAD_CHARS,
    MAX_CHARS,
    ToolCallContentBuffer,
)
from agentao.acp.protocol import METHOD_SESSION_UPDATE
from agentao.acp.transport import ACPTransport
from agentao.transport.events import AgentEvent, EventType

from .support.acp_server import RecordingServer


@pytest.fixture
def transport():
    server = RecordingServer()
    return ACPTransport(server=server, session_id="sess_test"), server


def _updates(server: RecordingServer) -> List[Dict[str, Any]]:
    out = []
    for method, params in server.notifications:
        assert method == METHOD_SESSION_UPDATE
        out.append(params["update"])
    return out


def _chunk(t: ACPTransport, text: str, call_id: str = "c1") -> None:
    t.emit(
        AgentEvent(
            EventType.TOOL_OUTPUT,
            {"tool": "run_shell_command", "call_id": call_id, "chunk": text},
        )
    )


def _complete(
    t: ACPTransport,
    *,
    status: str = "ok",
    error: Any = None,
    call_id: str = "c1",
    tool: str = "run_shell_command",
) -> None:
    t.emit(
        AgentEvent(
            EventType.TOOL_COMPLETE,
            {
                "tool": tool,
                "call_id": call_id,
                "status": status,
                "duration_ms": 1,
                "error": error,
            },
        )
    )


def _texts(update: Dict[str, Any]) -> List[str]:
    return [e["content"]["text"] for e in update.get("content", [])]


# ---------------------------------------------------------------------------
# The bug: a client kept only the last chunk
# ---------------------------------------------------------------------------

def test_every_update_restates_the_whole_collection(transport):
    """A client that replaces the collection must still end up with it all."""
    t, server = transport
    big = "A" * FLUSH_CHARS
    for marker in ("first", "second", "third"):
        _chunk(t, f"{marker}\n{big}")

    updates = _updates(server)
    assert len(updates) == 3, "each chunk here exceeds the flush threshold"
    final = _texts(updates[-1])
    assert len(final) == 1, "streamed text is coalesced into one entry"
    for marker in ("first", "second", "third"):
        assert marker in final[0]


def test_a_failure_does_not_erase_the_output_that_explains_it(transport):
    """The error rides beside the output, not instead of it."""
    t, server = transport
    _chunk(t, "configure: error: no acceptable C compiler\n")
    _complete(t, status="error", error="exit code 1")

    final = _updates(server)[-1]
    assert final["status"] == "failed"
    texts = _texts(final)
    assert "no acceptable C compiler" in texts[0]
    assert texts[-1] == "Error: exit code 1"


def test_a_held_back_chunk_still_reaches_the_client(transport):
    """Throttling delays a chunk; it must not drop it."""
    t, server = transport
    _chunk(t, "opening line\n")          # first chunk always flushes
    _chunk(t, "held back\n")             # under the threshold — no update
    assert len(_updates(server)) == 1

    _complete(t)
    final = _updates(server)[-1]
    assert final["status"] == "completed"
    assert "held back" in _texts(final)[0]
    assert "opening line" in _texts(final)[0]


def test_a_completion_with_nothing_new_leaves_the_client_copy_alone(transport):
    """Omitting ``content`` is how replace semantics say "unchanged"."""
    t, server = transport
    _chunk(t, "all of it\n")
    assert "content" in _updates(server)[-1]

    _complete(t)
    final = _updates(server)[-1]
    assert final["status"] == "completed"
    assert "content" not in final


def test_a_call_that_streamed_nothing_sends_no_content(transport):
    """Unchanged behaviour for the many tools that never stream."""
    t, server = transport
    _complete(t, tool="read_file")
    assert _updates(server) == [
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "c1",
            "status": "completed",
            "schema_version": 1,
        }
    ]


def test_an_empty_chunk_sends_the_status_without_clearing_the_content(transport):
    """``content: []`` would wipe the client's copy — send status alone."""
    t, server = transport
    _chunk(t, "")
    update = _updates(server)[-1]
    assert update["status"] == "in_progress"
    assert "content" not in update


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

def test_the_excerpt_is_bounded_and_says_what_it_elided(transport):
    """A long build log must not grow the per-update payload without limit."""
    t, server = transport
    line = "x" * FLUSH_CHARS
    _chunk(t, "HEAD-MARKER\n" + line)
    for _ in range(20):
        _chunk(t, line)
    _chunk(t, line + "\nTAIL-MARKER")
    _complete(t)

    with_content = [u for u in _updates(server) if "content" in u]
    text = _texts(with_content[-1])[0]
    assert "HEAD-MARKER" in text, "the start of the output is kept"
    assert "TAIL-MARKER" in text, "so is the end"
    assert "characters elided" in text
    # The marker itself is the only thing above the cap.
    assert len(text) < MAX_CHARS + 200


def test_every_update_stays_under_the_cap(transport):
    t, server = transport
    line = "y" * FLUSH_CHARS
    for _ in range(30):
        _chunk(t, line)
    for update in _updates(server):
        for text in _texts(update):
            assert len(text) < MAX_CHARS + 200


def test_the_elided_count_is_the_number_of_characters_dropped():
    buffer = ToolCallContentBuffer()
    total = MAX_CHARS * 3
    buffer.append("z" * total)
    text = buffer.text()
    # head + marker + tail, where head + tail == MAX_CHARS
    dropped = total - MAX_CHARS
    assert f"{dropped:,} characters elided" in text
    assert text.startswith("z" * HEAD_CHARS)


# ---------------------------------------------------------------------------
# Per-call state
# ---------------------------------------------------------------------------

def test_two_calls_streaming_at_once_do_not_share_a_buffer(transport):
    t, server = transport
    _chunk(t, "from A\n", call_id="a")
    _chunk(t, "from B\n", call_id="b")
    _complete(t, call_id="a")
    _complete(t, call_id="b")

    seen: Dict[str, str] = {}
    for update in _updates(server):
        seen.setdefault(update["toolCallId"], "")
        seen[update["toolCallId"]] += " ".join(_texts(update))

    assert "from A" in seen["a"] and "from B" not in seen["a"]
    assert "from B" in seen["b"] and "from A" not in seen["b"]


def test_the_buffer_is_released_when_the_call_ends(transport):
    t, _server = transport
    _chunk(t, "output\n")
    assert t._tool_call_content, "buffered while the call is in flight"
    _complete(t)
    assert t._tool_call_content == {}, "released on completion"


def test_a_todo_write_that_became_a_plan_releases_its_buffer(transport):
    """The plan branch returns early — it must not leak the buffer."""
    t, _server = transport
    t.emit(
        AgentEvent(
            EventType.TOOL_START,
            {
                "tool": "todo_write",
                "call_id": "c1",
                "args": {"todos": [{"content": "step", "status": "pending"}]},
            },
        )
    )
    _chunk(t, "noise\n")
    _complete(t, tool="todo_write")
    assert t._tool_call_content == {}


def test_a_cancelled_call_releases_its_buffer(transport):
    t, server = transport
    _chunk(t, "partial\n")
    _complete(t, status="cancelled")
    assert t._tool_call_content == {}
    assert _updates(server)[-1]["status"] == "failed"
