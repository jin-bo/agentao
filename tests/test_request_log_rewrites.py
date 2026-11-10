"""The request log shows what the model was sent, after a rewrite too (#635).

The log used to write ``messages[count:]``, so a message changed in place (a
microcompaction clip, the ``api_overflow`` clip that names a saved copy) was
never logged, and a history that got shorter logged nothing until something
else reset the count.
"""

from pathlib import Path

import pytest

from agentao.llm import LLMClient


@pytest.fixture
def client(tmp_path: Path):
    log_file = tmp_path / "agentao.log"
    c = LLMClient(
        api_key="test-api-key",
        base_url="https://api.example.com/v1",
        model="m",
        log_file=str(log_file),
    )
    offsets = {"read": 0}

    def new_text() -> str:
        for handler in c.logger.handlers:
            handler.flush()
        text = log_file.read_text(encoding="utf-8")
        chunk = text[offsets["read"]:]
        offsets["read"] = len(text)
        return chunk

    yield c, new_text
    for handler in list(c.logger.handlers):
        c.logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


SYSTEM = {"role": "system", "content": "you are an agent"}
REQUEST = {"role": "user", "content": "read the files"}
CALL = {
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
    ],
}
RESULT = {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "FULL TEXT " * 50}
CLIPPED = dict(RESULT, content="FULL [… 480 chars omitted by microcompact; full output saved to x …]")
TAIL = {"role": "user", "content": "<system-reminder>todos</system-reminder>"}


def _turn(c, request_id, messages, tail=0):
    c._log_request(request_id, {"model": "m", "messages": messages}, channel="turn", request_only=tail)


def test_a_message_rewritten_in_place_is_logged_again(client):
    c, new_text = client
    _turn(c, "r1", [SYSTEM, REQUEST, CALL, RESULT])
    new_text()

    _turn(c, "r2", [SYSTEM, REQUEST, CALL, CLIPPED])

    text = new_text()
    assert "logging 0 new, 1 rewritten" in text
    assert "Message 4 [tool] (rewritten):" in text
    assert "full output saved to x" in text
    assert "Message 2 [user]" not in text


def test_an_unchanged_request_logs_no_message(client):
    c, new_text = client
    _turn(c, "r1", [SYSTEM, REQUEST, CALL, RESULT])
    new_text()

    _turn(c, "r2", [SYSTEM, REQUEST, CALL, RESULT])

    text = new_text()
    assert "logging 0 new):" in text
    assert "  Message 2" not in text and "  Message 4" not in text


def test_a_shorter_history_is_logged_without_a_side_call(client):
    c, new_text = client
    _turn(c, "r1", [SYSTEM, REQUEST, CALL, RESULT, {"role": "assistant", "content": "done"}])
    new_text()
    summary = {"role": "user", "content": "[Compact Boundary] the summary"}

    _turn(c, "r2", [SYSTEM, summary, {"role": "assistant", "content": "done"}])

    text = new_text()
    assert "history shortened from 5" in text
    assert "the summary" in text
    assert "Message 3 [assistant] (rewritten):" in text


def test_a_side_call_does_not_reset_the_turn_log(client):
    c, new_text = client
    _turn(c, "r1", [SYSTEM, REQUEST, CALL, RESULT])
    c._log_request(
        "s1",
        {"model": "m", "messages": [{"role": "system", "content": "summarize"}, {"role": "user", "content": "x"}]},
        channel="side",
    )
    new_text()

    _turn(c, "r2", [SYSTEM, REQUEST, CALL, RESULT, {"role": "assistant", "content": "ok"}])

    text = new_text()
    assert "logging 1 new):" in text
    assert "system prompt unchanged" in text
    assert "system prompt changed" not in text


def test_the_request_only_tail_is_logged_each_time_and_not_remembered(client):
    c, new_text = client
    _turn(c, "r1", [SYSTEM, REQUEST, TAIL], tail=1)
    first = new_text()
    assert "Message 3 [user]:" in first

    answer = {"role": "assistant", "content": "", "tool_calls": CALL["tool_calls"]}
    _turn(c, "r2", [SYSTEM, REQUEST, answer, RESULT, TAIL], tail=1)

    text = new_text()
    # The slot the tail held is new history, not a rewrite of the tail.
    assert "Message 3 [assistant]:" in text
    assert "rewritten" not in text
    assert "Message 5 [user]:" in text


def test_a_moved_cache_marker_is_not_a_rewrite(client):
    """On Chat Completions the breakpoint moves each request, copy-on-mark."""
    c, new_text = client
    marked = dict(
        REQUEST,
        content=[{"type": "text", "text": REQUEST["content"], "cache_control": {"type": "ephemeral"}}],
    )
    _turn(c, "r1", [SYSTEM, marked, TAIL], tail=1)
    new_text()

    answer = {"role": "assistant", "content": "ok"}
    marked_answer = dict(
        answer, content=[{"type": "text", "text": "ok", "cache_control": {"type": "ephemeral"}}],
    )
    _turn(c, "r2", [SYSTEM, REQUEST, marked_answer, TAIL], tail=1)

    text = new_text()
    assert "rewritten" not in text
    assert "logging 2 new):" in text


def test_side_calls_are_each_logged_whole(client):
    """Two unrelated one-off calls: the second is not a rewrite of the first."""
    c, new_text = client
    side = lambda rid, text: c._log_request(  # noqa: E731 — local shorthand
        rid,
        {"model": "m", "messages": [{"role": "system", "content": "summarize"}, {"role": "user", "content": text}]},
        channel="side",
    )
    side("s1", "first")
    new_text()

    side("s2", "second")

    text = new_text()
    assert "rewritten" not in text
    assert "system prompt initial" in text
    assert "Message 2 [user]:" in text


def test_a_cache_marked_system_prompt_is_logged(client):
    c, new_text = client
    marked = dict(
        SYSTEM,
        content=[{"type": "text", "text": SYSTEM["content"], "cache_control": {"type": "ephemeral"}}],
    )
    _turn(c, "r1", [marked, REQUEST])

    assert "you are an agent" in new_text()


def test_the_non_streaming_turn_path_logs_on_the_turn_channel(client, monkeypatch):
    """The Gemini bypass and the streaming-unsupported fallback send the
    turn's request through ``chat``: it must keep the turn's state and not
    remember the request-only tail."""
    c, new_text = client
    seen = []
    monkeypatch.setattr(
        c, "_log_request",
        lambda rid, kw, **opts: seen.append(opts),
    )

    class _Stop(Exception):
        pass

    def _boom(*a, **k):
        raise _Stop()

    monkeypatch.setattr(c._adapter, "send", _boom)
    monkeypatch.setattr(c._adapter, "classify_retry", lambda e: (False, None, None))
    with pytest.raises(_Stop):
        c._emit_nonstreaming([SYSTEM, REQUEST, TAIL], None, None, None, cache_boundary=1)
    assert seen == [{"channel": "turn", "request_only": 1}]
