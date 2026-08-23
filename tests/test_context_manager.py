"""Test ContextManager: token estimation, compression, and memory recall."""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_llm(response_text: str = "[]"):
    """Create a mock LLMClient that returns response_text."""
    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.model = "test-model"

    mock_choice = Mock()
    mock_choice.message.content = response_text
    mock_choice.message.tool_calls = None
    mock_response = Mock()
    mock_response.choices = [mock_choice]
    mock_llm.chat.return_value = mock_response
    return mock_llm


def _make_memory_tool(tmp_path):
    from agentao.tools.memory import SaveMemoryTool
    from tests.support.memory import make_memory_manager
    mgr = make_memory_manager(tmp_path)
    return SaveMemoryTool(memory_manager=mgr)


def _make_messages(n: int) -> list:
    """Create n alternating user/assistant messages with some text."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Message number {i}. " * 20})
    return msgs


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def test_estimate_tokens_empty():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    assert cm.estimate_tokens([]) == 0


def test_estimate_tokens_string_content():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    msgs = [{"role": "user", "content": "a" * 400}]
    assert cm.estimate_tokens(msgs) == 100  # 400 / 4 = 100


def test_estimate_tokens_multiple_messages():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    msgs = [
        {"role": "user", "content": "a" * 400},
        {"role": "assistant", "content": "b" * 800},
    ]
    assert cm.estimate_tokens(msgs) == 300  # (400+800) / 4


def test_estimate_tokens_list_content():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    msgs = [{"role": "user", "content": [{"type": "text", "text": "x" * 400}]}]
    assert cm.estimate_tokens(msgs) == 100


def test_estimate_tokens_tool_calls():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "test", "arguments": "{}"}}],
        }
    ]
    result = cm.estimate_tokens(msgs)
    assert result >= 0  # Should not raise; tool_calls chars are counted


# ---------------------------------------------------------------------------
# Compression threshold
# ---------------------------------------------------------------------------

def test_needs_compression_false_below_threshold():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=100_000)
    # 10 msgs * 50 chars = 500 chars / 4 = 125 tokens = 0.125% of 100K
    msgs = [{"role": "user", "content": "x" * 50} for _ in range(10)]
    assert cm.needs_compression(msgs) is False


def test_needs_compression_true_above_threshold():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    # 2000 msgs * 4 chars = 8000 chars / 4 = 2000 tokens >> 1000 * 0.8
    msgs = [{"role": "user", "content": "abcd"} for _ in range(2_000)]
    assert cm.needs_compression(msgs) is True


# ---------------------------------------------------------------------------
# Tier-1 anchored threshold estimate
# ---------------------------------------------------------------------------

def test_threshold_uses_tier1_anchor_not_full_reencode():
    """When a fresh anchor exists, the threshold check must reuse the real
    prefix count and only estimate the tail — never re-encode the whole list."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    # Prefix is locally tiny (3 tokens) but the API reported it as 600 tokens.
    msgs = [{"role": "user", "content": "x" * 4} for _ in range(3)]
    cm.record_api_usage(600, message_count=3)
    # Append one tail message: 240 chars / 4 = 60 tokens. anchor+tail = 660.
    msgs.append({"role": "assistant", "content": "x" * 240})

    # estimate_tokens (full re-encode) must NOT be consulted on the hot path.
    cm.estimate_tokens = Mock(side_effect=AssertionError("full re-encode on hot path"))
    # 660 > 1000 * 0.65 (650) -> compress. A full local estimate would be
    # ~3 + 60 = 63 and would (wrongly) return False, so True proves the anchor.
    assert cm.needs_compression(msgs) is True


def test_threshold_falls_back_to_full_estimate_without_anchor():
    """No anchor recorded -> full local estimate is used."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    msgs = [{"role": "user", "content": "x" * 4} for _ in range(3)]
    msgs.append({"role": "assistant", "content": "x" * 240})
    # No record_api_usage call -> anchor is None -> full estimate (~63 tokens).
    assert cm.needs_compression(msgs) is False
    assert cm._threshold_token_estimate(msgs) == cm.estimate_tokens(msgs)


def test_invalidate_token_anchor_forces_full_estimate():
    """After history is mutated in place, the anchor is dropped and the
    threshold falls back to a full local estimate."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    msgs = [{"role": "user", "content": "x" * 4} for _ in range(3)]
    cm.record_api_usage(600, message_count=3)
    msgs.append({"role": "assistant", "content": "x" * 240})
    assert cm.needs_compression(msgs) is True  # anchor active

    cm.invalidate_token_anchor()
    assert cm._last_api_prompt_tokens is None
    assert cm._api_anchor_msg_count is None
    # Now the stale 600 is gone; full estimate (~63) is below threshold.
    assert cm.needs_compression(msgs) is False


