"""An empty/whitespace-only final assistant turn must not enter history verbatim.

Byte-level reasoning backends (Kimi, GLM, Qwen-via-Ollama) occasionally end a
turn with no text and no tool calls. Persisting ``{"role": "assistant",
"content": ""}`` leaves a contentless message that strict API proxies reject on
the next turn. The chat loop substitutes a neutral placeholder instead.
"""

from pathlib import Path
from types import SimpleNamespace

from agentao import Agentao


def _fake_response(content, *, reasoning=None):
    message = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None, model="test-model")


def _make_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )


def _assert_no_contentless_assistant_message(agent: Agentao) -> None:
    assistant_msgs = [m for m in agent.messages if m.get("role") == "assistant"]
    assert assistant_msgs, "expected an assistant message in history"
    for m in assistant_msgs:
        assert (m.get("content") or "").strip(), "contentless assistant message leaked into history"


def test_empty_final_content_substituted_with_placeholder():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("   \n  ")

    response = agent.chat("hi")

    assert response == "[No response]"
    _assert_no_contentless_assistant_message(agent)


def test_none_final_content_treated_as_empty():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response(None)

    response = agent.chat("hi")

    assert response == "[No response]"
    _assert_no_contentless_assistant_message(agent)


def test_empty_final_content_with_reasoning_gets_distinct_marker():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("", reasoning="thought hard")

    response = agent.chat("hi")

    assert response == "[No text response]"
    _assert_no_contentless_assistant_message(agent)


def test_nonempty_final_content_passes_through():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("the answer is 42")

    response = agent.chat("hi")

    assert response == "the answer is 42"


if __name__ == "__main__":
    test_empty_final_content_substituted_with_placeholder()
    test_none_final_content_treated_as_empty()
    test_empty_final_content_with_reasoning_gets_distinct_marker()
    test_nonempty_final_content_passes_through()
    print("ok")
