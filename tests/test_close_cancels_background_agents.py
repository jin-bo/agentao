"""``Agentao.close()`` cancels the background sub-agents it launched (codex #49814).

Before, ``close()`` released MCP, memory and the log but never touched
``bg_store``: a background sub-agent kept running on its daemon thread after
the agent (or the ACP session) was closed, still able to write files and spend
tokens, on resources being torn down under it.

The store is shared with sub-agents (so their check / cancel tools see the
parent's tasks) and maybe with other agents of the host, so ``close()`` must
cancel only the tasks launched under its own key. A cancel is a request: a
task still running after the bounded wait is named, never reported stopped.
"""

from __future__ import annotations

import logging
import threading
from typing import List

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundOwnerClosedError, BackgroundTaskStore
from agentao.cancellation import CancellationToken
from agentao.permissions import PermissionEngine


pytestmark = pytest.mark.usefixtures("isolated_home", "isolated_skill_dirs")


def _agent(tmp_path, store, name="a"):
    wd = tmp_path / name
    (wd / "user").mkdir(parents=True, exist_ok=True)
    return Agentao(
        working_directory=wd, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True, bg_store=store,
        logger=logging.getLogger(f"test.close_bg.{name}"),
        permission_engine=PermissionEngine(project_root=wd, user_root=wd / "user"),
    )


@pytest.fixture
def hold_in_chat(monkeypatch):
    """Every sub-agent's ``chat()`` waits for its token, then runs the real one,
    which stops before its first LLM request. Returns an event per entry."""
    entered: List[threading.Event] = []
    real_chat = Agentao.chat

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        signalled = threading.Event()
        cancellation_token.add_done_callback(signalled.set)
        if entered:
            entered[-1].set()
        signalled.wait(20)
        return real_chat(
            self, user_message, max_iterations=max_iterations,
            cancellation_token=cancellation_token, images=images,
        )

    monkeypatch.setattr(Agentao, "chat", chat)

    def arm() -> threading.Event:
        ev = threading.Event()
        entered.append(ev)
        return ev

    return arm


def _launch(agent) -> str:
    agent.tools.tools["agent_generalist"].execute(task="x", run_in_background=True)
    return max(agent.bg_store.list(), key=lambda r: r["created_at"])["id"]


def test_close_cancels_a_running_background_agent(tmp_path, hold_in_chat):
    store = BackgroundTaskStore(persistence_dir=None)
    agent = _agent(tmp_path, store)
    in_chat = hold_in_chat()
    agent_id = _launch(agent)
    assert in_chat.wait(10)

    agent.close()

    assert store.get(agent_id)["status"] == "cancelled"


def test_closing_one_agent_leaves_another_agents_tasks_alone(tmp_path, hold_in_chat):
    """A sub-agent gets its parent's store, and its own ``close()`` runs when it
    finishes: a close that cancelled every task in the store would kill the
    parent's background work each time a foreground sub-agent returned."""
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _agent(tmp_path, store, "parent")
    other = _agent(tmp_path, store, "other")
    try:
        in_chat = hold_in_chat()
        agent_id = _launch(parent)
        assert in_chat.wait(10)

        other.close()

        assert store.get(agent_id)["status"] == "running"
    finally:
        parent.close()
    assert store.get(agent_id)["status"] == "cancelled"


def test_a_launch_after_close_is_refused(tmp_path):
    store = BackgroundTaskStore(persistence_dir=None)
    agent = _agent(tmp_path, store)
    wrapper = agent.tools.tools["agent_generalist"]
    agent.close()

    with pytest.raises(BackgroundOwnerClosedError):
        wrapper.execute(task="x", run_in_background=True)
    assert store.list() == []


def test_close_owner_cancels_pending_and_names_what_did_not_stop():
    store = BackgroundTaskStore(persistence_dir=None)
    mine, theirs = object(), object()
    store.register("p1", "w", "t", owner=mine)                  # pending
    store.register("r1", "w", "t", owner=mine)                  # running, ignores its token
    store.mark_running("r1")
    store.register_token("r1", CancellationToken())
    store.register("o1", "w", "t", owner=theirs)
    store.register("u1", "w", "t")                              # no owner

    still_running = store.close_owner(mine, timeout=0.2)

    assert still_running == ["r1"]
    assert store.get("p1")["status"] == "cancelled"
    assert store.get_token("r1").is_cancelled                  # the request was sent
    assert store.get("o1")["status"] == "pending"
    assert store.get("u1")["status"] == "pending"
    with pytest.raises(BackgroundOwnerClosedError):
        store.register("p2", "w", "t", owner=mine)
    store.register("o2", "w", "t", owner=theirs)                # other owners still launch


def test_close_warns_about_a_task_still_running(tmp_path, monkeypatch, caplog):
    store = BackgroundTaskStore(persistence_dir=None)
    agent = _agent(tmp_path, store)
    store.register("r1", "w", "t", owner=agent._bg_owner)
    store.mark_running("r1")
    store.register_token("r1", CancellationToken())             # nobody watches it
    real = store.close_owner
    monkeypatch.setattr(store, "close_owner", lambda owner: real(owner, timeout=0.2))

    with caplog.at_level(logging.WARNING, logger="test.close_bg.a"):
        agent.close()

    assert any("still running" in r.getMessage() and "r1" in r.getMessage()
               for r in caplog.records)
    assert store.get("r1")["status"] == "running"               # not reported as stopped


def test_acp_session_close_cancels_them_too(tmp_path, hold_in_chat):
    from agentao.acp.models import AcpSessionState

    store = BackgroundTaskStore(persistence_dir=None)
    agent = _agent(tmp_path, store)
    in_chat = hold_in_chat()
    agent_id = _launch(agent)
    assert in_chat.wait(10)

    AcpSessionState(session_id="s", agent=agent).close()

    assert store.get(agent_id)["status"] == "cancelled"


def test_a_closed_owner_is_not_kept_alive_by_the_store():
    """Every sub-agent gets its parent's store and closes when it finishes."""
    import gc
    import weakref

    from agentao.agents.bg_store import BackgroundOwner

    store = BackgroundTaskStore(persistence_dir=None)
    owner = BackgroundOwner()
    ref = weakref.ref(owner)
    store.close_owner(owner, timeout=0)
    del owner
    gc.collect()
    assert ref() is None


def test_close_owner_names_a_running_task_get_hides(monkeypatch):
    """After a cwd rebind ``get()`` hides an owned task pinned elsewhere; the
    close must still name it as running."""
    store = BackgroundTaskStore(persistence_dir=None)
    mine = object()
    store.register("r1", "w", "t", owner=mine)
    store.mark_running("r1")
    store.register_token("r1", CancellationToken())
    monkeypatch.setattr(store, "get", lambda agent_id: None)

    assert store.close_owner(mine, timeout=0.1) == ["r1"]