def test_record_api_usage_legacy_no_count_clears_anchor():
    """Legacy callers passing only prompt_tokens must not engage the anchor
    (no message_count -> full estimate path stays in effect)."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    msgs = [{"role": "user", "content": "x" * 4} for _ in range(3)]
    msgs.append({"role": "assistant", "content": "x" * 240})
    cm.record_api_usage(600)  # no message_count
    assert cm._api_anchor_msg_count is None
    # Anchor not engaged -> full estimate (~63) -> below threshold.
    assert cm.needs_compression(msgs) is False


def test_threshold_anchor_count_exceeds_messages_falls_back():
    """If the anchored count is larger than the current list (e.g. history was
    trimmed), fall back to the full estimate rather than slice incorrectly."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    msgs = [{"role": "user", "content": "x" * 4} for _ in range(3)]
    cm.record_api_usage(600, message_count=10)  # count > len(msgs)
    # Falls back to full estimate (~3 tokens), not anchor.
    assert cm._threshold_token_estimate(msgs) == cm.estimate_tokens(msgs)


def test_threshold_guards_non_numeric_anchor():
    """A malformed provider usage field (non-int prompt_tokens) must not poison
    the threshold path; fall back to the full local estimate."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    msgs = [{"role": "user", "content": "x" * 4} for _ in range(3)]
    cm.record_api_usage(Mock(), message_count=3)  # garbage prompt_tokens
    # Must not raise; uses full estimate (~3 tokens).
    assert cm._threshold_token_estimate(msgs) == cm.estimate_tokens(msgs)
    assert cm.needs_compression(msgs) is False


def test_microcompaction_uses_anchored_threshold():
    """needs_microcompaction shares the anchored estimate (55-65% band)."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    msgs = [{"role": "user", "content": "x" * 4} for _ in range(3)]
    cm.record_api_usage(580, message_count=3)  # 58% via anchor
    cm.estimate_tokens = Mock(side_effect=AssertionError("full re-encode on hot path"))
    # 580 is in (550, 650] -> microcompaction warranted.
    assert cm.needs_microcompaction(msgs) is True


# ---------------------------------------------------------------------------
# Compression algorithm
# ---------------------------------------------------------------------------

def test_compress_messages_reduces_count():
    from agentao.context_manager import ContextManager

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"memories": []}')
        tmp = f.name

    try:
        memory_tool = _make_memory_tool(tmp)
        mock_llm = _make_mock_llm("Summary of the early conversation.")
        cm = ContextManager(mock_llm, memory_tool, max_tokens=200_000)

        original = _make_messages(20)
        compressed = cm.compress_messages(original)

        assert len(compressed) < len(original)
    finally:
        Path(tmp).unlink(missing_ok=True)


def test_compress_messages_prepends_summary_system_msg():
    from agentao.context_manager import ContextManager

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"memories": []}')
        tmp = f.name

    try:
        memory_tool = _make_memory_tool(tmp)
        mock_llm = _make_mock_llm("Important summary here.")
        cm = ContextManager(mock_llm, memory_tool, max_tokens=200_000)

        original = _make_messages(20)
        compressed = cm.compress_messages(original)

        # compressed[0] is the compact boundary marker; [1] is the summary
        assert compressed[0]["role"] == "system"
        assert "[Compact Boundary" in compressed[0]["content"]
        assert compressed[1]["role"] == "system"
        assert "[Conversation Summary]" in compressed[1]["content"]
    finally:
        Path(tmp).unlink(missing_ok=True)


def test_compress_messages_saves_summary_to_memory(tmp_path):
    from agentao.context_manager import ContextManager

    memory_tool = _make_memory_tool(tmp_path)
    mgr = memory_tool.memory_manager
    mock_llm = _make_mock_llm("This is a saved summary.")
    cm = ContextManager(mock_llm, memory_tool, max_tokens=200_000, memory_manager=mgr)

    original = _make_messages(20)
    cm.compress_messages(original)

    # Compaction summaries go to SQLite session_summaries table
    summaries = mgr.get_recent_session_summaries(limit=10)
    assert any("This is a saved summary." in s.summary_text for s in summaries)


def test_compress_messages_graceful_on_llm_error():
    from agentao.context_manager import ContextManager

    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.chat.side_effect = Exception("LLM unavailable")

    cm = ContextManager(mock_llm, Mock(), max_tokens=200_000)
    original = _make_messages(20)

    # Should return original messages unchanged on error
    result = cm.compress_messages(original)
    assert result == original


def test_compress_messages_too_few_messages():
    from agentao.context_manager import ContextManager

    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    original = _make_messages(3)

    # 3 messages is below minimum (5), should return as-is
    result = cm.compress_messages(original)
    assert result == original


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Usage stats
# ---------------------------------------------------------------------------

def test_get_usage_stats_structure():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=10_000)
    msgs = [{"role": "user", "content": "x" * 400}]
    stats = cm.get_usage_stats(msgs)

    assert "estimated_tokens" in stats
    assert "max_tokens" in stats
    assert "usage_percent" in stats
    assert "message_count" in stats
    assert "token_breakdown" in stats
    assert "token_count_source" in stats
    assert stats["max_tokens"] == 10_000
    assert stats["message_count"] == 1
    assert 0.0 <= stats["usage_percent"] <= 100.0


