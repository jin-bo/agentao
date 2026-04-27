"""Streaming path must accumulate ``delta.reasoning_content``.

DeepSeek / MiniMax / Kimi / OpenAI o-series style endpoints emit
``reasoning_content`` chunks alongside the regular ``content`` deltas. The
non-streaming path already exposes them on ``message.reasoning_content``;
this test pins down that ``chat_stream()`` does the same so thinking-model
output is not silently dropped on streaming providers.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _chunk(content=None, reasoning=None, finish=None):
    """Build a single SSE chunk with the fields chat_stream actually reads."""
    delta = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=reasoning,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(
        choices=[choice],
        usage=None,
        model="test-thinking-model",
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

    tc_delta_open = SimpleNamespace(
        index=0,
        id="call_1",
        function=SimpleNamespace(name="get_weather", arguments=""),
    )
    tc_delta_args = SimpleNamespace(
        index=0,
        id=None,
        function=SimpleNamespace(name=None, arguments='{"city":"SF"}'),
    )

    chunks = [
        _chunk(reasoning="I should check the weather."),
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[tc_delta_open],
                    reasoning_content=None,
                ),
                finish_reason=None,
            )],
            usage=None,
            model="test-thinking-model",
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[tc_delta_args],
                    reasoning_content=None,
                ),
                finish_reason="tool_calls",
            )],
            usage=None,
            model="test-thinking-model",
        ),
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


if __name__ == "__main__":
    test_chat_stream_accumulates_reasoning_content()
    print("✓ accumulates reasoning_content")
    test_chat_stream_reasoning_none_when_absent()
    print("✓ reasoning is None when absent")
    test_chat_stream_reasoning_with_tool_calls()
    print("✓ reasoning coexists with tool_calls")
