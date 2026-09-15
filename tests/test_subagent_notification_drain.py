"""Only the top-level runtime consumes background-agent notifications (#233).

A sub-agent is built with its parent's ``BackgroundTaskStore``
(``agents/tools/_wrapper.py``) so ``check_background_agent`` and
``cancel_background_agent`` resolve inside it. But the store's notification
queue is *drained*, not read, and every chat loop with a store drained it
(``runtime/chat_loop/_runner.py::_inject_background_notifications``): whichever
loop reached it first took every notification into its own history. A task
finishing while a sub-agent was mid-run reported into the sub-agent, and the
conversation that launched it never heard.

Sharing and consuming are now separate: the wrapper marks every runtime it
builds as a non-consumer. The tests drive the real wrapper construction path
with ``Agentao.chat`` replaced, because a real ``chat()`` is a networked LLM
turn — and the replacement runs the *real* drain from inside the sub-agent, at
the moment its loop would.
"""

from __future__ import annotations

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.runtime.chat_loop import ChatLoopRunner


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


@pytest.fixture
def store():
    return BackgroundTaskStore(persistence_dir=None)


def _parent(tmp_path, store, **kwargs):
    return Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        bg_store=store, **kwargs,
    )


def _drain_into(agent):
    """Run the chat loop's real notification step for ``agent``."""
    ChatLoopRunner(agent)._inject_background_notifications(
        [{"role": "system", "content": ""}], system_prompt="",
    )


def _notes(agent, needle):
    return [
        m for m in agent.messages
        if m.get("role") == "user" and needle in str(m.get("content"))
    ]


def _finish(store, agent_id, result):
    store.register(agent_id, "worker", "task")
    store.mark_running(agent_id)
    store.update(agent_id, status="completed", result=result)


def _replace_chat(monkeypatch, body):
    """Replace ``Agentao.chat`` so ``_run_sync`` builds a real sub-agent and
    hands it to ``body`` instead of running an LLM turn."""
    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        body(self)
        return "done"

    monkeypatch.setattr(Agentao, "chat", chat)


# ── the flag, at the drain ──────────────────────────────────────────────────


def test_a_runtime_marked_as_non_consumer_leaves_the_queue_alone(tmp_path, store):
    agent = _parent(tmp_path, store)
    agent._drains_background_notifications = False
    _finish(store, "A", "result of A")

    _drain_into(agent)

    assert agent.messages == []
    assert len(store.drain_notifications()) == 1


# ── the wrapper: a sub-agent it builds never consumes ───────────────────────


def test_a_sub_agent_does_not_take_the_parents_notification(tmp_path, store, monkeypatch):
    """The reported interleaving: A finishes while sub-agent B is mid-run, and
    B's loop reaches the queue before the parent's does."""
    parent = _parent(tmp_path, store, enable_builtin_agents=True)
    seen = []

    def while_sub_agent_runs(sub_agent):
        seen.append(sub_agent)
        _finish(store, "A", "result of A")
        _drain_into(sub_agent)

    _replace_chat(monkeypatch, while_sub_agent_runs)
    parent.tools.tools["agent_generalist"]._run_sync("x")

    (sub_agent,) = seen
    assert sub_agent is not parent
    assert _notes(sub_agent, "result of A") == []

    _drain_into(parent)
    _drain_into(parent)
    assert len(_notes(parent, "result of A")) == 1  # exactly once


def test_a_sub_agent_can_still_check_and_cancel_through_the_shared_store(
    tmp_path, store, monkeypatch,
):
    parent = _parent(tmp_path, store, enable_builtin_agents=True)
    store.register("queued", "worker", "task")
    _finish(store, "done", "finished work")
    _drain_into(parent)  # consume "done" now; only the cancel should arrive later
    parent.messages = []
    answers = {}

    def use_tools(sub_agent):
        tools = sub_agent.tool_runner._tools
        answers["check"] = tools.get("check_background_agent").execute(agent_id="done")
        answers["cancel"] = tools.get("cancel_background_agent").execute(agent_id="queued")
        _drain_into(sub_agent)

    _replace_chat(monkeypatch, use_tools)
    parent.tools.tools["agent_generalist"]._run_sync("x")

    assert "finished work" in answers["check"]
    assert "cancelled" in answers["cancel"]
    assert store.get("queued")["status"] == "cancelled"
    _drain_into(parent)
    assert len(_notes(parent, "(ID: queued) was cancelled")) == 1


def test_a_reset_still_drops_notifications_from_before_it(tmp_path, store, monkeypatch):
    """The generation cutoff from #237 and the consumer rule compose: the old
    task reports nowhere, the new one reports to the top level once."""
    parent = _parent(tmp_path, store, enable_builtin_agents=True)
    store.register("old", "worker", "task")
    store.mark_running("old")
    parent.clear_history()

    def while_sub_agent_runs(sub_agent):
        store.update("old", status="completed", result="stale result")
        _finish(store, "new", "fresh result")
        _drain_into(sub_agent)

    _replace_chat(monkeypatch, while_sub_agent_runs)
    parent.tools.tools["agent_generalist"]._run_sync("x")

    _drain_into(parent)
    assert _notes(parent, "stale result") == []
    assert len(_notes(parent, "fresh result")) == 1
