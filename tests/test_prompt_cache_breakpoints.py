"""Stage 0b: explicit ``cache_control`` breakpoints, opt-in, copy-on-mark.

``docs/design/llm-api-adapters.md`` §2.3 / §10 / §11. Three properties, and the
first is the one that would do damage if it broke:

1. **Copy-on-mark.** In the runtime the request's message dicts are the same
   objects as ``agent.messages`` (``[system] + agent.messages`` is a shallow
   concat). An in-place marker would therefore enter history, the session file,
   the replay record, ACP ``session/load`` and compaction — and accumulate one
   breakpoint per turn until the request 400s.
2. **At most three explicit markers**, leaving the fourth slot for the
   endpoint's automatic caching, counting markers the caller already placed.
3. **The conversation breakpoint sits at the end of stable history**, before
   stage 0a's request-only volatile tail. A breakpoint on the tail is never read
   back: the tail is different on the next request by construction.

Plus: off by default, and the markers never reach the replay record — they are
applied below it, at the wire boundary.
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao.llm._cache_control import (
    MAX_EXPLICIT_BREAKPOINTS,
    apply_cache_control,
    resolve_cache_control,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

EPHEMERAL = {"type": "ephemeral"}


def _client(**kwargs):
    from agentao.llm import LLMClient
    return LLMClient(
        api_key="k", base_url="https://example.test/v1", model="test-model",
        log_file=None, **kwargs,
    )


def _markers(messages, tools=None):
    """Every ``cache_control`` in a request, as ``(where, value)`` pairs."""
    found = []
    for i, m in enumerate(messages):
        if "cache_control" in m:
            found.append((f"messages[{i}]", m["cache_control"]))
        content = m.get("content")
        if isinstance(content, list):
            for j, part in enumerate(content):
                if isinstance(part, dict) and "cache_control" in part:
                    found.append((f"messages[{i}].content[{j}]", part["cache_control"]))
    for i, t in enumerate(tools or ()):
        if "cache_control" in t:
            found.append((f"tools[{i}]", t["cache_control"]))
    return found


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_off_by_default_and_the_request_is_byte_identical():
    plain = _client()
    assert plain.cache_control is None
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u"},
    ]
    tools = [{"type": "function", "function": {"name": "f"}}]
    before = copy.deepcopy((messages, tools))
    kwargs = plain._build_request_kwargs(
        messages, tools, 100, stream=False, cache_boundary=0,
    )
    assert _markers(kwargs["messages"], kwargs.get("tools")) == []
    assert (messages, tools) == before
    print("✅ Off by default; asking for a boundary changes nothing")


def test_an_unknown_format_raises_instead_of_quietly_doing_nothing():
    with pytest.raises(ValueError, match="Unknown prompt-cache format"):
        _client(prompt_cache="anthropc")
    with pytest.raises(ValueError, match="Unknown prompt-cache ttl"):
        _client(prompt_cache="anthropic", prompt_cache_ttl="2h")
    print("✅ A typo in the knob is loud")


def test_off_spellings_and_ttl_resolution():
    assert resolve_cache_control(None) is None
    for off in ("off", "OFF", "", "  ", "none", "false"):
        assert resolve_cache_control(off) is None, off
    assert resolve_cache_control("anthropic") == {"type": "ephemeral"}
    assert resolve_cache_control("Anthropic", "1h") == {
        "type": "ephemeral", "ttl": "1h",
    }
    assert resolve_cache_control("anthropic", "") == {"type": "ephemeral"}
    print("✅ Format / ttl resolution")


def test_a_host_supplied_llm_client_cannot_also_take_the_knob():
    """Raw-config family: the guard makes a silent no-op loud, the same way
    ``extra_body`` does."""
    from agentao import Agentao
    with pytest.raises(ValueError, match="prompt_cache"):
        Agentao(
            llm_client=_client(), working_directory=Path.cwd(),
            prompt_cache="anthropic",
        )
    print("✅ llm_client= and prompt_cache= are mutually exclusive")


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_three_breakpoints_at_system_last_tool_and_end_of_stable_history():
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "<system-reminder>volatile</system-reminder>"},
    ]
    tools = [
        {"type": "function", "function": {"name": "first"}},
        {"type": "function", "function": {"name": "last"}},
    ]

    out_messages, out_tools = apply_cache_control(
        messages, tools, EPHEMERAL, request_only_tail=1,
    )

    assert _markers(out_messages, out_tools) == [
        ("messages[0].content[0]", EPHEMERAL),
        ("messages[2].content[0]", EPHEMERAL),
        ("tools[1]", EPHEMERAL),
    ]
    # The conversation breakpoint is on the assistant turn, not on the tail.
    assert "cache_control" not in str(out_messages[3])
    # A string content became a one-element text part, which is the only way to
    # carry a block-level marker on this wire.
    assert out_messages[0]["content"] == [
        {"type": "text", "text": "S", "cache_control": EPHEMERAL},
    ]
    print("✅ system + last tool + end of stable history")


def test_the_marker_never_lands_on_the_volatile_tail():
    """With the tail excluded there is nothing else markable, so the
    conversation breakpoint is simply not placed — spending it on the tail
    would buy a cache write nothing reads back."""
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "<system-reminder>volatile</system-reminder>"},
    ]
    out_messages, _ = apply_cache_control(
        messages, None, EPHEMERAL, request_only_tail=1,
    )
    assert _markers(out_messages) == [("messages[0].content[0]", EPHEMERAL)]
    print("✅ No breakpoint on the tail")


def test_the_scan_walks_back_past_a_tool_call_only_assistant_message():
    """An assistant message carrying only ``tool_calls`` has no text part to
    hang a marker on. The message before it is a valid prefix boundary."""
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
    ]
    out_messages, _ = apply_cache_control(messages, None, EPHEMERAL)
    assert _markers(out_messages) == [
        ("messages[0].content[0]", EPHEMERAL),
        ("messages[1].content[0]", EPHEMERAL),
    ]
    print("✅ Backward scan skips an unmarkable message")


def test_a_multipart_message_is_marked_on_its_last_text_part():
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            {"type": "text", "text": "second"},
        ]},
    ]
    out_messages, _ = apply_cache_control(messages, None, EPHEMERAL)
    assert ("messages[1].content[2]", EPHEMERAL) in _markers(out_messages)
    # The image part is shared, not copied, and certainly not marked.
    assert out_messages[1]["content"][1] is messages[1]["content"][1]
    print("✅ Last text part of a multipart message")


# ---------------------------------------------------------------------------
# Copy-on-mark
# ---------------------------------------------------------------------------


def test_nothing_in_the_caller_s_input_is_mutated():
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": [{"type": "text", "text": "u1"}]},
        {"role": "assistant", "content": "a1"},
    ]
    tools = [{"type": "function", "function": {"name": "f"}}]
    before = copy.deepcopy((messages, tools))

    out_messages, out_tools = apply_cache_control(messages, tools, EPHEMERAL)

    assert (messages, tools) == before, "input was mutated"
    assert _markers(out_messages, out_tools), "nothing was marked at all"
    # Untouched messages are shared, not copied — the copy is per marked path.
    assert out_messages[1] is messages[1]
    assert out_messages[0] is not messages[0]
    print("✅ Copy along the marked path, share the rest")


def test_markers_do_not_accumulate_across_requests():
    """Every request is built from the unmarked history, so re-marking the same
    list twice yields one marker per site, not two."""
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u1"},
    ]
    first, _ = apply_cache_control(messages, None, EPHEMERAL)
    second, _ = apply_cache_control(messages, None, EPHEMERAL)
    assert len(_markers(first)) == len(_markers(second)) == 2
    print("✅ No accumulation")


def test_a_turn_never_puts_a_marker_into_history_or_the_replay_record():
    """End to end through ``chat()``: the markers exist only in the dict handed
    to ``.create()``. ``agent.messages`` and the replay delta see the request as
    it was *before* marking, because marking happens below both."""
    from agentao import Agentao
    from agentao.transport import EventType

    agent = Agentao(
        api_key="k", base_url="https://example.test/v1", model="test-model",
        working_directory=Path.cwd(), prompt_cache="anthropic",
    )
    agent.todo_tool.execute(todos=[{"content": "step one", "status": "pending"}])

    sent: list = []

    def _fake_stream(**kwargs):
        # Stands in for ``.create()``: record the fully assembled request.
        sent.append(agent.llm._build_request_kwargs(
            kwargs["messages"], kwargs["tools"], kwargs["max_tokens"],
            stream=True, cache_boundary=kwargs.get("cache_boundary"),
        ))
        message = SimpleNamespace(content="done", tool_calls=None, reasoning_content=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None, model="test-model",
        )

    agent.llm.chat_stream = _fake_stream
    events: list = []
    agent.transport.subscribe(lambda ev: events.append(ev))
    try:
        agent.chat("go")
    finally:
        agent.close()

    (request,) = sent
    placed = _markers(request["messages"], request.get("tools"))
    assert placed, "markers were configured but none were placed"
    assert len(placed) <= MAX_EXPLICIT_BREAKPOINTS

    assert _markers(agent.messages) == [], "a marker reached history"
    inspected = 0
    for ev in events:
        if ev.type in (EventType.LLM_CALL_DELTA, EventType.LLM_CALL_IO):
            payload = ev.data.get("added_messages") or ev.data.get("messages") or []
            assert payload, f"{ev.type} carried no messages to check"
            assert _markers(payload) == [], f"a marker reached {ev.type}"
            inspected += 1
    # Without this the loop above passes by never running — the replay half of
    # this test is the half that would rot silently.
    assert inspected >= 1, "no replay message payload was emitted to check"
    print(f"✅ {len(placed)} markers on the wire, none in history or replay")


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


def test_the_budget_is_three_leaving_one_slot_for_automatic_caching():
    assert MAX_EXPLICIT_BREAKPOINTS == 3
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    tools = [{"type": "function", "function": {"name": "f"}}]
    out_messages, out_tools = apply_cache_control(messages, tools, EPHEMERAL)
    assert len(_markers(out_messages, out_tools)) == MAX_EXPLICIT_BREAKPOINTS
    print("✅ Exactly three, fourth slot reserved")


def test_markers_the_caller_already_placed_count_against_the_budget():
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": [
            {"type": "text", "text": "u1", "cache_control": EPHEMERAL},
        ]},
        {"role": "assistant", "content": "a1"},
    ]
    tools = [{"type": "function", "function": {"name": "f"}}]
    out_messages, out_tools = apply_cache_control(messages, tools, EPHEMERAL)
    assert len(_markers(out_messages, out_tools)) == MAX_EXPLICIT_BREAKPOINTS

    # Already at the ceiling → nothing is added, and nothing is mutated.
    full = [
        {"role": "system", "content": [
            {"type": "text", "text": "S", "cache_control": EPHEMERAL},
        ]},
        {"role": "user", "content": [
            {"type": "text", "text": "u1", "cache_control": EPHEMERAL},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "a1", "cache_control": EPHEMERAL},
        ]},
    ]
    before = copy.deepcopy(full)
    out_full, _ = apply_cache_control(full, None, EPHEMERAL)
    assert len(_markers(out_full)) == MAX_EXPLICIT_BREAKPOINTS
    assert full == before
    print("✅ Caller markers count, and the ceiling holds")


def test_a_short_budget_keeps_the_most_covering_breakpoint():
    """Anthropic orders a prompt tools → system → messages, so the end-of-history
    breakpoint covers everything and is the one to keep."""
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u1"},
        # Two markers already placed elsewhere leave room for exactly one.
        {"role": "assistant", "content": [
            {"type": "text", "text": "a0", "cache_control": EPHEMERAL},
        ]},
        {"role": "user", "content": [
            {"type": "text", "text": "u0", "cache_control": EPHEMERAL},
        ]},
        {"role": "assistant", "content": "a1"},
    ]
    out_messages, _ = apply_cache_control(messages, None, EPHEMERAL)
    added = [m for m in _markers(out_messages) if m[0] == "messages[4].content[0]"]
    assert added, _markers(out_messages)
    assert "cache_control" not in str(out_messages[0])
    print("✅ Short budget spent on the end of history")


# ---------------------------------------------------------------------------
# The summarizer is deliberately not cached
# ---------------------------------------------------------------------------


def test_the_summarizer_call_carries_no_breakpoints():
    """``ContextManager`` summarization calls ``llm_client.chat(...)`` without a
    boundary: a one-shot prompt would pay the cache-write premium for a prefix
    nothing reads back. Marking is opted into per call, not per client."""
    client = _client(prompt_cache="anthropic")
    messages = [
        {"role": "system", "content": "summarize this"},
        {"role": "user", "content": "transcript"},
    ]
    kwargs = client._build_request_kwargs(messages, None, 100, stream=False)
    assert _markers(kwargs["messages"]) == []
    print("✅ No boundary passed, no markers")


def test_an_endpoint_change_drops_the_markers_and_a_key_rotation_keeps_them():
    """``prompt_cache`` asserts something about *one* endpoint. A new base URL is
    a new deployment that the assertion does not cover, and there is no latch
    here — an endpoint that 400s on the key would 400 on every request until
    someone noticed. A bare credential rotation changes neither."""
    client = _client(prompt_cache="anthropic", prompt_cache_ttl="1h")
    assert client.cache_control is not None

    # Key rotation, same endpoint: kept.
    client.reconfigure(api_key="k2")
    assert client.cache_control == {"type": "ephemeral", "ttl": "1h"}

    # New endpoint: dropped, along with the configured spellings a sub-agent
    # would otherwise inherit and re-enable for the new deployment.
    client.reconfigure(api_key="k2", base_url="https://other.test/v1")
    assert client.cache_control is None
    assert client.prompt_cache is None
    assert client.prompt_cache_ttl is None

    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}]
    kwargs = client._build_request_kwargs(
        messages, None, 100, stream=False, cache_boundary=0,
    )
    assert _markers(kwargs["messages"]) == []
    print("✅ Markers dropped on an endpoint change, kept on a key rotation")


def test_a_sub_agent_inherits_the_endpoint_s_prompt_cache_posture():
    """Same endpoint, same posture — the reason ``extra_body`` is inherited.
    Read off ``_llm_config``, the live snapshot the sub-agent factory builds
    from, so a runtime model switch does not drop it either."""
    from agentao import Agentao

    agent = Agentao(
        api_key="k", base_url="https://example.test/v1", model="test-model",
        working_directory=Path.cwd(), prompt_cache="anthropic",
        prompt_cache_ttl="1h",
    )
    try:
        cfg = agent._llm_config
        assert cfg["prompt_cache"] == "anthropic"
        assert cfg["prompt_cache_ttl"] == "1h"
        child = _client(
            prompt_cache=cfg["prompt_cache"],
            prompt_cache_ttl=cfg["prompt_cache_ttl"],
        )
        assert child.cache_control == {"type": "ephemeral", "ttl": "1h"}
    finally:
        agent.close()
    print("✅ Sub-agent inherits prompt_cache / prompt_cache_ttl")


def test_the_env_knob_reaches_the_client():
    import os
    from agentao.embedding.factory import discover_llm_kwargs

    old = {k: os.environ.get(k) for k in ("LLM_PROMPT_CACHE", "LLM_PROMPT_CACHE_TTL")}
    try:
        os.environ["LLM_PROMPT_CACHE"] = "anthropic"
        os.environ["LLM_PROMPT_CACHE_TTL"] = "1h"
        discovered = discover_llm_kwargs()
        assert discovered["prompt_cache"] == "anthropic"
        assert discovered["prompt_cache_ttl"] == "1h"
        # An empty value is "unset", the usual disable-this-var idiom.
        os.environ["LLM_PROMPT_CACHE"] = "  "
        assert "prompt_cache" not in discover_llm_kwargs()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("✅ LLM_PROMPT_CACHE / _TTL discovered")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
