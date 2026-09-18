"""Stage 1 of ``docs/design/llm-api-adapters.md``: the extraction changed nothing.

Moving the Chat Completions path out of ``LLMClient`` into an adapter and
adding a second wire in the same change makes "did the extraction alter
today's behaviour?" unanswerable by reading the diff. So the request is pinned
against a capture:

``tests/data/openai_completions_request_golden.json`` was written by
``LLMClient._build_request_kwargs`` at ``main@a2c8c6d`` — **before** the
extraction — from the inputs below. Key order is part of the comparison; it
is the order the SDK serializes. Do not regenerate the file from the current
build to make this pass: a golden rewritten by the code under test agrees with
it by construction.
"""

import json
import logging
from pathlib import Path
from unittest.mock import patch

from agentao.llm._openai_completions import OpenAICompletionsAdapter
from agentao.llm.client import LLMClient

GOLDEN = Path(__file__).parent / "data" / "openai_completions_request_golden.json"

MESSAGES = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "body"},
    {"role": "user", "content": "<system-reminder>tail</system-reminder>"},
]
TOOLS = [{"type": "function", "function": {
    "name": "read_file", "description": "d",
    "parameters": {"type": "object", "properties": {}}}}]


def _client(**kwargs) -> LLMClient:
    return LLMClient(
        api_key="k", base_url="http://x/v1", model="m",
        logger=logging.getLogger("test.extraction"), **kwargs,
    )


def _cases() -> dict:
    cases = {}
    plain = _client()
    cases["plain_nonstream"] = plain._build_request_kwargs(MESSAGES, TOOLS, 100, stream=False)
    cases["plain_stream"] = plain._build_request_kwargs(MESSAGES, TOOLS, 100, stream=True)
    cases["no_tools_no_max"] = plain._build_request_kwargs(MESSAGES, None, None, stream=False)

    latched = _client(extra_body={"reasoning_effort": "low"}, temperature=0.7)
    latched._use_max_completion_tokens = True
    cases["extra_body_mct"] = latched._build_request_kwargs(MESSAGES, TOOLS, 200, stream=True)

    cold = _client()
    cold.omit_temperature = True
    cases["omit_temperature"] = cold._build_request_kwargs(MESSAGES, TOOLS, 100, stream=False)

    cached = _client(prompt_cache="anthropic", prompt_cache_ttl="1h")
    cases["cache_marked"] = cached._build_request_kwargs(
        MESSAGES, TOOLS, 100, stream=False, cache_boundary=1,
    )
    cases["cache_unmarked_call"] = cached._build_request_kwargs(
        MESSAGES, TOOLS, 100, stream=False,
    )
    return cases


def test_the_request_is_byte_identical_to_the_pre_extraction_capture():
    expected = GOLDEN.read_text(encoding="utf-8")
    produced = json.dumps(_cases(), indent=1, sort_keys=False) + "\n"
    assert produced == expected
    print("✅ Chat Completions request unchanged by the extraction")


def test_the_default_wire_is_chat_completions_and_keeps_its_patch_surface():
    """``patch("agentao.llm.client.OpenAI")`` is how the suite stubs the SDK.

    The adapter builds the client now, so the class it builds from still has
    to be looked up through ``agentao.llm.client`` at construction time.
    """
    with patch("agentao.llm.client.OpenAI") as openai_cls:
        client = _client()
    assert client.api_format == "openai-completions"
    assert isinstance(client._adapter, OpenAICompletionsAdapter)
    assert client.client is openai_cls.return_value
    openai_cls.assert_called_once_with(api_key="k", base_url="http://x/v1", max_retries=0)


def test_reconfigure_rebuilds_the_sdk_client_through_the_adapter():
    with patch("agentao.llm.client.OpenAI") as openai_cls:
        client = _client()
        client.reconfigure(api_key="k2", base_url="http://y/v1", model="m2")
    assert openai_cls.call_args.kwargs == {
        "api_key": "k2", "base_url": "http://y/v1", "max_retries": 0,
    }
    assert client.client is openai_cls.return_value
