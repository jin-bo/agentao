"""The three batch endings run the shared post-batch step first (#632).

After a tool batch, a hook stop, ``complete_task`` and the doom loop each end
the turn. They used to return before the shared step, so they skipped both
halves of it:

- the cancel check — a batch cancelled by the user ended as a success;
- the skill/memory events — a ``save_memory`` in the batch took effect, but
  ``MEMORY_WRITE`` never fired.

Decision: a cancel wins over every ending, including an answer that
``complete_task`` already produced. The events still fire first, because the
writes they report already happened.
"""

from __future__ import annotations

import pytest

from agentao.cancellation import AgentCancelledError, CancellationToken
from agentao.transport.events import EventType
from agentao.plugins.models import StopHookResult

from tests.test_hooks_stop_doom_loop_no_double_dispatch import _agent_and_runner


def _set_ending(agent, ending):
    """Configure the mock tool runner so the batch ends the turn by ``ending``."""
    agent.tool_runner.last_hook_stop = "policy says stop" if ending == "hook_stop" else None
    agent.tool_runner.last_task_complete = "the answer" if ending == "complete_task" else None
    return ending == "doom_loop"


def _events(agent, monkeypatch):
    seen = []
    original = agent.transport.emit

    def emit(event):
        seen.append(event)
        return original(event)

    monkeypatch.setattr(agent.transport, "emit", emit)
    return seen


ENDINGS = ["hook_stop", "complete_task", "doom_loop"]


@pytest.mark.parametrize("ending", ENDINGS)
def test_a_cancel_during_the_batch_wins_over_the_ending(tmp_path, monkeypatch, ending):
    calls = []
    agent, runner = _agent_and_runner(tmp_path, monkeypatch, calls)
    doom = _set_ending(agent, ending)
    token = CancellationToken()

    def execute(tool_calls, cancellation_token=None):
        token.cancel("user pressed Ctrl+C")
        return doom, []

    agent.tool_runner.execute.side_effect = execute

    with pytest.raises(AgentCancelledError):
        runner.run("hi", max_iterations=10, token=token)

    assert calls == [], "a cancelled turn does not reach the Stop hook"


@pytest.mark.parametrize("ending", ENDINGS)
def test_a_memory_write_in_the_batch_is_emitted(tmp_path, monkeypatch, ending):
    calls = []
    agent, runner = _agent_and_runner(tmp_path, monkeypatch, calls)
    doom = _set_ending(agent, ending)
    seen = _events(agent, monkeypatch)

    def execute(tool_calls, cancellation_token=None):
        agent.memory_manager.write_version = 1   # save_memory ran in this batch
        return doom, []

    agent.tool_runner.execute.side_effect = execute

    runner.run("hi", max_iterations=10, token=CancellationToken())

    writes = [e for e in seen if e.type == EventType.MEMORY_WRITE]
    assert len(writes) == 1
    assert calls and calls[0]["turn_end_reason"] in {"hook_stop", "final_response", "doom_loop"}


@pytest.mark.parametrize("ending", ENDINGS)
def test_a_skill_activated_in_the_batch_is_emitted(tmp_path, monkeypatch, ending):
    calls = []
    agent, runner = _agent_and_runner(tmp_path, monkeypatch, calls)
    doom = _set_ending(agent, ending)
    seen = _events(agent, monkeypatch)

    def execute(tool_calls, cancellation_token=None):
        agent.skill_manager.get_active_skills.return_value = {"web-research": object()}
        return doom, []

    agent.tool_runner.execute.side_effect = execute

    runner.run("hi", max_iterations=10, token=CancellationToken())

    activated = [e for e in seen if e.type == EventType.SKILL_ACTIVATED]
    assert [e.data["skill"] for e in activated] == ["web-research"]


def test_a_cancelled_batch_still_reports_its_memory_write(tmp_path, monkeypatch):
    """The write already happened, so its event fires before the cancel raises."""
    calls = []
    agent, runner = _agent_and_runner(tmp_path, monkeypatch, calls)
    _set_ending(agent, "complete_task")
    seen = _events(agent, monkeypatch)
    token = CancellationToken()

    def execute(tool_calls, cancellation_token=None):
        agent.memory_manager.write_version = 1
        token.cancel("user pressed Ctrl+C")
        return False, []

    agent.tool_runner.execute.side_effect = execute

    with pytest.raises(AgentCancelledError):
        runner.run("hi", max_iterations=10, token=token)

    assert [e.type for e in seen].count(EventType.MEMORY_WRITE) == 1


def test_a_stop_hook_continue_does_not_emit_the_same_write_twice(tmp_path, monkeypatch):
    """After a forced continue the loop starts from the refreshed bookkeeping."""
    calls = []
    agent, runner = _agent_and_runner(tmp_path, monkeypatch, calls)
    seen = _events(agent, monkeypatch)
    batches = iter([("complete_task", 1), ("complete_task", 1)])

    def execute(tool_calls, cancellation_token=None):
        ending, version = next(batches)
        _set_ending(agent, ending)
        agent.memory_manager.write_version = version
        return False, []

    agent.tool_runner.execute.side_effect = execute
    verdicts = iter([
        StopHookResult(matched_rule_count=1, force_continue=True, stop_reason="go on"),
        StopHookResult(matched_rule_count=1),
    ])

    def dispatch(*, turn_end_reason, last_assistant_message):
        calls.append(turn_end_reason)
        return next(verdicts)

    monkeypatch.setattr(runner, "_dispatch_stop", dispatch)

    runner.run("hi", max_iterations=10, token=CancellationToken())

    assert calls == ["final_response", "final_response"]
    assert [e.type for e in seen].count(EventType.MEMORY_WRITE) == 1
