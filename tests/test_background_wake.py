"""Waking an idle interactive CLI when a background sub-agent's notice is queued.

Design: ``docs/design/background-subagent-wake.md`` §6.3 (step C). Three layers:

- the store's private ``_notification_snapshot`` and its push sequence;
- notice-before-terminal-event ordering on every background terminal path,
  which the embedded-host recipe leans on (and the case where a parent turn
  drains the notice first, which the recipe must not assume away);
- the CLI's wake decision, its loop-thread recheck, and ``run_loop``'s
  handling of the ``_BG_WAKE`` sentinel.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import Mock

import pytest

from agentao.agents.bg_store import BackgroundTaskStore
from agentao.cli import input_loop
from agentao.cli.app import _read_auto_wake
from agentao.cli.input_loop import (
    _BG_WAKE,
    _BG_WAKE_MESSAGE,
    _bg_wake_sequence,
    _try_bg_wake,
    get_user_input,
    run_loop,
)


# ---------------------------------------------------------------------------
# Store: snapshot and push sequence
# ---------------------------------------------------------------------------


def _settle(store: BackgroundTaskStore, agent_id: str = "a1") -> None:
    store.register(agent_id, "worker", "task")
    store.mark_running(agent_id)
    store.update(agent_id, status="completed", result="done")


def test_snapshot_starts_empty():
    assert BackgroundTaskStore()._notification_snapshot() == (False, 0)


def test_update_queues_a_notice_and_advances_the_sequence():
    store = BackgroundTaskStore()
    _settle(store)
    assert store._notification_snapshot() == (True, 1)


def test_push_notification_advances_the_sequence():
    store = BackgroundTaskStore()
    store.push_notification("hello")
    assert store._notification_snapshot() == (True, 1)


def test_drain_empties_the_queue_but_keeps_the_sequence():
    store = BackgroundTaskStore()
    _settle(store)
    store.drain_notifications()
    assert store._notification_snapshot() == (False, 1)


def test_snapshot_does_not_drain():
    store = BackgroundTaskStore()
    _settle(store)
    store._notification_snapshot()
    assert len(store.drain_notifications()) == 1


def test_new_conversation_empties_the_queue_but_keeps_the_sequence():
    store = BackgroundTaskStore()
    _settle(store)
    store.start_new_conversation()
    assert store._notification_snapshot() == (False, 1)


def test_suppressed_notice_neither_queues_nor_advances():
    """A task from before a reset settles silently: its record goes terminal,
    but nothing is queued, so there is nothing to wake for."""
    store = BackgroundTaskStore()
    store.register("a1", "worker", "task")
    store.mark_running("a1")
    store.start_new_conversation()
    store.update("a1", status="completed", result="done")
    assert store._notification_snapshot() == (False, 0)
    # The record itself is terminal — which is why ``list()`` is not the signal.
    assert store.get("a1")["status"] == "completed"


def test_pending_cancel_queues_a_notice():
    store = BackgroundTaskStore()
    store.register("a1", "worker", "task")
    store.cancel("a1")
    assert store._notification_snapshot() == (True, 1)


# ---------------------------------------------------------------------------
# Ordering: the notice is queued before the terminal event is published
# ---------------------------------------------------------------------------


class _RecordingStream:
    """Captures each event with the store's snapshot at publish time."""

    def __init__(self, store: BackgroundTaskStore, *, drain_on_terminal: bool = False):
        self.store = store
        self.drain_on_terminal = drain_on_terminal
        self.seen: List[Any] = []
        self.done = threading.Event()

    def publish(self, event: Any) -> None:
        if event.phase == "spawned":
            self.seen.append((event, None))
            return
        if self.drain_on_terminal:
            # A parent turn's drain wins the race against the host's reader.
            self.store.drain_notifications()
        self.seen.append((event, self.store._notification_snapshot()))
        self.done.set()


def _wrapper(tmp_path, store, stream, *, drive):
    from agentao.agents.tools import AgentToolWrapper
    from agentao.host.projection import HostSubagentEmitter

    wrapper = AgentToolWrapper(
        definition={"name": "worker", "description": "d"},
        all_tools={},
        llm_config_getter=lambda: {},
        working_directory=tmp_path,
        bg_store=store,
        subagent_emitter=HostSubagentEmitter(
            stream, parent_session_id_provider=lambda: "parent-s"
        ),
    )
    wrapper._build_sub_agent = lambda suppress_output: (object(), {})
    wrapper._drive_sub_agent = drive
    wrapper._roll_up_usage = lambda sub_agent: None
    wrapper._close_sub_agent = lambda sub_agent: None
    return wrapper


