"""Regression tests for microcompact_messages() in ContextManager."""

from unittest.mock import Mock

import pytest

from agentao.context_manager import ContextManager

_LIMIT = ContextManager.MICROCOMPACT_TOOL_LIMIT  # 3_000
_PRESERVE = ContextManager.MICROCOMPACT_PRESERVE_RECENT  # 5


def _cm():
    cm = ContextManager(llm_client=Mock(), memory_tool=Mock(), max_tokens=200_000)
    cm._encoding = None  # force heuristic, no tiktoken needed
    return cm


def _tool_msg(content: str) -> dict:
    return {"role": "tool", "tool_call_id": "x", "content": content}


def _user_msg(content: str) -> dict:
    return {"role": "user", "content": content}


def _assistant_msg(content: str) -> dict:
    return {"role": "assistant", "content": content}


# ---------------------------------------------------------------------------
# Core truncation behaviour
# ---------------------------------------------------------------------------

def test_old_large_result_is_truncated():
    large = "A" * (_LIMIT + 500)
    messages = [_tool_msg(large)] + [_tool_msg("short") for _ in range(_PRESERVE)]
    result = _cm().microcompact_messages(messages)
    assert len(result[0]["content"]) < len(large)
    assert "omitted by microcompact" in result[0]["content"]


def test_omission_marker_contains_char_count():
    large = "B" * (_LIMIT * 2)
    messages = [_tool_msg(large)] + [_tool_msg("s") for _ in range(_PRESERVE)]
    result = _cm().microcompact_messages(messages)
    omitted = len(large) - _LIMIT
    assert f"{omitted:,}" in result[0]["content"]


def test_preserves_last_n_tool_results_at_full_fidelity():
    large = "C" * (_LIMIT + 500)
    # First msg is old; last _PRESERVE are recent
    messages = [_tool_msg("old")] + [_tool_msg(large) for _ in range(_PRESERVE)]
    result = _cm().microcompact_messages(messages)
    # Recent ones should be untouched
    for msg in result[1:]:
        assert msg["content"] == large


def test_short_results_not_mutated():
    short = "x" * (_LIMIT - 1)
    messages = [_tool_msg(short)] + [_tool_msg("y") for _ in range(_PRESERVE)]
    result = _cm().microcompact_messages(messages)
    assert result[0]["content"] == short


def test_non_tool_messages_untouched():
    large_text = "Z" * (_LIMIT * 3)
    messages = [
        _user_msg(large_text),
        _assistant_msg(large_text),
        _tool_msg("short"),
    ]
    result = _cm().microcompact_messages(messages)
    assert result[0]["content"] == large_text
    assert result[1]["content"] == large_text


# ---------------------------------------------------------------------------
# Head/tail split ratio
# ---------------------------------------------------------------------------

def test_head_tail_split_ratio():
    # Content that's exactly 2× the limit so we can measure precisely
    content = "H" * _LIMIT + "T" * _LIMIT
    messages = [_tool_msg(content)] + [_tool_msg("s") for _ in range(_PRESERVE)]
    result = _cm().microcompact_messages(messages)
    truncated = result[0]["content"]
    expected_head = int(_LIMIT * ContextManager.MICROCOMPACT_HEAD_RATIO)
    assert truncated.startswith("H" * expected_head)
    expected_tail = _LIMIT - expected_head
    assert truncated.endswith("T" * expected_tail)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

def test_returns_new_list_not_mutating_original():
    large = "M" * (_LIMIT + 100)
    original = [_tool_msg(large)] + [_tool_msg("s") for _ in range(_PRESERVE)]
    original_content = original[0]["content"]
    _cm().microcompact_messages(original)
    assert original[0]["content"] == original_content


def test_empty_messages_returns_empty():
    assert _cm().microcompact_messages([]) == []
