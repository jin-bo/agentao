"""Stage 1 of ``docs/design/llm-api-adapters.md``, above ``llm/``.

An adapter can only *return* a richer response; what enters history is decided
by the runner. These tests run a real ``Agentao`` turn over the real
``anthropic`` SDK and a scripted socket, and then read the **second** request
off the wire — the only place a signed thinking block can be shown to have
survived the history write, the sanitizer and the translation back.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


from agentao import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.embedding.factory import discover_llm_kwargs
from agentao.llm._stream_response import ANTHROPIC_THINKING_BLOCKS
from agentao.llm.client import LLMClient
from agentao.runtime.chat_loop._serialize import (
    MAX_REASONING_HISTORY_CHARS,
    _attach_thinking_blocks,
)
from agentao.runtime.model import purge_thinking_artifacts
from agentao.runtime.sanitize import sanitize_assistant_message
from tests.support.anthropic_wire import (
    Wire,
    attach,
    message_end,
    message_start,
    redacted_thinking_block,
    stream_of,
    text_block,
    thinking_block,
    tool_use_block,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

LONG_THOUGHT = "weigh the options. " * 80  # ~1500 chars, 3x the display cap
SIGNED = {"type": "thinking", "thinking": LONG_THOUGHT, "signature": "SIG-abc=="}
REDACTED = {"type": "redacted_thinking", "data": "OPAQUE"}


def _agent(**kwargs) -> Agentao:
    return Agentao(
        api_key="test-key", base_url="https://api.example.test", model="claude-test",
        api_format="anthropic-messages", working_directory=Path.cwd(), **kwargs,
    )


def _tool_turn() -> bytes:
    return stream_of(
        message_start(input_tokens=900, cache_read_input_tokens=4000),
        thinking_block(0, [LONG_THOUGHT], SIGNED["signature"]),
        redacted_thinking_block(1, REDACTED["data"]),
        text_block(2, "Reading it."),
        tool_use_block(3, "toolu_1", "read_file", '{"file_path": "note.txt"}'),
        message_end("tool_use"),
    )


def _final_turn() -> bytes:
    return stream_of(
        message_start(input_tokens=50, cache_creation_input_tokens=200,
                      cache_read_input_tokens=5000),
        thinking_block(0, ["wrap up"], "SIG-final=="),
        text_block(1, "The note says hello."),
        message_end("end_turn"),
    )


@pytest.fixture
def turn():
    """One real turn: a signed-thinking tool call, the tool run, a final answer."""
    Path("note.txt").write_text("hello from disk", encoding="utf-8")
    agent = _agent()
    wire = attach(agent.llm, Wire(_tool_turn(), _final_turn()))
    try:
        reply = agent.chat("read the note")
        yield SimpleNamespace(agent=agent, wire=wire, reply=reply)
    finally:
        agent.close()


def test_signed_thinking_goes_back_whole_at_the_head_of_its_turn(turn):
    assert turn.reply == "The note says hello."
    first, second = turn.wire.requests

    assert first["system"] and isinstance(first["system"], str)
    assert all("input_schema" in tool for tool in first["tools"])

    assistant = second["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == [
        SIGNED,
        REDACTED,
        {"type": "text", "text": "Reading it."},
        {"type": "tool_use", "id": "toolu_1", "name": "read_file",
         "input": {"file_path": "note.txt"}},
    ]
    # The tool ran for real and its result answers the call in the next turn.
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert "hello from disk" in json.dumps(result)
    # The 500-character display copy is agentao's, not the provider's.
    assert "reasoning_content" not in json.dumps(second)


def test_history_holds_the_whole_blocks_beside_the_truncated_display_copy(turn):
    tool_msg = turn.agent.messages[1]
    final_msg = turn.agent.messages[-1]
    assert tool_msg[ANTHROPIC_THINKING_BLOCKS] == [SIGNED, REDACTED]
    assert len(tool_msg["reasoning_content"]) == MAX_REASONING_HISTORY_CHARS + 3
    assert final_msg[ANTHROPIC_THINKING_BLOCKS] == [
        {"type": "thinking", "thinking": "wrap up", "signature": "SIG-final=="},
    ]
    # The shape did not change — an extra key on an assistant dict, which
    # session files and replay already carry for ``reasoning_content``.
    json.dumps(turn.agent.messages)


def test_the_token_anchor_is_the_whole_prompt_not_the_uncached_remainder(turn):
    """§6.11, carried to where it matters: ``record_api_usage`` takes this as
    the true size of the prefix already sent. ``input_tokens`` alone would
    have anchored 50 where the provider read 5,250."""
    manager = turn.agent.context_manager
    assert manager._last_api_request_tokens == 50 + 200 + 5000
    assert manager._last_api_prompt_tokens == 5250  # no volatile tail this turn
    # system + [user, assistant, tool] — the persistent prefix of request two.
    assert manager._api_anchor_msg_count == 4
    assert turn.agent.llm.total_prompt_tokens == 4900 + 5250


def test_the_local_estimate_counts_thinking_that_will_be_sent_back(turn):
    manager = turn.agent.context_manager
    message = dict(turn.agent.messages[1])
    with_blocks = manager._count_message_tokens(message)
    message.pop(ANTHROPIC_THINKING_BLOCKS)
    assert with_blocks > manager._count_message_tokens(message) + 100


def test_a_model_switch_purges_the_blocks_with_the_other_thinking_artifacts(turn):
    turn.agent.set_model("claude-other")
    assert not any(ANTHROPIC_THINKING_BLOCKS in m for m in turn.agent.messages)
    assert not any("reasoning_content" in m for m in turn.agent.messages)


def test_purge_counts_the_carrier_key():
    messages = [{"role": "assistant", "content": "x", ANTHROPIC_THINKING_BLOCKS: [SIGNED]}]
    assert purge_thinking_artifacts(messages) == 1
    assert messages == [{"role": "assistant", "content": "x"}]


def test_the_sanitizer_leaves_a_signed_block_byte_for_byte():
    """An invisible tag character inside signed thinking stays: stripping it
    is a rewrite, and the signature covers the text."""
    smuggled = "plan\U000e0041\U000e0042 it"
    message = {
        "role": "assistant", "content": f"answer{chr(0xE0041)}",
        ANTHROPIC_THINKING_BLOCKS: [
            {"type": "thinking", "thinking": smuggled, "signature": "SIG"},
        ],
    }
    assert sanitize_assistant_message(message) is True
    assert message["content"] == "answer"
    assert message[ANTHROPIC_THINKING_BLOCKS][0]["thinking"] == smuggled


def test_only_a_real_list_of_blocks_is_attached():
    """``MagicMock`` responses are everywhere in this suite, and a mock answers
    any attribute. The carrier must not be written from one."""
    message: dict = {"role": "assistant", "content": "x"}
    _attach_thinking_blocks(message, MagicMock())
    _attach_thinking_blocks(message, SimpleNamespace(anthropic_thinking_blocks=None))
    _attach_thinking_blocks(message, SimpleNamespace(anthropic_thinking_blocks=[]))
    _attach_thinking_blocks(message, SimpleNamespace(anthropic_thinking_blocks=["x"]))
    assert ANTHROPIC_THINKING_BLOCKS not in message

    source = [dict(SIGNED)]
    _attach_thinking_blocks(message, SimpleNamespace(anthropic_thinking_blocks=source))
    assert message[ANTHROPIC_THINKING_BLOCKS] == [SIGNED]
    assert message[ANTHROPIC_THINKING_BLOCKS][0] is not source[0]  # a copy


def test_a_synthetic_final_message_does_not_repeat_the_signed_blocks():
    """Max-iterations builds a second assistant message from a response whose
    blocks are already on the tool-call message. A signed block is the record
    of one model output."""
    Path("note.txt").write_text("hello", encoding="utf-8")
    agent = _agent()
    attach(agent.llm, Wire(_tool_turn(), _tool_turn()))
    try:
        agent.chat("read the note", max_iterations=1)
        carriers = [m for m in agent.messages if ANTHROPIC_THINKING_BLOCKS in m]
        assert len(carriers) == 1 and "tool_calls" in carriers[0]
        assert agent.messages[-1]["role"] == "assistant"
        assert ANTHROPIC_THINKING_BLOCKS not in agent.messages[-1]
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_the_format_rides_the_provider_block_in_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_FORMAT", " anthropic-messages ")
    monkeypatch.setenv("OPENAI_API_FORMAT", "openai-completions")  # another block's
    assert discover_llm_kwargs()["api_format"] == "anthropic-messages"

    monkeypatch.setenv("ANTHROPIC_API_FORMAT", "  ")
    assert "api_format" not in discover_llm_kwargs()


def test_the_provider_name_alone_never_selects_the_wire(monkeypatch):
    """``LLM_PROVIDER=ANTHROPIC`` at an OpenAI-compatible gateway works today,
    and naming the block must not change what it speaks."""
    monkeypatch.setenv("LLM_PROVIDER", "ANTHROPIC")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_FORMAT", raising=False)
    assert "api_format" not in discover_llm_kwargs()


def test_api_format_is_raw_config_and_refused_next_to_an_injected_client():
    llm = LLMClient(api_key="k", base_url="http://x/v1", model="m", log_file=None)
    with pytest.raises(ValueError, match="api_format"):
        Agentao(llm_client=llm, api_format="anthropic-messages",
                working_directory=Path.cwd())


def test_a_sub_agent_is_built_on_the_parents_wire(monkeypatch):
    """Through the real factory: a sub-agent that fell back to the default
    would speak Chat Completions at a Messages endpoint."""
    built = []

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        built.append(self.llm.api_format)
        return ""

    parent = _agent(enable_builtin_agents=True,
                    bg_store=BackgroundTaskStore(persistence_dir=None))
    monkeypatch.setattr(Agentao, "chat", chat)
    try:
        parent.tools.tools["agent_generalist"]._run_sync("x")
    finally:
        parent.close()
    assert built == ["anthropic-messages"]


def _completions_wire(requests):
    """A real ``openai`` client over a scripted socket, one streamed answer."""
    import httpx
    import openai

    def chunk(delta, finish=None):
        body = {"id": "c1", "object": "chat.completion.chunk", "created": 0,
                "model": "gpt-x",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(body)}\n\n"

    def handler(request):
        requests.append((str(request.url), json.loads(request.content)))
        sse = chunk({"role": "assistant", "content": "over completions"})
        sse += chunk({}, "stop") + "data: [DONE]\n\n"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse.encode(),
        )

    return openai.OpenAI(
        api_key="k2", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_provider_switches_across_the_wire_and_back(turn, monkeypatch):
    """Both directions, read off the socket: after each switch the next
    request is the new protocol's, and nothing the old one minted rides it."""
    import anthropic
    import openai

    from agentao.cli.commands import provider as provider_cmd

    printed = []
    monkeypatch.setattr(provider_cmd.console, "print",
                        lambda *a, **k: printed.append(str(a[0]) if a else ""))
    for name, value in {
        "OTHER_API_KEY": "k2", "OTHER_BASE_URL": "https://other.test/v1",
        "OTHER_MODEL": "gpt-x",
        "CLAUDE_API_KEY": "k3", "CLAUDE_BASE_URL": "https://api.example.test",
        "CLAUDE_MODEL": "claude-test", "CLAUDE_API_FORMAT": "anthropic-messages",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("OTHER_API_FORMAT", raising=False)

    agent = turn.agent
    assert any(ANTHROPIC_THINKING_BLOCKS in m for m in agent.messages)
    agent.llm._adapter._max_output_tokens = 4096  # a latch the old wire learned
    cli = SimpleNamespace(agent=agent, current_provider="ANTHROPIC")

    provider_cmd.handle_provider_command(cli, "OTHER")
    assert cli.current_provider == "OTHER"
    assert agent.llm.api_format == "openai-completions"
    assert isinstance(agent.llm.client, openai.OpenAI)
    assert agent._llm_config["api_format"] == "openai-completions"
    assert any("anthropic-messages → " in line for line in printed)
    # Signed blocks mean nothing to the other wire, and the openai SDK would
    # forward the unknown key verbatim.
    assert not any(ANTHROPIC_THINKING_BLOCKS in m for m in agent.messages)

    sent = []
    agent.llm.client = _completions_wire(sent)
    assert agent.chat("and again") == "over completions"
    url, body = sent[0]
    assert url.endswith("/chat/completions")
    assert body["messages"][0]["role"] == "system"
    assert ANTHROPIC_THINKING_BLOCKS not in json.dumps(body)

    provider_cmd.handle_provider_command(cli, "CLAUDE")
    assert agent.llm.api_format == "anthropic-messages"
    assert isinstance(agent.llm.client, anthropic.Anthropic)
    # A fresh adapter: the limit learned before the round trip is not carried.
    assert agent.llm._adapter._max_output_tokens is None
    wire = attach(agent.llm, Wire(stream_of(
        message_start(input_tokens=10), text_block(0, "back"), message_end("end_turn"),
    )))
    assert agent.chat("once more") == "back"
    assert wire.urls[0].endswith("/v1/messages")
    assert isinstance(wire.requests[0]["system"], str)
    assert "over completions" in json.dumps(wire.requests[0]["messages"])


def test_a_bad_api_format_leaves_the_session_on_its_provider(monkeypatch):
    from agentao.cli.commands import provider as provider_cmd

    printed = []
    monkeypatch.setattr(provider_cmd.console, "print",
                        lambda *a, **k: printed.append(str(a[0]) if a else ""))
    for name, value in {
        "OTHER_API_KEY": "k2", "OTHER_BASE_URL": "https://other.test/v1",
        "OTHER_MODEL": "gpt-x", "OTHER_API_FORMAT": "openai-responses",
    }.items():
        monkeypatch.setenv(name, value)

    agent = _agent()
    cli = SimpleNamespace(agent=agent, current_provider="ANTHROPIC")
    try:
        provider_cmd.handle_provider_command(cli, "OTHER")
        assert cli.current_provider == "ANTHROPIC"
        assert (agent.llm.model, agent.llm.api_format) == ("claude-test", "anthropic-messages")
        assert "OTHER_API_FORMAT" in printed[-1]
    finally:
        agent.close()


@pytest.mark.parametrize("signed", [True, False])
def test_a_wire_switch_alone_clears_what_the_old_wire_asserted(signed):
    """Same model name, same URL, other protocol: still a switch. The anchor
    goes whether or not the purge found anything to remove."""
    agent = _agent(prompt_cache="anthropic")
    try:
        carrier = {ANTHROPIC_THINKING_BLOCKS: [SIGNED]} if signed else {}
        agent.messages.append({"role": "assistant", "content": "x", **carrier})
        agent.context_manager.record_api_usage(1234, 1)
        assert agent.context_manager._last_api_prompt_tokens is not None
        assert agent.llm.cache_control is not None

        agent.set_provider("k", api_format="openai-completions")

        assert agent.llm.api_format == "openai-completions"
        assert agent.llm.cache_control is None
        assert ANTHROPIC_THINKING_BLOCKS not in agent.messages[-1]
        assert agent.context_manager._last_api_prompt_tokens is None
    finally:
        agent.close()


def test_reconfigure_refuses_an_unknown_wire_before_touching_anything():
    llm = LLMClient(api_key="k", base_url="https://a.test/v1", model="m")
    client = llm.client
    with pytest.raises(ValueError):
        llm.reconfigure("k2", base_url="https://b.test", model="m2", api_format="nope")
    assert (llm.api_key, llm.base_url, llm.model) == ("k", "https://a.test/v1", "m")
    assert llm.client is client


def test_a_failed_client_build_leaves_reconfigure_undone(monkeypatch):
    """The value was fine and the build still failed (the lazy SDK import is
    the reachable case). Half a switch is the worst outcome: the new wire's
    adapter driving the old wire's SDK object."""
    from agentao.llm import _anthropic_messages as wire_mod

    llm = LLMClient(api_key="k", base_url="https://a.test/v1", model="m",
                    prompt_cache="anthropic")
    before = (llm.api_key, llm.base_url, llm.model, llm.api_format,
              llm._adapter, llm.client, llm.cache_control, llm.prompt_cache)

    def boom(self):
        raise ImportError("no anthropic here")

    monkeypatch.setattr(wire_mod.AnthropicMessagesAdapter, "create_client", boom)
    with pytest.raises(ImportError):
        llm.reconfigure("k2", base_url="https://b.test", model="m2",
                        api_format="anthropic-messages")
    after = (llm.api_key, llm.base_url, llm.model, llm.api_format,
             llm._adapter, llm.client, llm.cache_control, llm.prompt_cache)
    assert all(a is b or a == b for a, b in zip(after, before))
    assert after[4] is before[4] and after[5] is before[5]


def test_thinking_does_not_store_a_field_this_wire_rejects(monkeypatch):
    from agentao.cli.commands import provider as provider_cmd

    printed = []
    monkeypatch.setattr(provider_cmd.console, "print", lambda *a, **k: printed.append(str(a[0])))
    agent = _agent()
    try:
        provider_cmd.handle_thinking_command(SimpleNamespace(agent=agent), "high")
        assert "reasoning_effort" not in agent.llm.extra_body
        assert "anthropic-messages" in printed[-1]
    finally:
        agent.close()



def test_a_wire_switch_re_reads_which_extra_body_keys_are_structural(caplog):
    """``system`` is inert on Chat Completions; on Messages it replaces the
    whole system prompt, and only the new adapter's key set says so."""
    llm = LLMClient(api_key="k", base_url="https://a.test/v1", model="m",
                    extra_body={"system": "mine", "top_k": 5})
    with caplog.at_level("WARNING", logger=llm.logger.name):
        caplog.clear()
        llm.reconfigure("k", api_format="anthropic-messages")
    structural = [r.getMessage() for r in caplog.records
                  if "structural request fields" in r.getMessage()]
    assert len(structural) == 1
    assert "system" in structural[0] and "top_k" not in structural[0]
