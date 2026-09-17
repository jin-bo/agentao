"""A cancelled sub-agent settles as ``cancelled``, on every surface (#244).

``chat()`` does not raise on cancellation — ``runtime/turn.py`` absorbs
``AgentCancelledError`` (and ``KeyboardInterrupt``) and returns a marker with
``status="cancelled"``, and a token cancelled mid-stream lets the turn return
normally and is flipped to that same status in the ``finally`` there. Both
sub-agent paths nonetheless recovered the cancelled case from an ``except
AgentCancelledError`` branch that could never run, so every running cancel was
recorded as ``failed`` while a *pending* one was recorded as ``cancelled`` —
the same user action landing in two terminal states depending on timing.

The terminal state is now derived from the run's classification
(``_terminal_state``), which is the only thing all three cancel routes have in
common. These tests drive the real wrapper and the real ``chat()``; the
sub-agent stops before its first LLM request, so nothing is sent.
"""

from __future__ import annotations

import threading
from typing import Any, List

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.agents.tools._wrapper import (
    _MAX_ITERATIONS_REASON,
    _classify_subagent_outcome,
    _IncompleteOutcome,
    _terminal_state,
)
from agentao.cancellation import CancellationToken
from agentao.host.projection import HostSubagentEmitter
from agentao.permissions import PermissionEngine
from agentao.runtime.outcome import TurnOutcome


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


class _FakeStream:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


def _parent(tmp_path, bg_store=None):
    (tmp_path / "user").mkdir(exist_ok=True)
    return Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True, bg_store=bg_store,
        permission_engine=PermissionEngine(
            project_root=tmp_path, user_root=tmp_path / "user",
        ),
    )


def _watch(wrapper):
    """Record the wrapper's public events and its CLI progress callbacks."""
    stream = _FakeStream()
    wrapper._subagent_emitter = HostSubagentEmitter(
        stream, parent_session_id_provider=lambda: "parent-s",
    )
    progress: List[Any] = []
    wrapper._step_callback = lambda name, payload: progress.append((name, payload))
    return stream, progress


def _agent_end(progress):
    ends = [payload for name, payload in progress if name == "__agent_end__"]
    assert len(ends) == 1, f"expected one __agent_end__, got {len(ends)}"
    return ends[0]


# ── the mapping itself ─────────────────────────────────────────────────────


class TestTerminalState:
    def test_an_answered_run_is_completed(self):
        assert _terminal_state(None) == "completed"

    def test_a_cancelled_run_is_cancelled(self):
        assert _terminal_state(_IncompleteOutcome("cancelled", "d")) == "cancelled"

    @pytest.mark.parametrize(
        "reason", ["no_output", "doom_loop", "llm_error", _MAX_ITERATIONS_REASON],
    )
    def test_every_other_non_answer_is_failed(self, reason):
        assert _terminal_state(_IncompleteOutcome(reason, "d")) == "failed"

    def test_it_reads_the_reason_the_classifier_mints(self):
        """The two halves must agree on the spelling, so join them here."""
        outcome = TurnOutcome(
            text="[Cancelled: user-cancel]", status="cancelled",
            incomplete_reason=None, tool_count=0,
        )
        incomplete = _classify_subagent_outcome(
            outcome=outcome, task_complete=False,
            max_iterations_hit=False, max_turns=15,
        )
        assert _terminal_state(incomplete) == "cancelled"

    def test_complete_task_still_wins_over_a_later_cancel(self):
        """Precedence is the classifier's, unchanged: a sub-agent that said
        it was done is done, and a cancel arriving after does not rewrite it."""
        outcome = TurnOutcome(
            text="[Cancelled: user-cancel]", status="cancelled",
            incomplete_reason=None, tool_count=0,
        )
        incomplete = _classify_subagent_outcome(
            outcome=outcome, task_complete=True,
            max_iterations_hit=False, max_turns=15,
        )
        assert _terminal_state(incomplete) == "completed"

    def test_an_exhausted_budget_still_wins_over_a_simultaneous_cancel(self):
        outcome = TurnOutcome(
            text="[Cancelled: user-cancel]", status="cancelled",
            incomplete_reason=None, tool_count=0,
        )
        incomplete = _classify_subagent_outcome(
            outcome=outcome, task_complete=False,
            max_iterations_hit=True, max_turns=15,
        )
        assert incomplete.reason == _MAX_ITERATIONS_REASON
        assert _terminal_state(incomplete) == "failed"


# ── foreground ─────────────────────────────────────────────────────────────


