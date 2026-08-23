"""Regression tests for microcompact_messages() in ContextManager."""

from unittest.mock import Mock


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
    """The count is what actually fell out, and the whole result fits the limit.

    The notice is budgeted *inside* ``MICROCOMPACT_TOOL_LIMIT`` rather than
    appended on top, so the retained content is ``limit - len(notice)`` and the
    omitted figure is measured against that. Appending it made the output
    longer than the limit that produced it, which re-selected the result on
    every later pass — see ``test_microcompaction_is_a_fixed_point``.
    """
    large = "B" * (_LIMIT * 2)
    messages = [_tool_msg(large)] + [_tool_msg("s") for _ in range(_PRESERVE)]
    result = _cm().microcompact_messages(messages)
    body = result[0]["content"]
    assert len(body) <= _LIMIT
    kept_b = body.count("B")
    assert f"{len(large) - kept_b:,}" in body


def test_microcompaction_is_a_fixed_point():
    """A second pass over already-microcompacted content must change nothing.

    It used to change plenty: the output was ``limit + len(notice)`` chars, so
    it stayed over the limit forever. Pass 2 cut the honest
    ``197,020 chars omitted`` notice out of the middle and wrote ``45`` in its
    place; every pass after that reported ``40``. It also pinned
    ``microcompact_would_mutate()`` at True for the whole 55-65% band, so the
    no-op stand-down that exists to stop per-iteration PreCompact subprocesses
    never fired, and the token anchor was invalidated every iteration.
    """
    cm = _cm()
    original = "START\n" + "x" * 200_000 + "\nEND"
    msgs = [_tool_msg(original)] + [_tool_msg("s") for _ in range(_PRESERVE)]
    once = cm.microcompact_messages(msgs)
    assert cm.last_microcompact_mutated is True
    honest = once[0]["content"]
    notice = ContextManager._OMISSION_NOTICE.search(honest)
    retained = len(honest) - len(notice.group(0)) - 2  # the notice's own newlines
    reported = int(notice.group(1).replace(",", ""))
    assert reported == len(original) - retained, "the count must be what fell out"
    assert reported > 190_000, "not the ~45 a re-clip of its own output reported"

    twice = cm.microcompact_messages(once)
    assert cm.last_microcompact_mutated is False, "second pass must be a no-op"
    assert cm.microcompact_would_mutate(once) is False
    assert twice[0]["content"] == honest


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
    # The ratio applies to the space left after reserving the notice, not to
    # the raw limit — the notice lives inside the budget.
    avail = truncated.count("H") + truncated.count("T")
    assert len(truncated) <= _LIMIT
    expected_head = int(avail * ContextManager.MICROCOMPACT_HEAD_RATIO)
    assert truncated.startswith("H" * expected_head)
    assert truncated.endswith("T" * (avail - expected_head))


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
