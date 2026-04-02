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


def _make_memory_tool(tmp_path: str):
    from agentao.tools.memory import SaveMemoryTool
    return SaveMemoryTool(memory_file=tmp_path)


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


def test_compress_messages_saves_summary_to_memory():
    from agentao.context_manager import ContextManager

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"memories": []}')
        tmp = f.name

    try:
        memory_tool = _make_memory_tool(tmp)
        mock_llm = _make_mock_llm("This is a saved summary.")
        cm = ContextManager(mock_llm, memory_tool, max_tokens=200_000)

        original = _make_messages(20)
        cm.compress_messages(original)

        saved = memory_tool.get_all_memories()
        summary_mems = [m for m in saved if "conversation_summary" in m.get("key", "")]
        assert len(summary_mems) >= 1
        assert "conversation_summary" in summary_mems[0].get("tags", [])
    finally:
        Path(tmp).unlink(missing_ok=True)


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
    assert stats["max_tokens"] == 10_000
    assert stats["message_count"] == 1
    assert 0.0 <= stats["usage_percent"] <= 100.0


def test_get_usage_stats_correct_percent():
    from agentao.context_manager import ContextManager
    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    # 400 chars / 4 = 100 tokens = 10% of 1000
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


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

def test_full_flow_compress_saves_to_memory():
    """Integration test: compress messages saves summary to memory tool."""
    from agentao.context_manager import ContextManager

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"memories": []}')
        tmp = f.name

    try:
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

        memory_tool = _make_memory_tool(tmp)
        cm = ContextManager(mock_llm, memory_tool, max_tokens=200_000)

        original = _make_messages(20)
        compressed = cm.compress_messages(original)
        assert len(compressed) < len(original)

        # Compression should have saved a summary memory
        saved = memory_tool.get_all_memories()
        assert any("conversation_summary" in m["key"] for m in saved)

    finally:
        Path(tmp).unlink(missing_ok=True)


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
    test_compress_messages_saves_summary_to_memory()
    test_compress_messages_graceful_on_llm_error()
    test_compress_messages_too_few_messages()
    print("✓ Compression algorithm tests passed")

    # Usage stats
    test_get_usage_stats_structure()
    test_get_usage_stats_correct_percent()
    test_get_usage_stats_empty_messages()
    print("✓ Usage stats tests passed")

    # Integration
    test_full_flow_compress_saves_to_memory()
    print("✓ Integration test passed")

    print("\n✅ All ContextManager tests passed!")
