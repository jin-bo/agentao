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

def _cm():
    from agentao.context_manager import ContextManager
    return ContextManager(_make_mock_llm(), Mock(), max_tokens=200_000)


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
