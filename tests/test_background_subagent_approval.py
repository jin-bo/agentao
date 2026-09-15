"""A background sub-agent refuses the calls that need confirmation.

A background sub-agent runs on its own thread, with nobody to ask. It was built
with no callbacks at all, so it got a ``NullTransport``, and that approves every
confirmation: a call its parent would have put to the user ran unattended. In
one ``agentao run`` process, a foreground sub-agent's confirmation came back
refused and a background one's came back approved
(``docs/design/openworker-borrow-review.zh.md`` §1).

It now refuses. What permission rules allow outright still runs, and a denial
still denies, so a background run can do what the user granted ahead of time
and nothing that needed asking. A foreground sub-agent still asks its parent.

That depends on the sub-agent applying its rules at all, which it did not: the
wrapper rebuilt its permission engine but handed it to the runner, while the
planner that decides kept the ``None`` it was built with. A rule the user wrote
to deny a tool was ignored in every sub-agent.

Tool calls go through the sub-agent's real ``ToolRunner``; only
``Agentao.chat`` is replaced, because a real ``chat()`` is a networked LLM turn.
"""

from __future__ import annotations

import json
import time

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.permissions import PermissionEngine, PermissionMode


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


def _write_call(target):
    return ChatCompletionMessageToolCall(
        id="call-write", type="function",
        function={"name": "write_file", "arguments": json.dumps(
            {"file_path": str(target), "content": "written"},
        )},
    )


def _sub_agents_write(monkeypatch, target):
    """Make every sub-agent call ``write_file`` once; return its results."""
    results = []

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        _, messages = self.tool_runner.execute([_write_call(target)])
        self.messages.extend(messages)
        results.extend(m["content"] for m in messages)
        return ""

    monkeypatch.setattr(Agentao, "chat", chat)
    return results


def _parent(tmp_path, store, rules=None, mode=None, run_deny=None):
    """A parent with ``store``; with ``rules``, ``mode`` or ``run_deny``, a
    permission engine that reads the rules from the user-scope
    ``permissions.json`` and holds ``run_deny`` as ``agentao run``'s spec
    deny rules."""
    engine = None
    if rules is not None or mode is not None or run_deny is not None:
        user_root = tmp_path / "user"
        user_root.mkdir()
        (user_root / "permissions.json").write_text(json.dumps({"rules": rules or []}))
        engine = PermissionEngine(project_root=tmp_path, user_root=user_root)
        if mode is not None:
            engine.set_mode(mode)
        if run_deny is not None:
            engine.add_run_rules(deny=run_deny, source="run-spec")
    return Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True, bg_store=store, permission_engine=engine,
    )


def _eventually(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


def _run(parent, store, where):
    wrapper = parent.tools.tools["agent_generalist"]
    if where == "foreground":
        wrapper._run_sync("x")
        return
    wrapper.execute(task="x", run_in_background=True)
    assert _eventually(lambda: store.count_in_flight() == 0)
    (record,) = store.list()
    assert record["status"] == "completed", record


@pytest.mark.parametrize(
    "where, runs", [("background", False), ("foreground", True)],
)
def test_a_call_that_needs_confirmation_runs_only_where_someone_can_approve_it(
    tmp_path, monkeypatch, where, runs,
):
    """No permission rules, so ``write_file`` asks. The foreground sub-agent
    asks its parent, whose headless transport approves; the background one has
    nobody to ask and refuses."""
    target = tmp_path / "written.txt"
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(tmp_path, store)
    results = _sub_agents_write(monkeypatch, target)
    try:
        _run(parent, store, where)
    finally:
        parent.close()

    assert target.exists() is runs
    if not runs:
        (result,) = results
        assert "declined" in result


@pytest.mark.parametrize(
    "grant",
    [{"rules": [{"tool": "write_file", "action": "allow"}]}, {"mode": PermissionMode.FULL_ACCESS}],
    ids=["allow-rule", "full-access-mode"],
)
def test_a_call_the_user_allowed_still_runs_in_the_background(tmp_path, monkeypatch, grant):
    target = tmp_path / "written.txt"
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(tmp_path, store, **grant)
    _sub_agents_write(monkeypatch, target)
    try:
        _run(parent, store, "background")
    finally:
        parent.close()

    assert target.read_text() == "written"


@pytest.mark.parametrize("where", ["background", "foreground"])
def test_a_call_the_rules_deny_is_denied_in_a_sub_agent(tmp_path, monkeypatch, where):
    """In the foreground this was a bypass: with no engine deciding, the call
    asked the parent, whose headless transport approved it."""
    target = tmp_path / "written.txt"
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(tmp_path, store, rules=[{"tool": "write_file", "action": "deny"}])
    results = _sub_agents_write(monkeypatch, target)
    try:
        _run(parent, store, where)
    finally:
        parent.close()

    assert not target.exists()
    (result,) = results
    assert "not permitted" in result


@pytest.mark.parametrize("where", ["background", "foreground"])
def test_a_call_the_run_spec_denies_is_denied_in_a_sub_agent(tmp_path, monkeypatch, where):
    """``agentao run``'s spec deny rules are held on the parent's engine, not
    in a file, so a sub-agent that rebuilds its engine from disk missed them,
    and the ``workspace-write`` preset then allowed the write they deny."""
    target = tmp_path / "written.txt"
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(
        tmp_path, store,
        mode=PermissionMode.WORKSPACE_WRITE,
        run_deny=[{"tool": "write_file", "action": "deny"}],
    )
    results = _sub_agents_write(monkeypatch, target)
    try:
        _run(parent, store, where)
    finally:
        parent.close()

    assert not target.exists()
    (result,) = results
    assert "not permitted" in result


@pytest.mark.parametrize("where", ["background", "foreground"])
def test_a_rule_the_host_passed_in_code_applies_in_a_sub_agent(tmp_path, monkeypatch, where):
    """A host that builds its engine with ``rules=`` keeps them in memory only.
    A sub-agent that re-read the files got none of them, and the
    ``workspace-write`` preset allowed the write they deny."""
    target = tmp_path / "written.txt"
    store = BackgroundTaskStore(persistence_dir=None)
    engine = PermissionEngine(
        project_root=tmp_path, rules=[{"tool": "write_file", "action": "deny"}],
    )
    parent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True, bg_store=store, permission_engine=engine,
    )
    results = _sub_agents_write(monkeypatch, target)
    try:
        _run(parent, store, where)
    finally:
        parent.close()

    assert not target.exists()
    (result,) = results
    assert "not permitted" in result
