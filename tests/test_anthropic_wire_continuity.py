"""A session on the ``anthropic-messages`` wire survives what rewrites history.

The adapter tests prove a *given* message list translates; the runtime tests
prove one turn round-trips. Neither runs the two things that rebuild history
wholesale and then keep talking: **compaction** (which leaves ``role:
"system"`` summaries in the middle of history and cuts through a transcript
full of signed thinking and tool pairs) and **session restore** (``/sessions
resume`` and ACP ``session/load``, which bring signed blocks back from disk to
a model that may not have minted them).

The Messages API is strict where Chat Completions is lenient — first message
``user``, strict alternation, every ``tool_result`` answering a ``tool_use`` in
the message before it, a signed thinking block whole or not at all — so the
assertion is on the **request body the SDK serialized**, read off the socket,
for the turn *after* the rewrite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock

import pytest

from agentao import Agentao
from agentao.acp import session_load as acp_session_load
from agentao.cli.commands import sessions as sessions_cmd
from agentao.embedding.sessions import persist_agent_session
from agentao.llm._stream_response import ANTHROPIC_THINKING_BLOCKS
from tests.support.acp_agents import make_factory
from tests.support.acp_server import make_initialized_server
from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block,
    thinking_block, tool_use_block,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

THOUGHT = "weigh the options. " * 40
SUMMARY = "SUMMARY-MARKER: the user had note.txt read several times; it says hello."


def _tool_turn(i: int) -> bytes:
    return stream_of(
        message_start(input_tokens=900),
        thinking_block(0, [THOUGHT], f"SIG-tool-{i}=="),
        text_block(1, "Reading it."),
        tool_use_block(2, f"toolu_{i}", "read_file", '{"file_path": "note.txt"}'),
        message_end("tool_use"),
    )


def _final(i: int, text: str = "The note says hello.") -> bytes:
    return stream_of(
        message_start(input_tokens=50),
        thinking_block(0, ["wrap up"], f"SIG-final-{i}=="),
        text_block(1, text),
        message_end("end_turn"),
    )


def _agent() -> Agentao:
    return Agentao(
        api_key="test-key", base_url="https://api.example.test", model="claude-test",
        api_format="anthropic-messages", working_directory=Path.cwd(),
    )


def _rounds(agent: Agentao, n: int, *after: bytes) -> Wire:
    """``n`` real tool-calling turns, then whatever ``after`` scripts."""
    Path("note.txt").write_text("hello from disk", encoding="utf-8")
    script: List[bytes] = []
    for i in range(n):
        script += [_tool_turn(i), _final(i)]
    wire = attach(agent.llm, Wire(*script, *after))
    for i in range(n):
        agent.chat(f"read the note, round {i}")
    return wire


def _blocks(body: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    return [
        block for message in body["messages"]
        if isinstance(message["content"], list)
        for block in message["content"] if block.get("type") == kind
    ]


def _assert_the_api_would_take_it(body: Dict[str, Any]) -> None:
    """The structural rules the Messages API rejects a request for."""
    messages = body["messages"]
    roles = [m["role"] for m in messages]
    assert set(roles) <= {"user", "assistant"}, roles
    assert roles[0] == "user", roles
    assert all(a != b for a, b in zip(roles, roles[1:])), roles

    def ids(message, kind, key):
        content = message["content"]
        return [b[key] for b in content if b.get("type") == kind] if isinstance(content, list) else []

    for previous, message in zip([None, *messages], messages):
        results = ids(message, "tool_result", "tool_use_id")
        if results:
            assert previous is not None and message["role"] == "user"
            assert sorted(results) == sorted(ids(previous, "tool_use", "id"))
    for message, following in zip(messages, [*messages[1:], None]):
        calls = ids(message, "tool_use", "id")
        if calls:  # a call left unanswered is a 400 too
            assert following is not None
            assert sorted(calls) == sorted(ids(following, "tool_result", "tool_use_id"))
    # Thinking leads its turn, and is whole: text and signature, or not there.
    for message in messages:
        if message["role"] == "assistant" and isinstance(message["content"], list):
            kinds = [b["type"] for b in message["content"]]
            thinking = [k for k in kinds if k in ("thinking", "redacted_thinking")]
            assert kinds[: len(thinking)] == thinking, kinds
    for block in _blocks(body, "thinking"):
        assert block["signature"] and block["thinking"]


# -- compaction ---------------------------------------------------------------


@pytest.fixture
def compacted():
    """Four tool-calling turns, a real compaction, then two more turns.

    The summarizer's request goes over the same socket as the turns do.
    """
    agent = _agent()
    try:
        wire = _rounds(
            agent, 4,
            _final(90, SUMMARY),                       # the summarizer's answer
            _tool_turn(8), _final(8, "Still hello."),  # a tool-calling turn after it
        )
        sent_before = len(wire.requests)
        outcome = agent.compact()
        agent.source_after_compaction = agent.context_manager.get_usage_stats(
            agent.messages)["token_count_source"]
        reply = agent.chat("read it once more")
        yield agent, wire, outcome, reply, sent_before
    finally:
        agent.close()


def test_the_turn_after_a_compaction_is_a_request_the_api_would_take(compacted):
    agent, wire, outcome, reply, sent_before = compacted
    assert outcome.status == "success"
    assert reply == "Still hello."
    # One summarizer request, then the two of the tool-calling turn.
    assert len(wire.requests) == sent_before + 3
    for body in wire.requests[sent_before:]:
        _assert_the_api_would_take_it(body)


def test_the_summary_reaches_the_model_inside_the_first_user_message(compacted):
    """Compaction leaves ``role: "system"`` messages at the head of history.
    This wire has no such role inside ``messages``; they must arrive as text
    in the opening ``user`` turn, not be dropped and not be hoisted into
    ``system`` where they would rewrite the cached prefix."""
    agent, wire, *_ = compacted
    assert [m["role"] for m in agent.messages[:2]] == ["system", "system"]
    body = wire.requests[-1]
    opening = json.dumps(body["messages"][0])
    assert "SUMMARY-MARKER" in opening
    assert "SUMMARY-MARKER" not in json.dumps(body["system"])


def test_signed_thinking_the_compaction_kept_goes_back_whole_and_the_rest_is_gone(compacted):
    agent, wire, *_ = compacted
    sent = {block["signature"]: block["thinking"] for block in _blocks(wire.requests[-1], "thinking")}
    # History minus its last message: that one is the *answer* to this request.
    kept = {
        block["signature"]
        for message in agent.messages[:-1]
        for block in message.get(ANTHROPIC_THINKING_BLOCKS) or []
    }
    assert kept and set(sent) == kept          # exactly what history still holds
    assert "SIG-tool-0==" not in sent          # summarized away with its turn
    assert sent["SIG-tool-8=="] == THOUGHT     # whole, not the 500-char display copy


def test_the_token_anchor_is_re_established_by_the_first_response_after(compacted):
    """Compaction invalidates the anchor; leaving it unset would hand every
    later threshold check to the local estimate for the rest of the session."""
    agent, *_ = compacted
    # The old count described a history that no longer exists.
    assert agent.source_after_compaction == "local"
    stats = agent.context_manager.get_usage_stats(agent.messages)
    assert stats["token_count_source"] == "api"
    # And it is the new, shorter request's count, not the pre-compaction one.
    assert stats["estimated_tokens"] == 50


# -- session restore ----------------------------------------------------------


def _saved_session(tmp_path: Path) -> str:
    """Two real turns on this wire, persisted the way the CLI persists them."""
    agent = _agent()
    try:
        _rounds(agent, 2)
        assert any(m.get(ANTHROPIC_THINKING_BLOCKS) for m in agent.messages)
        path, session_id = persist_agent_session(agent, project_root=Path.cwd())
    finally:
        agent.close()
    # The precondition: the signatures really are on disk, so it is the
    # restore — not the save — that has to keep them off the next request.
    assert "SIG-tool-0==" in path.read_text(encoding="utf-8")
    return session_id


def _assert_restored_and_continues(agent: Agentao, wire: Wire) -> None:
    assert not any(ANTHROPIC_THINKING_BLOCKS in m or "reasoning_content" in m
                   for m in agent.messages)
    assert agent.chat("what did the note say?") == "It said hello."
    (body,) = wire.requests
    _assert_the_api_would_take_it(body)
    # Minted by whatever model the saved session ran: a signed block replayed
    # to another model is a 400, so none goes back — but the conversation does.
    assert _blocks(body, "thinking") == [] and "SIG-" not in json.dumps(body)
    assert [b["id"] for b in _blocks(body, "tool_use")] == ["toolu_0", "toolu_1"]
    assert "The note says hello." in json.dumps(body)


def test_a_resumed_session_continues_without_the_saved_signatures(tmp_path):
    session_id = _saved_session(tmp_path)
    agent = _agent()
    try:
        cli = Mock()
        cli.agent = agent
        sessions_cmd.resume_session(cli, session_id)
        wire = attach(agent.llm, Wire(_final(5, "It said hello.")))
        _assert_restored_and_continues(agent, wire)
    finally:
        agent.close()


def test_an_acp_loaded_session_continues_without_the_saved_signatures(tmp_path):
    session_id = _saved_session(tmp_path)
    agent = _agent()
    try:
        acp_session_load.handle_session_load(
            make_initialized_server(),
            {"sessionId": session_id, "cwd": str(Path.cwd()), "mcpServers": []},
            agent_factory=make_factory(agent),
        )
        wire = attach(agent.llm, Wire(_final(5, "It said hello.")))
        _assert_restored_and_continues(agent, wire)
    finally:
        agent.close()
