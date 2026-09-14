"""A sub-agent lets go of the MCP servers it connected (#239).

Every sub-agent is a fresh ``Agentao`` built by
``AgentToolWrapper._build_sub_agent`` (``agents/tools/_wrapper.py``), and building one
reads ``mcp.json`` and connects every server again. Nothing closed it, so each
spawn left a stdio server process running until the parent process exited:
the parent's ``close()`` does not reach a local, and collecting the sub-agent
does not end the process.

The server here is a real subprocess that speaks just enough MCP over stdio to
complete a handshake, and it writes a marker when its stdin reaches EOF. That
marker is the signal under test, because EOF is the client letting go. A
counter on ``connect_all`` / ``disconnect_all`` would only show that a call was
made, and there is no portable liveness check for a PID.

A background run closes the sub-agent only after its outcome is published,
because the close can block for seconds on a server that ignores EOF. Those
tests hold the sub-agent's close open and check that the outcome is already
visible and the child's server still attached, then release it.
"""

from __future__ import annotations

import json
import logging
import sys
import textwrap
import threading
import time

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.cancellation import CancellationToken
from agentao.embedding.permission_loader import PermissionConfigError
from agentao.permissions import PermissionEngine

# Echoing the client's protocol version is fine here: this suite tests
# lifecycle, not negotiation (``tests/support/mcp.py`` explains why a
# negotiation test must not echo).
_SERVER = textwrap.dedent('''
    import json, os, sys
    from pathlib import Path

    marks = Path(sys.argv[1])
    marks.mkdir(parents=True, exist_ok=True)
    (marks / f"started-{os.getpid()}").touch()
    out = sys.stdout.buffer

    def reply(msg_id, **body):
        out.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, **body}).encode() + b"\\n")
        out.flush()

    for line in iter(sys.stdin.buffer.readline, b""):
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            reply(msg["id"], result={
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lifecycle-probe", "version": "0"},
            })
        elif msg["method"] == "tools/list":
            reply(msg["id"], result={"tools": []})
        else:
            reply(msg["id"], error={"code": -32601, "message": msg["method"]})

    (marks / f"eof-{os.getpid()}").touch()
''')


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


@pytest.fixture
def marks(tmp_path):
    script = tmp_path / "lifecycle_probe_server.py"
    script.write_text(_SERVER)
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "probe": {"command": sys.executable, "args": [str(script), str(tmp_path / "marks")]},
    }}))
    return tmp_path / "marks"


def _pids(marks, kind):
    return {p.name.split("-", 1)[1] for p in marks.glob(f"{kind}-*")}


def _eof_within(marks, pids, timeout=10.0):
    """The subset of ``pids`` whose server has seen EOF, waiting up to ``timeout``."""
    deadline = time.monotonic() + timeout
    while True:
        seen = _pids(marks, "eof") & pids
        if seen == pids or time.monotonic() > deadline:
            return seen
        time.sleep(0.05)


def _eventually(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.05)
    return predicate()


def _assert_only_the_child_let_go(marks, parent, parents):
    # The premise: the sub-agent connected servers of its own. If a later
    # change stops it from connecting at all, rewrite this test around
    # that, do not just drop the check below.
    children = _pids(marks, "started") - parents
    assert len(children) == 1

    assert _eof_within(marks, children) == children
    # A wrongly closed parent server writes its marker a moment later, not at
    # once, so a single look right after the child's would miss it.
    assert not _eof_within(marks, parents, timeout=0.5)
    assert [s["status"] for s in parent.mcp_manager.get_server_status()] == ["connected"]


def _replace_chat(monkeypatch, body):
    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        return body(self)

    monkeypatch.setattr(Agentao, "chat", chat)


class _Boom(Exception):
    pass


# Each exit path arranges its case and returns ``(raises, kwargs, result)``: the
# exception ``_run_sync`` must raise (``None`` when it returns), the keyword
# arguments to call it with, and the result it must return.


def _finishes(monkeypatch, tmp_path):
    _replace_chat(monkeypatch, lambda agent: "done")
    return None, {}, "done"


