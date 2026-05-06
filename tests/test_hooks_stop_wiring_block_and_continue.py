"""Chat-loop wiring — natural-turn ``block`` and ``continue`` paths.

Drives ``runner.run()`` end-to-end with stubbed LLM and a stubbed
Stop helper:

  - ``blocking_error`` → final answer becomes ``[Blocked by Stop hook] ...``,
    no extra LLM call, transport carries ``outcome="block"``.
  - ``force_continue`` → loop reissues one more LLM call; the answer
    being continued from is preserved in ``agent.messages``; transport
    carries ``outcome="continue"``.
  - ``suppress_output`` → the ``<stop-hook>`` echo of additional_contexts
    is omitted from the assistant's final answer.
"""

from __future__ import annotations

from agentao.cancellation import CancellationToken
from agentao.plugins.models import StopHookResult

from tests.support.stop_precompact import make_runner_with_stub_llm


def test_blocking_error_returns_block_message_and_emits_block(tmp_path, monkeypatch):
    runner, transport, agent = make_runner_with_stub_llm(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "_dispatch_stop",
        lambda *, turn_end_reason, last_assistant_message: StopHookResult(
            blocking_error="lint failed", matched_rule_count=1,
        ),
    )

    result = runner.run("hi", max_iterations=10, token=CancellationToken())

    assert result == "[Blocked by Stop hook] lint failed"
    final_assistant = [m for m in agent.messages if m["role"] == "assistant"][-1]
    assert final_assistant["content"] == "[Blocked by Stop hook] lint failed"

    stop_events = transport.hook_fired_events("Stop")
    assert len(stop_events) == 1
    assert stop_events[0].data["outcome"] == "block"
    assert stop_events[0].data["turn_end_reason"] == "final_response"


def test_force_continue_reissues_llm_call_and_emits_continue(tmp_path, monkeypatch):
    runner, transport, agent = make_runner_with_stub_llm(tmp_path, monkeypatch)

    state = {"calls": 0}

    def fake_dispatch(*, turn_end_reason, last_assistant_message):
        state["calls"] += 1
        if state["calls"] == 1:
            return StopHookResult(
                force_continue=True,
                follow_up_message="please retry",
                stop_reason="please retry",
                matched_rule_count=1,
            )
        return StopHookResult(matched_rule_count=1)

    monkeypatch.setattr(runner, "_dispatch_stop", fake_dispatch)

    result = runner.run("hi", max_iterations=10, token=CancellationToken())

    assert result == "final answer"
    assert state["calls"] == 2
    outcomes = [e.data["outcome"] for e in transport.hook_fired_events("Stop")]
    assert outcomes == ["continue", "allow"]
    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert any("please retry" in (m.get("content") or "") for m in user_msgs)


def test_suppress_output_omits_stop_hook_echo_on_final_answer(tmp_path, monkeypatch):
    runner, transport, _agent = make_runner_with_stub_llm(
        tmp_path, monkeypatch, content="answer-body",
    )
    monkeypatch.setattr(
        runner, "_dispatch_stop",
        lambda *, turn_end_reason, last_assistant_message: StopHookResult(
            additional_contexts=["audit-note"],
            suppress_output=True,
            matched_rule_count=1,
        ),
    )

    result = runner.run("hi", max_iterations=10, token=CancellationToken())

    assert result == "answer-body"
    assert "<stop-hook>" not in result

    stop_events = transport.hook_fired_events("Stop")
    assert len(stop_events) == 1
    assert stop_events[0].data["added_context_count"] == 1
    assert stop_events[0].data["suppress_output"] is True


def test_suppress_output_false_appends_stop_hook_echo(tmp_path, monkeypatch):
    """Negative control — without suppress_output the contexts ride on
    the assistant's final answer as a ``<stop-hook>`` block."""
    runner, _transport, _agent = make_runner_with_stub_llm(
        tmp_path, monkeypatch, content="answer-body",
    )
    monkeypatch.setattr(
        runner, "_dispatch_stop",
        lambda *, turn_end_reason, last_assistant_message: StopHookResult(
            additional_contexts=["audit-note"],
            suppress_output=False,
            matched_rule_count=1,
        ),
    )

    result = runner.run("hi", max_iterations=10, token=CancellationToken())

    assert "<stop-hook>" in result
    assert "audit-note" in result