def test_get_usage_stats_correct_percent():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    cm._encoding = None  # force CJK-aware heuristic for deterministic count
    # 400 ASCII chars * 0.25 = 100 tokens = 10% of 1000
    msgs = [{"role": "user", "content": "x" * 400}]
    stats = cm.get_usage_stats(msgs)
    assert abs(stats["usage_percent"] - 10.0) < 0.1


def test_get_usage_stats_empty_messages():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    stats = cm.get_usage_stats([])
    assert stats["estimated_tokens"] == 0
    assert stats["message_count"] == 0
    assert stats["usage_percent"] == 0.0


def test_get_usage_stats_uses_api_tier1():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    cm.record_api_usage(9999)
    msgs = [{"role": "user", "content": "x" * 400}]
    stats = cm.get_usage_stats(msgs)
    assert stats["estimated_tokens"] == 9999
    assert stats["token_count_source"] == "api"
    # breakdown is always local estimate
    assert "token_breakdown" in stats


def test_get_usage_stats_local_when_no_api():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    cm._encoding = None
    msgs = [{"role": "user", "content": "x" * 400}]
    stats = cm.get_usage_stats(msgs)
    assert stats["token_count_source"] == "local"
    bd = stats["token_breakdown"]
    assert bd["total"] == stats["estimated_tokens"]


# ---------------------------------------------------------------------------
# Breakdown
# ---------------------------------------------------------------------------

def test_estimate_tokens_breakdown_structure():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    cm._encoding = None
    msgs = [
        {"role": "system", "content": "s" * 400},   # 100 tokens
        {"role": "user", "content": "u" * 800},     # 200 tokens
    ]
    bd = cm.estimate_tokens_breakdown(msgs)
    assert bd["system"] == 100
    assert bd["messages"] == 200
    assert bd["tools"] == 0
    assert bd["total"] == 300


def test_estimate_tokens_breakdown_with_tools():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    cm._encoding = None
    tools = [{"type": "function", "function": {"name": "t", "description": "d"}}]
    bd = cm.estimate_tokens_breakdown([], tools=tools)
    assert bd["tools"] > 0
    assert bd["total"] == bd["tools"]


# ---------------------------------------------------------------------------
# CJK heuristic
# ---------------------------------------------------------------------------

def test_heuristic_cjk_higher_than_ascii():
    from agentao.context_manager import _heuristic_token_count
    # "你好" (2 CJK chars at 1.3 each) >> "hi" (2 ASCII chars at 0.25 each)
    assert _heuristic_token_count("你好") > _heuristic_token_count("hi")


def test_heuristic_pure_ascii_equals_chars_over_4():
    from agentao.context_manager import _heuristic_token_count
    # For multiples of 4, should equal chars/4
    assert _heuristic_token_count("a" * 400) == 100
    assert _heuristic_token_count("x" * 800) == 200


# ---------------------------------------------------------------------------
# reasoning_content counted
# ---------------------------------------------------------------------------

def test_estimate_tokens_reasoning_content():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    cm._encoding = None
    msgs = [{"role": "assistant", "content": "", "reasoning_content": "r" * 400}]
    assert cm.estimate_tokens(msgs) == 100  # 400 * 0.25 = 100


# ---------------------------------------------------------------------------
# Tiktoken model mapping (skipped if tiktoken not installed)
# ---------------------------------------------------------------------------

def test_tiktoken_model_mapping():
    import pytest
    pytest.importorskip("tiktoken")
    from agentao.context_manager import _get_tiktoken_encoding
    assert _get_tiktoken_encoding("claude-sonnet-4-5") is not None   # cl100k_base
    assert _get_tiktoken_encoding("gpt-4") is not None               # cl100k_base
    assert _get_tiktoken_encoding("gpt-4o") is not None              # o200k_base
    assert _get_tiktoken_encoding("deepseek-chat") is not None       # cl100k_base
    assert _get_tiktoken_encoding("gemini-2.5-pro") is None          # no mapping
    assert _get_tiktoken_encoding("unknown-model-xyz") is None       # no mapping


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

def test_full_flow_compress_saves_to_memory(tmp_path):
    """Integration test: compress messages saves summary to SQLite via memory_manager."""
    from agentao.context_manager import ContextManager

    def mock_chat(**kwargs):
        mock_choice = Mock()
        mock_choice.message.content = "Early conversation summary."
        mock_choice.message.tool_calls = None
        mock_resp = Mock()
        mock_resp.choices = [mock_choice]
        return mock_resp

    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.chat = mock_chat

    memory_tool = _make_memory_tool(tmp_path)
    mgr = memory_tool.memory_manager
    cm = ContextManager(mock_llm, memory_tool, max_tokens=200_000, memory_manager=mgr)

    original = _make_messages(20)
    compressed = cm.compress_messages(original)
    assert len(compressed) < len(original)

    # Summaries go to SQLite session_summaries table
    summaries = mgr.get_recent_session_summaries(limit=10)
    assert any("Early conversation summary." in s.summary_text for s in summaries)