def _stats(incomplete=None):
    return {
        "agent_name": "worker", "incomplete": incomplete, "turns": 1,
        "tool_calls": 0, "tokens": 10, "duration_ms": 5,
    }


def _completed(sub_agent, **kw):
    return "done", _stats()


def _incomplete(sub_agent, **kw):
    from agentao.agents.tools._wrapper import _IncompleteOutcome
    return "partial", _stats(_IncompleteOutcome("max_iterations", "ran out"))


def _raises(sub_agent, **kw):
    raise RuntimeError("boom")


def _raises_cancelled(sub_agent, **kw):
    from agentao.cancellation import AgentCancelledError
    raise AgentCancelledError("stop")


@pytest.mark.parametrize(
    "drive, phase",
    [
        (_completed, "completed"),
        (_incomplete, "failed"),
        (_raises, "failed"),
        (_raises_cancelled, "cancelled"),
    ],
)
def test_notice_is_queued_before_the_terminal_event(tmp_path, drive, phase):
    store = BackgroundTaskStore()
    stream = _RecordingStream(store)
    _wrapper(tmp_path, store, stream, drive=drive)._launch_background("t", "")
    assert stream.done.wait(5)
    event, snapshot = stream.seen[-1]
    assert event.phase == phase
    assert event.parent_task_id is not None
    assert snapshot == (True, 1)


def test_pending_cancel_notice_is_queued_before_the_terminal_event(tmp_path):
    """``cancel()`` before the worker starts queues the notice itself; the
    worker then finds ``mark_running`` refused and publishes ``cancelled``."""
    store = BackgroundTaskStore()
    stream = _RecordingStream(store)
    real_mark_running = store.mark_running

    def cancel_first(agent_id):
        store.cancel(agent_id)
        return real_mark_running(agent_id)

    store.mark_running = cancel_first
    _wrapper(tmp_path, store, stream, drive=_completed)._launch_background("t", "")
    assert stream.done.wait(5)
    event, snapshot = stream.seen[-1]
    assert event.phase == "cancelled"
    assert snapshot == (True, 1)


def test_terminal_event_is_a_cue_not_proof_of_a_queued_notice(tmp_path):
    """A parent turn can drain the notice before a host handles the event:
    the event still arrives, with nothing left in the queue. The host-api
    recipe must not assume otherwise."""
    store = BackgroundTaskStore()
    stream = _RecordingStream(store, drain_on_terminal=True)
    _wrapper(tmp_path, store, stream, drive=_completed)._launch_background("t", "")
    assert stream.done.wait(5)
    event, snapshot = stream.seen[-1]
    assert event.phase == "completed"
    assert snapshot == (False, 1)


# ---------------------------------------------------------------------------
# CLI: the wake decision
# ---------------------------------------------------------------------------


def _cli(store=None, *, auto_wake=True, plan=False, images=None, last=0):
    return SimpleNamespace(
        _bg_auto_wake=auto_wake,
        _bg_last_wake_sequence=last,
        _plan_session=SimpleNamespace(is_active=plan),
        _staged_images=images or [],
        agent=SimpleNamespace(bg_store=store),
    )


def _pending_store(n: int = 1) -> BackgroundTaskStore:
    store = BackgroundTaskStore()
    for i in range(n):
        store.push_notification(f"notice {i}")
    return store


def test_wakes_on_a_pending_notice():
    assert _bg_wake_sequence(_cli(_pending_store())) == 1


def test_one_notice_wakes_without_waiting_for_other_tasks():
    store = _pending_store()
    store.register("other", "worker", "still running")
    store.mark_running("other")
    assert _bg_wake_sequence(_cli(store)) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"auto_wake": False},
        {"plan": True},
        {"images": [{"data": "x", "mimeType": "image/png"}]},
    ],
    ids=["auto_wake-off", "plan-mode", "staged-images"],
)
def test_does_not_wake(kwargs):
    assert _bg_wake_sequence(_cli(_pending_store(), **kwargs)) is None


def test_does_not_wake_without_a_store():
    assert _bg_wake_sequence(_cli(None)) is None


def test_does_not_wake_on_an_empty_queue():
    store = _pending_store()
    store.drain_notifications()
    assert _bg_wake_sequence(_cli(store)) is None


def test_terminal_record_without_a_notice_does_not_wake():
    store = BackgroundTaskStore()
    store.register("a1", "worker", "task")
    store.mark_running("a1")
    store.start_new_conversation()
    store.update("a1", status="completed", result="done")
    assert _bg_wake_sequence(_cli(store)) is None


