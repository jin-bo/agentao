"""A sub-agent that never answered must not be reported as a success.

``TurnOutcome`` (PR #126) exists because the bare result string cannot be
told apart from a real answer. The sub-agent wrapper is one layer down
from that contract and was ignoring it: it returned the child's text
verbatim, stapled a productivity footer ("8 turns, 12 tool calls") onto
it, and emitted ``phase="completed"`` on any run that did not raise — so
both the parent LLM and a host subscribed to ``SubagentLifecycleEvent``
were told a non-answer had succeeded.

Budget exhaustion gets its own check: ``max_iterations`` is a separate
axis from ``incomplete_reason`` by design, and sub-agents are handed a
*smaller* budget than the parent, so it is their most likely way to stop
short.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentao.agents.tools._wrapper import (
    _MAX_ITERATIONS_REASON,
    AgentToolWrapper,
    _classify_subagent_outcome,
)
from agentao.runtime.outcome import TurnOutcome


def _outcome(**kw) -> TurnOutcome:
    base = dict(
        text="some text", status="ok", incomplete_reason=None, tool_count=1,
    )
    base.update(kw)
    return TurnOutcome(**base)


def _classify(**kw):
    args = dict(
        outcome=_outcome(),
        task_complete=False,
        max_iterations_hit=False,
        max_turns=15,
    )
    args.update(kw)
    return _classify_subagent_outcome(**args)


class TestClassification:
    def test_real_answer_is_not_flagged(self):
        assert _classify() is None

    def test_task_complete_signal_wins_over_everything(self):
        """``complete_task`` is the agent declaring it is done."""
        result = _classify(
            outcome=_outcome(text="[No response]", incomplete_reason="no_output"),
            task_complete=True,
            max_iterations_hit=True,
        )
        assert result is None

    def test_budget_exhaustion_is_reported(self):
        result = _classify(max_iterations_hit=True, max_turns=7)
        assert result is not None
        assert result.reason == _MAX_ITERATIONS_REASON
        assert "7-turn budget" in result.detail

    def test_missing_outcome_is_not_treated_as_failure(self):
        """Absence of evidence is not evidence of failure."""
        assert _classify(outcome=None) is None

    def test_each_incomplete_reason_is_rendered(self):
        for reason in (
            "no_output",
            "reasoning_only",
            "length_truncated",
            "doom_loop",
            "llm_error",
        ):
            result = _classify(outcome=_outcome(incomplete_reason=reason))
            assert result is not None, reason
            assert result.reason == reason
            # Rendered for the parent LLM, not echoed as the wire token.
            assert result.detail and reason not in result.detail

    def test_unknown_reason_still_surfaces(self):
        result = _classify(outcome=_outcome(incomplete_reason="future_reason"))
        assert result is not None
        assert "future_reason" in result.detail

    def test_cancelled_status_is_reported(self):
        result = _classify(outcome=_outcome(status="cancelled"))
        assert result is not None
        assert result.reason == "cancelled"

    def test_error_status_is_reported(self):
        result = _classify(outcome=_outcome(status="error", error="boom"))
        assert result is not None
        assert result.reason == "error"


class TestResultFormatting:
    def _stats(self, incomplete=None):
        return {
            "agent_name": "code-explorer",
            "turns": 8,
            "tool_calls": 12,
            "tokens": 15000,
            "duration_ms": 4200,
            "incomplete": incomplete,
        }

    def test_successful_result_keeps_the_plain_shape(self):
        out = AgentToolWrapper._format_result("the answer", self._stats())
        assert out.startswith("the answer")
        assert "code-explorer: 8 turns" in out
        assert "did not finish" not in out

    def test_incomplete_result_is_labelled_before_the_footer(self):
        """The notice must precede the text — a footer alone reads as success."""
        incomplete = _classify(outcome=_outcome(incomplete_reason="doom_loop"))
        out = AgentToolWrapper._format_result("partial findings", self._stats(incomplete))

        assert out.index("did not finish") < out.index("partial findings")
        assert "Partial result:" in out
        assert "partial findings" in out

    def test_empty_response_placeholder_is_not_called_a_partial_result(self):
        """``[No response]`` is the harness's placeholder, not child output."""
        incomplete = _classify(outcome=_outcome(incomplete_reason="no_output"))
        out = AgentToolWrapper._format_result("[No response]", self._stats(incomplete))

        assert "Partial result:" not in out
        assert "did not finish" in out

    def test_blank_result_is_not_called_a_partial_result(self):
        incomplete = _classify(outcome=_outcome(incomplete_reason="no_output"))
        out = AgentToolWrapper._format_result("   ", self._stats(incomplete))
        assert "Partial result:" not in out

    def test_stats_footer_survives_on_the_incomplete_path(self):
        """Telemetry is still useful — it just must not stand alone."""
        incomplete = _classify(max_iterations_hit=True)
        out = AgentToolWrapper._format_result("stuff", self._stats(incomplete))
        assert "code-explorer: 8 turns, 12 tool calls" in out