if __name__ == "__main__":
    print("Running ContextManager tests...")

    # Token estimation
    test_estimate_tokens_empty()
    test_estimate_tokens_string_content()
    test_estimate_tokens_multiple_messages()
    test_estimate_tokens_list_content()
    test_estimate_tokens_tool_calls()
    print("✓ Token estimation tests passed")

    # Compression threshold
    test_needs_compression_false_below_threshold()
    test_needs_compression_true_above_threshold()
    print("✓ Compression threshold tests passed")

    # Compression algorithm
    test_compress_messages_reduces_count()
    test_compress_messages_prepends_summary_system_msg()
    with tempfile.TemporaryDirectory() as _td2:
        test_compress_messages_saves_summary_to_memory(Path(_td2))
    test_compress_messages_graceful_on_llm_error()
    test_compress_messages_too_few_messages()
    print("✓ Compression algorithm tests passed")

    # Usage stats
    test_get_usage_stats_structure()
    test_get_usage_stats_correct_percent()
    test_get_usage_stats_empty_messages()
    test_get_usage_stats_uses_api_tier1()
    test_get_usage_stats_local_when_no_api()
    print("✓ Usage stats tests passed")

    # Breakdown
    test_estimate_tokens_breakdown_structure()
    test_estimate_tokens_breakdown_with_tools()
    print("✓ Breakdown tests passed")

    # CJK heuristic
    test_heuristic_cjk_higher_than_ascii()
    test_heuristic_pure_ascii_equals_chars_over_4()
    print("✓ CJK heuristic tests passed")

    # reasoning_content
    test_estimate_tokens_reasoning_content()
    print("✓ reasoning_content test passed")

    # Integration
    import tempfile
    with tempfile.TemporaryDirectory() as _td:
        test_full_flow_compress_saves_to_memory(Path(_td))
    print("✓ Integration test passed")

    print("\n✅ All ContextManager tests passed!")


# ---------------------------------------------------------------------------
# _format_for_summary — tool-call invocations (F1)
#
# The summary prompt asks for "Every file examined, created, or modified" and
# for error messages quoted verbatim. Both live in the tool *call* arguments,
# not in the tool result — and an assistant turn that only called tools stores
# ``content == ""``, so before this the whole message was dropped and the
# summarizer saw results with no invocation.
# ---------------------------------------------------------------------------

def _cm(max_tokens=200_000):
    from agentao.context_manager import ContextManager
    return ContextManager(_make_mock_llm(), Mock(), max_tokens=max_tokens)


def _tool_call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def test_format_for_summary_renders_tool_call_from_empty_content_assistant():
    """A pure tool-call assistant turn (content=="") must not vanish."""
    cm = _cm()
    out = cm._format_for_summary([
        {"role": "assistant", "content": "",
         "tool_calls": [_tool_call("read_file", '{"file_path": "/repo/agentao/agent.py"}')]},
        {"role": "tool", "name": "read_file", "content": "x" * 5_000},
    ])
    assert "[Tool Call - read_file]" in out
    assert "/repo/agentao/agent.py" in out, out
    assert "[Tool Result - read_file]" in out


def test_format_for_summary_keeps_short_args_when_one_value_is_huge():
    """A write_file body must not evict the file_path beside it.

    This is the whole point of parsing the JSON instead of truncating the raw
    blob: the path is the datum the summary needs, and it may be emitted
    after the oversized one.
    """
    cm = _cm()
    args = json.dumps({"content": "B" * 60_000, "file_path": "/repo/deep/nested/target.py"})
    out = cm._format_for_summary([
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("write_file", args)]},
    ])
    assert "/repo/deep/nested/target.py" in out, out
    assert "B" * 300 not in out          # body was clipped
    assert "chars)" in out               # omission marker present


def test_format_for_summary_renders_content_and_tool_calls_together():
    """An assistant turn with both prose and calls keeps both."""
    cm = _cm()
    out = cm._format_for_summary([
        {"role": "assistant", "content": "Let me check the config.",
         "tool_calls": [_tool_call("run_shell_command", '{"command": "pytest -q"}')]},
    ])
    assert "[ASSISTANT]: Let me check the config." in out
    assert "[Tool Call - run_shell_command]" in out
    assert "pytest -q" in out


def test_format_for_summary_tolerates_malformed_tool_calls():
    """Non-JSON args, non-dict entries and a missing function must not raise."""
    cm = _cm()
    out = cm._format_for_summary([
        {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("broken", "{not valid json"),
            "not-a-dict",
            {"id": "x", "type": "function"},          # no function key
            _tool_call("scalar_args", '"just-a-string"'),
        ]},
        {"role": "assistant", "content": "", "tool_calls": "not-a-list"},
    ])
    assert "[Tool Call - broken]: {not valid json" in out
    assert "[Tool Call - unknown]" in out
    assert "[Tool Call - scalar_args]" in out