def test_a_notice_already_woken_for_does_not_wake_again():
    """The turn a wake started returned before draining (a ``UserPromptSubmit``
    hook refused it): the same notice must not wake the prompt again."""
    assert _bg_wake_sequence(_cli(_pending_store(), last=1)) is None


def test_a_new_notice_wakes_again_after_an_undrained_one():
    store = _pending_store(2)
    assert _bg_wake_sequence(_cli(store, last=1)) == 2


@pytest.mark.parametrize(
    "answer",
    [Mock(), (1, 1), (True, "1"), (True, 1.0), (True,), None],
    ids=["mock", "int-flag", "str-seq", "float-seq", "short", "none"],
)
def test_a_malformed_snapshot_fails_closed(answer):
    store = SimpleNamespace(_notification_snapshot=lambda: answer)
    assert _bg_wake_sequence(_cli(store)) is None


def test_a_raising_snapshot_fails_closed():
    def boom():
        raise RuntimeError("x")

    assert _bg_wake_sequence(_cli(SimpleNamespace(_notification_snapshot=boom))) is None


# ---------------------------------------------------------------------------
# CLI: the loop-thread recheck
# ---------------------------------------------------------------------------


class _FakeApp:
    def __init__(self, text: str = "", *, done: bool = False):
        self.current_buffer = SimpleNamespace(text=text)
        self.exits: List[Any] = []
        self._done = done

    def exit(self, result=None):
        if self._done:
            raise Exception("Return value already set.")
        self._done = True
        self.exits.append(result)


def test_recheck_exits_the_prompt_and_records_the_sequence():
    cli, app = _cli(_pending_store()), _FakeApp()
    _try_bg_wake(cli, app)
    assert app.exits == [_BG_WAKE]
    assert cli._bg_last_wake_sequence == 1


def test_typing_between_the_check_and_the_callback_wins():
    cli, app = _cli(_pending_store()), _FakeApp("half a sen")
    _try_bg_wake(cli, app)
    assert app.exits == []
    assert cli._bg_last_wake_sequence == 0


def test_a_notice_drained_before_the_callback_does_not_wake():
    store = _pending_store()
    cli, app = _cli(store), _FakeApp()
    store.drain_notifications()
    _try_bg_wake(cli, app)
    assert app.exits == []


def test_an_exit_that_already_happened_records_nothing():
    cli, app = _cli(_pending_store()), _FakeApp(done=True)
    _try_bg_wake(cli, app)
    assert cli._bg_last_wake_sequence == 0


def test_real_prompt_is_closed_by_the_ticker():
    """End to end through prompt_toolkit: the ticker thread notices, the loop
    thread exits the prompt, and ``get_user_input`` returns the sentinel."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    store = _pending_store()
    with create_pipe_input() as pipe:
        cli = _cli(store)
        cli.current_mode = None
        cli._prompt_session = PromptSession(input=pipe, output=DummyOutput())
        # Without a wake the prompt would wait forever: submit an empty line
        # after a bound, so a broken ticker fails the assertion, not the run.
        fallback = threading.Timer(5.0, pipe.send_text, args=("\r",))
        fallback.start()
        try:
            assert get_user_input(cli) is _BG_WAKE
        finally:
            fallback.cancel()
    assert cli._bg_last_wake_sequence == 1


# ---------------------------------------------------------------------------
# CLI: run_loop handling
# ---------------------------------------------------------------------------


def test_run_loop_runs_exactly_one_turn_for_a_wake(monkeypatch):
    turns: List[str] = []
    monkeypatch.setattr(
        input_loop, "_run_agent_turn", lambda cli, msg, images=None: turns.append(msg)
    )
    cli = Mock()
    cli._staged_images = []
    cli._plan_session.is_active = False
    cli._get_user_input.side_effect = [_BG_WAKE, "/exit"]
    run_loop(cli)
    assert turns == [_BG_WAKE_MESSAGE]
    cli.agent.chat.assert_not_called()


# ---------------------------------------------------------------------------
# Setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "settings, expected",
    [
        ({}, True),
        ({"background_agents": {}}, True),
        ({"background_agents": {"auto_wake": True}}, True),
        ({"background_agents": {"auto_wake": False}}, False),
        ({"background_agents": {"auto_wake": "false"}}, True),
        ({"background_agents": {"auto_wake": 0}}, True),
        ({"background_agents": False}, True),
    ],
)
def test_read_auto_wake(settings, expected):
    assert _read_auto_wake(settings) is expected