def _raises(monkeypatch, tmp_path):
    def boom(agent):
        raise _Boom

    _replace_chat(monkeypatch, boom)
    return _Boom, {}, None


def _is_cancelled(monkeypatch, tmp_path):
    # The real ``chat()`` runs here, because it does not raise on cancellation:
    # ``runtime/turn.py`` absorbs ``AgentCancelledError`` and returns a marker,
    # so a cancelled sub-agent leaves ``_run_sync`` through its ordinary return.
    # The token is cancelled before the first LLM call, so nothing is sent.
    token = CancellationToken()
    token.cancel("user-cancel")
    return None, {"cancellation_token": token}, "[Cancelled: user-cancel]"


def _fails_in_setup(monkeypatch, tmp_path):
    # The sub-agent's permission rules are loaded after construction, and a
    # malformed user-scope file fails closed (the project-scope file is ignored,
    # so it cannot trigger this). Written only after the parent is built, so
    # the parent is unaffected.
    (tmp_path / "user" / "permissions.json").write_text("{not json")
    _replace_chat(monkeypatch, lambda agent: pytest.fail("chat() ran past a failed setup"))
    return PermissionConfigError, {}, None


@pytest.mark.parametrize(
    "exit_path", [_finishes, _raises, _is_cancelled, _fails_in_setup],
    ids=["finishes", "raises", "cancelled", "setup-fails"],
)
def test_a_sub_agent_disconnects_its_mcp_servers_on_every_exit_path(
    tmp_path, marks, monkeypatch, exit_path,
):
    (tmp_path / "user").mkdir()
    parent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True,
        permission_engine=PermissionEngine(project_root=tmp_path, user_root=tmp_path / "user"),
    )
    try:
        parents = _pids(marks, "started")
        assert len(parents) == 1

        raises, kwargs, expected = exit_path(monkeypatch, tmp_path)
        run = parent.tools.tools["agent_generalist"]._run_sync
        if raises is None:
            result, stats = run("x", **kwargs)
            # Stats read the sub-agent's history, so they have to be taken
            # before it is closed.
            assert result == expected
            assert stats["agent_name"] == "generalist"
        else:
            with pytest.raises(raises):
                run("x", **kwargs)

        _assert_only_the_child_let_go(marks, parent, parents)
    finally:
        parent.close()


# ── background: the outcome is published before the close ──────────────────
#
# Each exit path arranges its case and returns ``(during, expected, answer)``:
# a callable run with the task id right after launch, the record fields the
# settled task must carry, and text its result must contain (``None`` when
# there is no result to check).


def _bg_finishes(monkeypatch, store, tmp_path):
    _replace_chat(monkeypatch, lambda agent: "done")
    return (lambda agent_id: None), {"status": "completed"}, "done"


def _bg_raises(monkeypatch, store, tmp_path):
    def boom(agent):
        raise _Boom("boom")

    _replace_chat(monkeypatch, boom)
    return (lambda agent_id: None), {"status": "failed", "error": "boom"}, None


def _bg_is_cancelled(monkeypatch, store, tmp_path):
    # A real cancel of a running task: the sub-agent is inside ``chat()`` when
    # the store signals its token, and the real ``chat()`` then stops before
    # its first LLM call. The stub waits on the token itself rather than on an
    # event of its own, so the test's teardown, which cancels whatever is
    # still in flight, also releases it when an assertion fails first.
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

    def cancel(agent_id):
        assert in_chat.wait(10)
        assert store.cancel(agent_id).startswith("Cancellation signal sent")

    # Today's value, pinned on purpose: a running cancel is recorded as
    # ``failed`` rather than ``cancelled`` (#244). This suite tests when the
    # close happens, not that vocabulary; whoever fixes #244 updates this.
    return cancel, {"status": "failed", "incomplete_reason": "cancelled"}, None


def _bg_fails_in_setup(monkeypatch, store, tmp_path):
    # As ``_fails_in_setup``: the background path composes build / drive /
    # close itself, so its setup failure needs its own case.
    (tmp_path / "user" / "permissions.json").write_text("{not json")
    _replace_chat(monkeypatch, lambda agent: pytest.fail("chat() ran past a failed setup"))
    return (lambda agent_id: None), {"status": "failed", "incomplete_reason": None}, None