def test_format_for_summary_caps_tool_calls_per_message():
    """A wide parallel-call turn is bounded, and says how much it dropped."""
    from agentao.context_manager import ContextManager
    cm = _cm()
    n = ContextManager._MAX_TOOL_CALLS_RENDERED + 3
    calls = [_tool_call("read_file", json.dumps({"file_path": f"/f{i}.py"}), f"c{i}")
             for i in range(n)]
    out = cm._format_for_summary([{"role": "assistant", "content": "", "tool_calls": calls}])
    assert out.count("[Tool Call - read_file]") == ContextManager._MAX_TOOL_CALLS_RENDERED
    assert "3 more tool call(s) omitted" in out


def test_format_for_summary_tool_messages_still_have_no_call_line():
    """role=='tool' short-circuits — a stray tool_calls key must not render."""
    cm = _cm()
    out = cm._format_for_summary([
        {"role": "tool", "name": "read_file", "content": "data",
         "tool_calls": [_tool_call("should_not_render", "{}")]},
    ])
    assert "should_not_render" not in out
    assert out == "[Tool Result - read_file]: data"


# ---------------------------------------------------------------------------
# F2 — split-point selection must not silently no-op
#
# The split used to *require* a 'user' boundary in the summarizable range.
# A long agentic turn routinely ends in 20 consecutive assistant/tool messages
# (~10 tool calls), which made compaction a permanent no-op: it returned the
# history unchanged, counted no failure, and the caller re-entered every
# iteration until the API rejected the context.
# ---------------------------------------------------------------------------

def _agentic_tail(n_calls):
    """assistant(tool_calls) + tool, repeated — no user message anywhere."""
    out = []
    for i in range(n_calls):
        out.append({"role": "assistant", "content": "",
                    "tool_calls": [_tool_call("read_file",
                                              json.dumps({"file_path": f"/f{i}.py"}), f"c{i}")]})
        out.append({"role": "tool", "name": "read_file", "content": f"body {i}"})
    return out


def test_find_split_index_prefers_user_boundary():
    from agentao.context_manager import ContextManager
    msgs = [{"role": "assistant", "content": "a"},
            {"role": "tool", "name": "t", "content": "r"},
            {"role": "user", "content": "next request"},
            {"role": "assistant", "content": "b"}]
    assert ContextManager._find_split_index(msgs, 1) == 2


def test_find_split_index_falls_back_to_non_tool_boundary():
    """No user message in range → still finds a safe (non-tool) split."""
    from agentao.context_manager import ContextManager
    msgs = [{"role": "user", "content": "go"}] + _agentic_tail(4)
    idx = ContextManager._find_split_index(msgs, 3)
    assert idx is not None
    assert msgs[idx]["role"] != "tool", msgs[idx]


def test_find_split_index_never_lands_on_a_tool_message():
    """Orphaned tool results are the one thing the split must never produce."""
    from agentao.context_manager import ContextManager
    msgs = [{"role": "user", "content": "go"}] + _agentic_tail(6)
    for start in range(len(msgs)):
        idx = ContextManager._find_split_index(msgs, start)
        if idx is not None:
            assert msgs[idx].get("role") != "tool", (start, idx)


