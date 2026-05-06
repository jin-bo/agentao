"""Stop hook re-entry cap.

A pathological hook that always returns ``force_continue`` must not
spin the chat loop forever. The cap defaults to 3 (constructor
parameter on ``ChatLoopRunner``); on hitting the cap the loop emits
``outcome="reentry_capped"`` and ends the turn.
"""

from __future__ import annotations

from agentao.cancellation import CancellationToken
from agentao.plugins.models import StopHookResult

from tests.support.stop_precompact import make_runner_with_stub_llm


def test_reentry_cap_terminates_pathological_hook(tmp_path, monkeypatch):
    runner, transport, _agent = make_runner_with_stub_llm(
        tmp_path, monkeypatch, stop_reentry_cap=2,
    )

    monkeypatch.setattr(
        runner, "_dispatch_stop",
        lambda *, turn_end_reason, last_assistant_message: StopHookResult(
            force_continue=True,
            follow_up_message="keep going",
            stop_reason="keep going",
            matched_rule_count=1,
        ),
    )

    result = runner.run("hi", max_iterations=10, token=CancellationToken())

    assert result == "final answer"
    # Cap=2 → expect two ``continue`` emits then one ``reentry_capped``.
    outcomes = [e.data["outcome"] for e in transport.hook_fired_events("Stop")]
    assert outcomes.count("continue") == 2
    assert outcomes.count("reentry_capped") == 1
    cap_event = transport.hook_fired_events("Stop")[-1]
    assert cap_event.data["outcome"] == "reentry_capped"
    assert cap_event.data["turn_end_reason"] == "final_response"
    assert cap_event.data["at_max_iter"] is False
