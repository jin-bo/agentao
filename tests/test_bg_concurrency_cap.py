"""The cap on a ``BackgroundTaskStore``'s tasks in flight.

Design: ``docs/design/codex-subagent-v2-vs-agentao.zh.md`` §3. A launch past
``max_concurrent`` is refused (``BackgroundCapacityError``), never queued;
only the store's own pending/running tasks count; a slot comes back on every
terminal state.
"""

from __future__ import annotations

import threading
import time
from typing import Any, List

import pytest

from agentao.agents import BackgroundCapacityError
from agentao.agents.bg_store import (
    DEFAULT_MAX_CONCURRENT,
    BackgroundTaskStore,
    _reset_recovery_guard_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_recovery_guard():
    _reset_recovery_guard_for_tests()
    yield
    _reset_recovery_guard_for_tests()


def _fill(store: BackgroundTaskStore, n: int, prefix: str = "a") -> List[str]:
    ids = [f"{prefix}{i}" for i in range(n)]
    for agent_id in ids:
        store.register(agent_id, "worker", "task")
    return ids


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_cap_is_six():
    assert DEFAULT_MAX_CONCURRENT == 6
    assert BackgroundTaskStore().max_concurrent == 6


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "3"])
def test_cap_must_be_a_positive_int(bad):
    with pytest.raises(ValueError, match="max_concurrent"):
        BackgroundTaskStore(max_concurrent=bad)


def test_none_means_no_cap():
    store = BackgroundTaskStore(max_concurrent=None)
    _fill(store, 50)
    assert len(store.list()) == 50


def test_cap_is_keyword_only():
    with pytest.raises(TypeError):
        BackgroundTaskStore(None, None, 3)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Refusal
# ---------------------------------------------------------------------------


def test_launch_past_the_cap_is_refused_and_leaves_no_record():
    store = BackgroundTaskStore(max_concurrent=2)
    _fill(store, 2)
    with pytest.raises(BackgroundCapacityError) as info:
        store.register("extra", "worker", "task")
    assert info.value.limit == 2
    assert store.get("extra") is None
    assert "extra" not in store._owned_ids


def test_refusal_text_tells_the_model_what_to_do():
    store = BackgroundTaskStore(max_concurrent=1)
    _fill(store, 1)
    with pytest.raises(BackgroundCapacityError) as info:
        store.register("extra", "worker", "task")
    text = str(info.value)
    assert "1 background agents are already running" in text
    assert "check_background_agent" in text
    assert "run_in_background=false" in text


def test_running_tasks_count_too():
    store = BackgroundTaskStore(max_concurrent=2)
    for agent_id in _fill(store, 2):
        store.mark_running(agent_id)
    with pytest.raises(BackgroundCapacityError):
        store.register("extra", "worker", "task")


# ---------------------------------------------------------------------------
# A slot comes back on every terminal state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_slot_is_reusable_after_a_running_task_settles(status):
    store = BackgroundTaskStore(max_concurrent=1)
    store.register("a0", "worker", "task")
    store.mark_running("a0")
    store.update("a0", status=status, result="r")
    store.register("a1", "worker", "task")
    assert store.get("a1")["status"] == "pending"


def test_slot_is_reusable_after_a_pending_cancel():
    store = BackgroundTaskStore(max_concurrent=1)
    store.register("a0", "worker", "task")
    store.cancel("a0")
    store.register("a1", "worker", "task")
    assert store.get("a1")["status"] == "pending"


def test_new_conversation_does_not_free_a_slot():
    """A task from before ``/clear`` still runs — and still spends."""
    store = BackgroundTaskStore(max_concurrent=1)
    store.register("a0", "worker", "task")
    store.start_new_conversation()
    with pytest.raises(BackgroundCapacityError):
        store.register("a1", "worker", "task")


# ---------------------------------------------------------------------------
# Only this store's tasks count
# ---------------------------------------------------------------------------


def test_sibling_tasks_on_the_shared_file_do_not_count(tmp_path):
    first = BackgroundTaskStore(persistence_dir=tmp_path, max_concurrent=2)
    first.recover()
    for agent_id in _fill(first, 2, prefix="s"):
        first.mark_running(agent_id)

    second = BackgroundTaskStore(persistence_dir=tmp_path, max_concurrent=2)
    second.recover()  # guarded path: loads first's records without owning them
    assert {r["status"] for r in second.list() if r["id"].startswith("s")} == {"running"}

    _fill(second, 2, prefix="b")
    with pytest.raises(BackgroundCapacityError):
        second.register("b-extra", "worker", "task")