def _hold_sub_agent_close(monkeypatch):
    """Make every sub-agent's ``close()`` wait until released, then run the
    real one. The parent's close runs straight through."""
    closing, release = threading.Event(), threading.Event()
    real_close = Agentao.close

    def close(self):
        if self._drains_background_notifications is False:
            closing.set()
            release.wait(10)
        real_close(self)

    monkeypatch.setattr(Agentao, "close", close)
    return closing, release


@pytest.mark.parametrize(
    "exit_path", [_bg_finishes, _bg_raises, _bg_is_cancelled, _bg_fails_in_setup],
    ids=["finishes", "raises", "cancelled", "setup-fails"],
)
def test_a_background_sub_agent_publishes_its_outcome_before_it_closes(
    tmp_path, marks, monkeypatch, exit_path,
):
    (tmp_path / "user").mkdir()
    store = BackgroundTaskStore(persistence_dir=None)
    parent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True, bg_store=store,
        permission_engine=PermissionEngine(project_root=tmp_path, user_root=tmp_path / "user"),
    )
    closing, release = _hold_sub_agent_close(monkeypatch)
    try:
        parents = _pids(marks, "started")
        assert len(parents) == 1

        during, expected, answer = exit_path(monkeypatch, store, tmp_path)
        parent.tools.tools["agent_generalist"].execute(task="x", run_in_background=True)
        (record,) = store.list()
        agent_id = record["id"]
        during(agent_id)

        assert closing.wait(10), "the sub-agent was never closed"

        # While the close is held, the outcome is already out.
        record = store.get(agent_id)
        assert {key: record[key] for key in expected} == expected
        checked = parent.tools.tools["check_background_agent"].execute(agent_id=agent_id)
        if answer is not None:
            assert answer in record["result"]
            assert answer in checked
        notes = store.drain_notifications()
        assert len(notes) == 1 and f"(ID: {agent_id})" in notes[0]
        assert store.get_token(agent_id) is None
        assert "nothing to cancel" in store.cancel(agent_id)
        # And the close really is still pending: the child's server is attached.
        children = _pids(marks, "started") - parents
        assert len(children) == 1
        assert not _eof_within(marks, children, timeout=0.5)

        release.set()
        _assert_only_the_child_let_go(marks, parent, parents)
    finally:
        # A failed assertion above can leave the worker blocked in a stub;
        # cancelling what is still in flight lets it finish without an LLM call.
        for task in store.list():
            store.cancel(task["id"])
        release.set()
        parent.close()


# ── a failing close ─────────────────────────────────────────────────────────


def test_a_failing_close_is_logged_and_does_not_replace_the_outcome(
    tmp_path, monkeypatch, caplog,
):
    store = BackgroundTaskStore(persistence_dir=None)
    parent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True, bg_store=store,
    )
    real_close = Agentao.close

    def close(self):
        if self._drains_background_notifications is False:
            raise RuntimeError("close failed")
        real_close(self)

    monkeypatch.setattr(Agentao, "close", close)
    _replace_chat(monkeypatch, lambda agent: "done")
    wrapper = parent.tools.tools["agent_generalist"]
    try:
        with caplog.at_level(logging.WARNING, logger="agentao.agents.tools._wrapper"):
            result, _ = wrapper._run_sync("x")
            assert result == "done"

            wrapper.execute(task="x", run_in_background=True)
            assert _eventually(lambda: store.count_in_flight() == 0)
            (record,) = store.list()
            assert record["status"] == "completed"
            # The background close runs after the record settles, so wait for
            # its log line rather than reading the log straight away.
            assert _eventually(lambda: len(_close_failures(caplog)) == 2)
    finally:
        parent.close()


def _close_failures(caplog):
    return [
        r for r in caplog.records
        if r.getMessage() == "Closing a sub-agent failed (generalist)" and r.exc_info
    ]
