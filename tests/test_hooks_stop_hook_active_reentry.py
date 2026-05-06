"""``stop_hook_active`` payload field — true on re-entries.

The wiring sets ``stop_hook_active = (self._stop_reentries > 0)``: the
first dispatch in a chat() invocation reports ``False``; subsequent
dispatches after a force_continue report ``True``. Reset at the start
of a fresh chat() (ChatLoopRunner is instantiated fresh each turn).
"""

from __future__ import annotations

from agentao.cancellation import CancellationToken
from agentao.plugins.models import StopHookResult
from agentao.runtime.chat_loop import ChatLoopRunner

from tests.support.stop_precompact import make_bare_agent, make_runner_with_stub_llm


def test_stop_hook_active_flips_false_to_true_across_reentries(tmp_path, monkeypatch):
    runner, _transport, _agent = make_runner_with_stub_llm(
        tmp_path, monkeypatch, stop_reentry_cap=3,
    )

    captured: list[bool] = []

    def spying_dispatch(*, turn_end_reason, last_assistant_message):
        # Mirror the wiring's ``self._stop_reentries > 0`` formula.
        captured.append(runner._stop_reentries > 0)
        if len(captured) < 3:
            return StopHookResult(
                force_continue=True,
                follow_up_message="keep going",
                stop_reason="keep going",
                matched_rule_count=1,
            )
        return StopHookResult(matched_rule_count=1)

    monkeypatch.setattr(runner, "_dispatch_stop", spying_dispatch)

    runner.run("hi", max_iterations=10, token=CancellationToken())

    # Three dispatches: fresh (False), first re-entry (True), second
    # re-entry (True).
    assert captured == [False, True, True]


def test_stop_hook_active_resets_per_chat_invocation(tmp_path):
    """A fresh ``ChatLoopRunner`` (constructed per ``chat()`` call)
    starts with ``_stop_reentries == 0``."""
    agent = make_bare_agent(tmp_path)
    agent._plugin_hook_rules = []

    runner_a = ChatLoopRunner(agent)
    runner_a._stop_reentries = 5  # simulate prior in-turn re-entries
    assert runner_a._stop_reentries == 5

    runner_b = ChatLoopRunner(agent)
    assert runner_b._stop_reentries == 0
