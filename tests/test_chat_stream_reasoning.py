"""Streaming path must accumulate ``delta.reasoning_content``.

DeepSeek / MiniMax / Kimi / OpenAI o-series style endpoints emit
``reasoning_content`` chunks alongside the regular ``content`` deltas. The
non-streaming path already exposes them on ``message.reasoning_content``;
this test pins down that ``chat_stream()`` does the same so thinking-model
output is not silently dropped on streaming providers.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _chunk(content=None, reasoning=None, finish=None, tool_calls=None):
    """Build a single SSE chunk with the fields chat_stream actually reads."""
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(
        choices=[choice],
        usage=None,
        model="test-thinking-model",
    )


def _tc_delta(*, index=None, id=None, name=None, arguments=None):
    """One streaming tool_call delta, shaped as the OpenAI SDK yields it.

    The first delta of a call carries ``id``/``name``; continuation deltas
    carry only ``arguments``. ``index`` is None for providers that omit it.
    """
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _usage_only_chunk(prompt=10, completion=5):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
        model="test-thinking-model",
    )


def _make_client():
    """Construct a real LLMClient with the OpenAI SDK swapped for a Mock."""
    with patch("agentao.llm.client.OpenAI") as openai_cls:
        openai_cls.return_value = MagicMock()
        from agentao.llm.client import LLMClient
        client = LLMClient(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-thinking-model",
        )
    return client


def test_chat_stream_accumulates_reasoning_content():
    client = _make_client()

    chunks = [
        _chunk(reasoning="Let me "),
        _chunk(reasoning="think step "),
        _chunk(reasoning="by step.\n"),
        _chunk(content="The answer is "),
        _chunk(content="42.", finish="stop"),
        _usage_only_chunk(),
    ]
    client.client.chat.completions.create = MagicMock(return_value=iter(chunks))

    response = client.chat_stream(
        messages=[{"role": "user", "content": "What is 6 * 7?"}],
        tools=None,
        max_tokens=128,
    )

    msg = response.choices[0].message
    assert msg.content == "The answer is 42."
    assert msg.reasoning_content == "Let me think step by step.\n"
    assert response.choices[0].finish_reason == "stop"


def test_chat_stream_reasoning_none_when_absent():
    """Providers that don't emit reasoning_content must yield None, not ''."""
    client = _make_client()

    chunks = [
        _chunk(content="hello"),
        _chunk(content=" world", finish="stop"),
        _usage_only_chunk(),
    ]
    client.client.chat.completions.create = MagicMock(return_value=iter(chunks))

    response = client.chat_stream(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        max_tokens=64,
    )

    msg = response.choices[0].message
    assert msg.content == "hello world"
    assert msg.reasoning_content is None


def test_chat_stream_reasoning_with_tool_calls():
    """reasoning_content must coexist with tool_calls in the same response."""
    client = _make_client()

    tc_delta_open = _tc_delta(index=0, id="call_1", name="get_weather", arguments="")
    tc_delta_args = _tc_delta(index=0, arguments='{"city":"SF"}')

    chunks = [
        _chunk(reasoning="I should check the weather."),
        _chunk(tool_calls=[tc_delta_open]),
        _chunk(tool_calls=[tc_delta_args], finish="tool_calls"),
        _usage_only_chunk(),
    ]
    client.client.chat.completions.create = MagicMock(return_value=iter(chunks))

    response = client.chat_stream(
        messages=[{"role": "user", "content": "weather?"}],
        tools=[{"type": "function", "function": {"name": "get_weather"}}],
        max_tokens=64,
    )

    msg = response.choices[0].message
    assert msg.reasoning_content == "I should check the weather."
    assert msg.tool_calls is not None
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].function.name == "get_weather"
    assert msg.tool_calls[0].function.arguments == '{"city":"SF"}'


def _run_tool_stream(client, chunks, tools=None):
    """Drive chat_stream over ``chunks`` and return the rebuilt message."""
    client.client.chat.completions.create = MagicMock(
        return_value=iter([*chunks, _usage_only_chunk()])
    )
    response = client.chat_stream(
        messages=[{"role": "user", "content": "go"}],
        tools=tools or [{"type": "function", "function": {"name": "noop"}}],
        max_tokens=64,
    )
    return response.choices[0].message


def test_chat_stream_single_tool_call_without_index():
    """A whole tool call delivered in one indexless delta (the goose #10023
    shape: local/self-hosted servers and gateways that omit ``index``) must be
    rebuilt intact."""
    msg = _run_tool_stream(_make_client(), [
        _chunk(tool_calls=[_tc_delta(
            id="functions.get_weather:0",
            name="get_weather",
            arguments='{"city":"Paris"}',
        )], finish="tool_calls"),
    ])

    assert msg.tool_calls is not None
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].function.name == "get_weather"
    assert msg.tool_calls[0].function.arguments == '{"city":"Paris"}'