def test_orphans_reclassified_by_recover_do_not_count(tmp_path):
    earlier = BackgroundTaskStore(persistence_dir=tmp_path)
    _fill(earlier, 3)
    _reset_recovery_guard_for_tests()  # a fresh process

    store = BackgroundTaskStore(persistence_dir=tmp_path, max_concurrent=3)
    assert store.recover() is True
    _fill(store, 3, prefix="n")


# ---------------------------------------------------------------------------
# Concurrent launches cannot both take the last slot
# ---------------------------------------------------------------------------


def test_concurrent_registers_never_exceed_the_cap():
    """The count is slowed down so that a check made outside ``_lock`` would
    let most of the threads through; held under the lock, exactly ``cap``
    succeed."""
    cap, threads_n = 3, 16
    store = BackgroundTaskStore(max_concurrent=cap)
    real_count = store._in_flight_owned_ids_locked

    def slow_count():
        ids = real_count()
        time.sleep(0.02)
        return ids

    store._in_flight_owned_ids_locked = slow_count
    barrier = threading.Barrier(threads_n)
    ok: List[str] = []
    refused: List[str] = []
    ok_lock = threading.Lock()

    def launch(i: int) -> None:
        barrier.wait()
        try:
            store.register(f"t{i}", "worker", "task")
        except BackgroundCapacityError:
            with ok_lock:
                refused.append(f"t{i}")
        else:
            with ok_lock:
                ok.append(f"t{i}")

    workers = [threading.Thread(target=launch, args=(i,)) for i in range(threads_n)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(10)

    assert len(ok) == cap
    assert len(refused) == threads_n - cap
    assert len(store.list()) == cap


# ---------------------------------------------------------------------------
# The tool: a refused launch leaves nothing behind
# ---------------------------------------------------------------------------


class _Stream:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


def _wrapper(tmp_path, store, stream, release: threading.Event):
    from agentao.agents.tools import AgentToolWrapper
    from agentao.host.projection import HostSubagentEmitter

    def drive(sub_agent, **kw):
        release.wait(10)
        return "done", {
            "agent_name": "worker", "incomplete": None, "turns": 1,
            "tool_calls": 0, "tokens": 1, "duration_ms": 1,
        }

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
    wrapper._build_sub_agent = lambda suppress_output, skill_manager=None: (object(), {})
    wrapper._drive_sub_agent = drive
    wrapper._roll_up_usage = lambda sub_agent: None
    wrapper._close_sub_agent = lambda sub_agent: None
    return wrapper


def test_tool_launch_past_the_cap_raises_before_any_side_effect(tmp_path):
    store = BackgroundTaskStore(max_concurrent=2)
    stream = _Stream()
    release = threading.Event()
    wrapper = _wrapper(tmp_path, store, stream, release)
    try:
        wrapper.execute(task="one", run_in_background=True)
        wrapper.execute(task="two", run_in_background=True)
        spawned_before = [e for e in stream.events if e.phase == "spawned"]
        tokens_before = dict(store._tokens)

        with pytest.raises(BackgroundCapacityError):
            wrapper.execute(task="three", run_in_background=True)

        assert [e for e in stream.events if e.phase == "spawned"] == spawned_before
        assert store._tokens == tokens_before
        assert len(store.list()) == 2
    finally:
        release.set()


def test_schema_states_the_cap(tmp_path):
    release = threading.Event()
    store = BackgroundTaskStore(max_concurrent=4)
    wrapper = _wrapper(tmp_path, store, _Stream(), release)
    text = wrapper.parameters["properties"]["run_in_background"]["description"]
    assert "At most 4 background agents run at the same time" in text

    store.max_concurrent = None
    text = wrapper.parameters["properties"]["run_in_background"]["description"]
    assert "At most" not in text


def test_cli_agent_bg_past_the_cap_prints_a_refusal(tmp_path, monkeypatch):
    """``/agent bg`` goes through the same tool; the refusal must reach the
    user as a line, not as a traceback out of the command handler."""
    from types import SimpleNamespace

    from agentao.cli.commands_ext import agents as agents_mod

    store = BackgroundTaskStore(max_concurrent=1)
    release = threading.Event()
    tool = _wrapper(tmp_path, store, _Stream(), release)
    printed: List[str] = []
    monkeypatch.setattr(
        agents_mod.console, "print",
        lambda *a, **k: printed.append(str(a[0]) if a else ""),
    )
    cli = SimpleNamespace(agent=SimpleNamespace(
        bg_store=store,
        tools=SimpleNamespace(get=lambda name: tool),
    ))
    try:
        agents_mod.handle_agent_command(cli, "bg worker first")
        agents_mod.handle_agent_command(cli, "bg worker second")
    finally:
        release.set()

    assert "started" in printed[0]
    assert "Not started: 1 background agents are already running" in printed[1]
    assert "/agent cancel" in printed[1]
    assert len(store.list()) == 1