def test_compress_messages_compacts_a_tail_with_no_user_message(tmp_path):
    """The regression itself: an all-assistant/tool tail must still compact."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm("SUMMARY TEXT"), _make_memory_tool(tmp_path),
                        max_tokens=200_000)
    msgs = [{"role": "user", "content": "start"}] + _agentic_tail(25)
    out = cm.compress_messages(msgs)
    assert len(out) < len(msgs), "compaction was a no-op"
    assert out[0]["content"].startswith("[Compact Boundary")
    assert cm._consecutive_compact_failures == 0


def test_compress_messages_no_safe_split_counts_a_failure():
    """A history that is nothing but tool messages trips the breaker."""
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm("S"), Mock(), max_tokens=200_000)
    msgs = [{"role": "tool", "name": "t", "content": f"r{i}"} for i in range(10)]
    for expected in (1, 2, 3):
        assert cm.compress_messages(msgs) == msgs
        assert cm._consecutive_compact_failures == expected
    assert cm.compaction_circuit_open is True


def test_circuit_open_property_tracks_the_limit():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)
    assert cm.compaction_circuit_open is False
    cm._consecutive_compact_failures = ContextManager.CIRCUIT_BREAKER_LIMIT - 1
    assert cm.compaction_circuit_open is False
    cm._consecutive_compact_failures = ContextManager.CIRCUIT_BREAKER_LIMIT
    assert cm.compaction_circuit_open is True


# ---------------------------------------------------------------------------
# F3 — microcompaction reports whether it actually shortened anything
# ---------------------------------------------------------------------------

def test_microcompact_reports_mutation():
    cm = _cm()
    big = [{"role": "tool", "name": "t", "content": "x" * 50_000} for _ in range(8)]
    cm.microcompact_messages(big)
    assert cm.last_microcompact_mutated is True


def test_microcompact_reports_no_mutation_when_nothing_oversized():
    """A fresh list is always returned, so identity cannot answer this."""
    cm = _cm()
    small = [{"role": "tool", "name": "t", "content": "short"} for _ in range(8)]
    out = cm.microcompact_messages(small)
    assert out is not small          # new list, as before
    assert cm.last_microcompact_mutated is False


# ---------------------------------------------------------------------------
# _format_for_summary — input budget (§3 P1)
#
# The 9-section prompt demands error messages "verbatim" and calls Files and
# Errors "the most important" sections, while the input pipeline used to clip
# every tool result to 200 chars and every message to 500. Measured against
# the saved sessions in ``.agentao/sessions``: 12% of tool-result content
# survived. These pin the replacement — content-tiered per-entry ceilings
# under one recency-ordered total budget.
# ---------------------------------------------------------------------------

def test_failing_tool_result_keeps_its_tail_where_the_error_actually_is():
    """A traceback sits at the END of a failing command's output.

    Head-only truncation would satisfy the larger failure budget and still
    drop the one string ``## 4. Errors and Fixes`` asks to be quoted verbatim.
    """
    cm = _cm()
    noise = "compiling module\n" * 900
    out = cm._clip_tool_result(noise + "Traceback (most recent call last):\nAssertionError: boom")
    assert out.endswith("AssertionError: boom")
    assert out.startswith("compiling module")
    assert "omitted" in out


def test_failure_marker_is_searched_past_the_plain_budget():
    """Most commands run fine for a while and fail at the end.

    Scanning only the first ``_TOOL_RESULT_TRUNCATION`` chars for a marker
    would file every one of those under the plain tier.
    """
    cm = _cm()
    text = "ok\n" * 2000 + "\nERROR: exit code 1"
    assert len(text) > cm._TOOL_RESULT_TRUNCATION
    assert len(cm._clip_tool_result(text)) > cm._TOOL_RESULT_TRUNCATION


def test_successful_tool_result_stays_on_the_plain_budget():
    cm = _cm()
    out = cm._clip_tool_result("x" * 50_000)
    assert out.startswith("x" * cm._TOOL_RESULT_TRUNCATION)
    assert not out.startswith("x" * (cm._TOOL_RESULT_TRUNCATION + 1))


def test_every_clip_tier_marks_the_cut():
    """An unmarked clip reads as a complete result.

    ``_clip_args`` already documents the failure one layer down: the summarizer
    cannot tell an amputated path or command from a whole one, and quotes it as
    fact. The plain tier used to cut silently.
    """
    cm = _cm()
    plain = cm._clip_tool_result("x" * 50_000)
    failing = cm._clip_tool_result("Traceback (most recent call last):\n" + "x" * 50_000)
    assert "49,000 chars omitted" in plain
    assert "chars omitted" in failing


def test_a_microcompacted_failure_passes_through_the_summary_clip_untouched():
    """The two head+tail clips must not land on the same boundary.

    ``compress_messages`` microcompacts the whole list before the transcript is
    built. When ``_ERROR_RESULT_TRUNCATION`` equalled ``MICROCOMPACT_TOOL_LIMIT``
    the second clip cut exactly where the first one had written
    ``[… 200,000 chars omitted by microcompact …]`` — deleting the only honest
    record of the loss and replacing it with a claim of ~45 chars.
    """
    from agentao.context_manager import ContextManager
    cm = _cm()
    original = "build\n" * 20 + "x" * 200_000 + "\nTraceback (most recent call last):\nboom"
    microcompacted = ContextManager._head_tail_clip(
        original, cm.MICROCOMPACT_TOOL_LIMIT, note="omitted by microcompact"
    )
    out = cm._clip_tool_result(microcompacted)
    assert out == microcompacted
    # Measured against the retained content, since the notice is budgeted
    # inside the limit rather than appended on top of it.
    kept = len(microcompacted) - len(cm._OMISSION_NOTICE.search(microcompacted).group(0)) - 2
    honest = f"{len(original) - kept:,} chars omitted by microcompact"
    assert honest in out, "the real omitted count must survive"


def test_failure_markers_do_not_fire_on_ordinary_source_code():
    """Over-tiering is not free: it takes a 4x share of a budget spent
    newest-first, so each mis-tiered success evicts *older* messages whole.

    The bare-word scan this replaced (``traceback|exception|\\berror\\b|…``)
    matched 169 of this repo's 272 source files — i.e. two thirds of ordinary
    ``read_file`` results, the single largest class of tool output measured.
    """
    from agentao.context_manager import ContextManager as CM
    benign = [
        'except Exception as e:\n    raise ValueError("bad")\n',
        '    ERROR = "error"   # enum member\n',
        '    Raises:\n        KeyError: with a descriptive message\n',
        '    error: Optional[Dict[str, Any]] = None\n',
    ]
    for text in benign:
        assert not CM._FAILURE_MARKERS.search(text), text
    diagnostics = [
        "Traceback (most recent call last):\nValueError: bad",
        "FAILED tests/test_a.py::test_b - AssertionError\n1 failed, 3 passed",
        "bash: foo: command not found\nExit code: 127",
        "cp: /etc/hosts: Permission denied",
        "fatal: not a git repository",
        "npm ERR! code E404",
        "curl: (7) Failed to connect: Connection refused",
        "x.py:1: error: Incompatible types\nFound 1 error in 1 file",
        "./main.go:5:2: undefined: foo\nexit status 1",
    ]
    for text in diagnostics:
        assert CM._FAILURE_MARKERS.search(text), text


def test_short_tool_result_is_returned_untouched():
    cm = _cm()
    assert cm._clip_tool_result("done") == "done"


def test_transcript_stays_within_the_token_budget():
    """Nothing bounded the transcript before; a tool-dense window could
    overflow the summarization call, and a failed summarization increments the
    circuit breaker — turning a fidelity fix into a compaction outage."""
    from agentao.context_manager import _heuristic_token_count
    cm = _cm(max_tokens=20_000)
    msgs = [{"role": "user", "content": "u" * 4_000} for _ in range(200)]
    out = cm._format_for_summary(msgs)
    # Budget plus the one elision line added after accounting.
    assert _heuristic_token_count(out) <= cm._summary_input_budget() + 100


def test_budget_drops_the_oldest_and_says_so():
    cm = _cm(max_tokens=20_000)
    msgs = [{"role": "user", "content": f"MARK{i} " + "u" * 4_000} for i in range(200)]
    out = cm._format_for_summary(msgs)
    assert "MARK199" in out, "newest message must survive"
    assert "MARK0" not in out, "oldest must be the one dropped"
    assert "earlier message(s) omitted" in out.split("\n")[0]


def test_survivors_are_contiguous_under_wildly_varying_message_sizes():
    """Allocation runs newest->oldest, so what survives must be a suffix.

    Randomised sizes on purpose. The first version of this test used uniform
    2KB messages and passed against an implementation that *skipped* a block
    too big to fit and kept spending on older ones — punching a hole in the
    middle of the transcript and handing the summarizer a history that omits a
    step without saying where. Only a mix containing the occasional giant
    exposes it, so the mix is the test.
    """
    import random
    from agentao.context_manager import _heuristic_token_count
    rng = random.Random(7)
    for trial in range(200):
        cm = _cm(max_tokens=rng.choice([8_000, 20_000, 60_000, 200_000]))
        n = rng.randint(1, 120)
        msgs = [
            {
                "role": rng.choice(["user", "assistant", "tool"]),
                "name": "read_file",
                "content": f"MARK{i:03d} " + "u" * rng.choice([10, 500, 9_000, 60_000]),
            }
            for i in range(n)
        ]
        out = cm._format_for_summary(msgs)
        seen = [i for i in range(n) if f"MARK{i:03d}" in out]
        assert seen, f"trial {trial}: empty transcript"
        assert seen == list(range(seen[0], n)), f"trial {trial}: gap at {seen[:5]}"
        assert _heuristic_token_count(out) <= cm._summary_input_budget() + 100


def test_a_single_oversized_message_still_produces_a_transcript():
    """An empty transcript summarizes to nothing, which counts as a failure
    and increments the circuit breaker.

    The block genuinely has to exceed the budget, which an ASCII message cannot
    do: ``_MESSAGE_TRUNCATION`` caps it at 2_000 chars = ~500 estimated tokens
    against a 2_000-token floor. CJK is charged 1.3 tok/char, so 2_000 Chinese
    characters cost ~2_600 — the first input that actually reaches the branch.
    """
    from agentao.context_manager import _heuristic_token_count
    cm = _cm(max_tokens=20_000)          # budget == the 2_000-token floor
    msgs = [{"role": "user", "content": "严重错误：无法打开该文件。" * 500}]
    out = cm._format_for_summary(msgs)
    assert _heuristic_token_count(out) > cm._summary_input_budget(), (
        "guard: this input must exercise the keep-the-newest-block-anyway branch"
    )
    assert out.strip()
    assert "[USER]" in out


def test_write_file_result_no_longer_gets_a_privileged_budget():
    """``write_file`` returns ``f"Successfully {action} {path}"`` — a
    confirmation bounded by a path length (measured median 114 chars). The
    1000-char carve-out it used to get was structurally unreachable, and the
    content it meant to preserve is in the call arguments.

    Asserted behaviourally: an ``hasattr`` check for the deleted constant
    passes for any misspelling of it, and would keep passing if the carve-out
    came back under a new name.
    """
    cm = _cm()
    out = cm._format_for_summary([
        {"role": "assistant", "content": "",
         "tool_calls": [_tool_call("write_file", json.dumps(
             {"file_path": "/repo/x.py", "content": "C" * 40_000}))]},
        {"role": "tool", "name": "write_file", "content": "y" * 50_000},
    ])
    assert "/repo/x.py" in out, "the path lives in the call args, not the result"
    body = out.split("[Tool Result - write_file]: ")[1]
    assert body.startswith("y" * cm._TOOL_RESULT_TRUNCATION)
    assert not body.startswith("y" * (cm._TOOL_RESULT_TRUNCATION + 1))


def test_a_carried_conversation_summary_is_never_evicted_by_the_budget():
    """It is the oldest block in the window and the only record of everything
    before the previous compaction.

    ``compress_messages`` returns ``[boundary, summary, …, recent]``, so plain
    newest-first spending drops the summary *first* — and sections 1 and 6 of
    the prompt ("every explicit goal the user stated", "all non-trivial user
    messages") describe content that by then lives nowhere else. Each further
    compaction would amputate the whole accumulated history.
    """
    cm = _cm(max_tokens=200_000)
    carry = {
        "role": "system",
        "content": (
            "[Conversation Summary]\n## 1. Primary Request and Intent\n"
            "SHIP THE PARSER REWRITE\n" + "detail line\n" * 400
            + cm.SUMMARY_END_MARKER + "\n(historical context)"
        ),
    }
    filler = []
    for i in range(200):
        filler.append({"role": "assistant", "content": "",
                       "tool_calls": [_tool_call("read_file", '{"path": "/a.py"}', f"c{i}")]})
        filler.append({"role": "tool", "name": "read_file", "content": "def f():\n" * 400})
    out = cm._format_for_summary([{"role": "system", "content": "[Compact Boundary]"}] + [carry] + filler)

    assert "SHIP THE PARSER REWRITE" in out, "the carried summary must survive"
    assert "earlier message(s) omitted" in out, "the window really did overflow"
    # …and it stays chronological: summary first, then the elision seam.
    assert out.index("SHIP THE PARSER REWRITE") < out.index("earlier message(s) omitted")
    # The carve-out is bounded so it cannot starve the live tail.
    assert cm.count_tokens_in_text(
        out.split("earlier message(s) omitted")[0]
    ) <= cm._summary_input_budget() // 2 + 100


# ---------------------------------------------------------------------------
# Double truncation: microcompact -> summary clip
#
# The live 55% pass rewrites history in place, so a result reaching
# ``_format_for_summary`` has usually been cut once already. The second cut
# used to report only its own slice, and ``compress_messages`` added a third
# by microcompacting the half it was about to discard.
# ---------------------------------------------------------------------------

def test_second_clip_reports_the_total_lost_not_just_its_own_slice():
    """A 200KB result clipped twice must not claim it lost 2,000 characters."""
    from agentao.context_manager import ContextManager
    cm = _cm()
    original = "setup\n" * 10 + "y" * 200_000 + "\ndone"
    once = ContextManager._head_tail_clip(
        original, cm.MICROCOMPACT_TOOL_LIMIT, note="omitted by microcompact"
    )
    twice = cm._clip_tool_result(once)
    reported = max(
        int(m.group(1).replace(",", ""))
        for m in ContextManager._OMISSION_NOTICE.finditer(twice)
    )
    assert reported > 190_000, f"reported {reported:,}, the real loss is ~199,000"


def test_compress_does_not_microcompact_the_half_it_is_about_to_discard():
    """Clipping the summarize window before summarizing it is pure loss.

    ``_format_for_summary`` applies its own content-aware budget — four times
    the room for a failure, and the tail kept where the diagnostic is. Cutting
    the same text to 3,000 chars first only takes that choice away, and it is
    the ``MICROCOMPACT_TOOL_LIMIT`` half of the double truncation.
    """
    cm = _cm()
    seen = {}
    real = cm.microcompact_messages

    def _spy(messages):
        seen["contents"] = [str(m.get("content", "")) for m in messages]
        return real(messages)

    old_result = "OLD-RESULT-" + "z" * 50_000
    msgs = (
        [{"role": "user", "content": "start"}]
        + [{"role": "tool", "name": "run_shell_command", "content": old_result}]
        + [{"role": "assistant", "content": f"step {i}"} for i in range(8)]
        + [{"role": "user", "content": "go on"}]
        + [{"role": "assistant", "content": f"more {i}"} for i in range(24)]
    )
    summarized = {}
    cm.microcompact_messages = _spy  # type: ignore[method-assign]

    def _capture(window):
        summarized["raw"] = "".join(str(x.get("content", "")) for x in window)
        return ""

    # ``_summarize_messages`` is what calls ``_format_for_summary``; stub it at
    # that level so the window it receives is observable.
    cm._summarize_messages = _capture  # type: ignore[method-assign]
    cm.compress_messages(msgs, is_auto=True)

    assert "contents" in seen, "microcompaction must still run on the kept half"
    assert not any(c.startswith("OLD-RESULT-") for c in seen["contents"]), (
        "the summarized half must never reach microcompaction"
    )
    assert len(seen["contents"]) < len(msgs)
    # And the summarizer sees the result at full length, not pre-clipped.
    assert len(summarized["raw"]) > cm.MICROCOMPACT_TOOL_LIMIT
    assert "OLD-RESULT-" in summarized["raw"]
