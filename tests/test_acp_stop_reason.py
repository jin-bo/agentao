"""ACP ``stopReason`` mapping (G3).

Until 0.4.16 `session/prompt` could only answer ``end_turn`` or
``cancelled`` — its own TODO said the richer reasons were unavailable
because ``agent.chat()`` returned no structured termination metadata.
``TurnOutcome`` shipped in 0.4.15, so the blocker is gone.

What is asserted here is the *mapping*, including the cases that
deliberately stay ``end_turn``. A mapping is only meaningful if the
values it declines to produce are pinned too — otherwise a later change
that returns ``max_tokens`` for everything would still pass.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentao.acp.schema import AcpSessionPromptResponse
from agentao.acp.session_prompt import _stop_reason_for


def _outcome(reason=None):
    return SimpleNamespace(incomplete_reason=reason)


class TestBudgetReasonsMap:
    def test_length_truncated_is_max_tokens(self):
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome("length_truncated"),
            max_iterations_hit=False,
        ) == "max_tokens"

    def test_doom_loop_is_max_turn_requests(self):
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome("doom_loop"),
            max_iterations_hit=False,
        ) == "max_turn_requests"

    def test_iteration_budget_is_max_turn_requests(self):
        """Not an incomplete_reason -- it rides the transport flag."""
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome(None), max_iterations_hit=True,
        ) == "max_turn_requests"


class TestReasonsThatStayEndTurn:
    """These are decisions, not gaps -- pin them so they can't drift."""

    @pytest.mark.parametrize("reason", ["no_output", "reasoning_only"])
    def test_silent_turns_still_ended_normally(self, reason):
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome(reason), max_iterations_hit=False,
        ) == "end_turn"

    def test_llm_error_is_end_turn_not_refusal(self):
        """`refusal` means the agent declined on content grounds.

        Reporting an API outage as a refusal trades a vague answer for a
        false one. The error text is the turn's content and has already
        been streamed.
        """
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome("llm_error"),
            max_iterations_hit=False,
        ) == "end_turn"

    def test_healthy_turn(self):
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome(None), max_iterations_hit=False,
        ) == "end_turn"

    def test_unknown_future_reason_degrades_to_end_turn(self):
        """A new incomplete_reason must not crash an ACP turn."""
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome("some_future_reason"),
            max_iterations_hit=False,
        ) == "end_turn"

    def test_missing_outcome_degrades(self):
        assert _stop_reason_for(
            cancelled=False, outcome=None, max_iterations_hit=False,
        ) == "end_turn"


class TestPrecedence:
    def test_cancelled_beats_every_other_signal(self):
        """The client asked to stop; that outranks the turn's own state."""
        assert _stop_reason_for(
            cancelled=True, outcome=_outcome("length_truncated"),
            max_iterations_hit=True,
        ) == "cancelled"

    def test_iteration_budget_beats_incomplete_reason(self):
        """Both can be true; the cap is the more specific account."""
        assert _stop_reason_for(
            cancelled=False, outcome=_outcome("no_output"),
            max_iterations_hit=True,
        ) == "max_turn_requests"


class TestEveryEmittedValueIsSchemaValid:
    """A stopReason the frozen response model rejects is a wire break."""

    @pytest.mark.parametrize("kwargs", [
        dict(cancelled=True, outcome=None, max_iterations_hit=False),
        dict(cancelled=False, outcome=_outcome(None), max_iterations_hit=True),
        dict(cancelled=False, outcome=_outcome("length_truncated"), max_iterations_hit=False),
        dict(cancelled=False, outcome=_outcome("doom_loop"), max_iterations_hit=False),
        dict(cancelled=False, outcome=_outcome("no_output"), max_iterations_hit=False),
        dict(cancelled=False, outcome=_outcome("llm_error"), max_iterations_hit=False),
    ])
    def test_validates(self, kwargs):
        AcpSessionPromptResponse(stopReason=_stop_reason_for(**kwargs))

    def test_max_tokens_is_in_the_enum(self):
        """It was missing until 0.4.16 -- the local enum had drifted from
        ACP v1, so the schema could not express a token-limit stop even
        once the runtime could detect one."""
        AcpSessionPromptResponse(stopReason="max_tokens")

    def test_all_five_acp_v1_members_accepted(self):
        for v in ("end_turn", "cancelled", "max_tokens",
                  "max_turn_requests", "refusal"):
            AcpSessionPromptResponse(stopReason=v)

    def test_invented_value_rejected(self):
        with pytest.raises(Exception):
            AcpSessionPromptResponse(stopReason="ran_out_of_patience")


class TestTransportFlag:
    def test_acp_transport_records_iteration_exhaustion(self):
        """The signal cannot be recovered from TurnOutcome after the fact,
        so it has to be recorded as it happens."""
        from agentao.acp._transport_interaction import _InteractionMixin

        class T(_InteractionMixin):
            _session_id = "s1"
            _server = None

        t = T()
        assert t.max_iterations_hit is False, "must default to False"
        assert t.on_max_iterations(10, []) == {"action": "stop"}
        assert t.max_iterations_hit is True
