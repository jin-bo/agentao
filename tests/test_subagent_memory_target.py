"""A sub-agent's ``save_memory`` writes where the parent's writes (#260).

Built-ins reach a sub-agent as the child's *own* instances (#255). That is
right for the filesystem and the shell, which are bound to the parent's
backends — and it took the memory manager with it as a side effect. A child's
manager is built bare: project store only, never the one a host injected. So a
long-term memory a sub-agent was asked to save went into a store nothing reads,
a ``scope="user"`` request was downgraded to project without a word, and a host
that injected a ``MemoryManager`` was not in the loop for any of it.

The fix moves exactly one attribute: the sub-agent's ``save_memory`` instance
points at the parent's ``MemoryManager``. Everything else about the child's
memory is still the child's — its session id, the session summaries its own
compaction writes, and the stores its ``close()`` releases — so these tests
hold the line from both sides: the long-term write must land on the parent, and
nothing session-shaped may follow it there.

Every sub-agent call runs through the sub-agent's real ``ToolRunner``. Only
``Agentao.chat`` is replaced, because a real one is a networked LLM turn; the
replacement runs the calls a model would have sent and records their results.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.agents.tools._wrapper import _bind_parent_memory_target
from agentao.memory import MemoryManager
from agentao.memory.storage import SQLiteMemoryStore
from agentao.tools import SaveMemoryTool

from tests.support.tools import NamedTool


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


# The child's own bare manager opens this path (``working_directory`` +
# ``.agentao/memory.db``). The host's stores below live somewhere else on
# purpose, so "landed on the parent's manager" and "landed in the child's own
# store" are two distinguishable outcomes rather than one shared file.
def _child_own_store(tmp_path) -> SQLiteMemoryStore:
    return SQLiteMemoryStore.open(tmp_path / ".agentao" / "memory.db")


def _host_manager(
    tmp_path, *, user_store: bool = True, transient: bool = False
) -> MemoryManager:
    """A host-injected manager. ``transient`` makes ``close()`` actually bite.

    A file-backed store closes its connection at the end of every statement, so
    ``MemoryManager.close()`` on one is a no-op and a test that closed the
    parent's stores by mistake would still pass. The ``:memory:`` backing is the
    one that holds a connection between calls — close it and the next write
    reconnects to an empty database with no schema.
    """
    def _store(name: str) -> SQLiteMemoryStore:
        if transient:
            return SQLiteMemoryStore(":memory:")
        return SQLiteMemoryStore.open(tmp_path / "host" / name)

    return MemoryManager(
        project_store=_store("project.db"),
        user_store=_store("user.db") if user_store else None,
    )


def _parent(tmp_path, *, manager=None, **kwargs) -> Agentao:
    kwargs.setdefault("enable_builtin_agents", True)
    return Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        memory_manager=manager if manager is not None else _host_manager(tmp_path),
        **kwargs,
    )


def _call(name, **arguments):
    return ChatCompletionMessageToolCall(
        id=f"call-{name}", type="function",
        function={"name": name, "arguments": json.dumps(arguments)},
    )


def _sub_agents_call(monkeypatch, *calls, before=None):
    """Make every sub-agent run ``calls`` through its own ``ToolRunner``.

    ``before`` runs against the sub-agent first, for the tests that need the
    child to touch its *own* manager before the tool calls land.
    """
    results, sub_agents = {}, []

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        sub_agents.append(self)
        if before is not None:
            before(self)
        _, messages = self.tool_runner.execute(list(calls))
        self.messages.extend(messages)
        by_id = {m["tool_call_id"]: m["content"] for m in messages}
        results.update({c.function.name: by_id[c.id] for c in calls})
        return ""

    monkeypatch.setattr(Agentao, "chat", chat)
    return results, sub_agents


def _run(parent, agent="agent_generalist"):
    return parent.tools.tools[agent]._run_sync("x")


def _keys(manager, **kwargs):
    return {e.key_normalized for e in manager.get_all_entries(**kwargs)}


def _eventually(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


# ── the write lands on the parent's manager ─────────────────────────────────


class TestTheWriteLandsOnTheParentsManager:
    def test_a_sub_agents_save_goes_through_the_hosts_injected_manager(
        self, tmp_path, monkeypatch
    ):
        manager = _host_manager(tmp_path)
        parent = _parent(tmp_path, manager=manager)
        results, _ = _sub_agents_call(
            monkeypatch,
            _call("save_memory", key="build_command", value="uv run pytest"),
        )
        try:
            _run(parent)
        finally:
            parent.close()

        assert "Saved memory" in results["save_memory"]
        assert "build_command" in _keys(manager)

    def test_an_explicit_user_scope_reaches_the_parents_user_store(
        self, tmp_path, monkeypatch
    ):
        manager = _host_manager(tmp_path)
        parent = _parent(tmp_path, manager=manager)
        results, _ = _sub_agents_call(
            monkeypatch,
            _call(
                "save_memory", key="preferred_language",
                value="Reply in Chinese", scope="user",
            ),
        )
        try:
            _run(parent)
        finally:
            parent.close()

        assert "Saved memory" in results["save_memory"]
        # The point of the issue: this used to be downgraded to project scope
        # on a manager that had no user store, on a parent that did.
        assert "preferred_language" in _keys(manager, scope="user")
        assert "preferred_language" not in _keys(manager, scope="project")

    def test_nothing_is_written_to_the_childs_own_project_store(
        self, tmp_path, monkeypatch
    ):
        parent = _parent(tmp_path)
        _sub_agents_call(
            monkeypatch,
            _call("save_memory", key="build_command", value="uv run pytest"),
        )
        try:
            _run(parent)
        finally:
            parent.close()

        # Opened fresh: the child's own store is closed with the child, and
        # the question is what is on disk at the path its bare manager used.
        own = _child_own_store(tmp_path)
        try:
            assert own.list_memories() == []
        finally:
            own.close()


    def test_a_background_sub_agent_writes_through_the_parents_store(
        self, tmp_path, monkeypatch
    ):
        """The rebind's one genuinely cross-thread caller.

        A background sub-agent runs on a thread of its own, and the parent's
        manager is now what it writes through. On the transient backing that is
        a single shared sqlite3 connection, which used to refuse a write from
        any other thread — so this is the case that pays for the locking in
        ``SQLiteMemoryStore._connect``. Transient on purpose: the file backing
        opens a private connection per statement and would pass either way.
        """
        manager = _host_manager(tmp_path, transient=True)
        parent = _parent(
            tmp_path, manager=manager, bg_store=BackgroundTaskStore(persistence_dir=None),
        )
        _sub_agents_call(
            monkeypatch, _call("save_memory", key="from_background", value="v"),
        )
        try:
            parent.tools.tools["agent_generalist"].execute("x", run_in_background=True)
            assert _eventually(lambda: "from_background" in _keys(manager))
            # Let the background thread finish before ``parent.close()`` takes
            # the manager out from under it: the write it is verified to have
            # made is not the last thing it does, and closing a transient store
            # mid-run is a different bug's territory.
            assert _eventually(
                lambda: not any(
                    t.is_alive() for t in threading.enumerate()
                    if t.name.startswith("bg-agent-")
                )
            )
        finally:
            parent.close()


# ── only the write target moves ─────────────────────────────────────────────


class TestOnlyTheWriteTargetMoves:
    def test_the_child_keeps_its_own_manager_and_rebinds_only_the_tool(
        self, tmp_path, monkeypatch
    ):
        manager = _host_manager(tmp_path)
        parent = _parent(tmp_path, manager=manager)
        _, sub_agents = _sub_agents_call(
            monkeypatch, _call("save_memory", key="k", value="v"),
        )
        try:
            _run(parent)
        finally:
            parent.close()

        child = sub_agents[0]
        assert child.tools.tools["save_memory"].memory_manager is manager
        # …and that is the whole of it. The agent property still answers with
        # the child's own manager, which is what carries the session id and
        # what ``close()`` releases.
        assert child.memory_manager is not manager
        assert child.context_manager.memory_manager is child.memory_manager

    def test_a_childs_session_summary_does_not_land_in_the_parents_store(
        self, tmp_path, monkeypatch
    ):
        manager = _host_manager(tmp_path)
        parent = _parent(tmp_path, manager=manager)
        _sub_agents_call(
            monkeypatch,
            _call("save_memory", key="build_command", value="uv run pytest"),
            before=lambda child: child.memory_manager.save_session_summary(
                "the child compacted its own history"
            ),
        )
        try:
            _run(parent)
        finally:
            parent.close()

        # The long-term memory crossed over; the session summary did not.
        assert "build_command" in _keys(manager)
        assert manager.project_store.list_session_summaries() == []

    def test_closing_the_sub_agent_leaves_the_parents_stores_usable(
        self, tmp_path, monkeypatch
    ):
        # Transient stores on purpose: ``close()`` on a file-backed store is a
        # no-op, so this is the only backing on which closing the parent's
        # stores by mistake is something a test can see.
        manager = _host_manager(tmp_path, transient=True)
        parent = _parent(tmp_path, manager=manager)
        _sub_agents_call(
            monkeypatch, _call("save_memory", key="from_child", value="v"),
        )
        try:
            # ``_run_sync`` closes the sub-agent in a ``finally``. Handing the
            # child the manager itself would have closed the parent's stores
            # the moment the sub-task finished.
            _run(parent)
            assert "Saved memory" in manager.save_from_tool("from_parent", "v", [])
            assert _keys(manager) >= {"from_child", "from_parent"}
        finally:
            parent.close()


# ── the narrowing rules still decide who gets the tool ──────────────────────


class TestTheNarrowingRulesStillGovern:
    def test_a_definition_that_does_not_list_save_memory_does_not_get_it(
        self, tmp_path, monkeypatch
    ):
        parent = _parent(tmp_path)
        results, sub_agents = _sub_agents_call(
            monkeypatch, _call("save_memory", key="k", value="v"),
        )
        try:
            _run(parent, agent="agent_codebase_investigator")
        finally:
            parent.close()

        assert "save_memory" not in sub_agents[0].tools.tools
        assert "not found" in results["save_memory"]

    def test_a_tool_the_parent_disabled_does_not_come_back(
        self, tmp_path, monkeypatch
    ):
        parent = _parent(tmp_path, disable_tools={"save_memory"})
        results, sub_agents = _sub_agents_call(
            monkeypatch, _call("save_memory", key="k", value="v"),
        )
        try:
            _run(parent)
        finally:
            parent.close()

        assert "save_memory" not in parent.tools.tools
        assert "save_memory" not in sub_agents[0].tools.tools
        assert "not found" in results["save_memory"]

    def test_a_host_tool_named_save_memory_is_not_swapped_for_the_built_in(
        self, tmp_path, monkeypatch
    ):
        parent = _parent(tmp_path)
        parent.add_tool(NamedTool("save_memory"), replace=True)
        results, sub_agents = _sub_agents_call(
            monkeypatch, _call("save_memory", key="k", value="v"),
        )
        try:
            _run(parent)
        finally:
            parent.close()

        # The host's replacement declares no ``copies_to_subagents``, so it is
        # left out *by name* — the built-in it replaced must not reappear
        # underneath it just because the rebind would have worked on it.
        assert "save_memory" not in sub_agents[0].tools.tools
        assert "not found" in results["save_memory"]


# ── the rebind fails closed ─────────────────────────────────────────────────


class TestTheRebindFailsClosed:
    """Leaving the tool out beats letting it write into the dark.

    Absent, the model is told the tool does not exist, which is true. Present
    and unrebound, it answers "Saved memory: x" for a write into a store
    nothing will ever read — which is the defect this whole file is about.
    """

    def _run_with_broken_parent_tool(self, tmp_path, monkeypatch, break_it):
        parent = _parent(tmp_path)
        break_it(parent.tools.tools["save_memory"])
        results, sub_agents = _sub_agents_call(
            monkeypatch, _call("save_memory", key="k", value="v"),
        )
        try:
            _run(parent)
        finally:
            parent.close()
        return results, sub_agents

    def test_a_parent_instance_with_no_memory_manager_leaves_the_tool_out(
        self, tmp_path, monkeypatch, caplog
    ):
        def _break(tool):
            del tool.memory_manager

        with caplog.at_level(logging.WARNING, logger="agentao.agents.tools._wrapper"):
            results, sub_agents = self._run_with_broken_parent_tool(
                tmp_path, monkeypatch, _break,
            )

        assert "save_memory" not in sub_agents[0].tools.tools
        assert "not found" in results["save_memory"]
        assert any("save_memory" in r.getMessage() for r in caplog.records)

    def test_a_parent_manager_of_none_leaves_the_tool_out(
        self, tmp_path, monkeypatch, caplog
    ):
        def _break(tool):
            tool.memory_manager = None

        with caplog.at_level(logging.WARNING, logger="agentao.agents.tools._wrapper"):
            results, sub_agents = self._run_with_broken_parent_tool(
                tmp_path, monkeypatch, _break,
            )

        assert "save_memory" not in sub_agents[0].tools.tools
        assert "not found" in results["save_memory"]
        assert any(
            "no memory manager" in r.getMessage() for r in caplog.records
        )

    def test_an_own_instance_that_keeps_no_memory_manager_is_refused(self, tmp_path):
        parent_tool = SaveMemoryTool(memory_manager=_host_manager(tmp_path))
        assert _bind_parent_memory_target(NamedTool("save_memory"), parent_tool, "a") is False

    def test_an_own_instance_that_will_not_take_the_attribute_is_refused(
        self, tmp_path
    ):
        class ReadOnlyTarget(NamedTool):
            @property
            def memory_manager(self):  # no setter: assignment raises
                return None

        parent_tool = SaveMemoryTool(memory_manager=_host_manager(tmp_path))
        own = ReadOnlyTarget("save_memory")
        # ``hasattr`` is satisfied, so this is the branch after it.
        assert hasattr(own, "memory_manager")
        assert _bind_parent_memory_target(own, parent_tool, "a") is False

    def test_a_successful_rebind_reports_true_and_moves_the_target(self, tmp_path):
        manager = _host_manager(tmp_path)
        own = SaveMemoryTool(memory_manager=_host_manager(tmp_path))
        assert _bind_parent_memory_target(
            own, SaveMemoryTool(memory_manager=manager), "a",
        ) is True
        assert own.memory_manager is manager


# ── the scope downgrade says so ─────────────────────────────────────────────


class TestTheScopeDowngradeIsLogged:
    """Nothing distinguished "project because you asked" from "project because
    this manager has no user store". The behaviour is unchanged; the silence
    is not."""

    def _project_only(self, tmp_path) -> MemoryManager:
        return _host_manager(tmp_path, user_store=False)

    def test_an_explicit_user_scope_request_warns(self, tmp_path, caplog):
        manager = self._project_only(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="agentao.memory.manager"):
            saved = manager.save_from_tool("k", "v", [], scope="user")

        assert "Saved memory" in saved
        assert "k" in _keys(manager, scope="project")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "user scope" in warnings[0].getMessage()

    def test_an_inferred_user_scope_does_not_warn(self, tmp_path, caplog):
        manager = self._project_only(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="agentao.memory.manager"):
            # Classified user-scope by the ``user_`` prefix, not asked for.
            # On a project-only manager that is the ordinary case, and a
            # warning on most writes trains the reader to ignore all of them.
            manager.save_from_tool("user_preferred_language", "zh", [])

        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
        assert any(
            r.levelno == logging.DEBUG and "project scope" in r.getMessage()
            for r in caplog.records
        )

    def test_a_configured_user_store_neither_downgrades_nor_logs(
        self, tmp_path, caplog
    ):
        manager = _host_manager(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="agentao.memory.manager"):
            manager.save_from_tool("k", "v", [], scope="user")

        assert "k" in _keys(manager, scope="user")
        # Scoped to this module's logger: ``caplog`` captures at the root, so a
        # bare ``== []`` fails on any unrelated debug line from anywhere in
        # agentao.
        assert [
            r for r in caplog.records if r.name == "agentao.memory.manager"
        ] == []

    def test_the_downgrade_log_carries_no_memory_content(self, tmp_path, caplog):
        manager = self._project_only(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="agentao.memory.manager"):
            manager.save_from_tool(
                "api_endpoint", "https://internal.example/secret-path", [],
                scope="user",
            )

        text = " ".join(r.getMessage() for r in caplog.records)
        assert "secret-path" not in text
        assert "api_endpoint" not in text


class TestARaisingPropertyDoesNotAbortTheSpawn:
    """``hasattr`` swallows only ``AttributeError``.

    Every other read in ``_bind_parent_memory_target`` is wrapped, and this one
    was not, so a ``memory_manager`` property raising anything else propagated
    out of ``_narrow_tools`` and failed the whole sub-task — the one outcome a
    function documented as fail-closed exists to avoid.
    """

    def test_a_raising_property_on_the_childs_instance_is_refused(self, tmp_path):
        class Exploding(NamedTool):
            @property
            def memory_manager(self):
                raise RuntimeError("boom")

        parent_tool = SaveMemoryTool(memory_manager=_host_manager(tmp_path))
        assert _bind_parent_memory_target(
            Exploding("save_memory"), parent_tool, "a",
        ) is False

    def test_a_raising_property_on_the_parents_instance_is_refused(self, tmp_path):
        class Exploding(NamedTool):
            @property
            def memory_manager(self):
                raise RuntimeError("boom")

        own = SaveMemoryTool(memory_manager=_host_manager(tmp_path))
        assert _bind_parent_memory_target(
            own, Exploding("save_memory"), "a",
        ) is False
