"""Generic Chat Completions contract, not a claim of live gateway support."""

import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import openai
import pytest

from agentao.embedding.factory import discover_llm_kwargs
from agentao.llm.client import LLMClient
from agentao.runtime.model import set_provider


@pytest.fixture
def gateway(monkeypatch):
    """Keep the real SDK, replacing only HTTP; unexpected requests fail closed."""
    requests, replies, clients = [], [], []

    def handle(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        assert replies, "Unexpected gateway request"
        return replies.pop(0)

    def make_client(**kwargs):
        client = openai.OpenAI(
            **kwargs,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )
        clients.append(client)
        return client

    monkeypatch.setattr("agentao.llm.client.OpenAI", make_client)
    yield requests, replies
    for client in clients:
        client.close()
    assert not replies, "A scripted response was not consumed"


def client(**overrides):
    config = dict(api_key="test-a2agent-key", base_url="https://api.a2agent.me/v1",
                  model="test-model", logger=logging.getLogger(__name__), log_file=None)
    config.update(overrides)
    return LLMClient(**config)


def stream(*deltas, finish="stop"):
    chunks = [
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1,
         "model": "test-model", "choices": [{"index": 0, "delta": delta,
         "finish_reason": None}]}
        for delta in deltas
    ]
    chunks.append({"id": "chatcmpl-test", "object": "chat.completion.chunk",
                   "created": 1, "model": "test-model", "choices": [
                       {"index": 0, "delta": {}, "finish_reason": finish}]})
    data = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=(data + "data: [DONE]\n\n").encode())


@pytest.mark.parametrize("provider", ["A2AGENT", "MY_GATEWAY"])
def test_environment_keeps_provider_names_generic(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider.lower())
    for suffix, value in {"API_KEY": "placeholder", "BASE_URL": "https://api.a2agent.me/v1",
                          "MODEL": "explicit-model", "API_FORMAT": "openai-completions"}.items():
        monkeypatch.setenv(f"{provider}_{suffix}", value)
    config = discover_llm_kwargs()
    assert {key: config[key] for key in ("api_key", "base_url", "model", "api_format")} == {
        "api_key": "placeholder", "base_url": "https://api.a2agent.me/v1",
        "model": "explicit-model", "api_format": "openai-completions",
    }


def test_streaming_text_and_bearer_auth(gateway):
    requests, replies = gateway
    replies.append(stream({"content": "Hello"}, {"content": " world"}))
    chunks = []
    response = client().chat_stream(
        [{"role": "user", "content": "Hello"}], on_text_chunk=chunks.append)
    assert chunks == ["Hello", " world"]
    assert response.choices[0].message.content == "Hello world"
    assert response.choices[0].finish_reason == "stop"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer test-a2agent-key"
    body = json.loads(requests[0].content)
    assert body["model"] == "test-model"
    assert body["stream"] is True


def test_streamed_tool_arguments_and_result_continuation(gateway):
    requests, replies = gateway
    replies.extend([
        stream({"tool_calls": [{"index": 0, "id": "call_echo", "type": "function",
                                "function": {"name": "echo", "arguments": '{"text":'}}]},
               {"tool_calls": [{"index": 0, "function": {"arguments": '"hello"}'}}]},
               finish="tool_calls"),
        stream({"content": "hello"}),
    ])
    llm = client()
    tools = [{"type": "function", "function": {"name": "echo", "description": "Echo text",
              "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                             "required": ["text"]}}}]
    messages = [{"role": "user", "content": "Echo hello"}]
    response = llm.chat_stream(messages, tools=tools)
    call = response.choices[0].message.tool_calls[0]
    assert response.choices[0].finish_reason == "tool_calls"
    assert call.id == "call_echo"
    assert call.function.name == "echo"
    assert json.loads(call.function.arguments) == {"text": "hello"}
    messages.extend([
        {"role": "assistant", "content": None, "tool_calls": [{"id": call.id,
         "type": "function", "function": {"name": call.function.name,
                                             "arguments": call.function.arguments}}]},
        {"role": "tool", "tool_call_id": call.id, "content": "hello"},
    ])
    assert llm.chat_stream(messages, tools=tools).choices[0].message.content == "hello"
    assert len(requests) == 2
    body = json.loads(requests[1].content)
    assert body["tools"] == tools
    assert body["messages"][-2:] == messages[-2:]


def test_provider_switch_routes_new_credentials_and_explicit_model(gateway):
    requests, replies = gateway
    replies.extend([stream({"content": "first"}), stream({"content": "second"})])
    llm = client()
    llm.chat_stream([{"role": "user", "content": "first"}])
    agent = SimpleNamespace(llm=llm, messages=[], context_manager=Mock(), transport=Mock())
    set_provider(agent, api_key="test-other-key", base_url="https://other.invalid/v1",
                 model="other-model", api_format="openai-completions")
    llm.chat_stream([{"role": "user", "content": "second"}])
    assert [(request.url.host, request.headers["authorization"],
             json.loads(request.content)["model"]) for request in requests] == [
        ("api.a2agent.me", "Bearer test-a2agent-key", "test-model"),
        ("other.invalid", "Bearer test-other-key", "other-model"),
    ]


@pytest.mark.skipif(os.getenv("AGENTAO_TEST_A2AGENT_LIVE") != "1",
                    reason="Live gateway access requires explicit opt-in")
def test_live_a2agent_text_stream(monkeypatch):
    """Paid, synthetic-text-only smoke test; never reads a local .env file."""
    key, model = os.getenv("A2AGENT_API_KEY"), os.getenv("A2AGENT_MODEL")
    if not key or not model:
        pytest.fail("Set A2AGENT_API_KEY and A2AGENT_MODEL explicitly", pytrace=False)
    monkeypatch.setattr("agentao.llm.client.MAX_RETRY_ATTEMPTS", 1)
    logger = logging.Logger("a2agent-live", level=logging.CRITICAL)
    llm = client(api_key=key, model=model, max_tokens=128, logger=logger)
    llm.client.timeout = httpx.Timeout(30.0)
    chunks = []
    try:
        response = llm.chat_stream(
            [{"role": "user", "content": "Reply with hello."}], on_text_chunk=chunks.append)
        assert chunks and response.choices[0].message.content
    finally:
        llm.client.close()
