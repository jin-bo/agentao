"""A summary cut off at the token limit is refused, not committed (codex #53451).

Except on API overflow: the rung after that compaction keeps no summary at all.

The summary replaces history for good. A response that stopped at the token
limit is usually still inside ``<analysis>``; ``_format_summary`` then finds no
``<summary>`` block and keeps the unclosed scratchpad as the whole summary.
Everything after the cut was lost and the turn carried on as if it had worked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.context_manager import ContextManager


def _cm(finish_reason, content):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    message = SimpleNamespace(content=content)
    llm.chat.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        finish_reason_reported=True,
    )
    return ContextManager(llm, Mock(), max_tokens=200_000)


def _history(n=12):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"user {i} " + "x" * 200})
        out.append({"role": "assistant", "content": f"assistant {i} " + "y" * 200})
    return out


CUT_OFF = "<analysis>\nThe user asked for three things. First, the"


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "MAX_TOKENS", "model_length"])
def test_a_truncated_summary_fails_the_compaction(finish_reason):
    cm = _cm(finish_reason, CUT_OFF)
    history = _history()

    outcome = cm._run_compaction(history, is_auto=True, reason="compression_threshold")

    assert outcome.status == "failed"
    assert outcome.messages is history                  # history unchanged
    assert outcome.detail == "summary_truncated"
    assert cm.circuit_breaker_failures == 1             # counts like an empty one
    assert cm.last_compaction_failure == "summary_truncated"
    assert cm.last_summary_truncated is True


def test_a_manual_compact_refuses_it_too():
    cm = _cm("length", CUT_OFF)

    outcome = cm._run_compaction(_history(), is_auto=False, reason="manual_cli")

    assert outcome.status == "failed"
    assert outcome.detail == "summary_truncated"


def test_api_overflow_keeps_a_truncated_summary():
    """The next overflow rung is ``minimal_history``, which keeps no summary.

    Refusing the partial summary there would lose more than keeping it, so the
    overflow path commits it and logs a warning instead.
    """
    cm = _cm("length", CUT_OFF)

    outcome = cm._run_compaction(_history(), is_auto=True, reason="api_overflow")

    assert outcome.status == "success"
    assert cm.circuit_breaker_failures == 0
    assert cm.last_summary_truncated is True
    assert any("The user asked for three things" in str(m.get("content"))
               for m in outcome.messages)
    warned = " ".join(str(c.args[0]) for c in cm.llm_client.logger.warning.call_args_list)
    assert "kept on the API-overflow path" in warned


def test_a_complete_summary_still_commits():
    cm = _cm("stop", "<analysis>x</analysis><summary>## 1. Primary Request\nok</summary>")

    outcome = cm._run_compaction(_history(), is_auto=True, reason="compression_threshold")

    assert outcome.status == "success"
    assert cm.last_summary_truncated is False


def test_the_cli_names_the_truncation():
    from agentao.cli.commands.compact import _FAILURE_HINTS

    assert "cut off" in _FAILURE_HINTS["summary_truncated"]


def test_length_spellings_match_the_chat_loop():
    """One set: the chat loop reads the summarizer's (#53451)."""
    from agentao import context_manager
    from agentao.runtime.chat_loop import _runner

    assert context_manager._LENGTH_FINISH_REASONS == _runner._LENGTH_FINISH_REASONS