def test_a_cancelled_foreground_sub_agent_is_cancelled_not_failed(tmp_path):
    parent = _parent(tmp_path)
    try:
        wrapper = parent.tools.tools["agent_generalist"]
        stream, progress = _watch(wrapper)

        # The real ``chat()`` runs, and stops at its first cancellation check.
        token = CancellationToken()
        token.cancel("user-cancel")
        wrapper._cancellation_token = token

        out = wrapper.execute(task="x")
    finally:
        parent.close()

    assert [e.phase for e in stream.events] == ["spawned", "cancelled"]
    # A cancel is not an error, so the phase carries the whole fact.
    assert stream.events[-1].error_type is None

    end = _agent_end(progress)
    assert end.state == "cancelled"
    # ``error`` is the failure detail; the state says this was a cancel.
    assert end.error is None

    # The parent LLM is still told the sub-agent did not finish...
    assert "did not finish: it was cancelled" in out
    # ...but agentao's own cancel marker is not handed to it as the child's
    # work. ``[Cancelled: user-cancel]`` is harness-authored turn text.
    assert "Partial result:" not in out
    assert "[Cancelled:" not in out


# ── background ─────────────────────────────────────────────────────────────


def _cancel_once_running(monkeypatch):
    """Make every sub-agent's ``chat()`` wait until its token is signalled,
    then run the real one — which stops before the first LLM request."""
    in_chat = threading.Event()
    real_chat = Agentao.chat

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        signalled = threading.Event()
        cancellation_token.add_done_callback(signalled.set)
        in_chat.set()
        signalled.wait(10)
        return real_chat(
            self, user_message, max_iterations=max_iterations,
            cancellation_token=cancellation_token, images=images,
        )

    monkeypatch.setattr(Agentao, "chat", chat)
    return in_chat


def _eventually(predicate, timeout=10.0):
    deadline = threading.Event()
    timer = threading.Timer(timeout, deadline.set)
    timer.start()
    try:
        while not predicate():
            if deadline.wait(0.05):
                return predicate()
        return True
    finally:
        timer.cancel()


def test_a_cancelled_background_sub_agent_keeps_its_work(tmp_path, monkeypatch):
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(tmp_path, bg_store=store)
    try:
        wrapper = parent.tools.tools["agent_generalist"]
        stream, _ = _watch(wrapper)
        in_chat = _cancel_once_running(monkeypatch)

        wrapper.execute(task="x", run_in_background=True)
        (record,) = store.list()
        agent_id = record["id"]

        assert in_chat.wait(10)
        assert store.cancel(agent_id).startswith("Cancellation signal sent")
        assert _eventually(lambda: store.get(agent_id)["status"] != "running")

        rec = store.get(agent_id)
        assert rec["status"] == "cancelled"
        # Not "failed, but which kind" — the status already names the cause.
        assert rec["incomplete_reason"] is None
        assert rec["error"] is None
        # ``update`` overwrites result and counters unconditionally, so a
        # cancel that did not pass them would erase the run's work.
        assert "did not finish: it was cancelled" in rec["result"]
        assert rec["turns"] >= 1
        assert rec["duration_ms"] >= 0

        # The tool the parent LLM reads hands back that stored work.
        checked = parent.tools.tools["check_background_agent"].execute(agent_id=agent_id)
        assert "was cancelled" in checked
        assert "did not finish: it was cancelled" in checked

        notes = store.drain_notifications()
        assert len(notes) == 1
        assert "was cancelled" in notes[0] and "failed" not in notes[0]

        assert _eventually(lambda: len(stream.events) == 2)
        assert [e.phase for e in stream.events] == ["spawned", "cancelled"]
        assert stream.events[-1].error_type is None
    finally:
        parent.close()


def test_a_pending_cancel_and_a_running_cancel_agree(tmp_path, monkeypatch):
    """The two cancel routes used to disagree: a task cancelled before it
    started was ``cancelled``, one cancelled a moment later was ``failed``."""
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(tmp_path, bg_store=store)
    try:
        wrapper = parent.tools.tools["agent_generalist"]
        _watch(wrapper)
        in_chat = _cancel_once_running(monkeypatch)

        wrapper.execute(task="running", run_in_background=True)
        (running,) = store.list()
        assert in_chat.wait(10)
        store.cancel(running["id"])
        assert _eventually(lambda: store.get(running["id"])["status"] != "running")

        store.register("pending-one", "generalist", "queued")
        store.register_token("pending-one", CancellationToken())
        store.cancel("pending-one")

        assert store.get(running["id"])["status"] == "cancelled"
        assert store.get("pending-one")["status"] == "cancelled"
    finally:
        parent.close()
