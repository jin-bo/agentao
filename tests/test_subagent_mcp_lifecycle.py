"""A sub-agent lets go of the MCP servers it connected (#239).

Every sub-agent is a fresh ``Agentao`` built inside
``AgentToolWrapper._run_sync`` (``agents/tools/_wrapper.py``), and building one
reads ``mcp.json`` and connects every server again. Nothing closed it, so each
spawn left a stdio server process running until the parent process exited:
the parent's ``close()`` does not reach a local, and collecting the sub-agent
does not end the process.

The server here is a real subprocess that speaks just enough MCP over stdio to
complete a handshake, and it writes a marker when its stdin reaches EOF. That
marker is the signal under test, because EOF is the client letting go. A
counter on ``connect_all`` / ``disconnect_all`` would only show that a call was
made, and there is no portable liveness check for a PID.
"""

from __future__ import annotations

import json
import sys
import textwrap
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


def test_a_background_sub_agent_disconnects_its_mcp_servers(tmp_path, marks, monkeypatch):
    """The same close, on the daemon thread ``_launch_background`` runs it on:
    the sub-agent's MCP event loop is created, run and closed off the main
    thread, and a long session spawns these without ever waiting on one."""
    store = BackgroundTaskStore(persistence_dir=None)
    parent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True, bg_store=store,
    )
    try:
        parents = _pids(marks, "started")
        assert len(parents) == 1

        _replace_chat(monkeypatch, lambda agent: "done")
        parent.tools.tools["agent_generalist"].execute(task="x", run_in_background=True)

        assert _eventually(lambda: store.count_in_flight() == 0)
        (record,) = store.list()
        assert record["status"] == "completed"

        _assert_only_the_child_let_go(marks, parent, parents)
    finally:
        parent.close()