# ---------------------------------------------------------------------------
# Code-review follow-ups: the guards must fire on real data, not just when
# the classifier is called directly with the right flags.
# ---------------------------------------------------------------------------


class TestTaskCompleteDetection:
    """``TaskComplete`` never propagates out of ``chat()``.

    ``ToolExecutor._execute_one`` catches it and converts it to an ordinary
    tool result, so the wrapper's ``except TaskComplete`` is dead and the
    "explicit completion wins" guard has to key off history instead.
    """

    def test_finds_the_payload_from_history(self):
        from agentao.agents.tools._wrapper import _find_task_complete_result

        agent = SimpleNamespace(messages=[
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "name": "complete_task",
             "content": "the finished analysis"},
        ])
        assert _find_task_complete_result(agent) == "the finished analysis"

    def test_returns_none_when_never_called(self):
        from agentao.agents.tools._wrapper import _find_task_complete_result

        agent = SimpleNamespace(messages=[
            {"role": "tool", "tool_call_id": "c1", "name": "read_file",
             "content": "file body"},
        ])
        assert _find_task_complete_result(agent) is None

    def test_takes_the_last_call(self):
        from agentao.agents.tools._wrapper import _find_task_complete_result

        agent = SimpleNamespace(messages=[
            {"role": "tool", "tool_call_id": "c1", "name": "complete_task",
             "content": "first"},
            {"role": "tool", "tool_call_id": "c2", "name": "complete_task",
             "content": "second"},
        ])
        assert _find_task_complete_result(agent) == "second"

    def test_survives_malformed_history(self):
        from agentao.agents.tools._wrapper import _find_task_complete_result

        assert _find_task_complete_result(SimpleNamespace(messages=None)) is None
        assert _find_task_complete_result(SimpleNamespace()) is None
        assert _find_task_complete_result(
            SimpleNamespace(messages=["junk", None, {}])
        ) is None


class TestHarnessNoticesAreNotAttributedToTheSubAgent:
    """Only real child output may be labelled "Partial result"."""

    def _stats(self, incomplete):
        return {
            "agent_name": "explorer", "turns": 3, "tool_calls": 4,
            "tokens": 900, "duration_ms": 100, "incomplete": incomplete,
        }

    @pytest.mark.parametrize("notice", [
        "[No response]",
        "[LLM API error: Connection reset by peer]",
        "Maximum tool call iterations reached.",
        "   ",
    ])
    def test_harness_notices_are_not_partial_results(self, notice):
        incomplete = _classify(outcome=_outcome(incomplete_reason="llm_error"))
        out = AgentToolWrapper._format_result(notice, self._stats(incomplete))
        assert "Partial result:" not in out
        assert "did not finish" in out

    def test_real_child_text_is_still_labelled(self):
        incomplete = _classify(outcome=_outcome(incomplete_reason="doom_loop"))
        out = AgentToolWrapper._format_result(
            "I found three call sites", self._stats(incomplete)
        )
        assert "Partial result:" in out
        assert "I found three call sites" in out


class TestBackgroundIncompleteResultsRemainReachable:
    def test_failed_status_still_surfaces_the_stored_result(self):
        """Incomplete background runs are stored ``failed`` but keep their work.

        The ``failed`` branch previously returned only ``error``, so routing
        incomplete runs there would have silently discarded everything a
        long background sub-agent produced.
        """
        from agentao.agents.bg_store import BackgroundTaskStore
        from agentao.agents.tools._bg_tools import CheckBackgroundAgentTool

        store = BackgroundTaskStore()
        store.register("bg1", "researcher", "dig through the repo")
        store.update(
            "bg1", status="failed",
            result="[researcher did not finish: budget]\nPartial result:\nfindings here",
            error="it used its entire 15-turn budget without finishing",
        )

        out = CheckBackgroundAgentTool(store).execute(agent_id="bg1")

        assert "findings here" in out
        assert "budget" in out