def test_chat_stream_parallel_tool_calls_one_chunk_without_index():
    """Two distinct indexless calls in one chunk must stay separate. Distinct
    names make the assertion discriminating: a collision concatenates them into
    "get_weatherget_time" instead of yielding two calls."""
    msg = _run_tool_stream(_make_client(), [
        _chunk(tool_calls=[
            _tc_delta(id="c0", name="get_weather", arguments='{"city":"Paris"}'),
            _tc_delta(id="c1", name="get_time", arguments='{"tz":"JST"}'),
        ], finish="tool_calls"),
    ])

    assert [tc.function.name for tc in msg.tool_calls] == ["get_weather", "get_time"]
    assert [tc.function.arguments for tc in msg.tool_calls] == [
        '{"city":"Paris"}', '{"tz":"JST"}',
    ]


def test_chat_stream_parallel_tool_calls_across_chunks_without_index():
    """Indexless parallel calls arriving one-per-chunk must stay distinct.
    A chunk-local position would reset to 0 each chunk and collapse both onto
    one key; the id-keyed accumulator gives each its own. Guards goose #10023's
    cross-chunk case."""
    msg = _run_tool_stream(_make_client(), [
        _chunk(tool_calls=[_tc_delta(id="c0", name="get_weather", arguments='{"city":"Paris"}')]),
        _chunk(tool_calls=[_tc_delta(id="c1", name="get_time", arguments='{"tz":"JST"}')],
               finish="tool_calls"),
    ])

    assert [tc.function.name for tc in msg.tool_calls] == ["get_weather", "get_time"]
    assert [tc.function.arguments for tc in msg.tool_calls] == [
        '{"city":"Paris"}', '{"tz":"JST"}',
    ]


def test_chat_stream_fragmented_arguments_without_index():
    """An indexless call whose arguments are fragmented across chunks (first
    delta carries id+name, continuations carry arguments only) must reassemble
    onto the same call, not spawn phantom entries."""
    msg = _run_tool_stream(_make_client(), [
        _chunk(tool_calls=[_tc_delta(id="c0", name="get_weather", arguments='{"city":')]),
        _chunk(tool_calls=[_tc_delta(arguments='"Paris"')]),
        _chunk(tool_calls=[_tc_delta(arguments='}')], finish="tool_calls"),
    ])

    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].function.name == "get_weather"
    assert msg.tool_calls[0].function.arguments == '{"city":"Paris"}'


def test_chat_stream_mixed_indexed_and_indexless_tool_calls():
    """A stream mixing an indexed call with an indexless one across chunks must
    keep them distinct. The pre-fix code crashed here (``sorted({0, None})``
    TypeError); a chunk-local position would silently collide the indexless
    call onto key 0. The synthetic-key accumulator keeps both."""
    msg = _run_tool_stream(_make_client(), [
        _chunk(tool_calls=[_tc_delta(index=0, id="a", name="get_weather", arguments='{"city":"Paris"}')]),
        _chunk(tool_calls=[_tc_delta(id="b", name="get_time", arguments='{"tz":"JST"}')],
               finish="tool_calls"),
    ])

    assert [tc.function.name for tc in msg.tool_calls] == ["get_weather", "get_time"]
    assert [tc.function.arguments for tc in msg.tool_calls] == [
        '{"city":"Paris"}', '{"tz":"JST"}',
    ]


if __name__ == "__main__":
    test_chat_stream_accumulates_reasoning_content()
    print("✓ accumulates reasoning_content")
    test_chat_stream_reasoning_none_when_absent()
    print("✓ reasoning is None when absent")
    test_chat_stream_reasoning_with_tool_calls()
    print("✓ reasoning coexists with tool_calls")
    test_chat_stream_single_tool_call_without_index()
    print("✓ single tool call without index")
    test_chat_stream_parallel_tool_calls_one_chunk_without_index()
    print("✓ parallel indexless calls in one chunk stay distinct")
    test_chat_stream_parallel_tool_calls_across_chunks_without_index()
    print("✓ parallel indexless calls across chunks stay distinct")
    test_chat_stream_fragmented_arguments_without_index()
    print("✓ fragmented indexless arguments reassemble")
    test_chat_stream_mixed_indexed_and_indexless_tool_calls()
    print("✓ mixed indexed/indexless calls stay distinct")
