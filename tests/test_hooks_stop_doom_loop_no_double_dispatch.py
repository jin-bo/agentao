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
from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import make_runner_with_rules


def test_doom_loop_dispatches_stop_exactly_once(tmp_path, monkeypatch):
    stop_rule = ParsedHookRule(
        event="Stop", hook_type="command", command="echo", plugin_name="t",
    )
    runner, _transport = make_runner_with_rules(tmp_path, rules=[stop_rule])
    agent = runner._agent

    calls: list[dict] = []

    def fake_dispatch_stop(*, turn_end_reason: str, last_assistant_message: str) -> None:
        calls.append({
            "turn_end_reason": turn_end_reason,
            "last_assistant_message": last_assistant_message,
        })

    monkeypatch.setattr(runner, "_dispatch_stop", fake_dispatch_stop)
    monkeypatch.setattr(runner, "_maybe_microcompact", lambda m, s: (m, s))
    monkeypatch.setattr(runner, "_maybe_full_compress", lambda m, s: (m, s))
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
        lambda m, s, t, k: fake_outcome,
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
