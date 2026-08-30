"""Regression: doom-loop Stop must not double-dispatch as max_iterations.

The doom-loop branch in ``ChatLoopRunner.run`` dispatches ``Stop`` with
``turn_end_reason="doom_loop"`` and must finalize the turn directly. A
prior version used ``break`` here, which exited the inner loop into the
generic max-iterations finalizer and emitted a SECOND Stop for the same
turn with ``turn_end_reason="max_iterations"`` — wrong reason and wrong
count.

This test drives the chat loop with a stubbed LLM + tool runner and
asserts that exactly one ``_dispatch_stop`` call is made, with reason
``"doom_loop"``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentao.cancellation import CancellationToken
from agentao.plugins.models import ParsedHookRule, StopHookResult

from tests.support.stop_precompact import make_runner_with_rules

def _agent_and_runner(tmp_path, monkeypatch, calls):
    """The doom test's own setup, extracted so three tests share one stub stack.

    Returns ``(agent, runner)`` with the LLM stubbed to emit exactly one tool
    call, ``_dispatch_stop`` recording into ``calls``, and a ``MagicMock`` tool
    runner the caller configures.
    """
    stop_rule = ParsedHookRule(
        event="Stop", hook_type="command", command="echo", plugin_name="t",
    )
    runner, _transport = make_runner_with_rules(tmp_path, rules=[stop_rule])
    agent = runner._agent

    def fake_dispatch_stop(*, turn_end_reason: str, last_assistant_message: str) -> StopHookResult:
        calls.append({
            "turn_end_reason": turn_end_reason,
            "last_assistant_message": last_assistant_message,
        })
        return StopHookResult(matched_rule_count=1)

    monkeypatch.setattr(runner, "_dispatch_stop", fake_dispatch_stop)
    monkeypatch.setattr(runner, "_maybe_microcompact", lambda m, s, tokens=None: (m, s))
    monkeypatch.setattr(runner, "_maybe_full_compress", lambda m, s, tokens=None: (m, s))
    monkeypatch.setattr(runner, "_inject_background_notifications", lambda m, s: m)

    fake_tc = SimpleNamespace(
        id="call_0", type="function",
        function=SimpleNamespace(name="x", arguments="{}"),
    )
    fake_outcome = SimpleNamespace(
        error_return=None,
        response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content="thinking", tool_calls=[fake_tc], reasoning_content=None,
            ))],
            usage=None,
        ),
        messages_with_system=[{"role": "system", "content": ""}],
        system_prompt="",
    )
    monkeypatch.setattr(
        runner, "_call_llm_with_overflow_recovery",
        lambda m, s, t, k, **kw: fake_outcome,
    )

    agent.tool_runner = MagicMock()
    agent.tool_runner.normalize_tool_calls.side_effect = lambda tcs: (list(tcs), False)
    agent.tool_runner.reset = MagicMock()
    monkeypatch.setattr(agent, "_build_system_prompt", lambda: "")
    agent.skill_manager = MagicMock()
    agent.skill_manager.get_active_skills.return_value = {}
    agent.memory_manager = MagicMock()
    agent.memory_manager.write_version = 0
    agent.tools = MagicMock()
    agent.tools.to_openai_format.return_value = []
    return agent, runner


def test_a_post_tool_use_hook_stop_ends_the_turn_through_the_ordinary_path(tmp_path, monkeypatch):
    """`continue: false` on a Post* event ends the **turn**, and it maps through
    the ordinary turn-outcome path — the same ``_resolve_stop_hook`` the doom
    detector uses — so `agentao run` needs no exit code of its own for it.
    """
    calls = []
    agent, runner = _agent_and_runner(tmp_path, monkeypatch, calls)

    # The tools ran; the hook then asked for the turn to end.
    agent.tool_runner.execute.return_value = (False, [])
    agent.tool_runner.last_hook_stop = "policy says stop"

    runner.run("hi", max_iterations=10, token=CancellationToken())

    assert len(calls) == 1, calls
    assert calls[0]["turn_end_reason"] == "hook_stop"


def test_a_mock_tool_runner_does_not_accidentally_stop_a_turn(tmp_path, monkeypatch):
    """The seam reads a **string**, not a truthy value. This codebase substitutes
    ``MagicMock`` runners freely, and every one of them answers any attribute —
    an ``is not None`` test here would let a stub end turns it never meant to."""
    calls = []
    agent, runner = _agent_and_runner(tmp_path, monkeypatch, calls)
    agent.tool_runner.execute.return_value = (True, [])   # doom, not a hook stop

    runner.run("hi", max_iterations=10, token=CancellationToken())

    assert calls[0]["turn_end_reason"] == "doom_loop"


def test_doom_loop_dispatches_stop_exactly_once(tmp_path, monkeypatch):
    stop_rule = ParsedHookRule(
        event="Stop", hook_type="command", command="echo", plugin_name="t",
    )
    runner, _transport = make_runner_with_rules(tmp_path, rules=[stop_rule])
    agent = runner._agent

    calls: list[dict] = []

    def fake_dispatch_stop(*, turn_end_reason: str, last_assistant_message: str) -> StopHookResult:
        calls.append({
            "turn_end_reason": turn_end_reason,
            "last_assistant_message": last_assistant_message,
        })
        # Phase B: helper must return StopHookResult — a clean "allow"
        # path with no force_continue / blocking_error so the chat loop
        # finalizes the doom-arm normally.
        return StopHookResult(matched_rule_count=1)

    monkeypatch.setattr(runner, "_dispatch_stop", fake_dispatch_stop)
    monkeypatch.setattr(runner, "_maybe_microcompact", lambda m, s, tokens=None: (m, s))
    monkeypatch.setattr(runner, "_maybe_full_compress", lambda m, s, tokens=None: (m, s))
    monkeypatch.setattr(
        runner, "_inject_background_notifications", lambda m, s: m,
    )

    fake_tc = SimpleNamespace(
        id="call_0",
        type="function",
        function=SimpleNamespace(name="x", arguments="{}"),
    )
    fake_message = SimpleNamespace(
        content="thinking",
        tool_calls=[fake_tc],
        reasoning_content=None,
    )
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=fake_message)],
        usage=None,
    )
    fake_outcome = SimpleNamespace(
        error_return=None,
        response=fake_response,
        messages_with_system=[{"role": "system", "content": ""}],
        system_prompt="",
    )
    monkeypatch.setattr(
        runner,
        "_call_llm_with_overflow_recovery",
        # Accept **kw so the stub tolerates optional kwargs the real method
        # takes (e.g. image_fallback_text / image_fallback_index).
        lambda m, s, t, k, **kw: fake_outcome,
    )

    agent.tool_runner = MagicMock()
    agent.tool_runner.normalize_tool_calls.side_effect = (
        lambda tcs: (list(tcs), False)
    )
    agent.tool_runner.execute.return_value = (True, [])
    agent.tool_runner.reset = MagicMock()

    monkeypatch.setattr(agent, "_build_system_prompt", lambda: "")
    agent.skill_manager = MagicMock()
    agent.skill_manager.get_active_skills.return_value = {}
    agent.memory_manager = MagicMock()
    agent.memory_manager.write_version = 0
    agent.tools = MagicMock()
    agent.tools.to_openai_format.return_value = []

    result = runner.run("hi", max_iterations=10, token=CancellationToken())

    assert len(calls) == 1, calls
    assert calls[0]["turn_end_reason"] == "doom_loop"
    assert "doom" in calls[0]["last_assistant_message"].lower() or \
        calls[0]["last_assistant_message"] == "thinking"
    assert result == calls[0]["last_assistant_message"]
