"""A tool call from a length-truncated assistant message must not be executed.

When an assistant message stops with ``finish_reason == "length"``, the streamed
tool-call arguments may be cut off mid-JSON — the repair pipeline can even
balance the brackets by luck and yield valid-but-wrong args. Executing such a
call risks running a destructive action the model never finished composing. The
chat loop instead answers each call with a re-issue prompt (keeping history
well-formed) and loops so the model re-issues with complete arguments.

Ported from the pi-mono #6285 lesson (strict-parse alone is insufficient; the
``finish_reason`` signal is the robust discriminator).
"""

from pathlib import Path
from types import SimpleNamespace

from agentao import Agentao
from agentao.runtime.chat_loop._runner import (
    LENGTH_TRUNCATED_TOOL_CALL_MESSAGE,
    LENGTH_TRUNCATION_ABORT_THRESHOLD,
)


def _fake_tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _fake_response(content, *, tool_calls=None, finish_reason="stop", reasoning=None):
    message = SimpleNamespace(
        content=content, tool_calls=tool_calls, reasoning_content=reasoning
    )
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None, model="test-model")


def _scripted_llm_call(responses):
    """Return an ``_llm_call`` stub that yields ``responses`` in order."""
    it = iter(responses)

    def _call(messages, tools, token):
        return next(it)

    return _call


def _make_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )


def test_length_truncated_tool_call_is_not_executed_and_reissued():
    agent = _make_agent()
    # First turn: a read_file call cut off mid-arguments at the token limit.
    # Second turn: the model re-issues as plain text and the turn ends.
    agent._llm_call = _scripted_llm_call([
        _fake_response(
            "let me read that file",
            tool_calls=[_fake_tool_call(
                "call_trunc", "read_file", '{"file_path": "/tmp/does-not',
            )],
            finish_reason="length",
        ),
        _fake_response("re-issued and done", finish_reason="stop"),
    ])

    response = agent.chat("read that file")

    assert response == "re-issued and done"

    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1, "expected exactly one synthetic tool result"
    # The tool never ran — the answer is the re-issue notice, not a read_file
    # result / "file does not exist" error.
    assert tool_msgs[0]["content"] == LENGTH_TRUNCATED_TOOL_CALL_MESSAGE

    # History stays well-formed: the assistant tool_call is answered by a
    # tool-role message carrying the same id.
    asst = [
        m for m in agent.messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert len(asst) == 1
    call_id = asst[0]["tool_calls"][0]["id"]
    assert call_id, "tool_call id must be non-empty for strict APIs"
    assert tool_msgs[0]["tool_call_id"] == call_id


def test_length_finish_without_tool_calls_returns_partial_text():
    # A length cutoff with no tool calls is just a truncated text answer — the
    # guard does not apply and the partial text is returned as-is.
    agent = _make_agent()
    agent._llm_call = _scripted_llm_call([
        _fake_response("partial answer cut off", finish_reason="length"),
    ])

    response = agent.chat("hi")

    assert response == "partial answer cut off"
    assert not [m for m in agent.messages if m.get("role") == "tool"]


def test_stop_finish_with_tool_calls_executes_normally():
    # Regression: a normally-finished ("stop") tool call still executes.
    agent = _make_agent()
    agent._llm_call = _scripted_llm_call([
        _fake_response(
            "reading",
            tool_calls=[_fake_tool_call(
                "call_ok", "read_file",
                '{"file_path": "/tmp/agentao-nonexistent-xyz-1234"}',
            )],
            finish_reason="stop",
        ),
        _fake_response("done", finish_reason="stop"),
    ])

    response = agent.chat("read it")

    assert response == "done"
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    # The real execution path ran, not the truncation guard.
    assert tool_msgs[0]["content"] != LENGTH_TRUNCATED_TOOL_CALL_MESSAGE


def test_multiple_truncated_calls_each_get_a_reissue_result():
    # A length-truncated turn may carry several tool calls (earlier ones may
    # even be complete). None execute; every call is answered so the assistant
    # message is followed by matching tool results.
    agent = _make_agent()
    agent._llm_call = _scripted_llm_call([
        _fake_response(
            "batch",
            tool_calls=[
                _fake_tool_call("call_a", "read_file", '{"file_path": "/tmp/a"}'),
                _fake_tool_call("call_b", "read_file", '{"file_path": "/tmp/b'),
            ],
            finish_reason="length",
        ),
        _fake_response("re-issued", finish_reason="stop"),
    ])

    response = agent.chat("read both")

    assert response == "re-issued"
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2, "every truncated call must be answered"
    assert all(m["content"] == LENGTH_TRUNCATED_TOOL_CALL_MESSAGE for m in tool_msgs)
    # Each result pairs with its originating call id.
    asst = next(
        m for m in agent.messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    call_ids = {tc["id"] for tc in asst["tool_calls"]}
    result_ids = {m["tool_call_id"] for m in tool_msgs}
    assert result_ids == call_ids


def test_persistent_truncation_aborts_the_turn():
    # A model stuck re-truncating the same oversized call must not burn the
    # whole max_iterations budget. After LENGTH_TRUNCATION_ABORT_THRESHOLD
    # consecutive truncations the turn is finalized instead of re-issued.
    truncated = _fake_response(
        "still too long",
        tool_calls=[_fake_tool_call("call_x", "read_file", '{"file_path": "/tmp/x')],
        finish_reason="length",
    )
    # Supply more than enough identical truncated turns; the guard should stop
    # consuming them once the threshold trips.
    agent = _make_agent()
    agent._llm_call = _scripted_llm_call([truncated] * (LENGTH_TRUNCATION_ABORT_THRESHOLD + 5))

    response = agent.chat("do it")

    # The turn ended on the abort backstop, not by exhausting the script.
    assert isinstance(response, str) and response
    # Exactly THRESHOLD assistant tool-call turns were recorded before aborting.
    asst_tool_turns = [
        m for m in agent.messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert len(asst_tool_turns) == LENGTH_TRUNCATION_ABORT_THRESHOLD


def test_non_openai_length_spelling_triggers_guard():
    # Providers that spell the truncation reason differently (Gemini's
    # "MAX_TOKENS", etc.) must also gate execution.
    agent = _make_agent()
    agent._llm_call = _scripted_llm_call([
        _fake_response(
            "reading",
            tool_calls=[_fake_tool_call(
                "call_g", "read_file", '{"file_path": "/tmp/does-not',
            )],
            finish_reason="MAX_TOKENS",
        ),
        _fake_response("done", finish_reason="stop"),
    ])

    response = agent.chat("read it")

    assert response == "done"
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == LENGTH_TRUNCATED_TOOL_CALL_MESSAGE


def test_truncated_before_name_streams_uses_matching_placeholder():
    # If the cutoff lands before the tool name streamed, the assistant
    # tool_call and its synthetic result must carry the SAME placeholder name —
    # strict proxies reject a name mismatch between a call and its result.
    agent = _make_agent()
    agent._llm_call = _scripted_llm_call([
        _fake_response(
            "calling",
            tool_calls=[_fake_tool_call("call_noname", "", '{"file_path": "/tmp')],
            finish_reason="length",
        ),
        _fake_response("done", finish_reason="stop"),
    ])

    response = agent.chat("go")

    assert response == "done"
    asst = next(
        m for m in agent.messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    tool_msg = next(m for m in agent.messages if m.get("role") == "tool")
    call_name = asst["tool_calls"][0]["function"]["name"]
    assert call_name, "empty name must be replaced by a placeholder"
    assert tool_msg["name"] == call_name


if __name__ == "__main__":
    test_length_truncated_tool_call_is_not_executed_and_reissued()
    test_length_finish_without_tool_calls_returns_partial_text()
    test_stop_finish_with_tool_calls_executes_normally()
    test_multiple_truncated_calls_each_get_a_reissue_result()
    test_persistent_truncation_aborts_the_turn()
    test_non_openai_length_spelling_triggers_guard()
    test_truncated_before_name_streams_uses_matching_placeholder()
    print("ok")
