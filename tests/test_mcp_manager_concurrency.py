"""MCP calls from many threads share one loop thread (#241) and one owner task
per connection (#243).

The tool executor runs a batch's tool calls on parallel threads, and every MCP
tool of an agent goes through its ``McpClientManager``. The manager used to run
its loop inside whichever caller got there first, so a second caller got "This
event loop is already running" and its call was lost. It also entered each
connection in one task and exited it from another, which logged "Attempted to
exit cancel scope in a different task" on every disconnect.

The server is a real subprocess speaking just enough MCP over stdio, written
without the SDK so it works on both SDK majors. It records what these tests
need on disk: a ``started-<pid>`` per launch, ``called-<tool>`` when a call
arrives, ``overlap`` when two calls are in flight at once, and ``eof-<pid>``
when its stdin closes. A ``refuse`` file makes it exit at launch; a ``mute``
file makes it read stdin and never answer; a ``hang`` file makes it leave tool
calls unanswered; a ``deafen`` file makes it close its stdin after a tool call
and stay alive.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import signal
import sys
import textwrap
import threading
import time

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from agentao.agent import Agentao
from agentao.mcp.client import (
    McpClient,
    McpClientManager,
    McpManagerClosedError,
    ServerStatus,
)

# Echoing the client's protocol version is fine: this suite tests call
# scheduling and connection lifetime, not negotiation (``tests/support/mcp.py``
# explains why a negotiation test must not echo).
_SERVER = textwrap.dedent('''
    import json, os, sys, threading, time
    from pathlib import Path

    marks = Path(sys.argv[1])
    delay = float(sys.argv[2])
    marks.mkdir(parents=True, exist_ok=True)
    (marks / f"started-{os.getpid()}").touch()
    if (marks / "refuse").exists():
        sys.exit(1)
    if (marks / "mute").exists():
        for _ in iter(sys.stdin.buffer.readline, b""):
            pass
        (marks / f"eof-{os.getpid()}").touch()
        sys.exit(0)

    out = sys.stdout.buffer
    write_lock = threading.Lock()
    active = [0]
    tools = [{"name": n, "inputSchema": {"type": "object"}} for n in ("slow_a", "slow_b", "slow_c")]

    def reply(msg_id, **body):
        with write_lock:
            out.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, **body}).encode() + b"\\n")
            out.flush()

    def call(msg):
        name = msg["params"]["name"]
        with write_lock:
            active[0] += 1
            if active[0] >= 2:
                (marks / "overlap").touch()
        (marks / f"called-{name}").touch()
        if (marks / "hang").exists():
            return  # never answered
        time.sleep(delay)
        with write_lock:
            active[0] -= 1
        reply(msg["id"], result={"content": [{"type": "text", "text": name}], "isError": False})

    for line in iter(sys.stdin.buffer.readline, b""):
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg["method"]
        if method == "initialize":
            reply(msg["id"], result={
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "concurrency-probe", "version": "0"},
            })
        elif method == "tools/list":
            reply(msg["id"], result={"tools": tools})
        elif method == "tools/call":
            threading.Thread(target=call, args=(msg,), daemon=True).start()
            if (marks / "deafen").exists():
                # Stop reading but stay alive with stdout open: the client's
                # next write fails, and it never sees end-of-file.
                os.close(0)  # ``sys.stdin.close()`` leaves the descriptor open
                time.sleep(30)
                sys.exit(0)
        else:
            reply(msg["id"], error={"code": -32601, "message": method})

    (marks / f"eof-{os.getpid()}").touch()
''')

_TEARDOWN_WARNINGS = ("cancel scope", "Error disconnecting")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


@pytest.fixture
def server(tmp_path):
    """Build ``(config, marks)`` for a stdio server whose calls take ``delay`` seconds."""
    script = tmp_path / "concurrency_probe_server.py"
    script.write_text(_SERVER)
    marks = tmp_path / "marks"

    def make(delay=0.5):
        config = {"command": sys.executable, "args": [str(script), str(marks), str(delay)], "trust": True}
        return config, marks

    return make


def _eventually(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


def _started(marks):
    return sorted(marks.glob("started-*"))


def _connected(config):
    manager = McpClientManager({"probe": config})
    try:
        manager.connect_all()
        assert [s["status"] for s in manager.get_server_status()] == ["connected"]
    except BaseException:
        # The caller's ``finally`` never sees a manager this did not return.
        manager.disconnect_all(timeout=0)
        raise
    return manager


def _in_thread(fn, outcome, key):
    def run():
        try:
            outcome[key] = fn()
        except BaseException as exc:  # recorded, asserted by the test
            outcome[key] = exc

    # Daemon: a regression that leaves a call blocked forever must fail the
    # test, not keep the interpreter from exiting after it.
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _teardown_warnings(caplog):
    return [
        r.getMessage() for r in caplog.records
        if any(text in r.getMessage() for text in _TEARDOWN_WARNINGS)
    ]


def test_two_mcp_calls_in_one_batch_run_at_the_same_time(tmp_path, server):
    """The user-visible case, through the real ``ToolRunner``: one model
    response with two MCP tool calls, which the executor runs on two threads.
    Both come back, and the server saw them in flight together."""
    config, marks = server()
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "mcp.json").write_text(json.dumps({"mcpServers": {"probe": config}}))
    agent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
    )
    try:
        calls = [
            ChatCompletionMessageToolCall(
                id=f"c-{name}", type="function",
                function={"name": f"mcp_probe_{name}", "arguments": "{}"},
            )
            for name in ("slow_a", "slow_b")
        ]
        _, messages = agent.tool_runner.execute(calls)
        assert {m["tool_call_id"]: m["content"] for m in messages} == {
            "c-slow_a": "slow_a",
            "c-slow_b": "slow_b",
        }
        assert (marks / "overlap").exists()
    finally:
        agent.close()


def _kill(pid):
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def _fail_together_on_the_current_session(manager):
    """Hold calls on the live session until two have arrived, so both run on
    it — and both fail on it — rather than the second finding it replaced."""
    session = manager.get_client("probe")._session
    real_call = session.call_tool
    arrived = {"n": 0}
    both = threading.Event()

    async def gated(*args, **kwargs):
        arrived["n"] += 1
        if arrived["n"] == 2:
            both.set()
        while not both.is_set():
            await asyncio.sleep(0.01)
        return await real_call(*args, **kwargs)

    session.call_tool = gated


def test_two_calls_that_fail_on_one_dropped_connection_reconnect_once(server, caplog):
    config, marks = server(delay=0.1)
    manager = _connected(config)
    try:
        (first,) = _started(marks)
        _kill(int(first.name.split("-", 1)[1]))
        _fail_together_on_the_current_session(manager)

        outcome = {}
        with caplog.at_level(logging.WARNING, logger="agentao.mcp"):
            threads = [
                _in_thread(lambda n=name: manager.call_tool("probe", n, {}), outcome, name)
                for name in ("slow_a", "slow_b")
            ]
            for thread in threads:
                thread.join(20)

        assert outcome == {"slow_a": "slow_a", "slow_b": "slow_b"}
        # One launch for the original connection, one for the reconnect. A
        # second caller that tore down the first one's new connection would
        # have launched a third.
        assert len(_started(marks)) == 2
        # Only #243's warning: closing a transport whose server was killed can
        # rightly report the broken pipe (seen on Linux with mcp 1.30).
        assert [w for w in _teardown_warnings(caplog) if "cancel scope" in w] == []
    finally:
        manager.disconnect_all()


_CALLS = ("slow_a", "slow_b", "slow_c")


def _call_each(manager, names, outcome):
    return [
        _in_thread(lambda n=name: manager.call_tool("probe", n, {}), outcome, name)
        for name in names
    ]


def test_calls_waiting_on_a_server_that_dies_all_retry(server):
    """Three calls are waiting for their answers when the server dies. Before
    mcp 1.30, the session fails pending requests from a loop over a dict that
    each answer shrinks, so it raises after the second and never answers the
    third. A call must not rely on the session to learn that its connection is
    gone. All three retry, over one reconnect."""
    config, marks = server(delay=0.1)
    manager = _connected(config)
    outcome = {}
    try:
        (first,) = _started(marks)
        (marks / "hang").touch()
        threads = _call_each(manager, _CALLS, outcome)
        assert _eventually(lambda: all((marks / f"called-{n}").exists() for n in _CALLS))
        (marks / "hang").unlink()
        _kill(int(first.name.split("-", 1)[1]))
        for thread in threads:
            thread.join(20)

        assert outcome == {n: n for n in _CALLS}
        assert len(_started(marks)) == 2
    finally:
        manager.disconnect_all()


def test_calls_waiting_when_the_transport_fails_all_retry(server):
    """The server stops reading but stays alive, so the next write to it fails
    and no end-of-file ever arrives. mcp 1.x's task group then cancels its own
    receive loop, which leaves every pending request unanswered. This is what
    a server dying mid-call can look like on Linux, where the write can fail
    before the read sees end-of-file (seen in CI on 1.26)."""
    config, marks = server(delay=0.1)
    manager = _connected(config)
    outcome = {}
    try:
        (marks / "hang").touch()
        (marks / "deafen").touch()
        threads = _call_each(manager, ["slow_a"], outcome)
        assert _eventually(lambda: (marks / "called-slow_a").exists())
        (marks / "hang").unlink()
        (marks / "deafen").unlink()
        threads += _call_each(manager, ["slow_b"], outcome)
        for thread in threads:
            thread.join(20)

        assert outcome == {"slow_a": "slow_a", "slow_b": "slow_b"}
        assert len(_started(marks)) == 2
    finally:
        manager.disconnect_all()


def test_two_calls_that_find_the_server_gone_try_to_reconnect_once(server):
    """When the reconnect itself fails, the second caller takes that result
    instead of paying for another attempt against the same dead server."""
    config, marks = server(delay=0.1)
    manager = _connected(config)
    try:
        (first,) = _started(marks)
        (marks / "refuse").touch()
        _kill(int(first.name.split("-", 1)[1]))
        _fail_together_on_the_current_session(manager)

        outcome = {}
        threads = [
            _in_thread(lambda n=name: manager.call_tool("probe", n, {}), outcome, name)
            for name in ("slow_a", "slow_b")
        ]
        for thread in threads:
            thread.join(30)

        assert set(outcome) == {"slow_a", "slow_b"}
        assert all(
            isinstance(text, str) and text.startswith("MCP connection error for 'probe'")
            for text in outcome.values()
        ), outcome
        assert len(_started(marks)) == 2
    finally:
        manager.disconnect_all()


def test_disconnect_all_waits_for_a_call_in_flight(server):
    config, marks = server(delay=0.5)
    manager = _connected(config)
    outcome = {}
    worker = _in_thread(lambda: manager.call_tool("probe", "slow_a", {}), outcome, "call")
    try:
        assert _eventually(lambda: (marks / "called-slow_a").exists())
        manager.disconnect_all(timeout=5.0)
    finally:
        # Idempotent; closes the manager when the assertion above failed.
        manager.disconnect_all(timeout=0)
        worker.join(10)

    assert outcome == {"call": "slow_a"}
    assert manager.clients == {}
    assert not manager._thread.is_alive()
    assert _eventually(lambda: any(marks.glob("eof-*")))


def test_disconnect_all_cancels_a_call_that_outlives_its_budget(server):
    config, marks = server(delay=60.0)
    manager = _connected(config)
    outcome = {}
    worker = _in_thread(lambda: manager.call_tool("probe", "slow_a", {}), outcome, "call")
    try:
        assert _eventually(lambda: (marks / "called-slow_a").exists())
        started = time.monotonic()
        manager.disconnect_all(timeout=0.2)
        elapsed = time.monotonic() - started
    finally:
        manager.disconnect_all(timeout=0)  # idempotent; see above
        worker.join(10)

    assert elapsed < 8.0
    assert isinstance(outcome.get("call"), concurrent.futures.CancelledError), outcome
    assert not manager._thread.is_alive()
    assert _eventually(lambda: any(marks.glob("eof-*")))


def test_disconnect_all_during_a_connect_does_not_wait_out_the_handshake(server):
    """A close that races a connect: the server never answers the handshake,
    whose ``startup`` budget is 60 s. The close aborts the connect instead of
    waiting out that budget, and the server still gets its stdin closed."""
    config, marks = server()
    config["timeout"] = {"startup": 60}
    marks.mkdir(parents=True, exist_ok=True)
    (marks / "mute").touch()
    manager = McpClientManager({"probe": config})
    outcome = {}
    worker = _in_thread(manager.connect_all, outcome, "connect")
    try:
        assert _eventually(lambda: bool(_started(marks)))
        started = time.monotonic()
        manager.disconnect_all(timeout=0.2)
        elapsed = time.monotonic() - started
    finally:
        manager.disconnect_all(timeout=0)  # idempotent; see above
        worker.join(10)

    assert elapsed < 8.0
    assert not worker.is_alive()
    assert not manager._thread.is_alive()
    assert _eventually(lambda: any(marks.glob("eof-*")))


def test_a_call_after_disconnect_all_says_the_manager_is_closed(server):
    config, marks = server()
    manager = _connected(config)
    manager.disconnect_all()

    with pytest.raises(McpManagerClosedError):
        manager.call_tool("probe", "slow_a", {})
    assert len(_started(marks)) == 1
    assert not (marks / "called-slow_a").exists()


def test_a_call_from_the_loop_thread_raises_instead_of_deadlocking():
    manager = McpClientManager({})
    outcome = {}

    async def reenter():
        manager._run(asyncio.sleep(0))

    worker = _in_thread(lambda: manager._run(reenter()), outcome, "call")
    try:
        worker.join(5)
        assert not worker.is_alive(), "a call from the loop thread waited on itself"
        assert isinstance(outcome.get("call"), RuntimeError)
        assert "own event loop thread" in str(outcome["call"])
    finally:
        manager.disconnect_all()


def test_an_interrupted_wait_cancels_its_call(server, monkeypatch):
    """Ctrl+C lands in the thread waiting on the result. The call must not
    keep running on the loop unobserved, where a disconnect would then wait
    out its whole budget for it."""
    config, marks = server(delay=60.0)
    manager = _connected(config)
    caller = threading.current_thread()
    real_wait = concurrent.futures.wait

    def interrupted(fs, timeout=None, **kwargs):
        if threading.current_thread() is caller:
            assert _eventually(lambda: (marks / "called-slow_a").exists())
            raise KeyboardInterrupt
        return real_wait(fs, timeout, **kwargs)

    try:
        monkeypatch.setattr(concurrent.futures, "wait", interrupted)
        with pytest.raises(KeyboardInterrupt):
            manager.call_tool("probe", "slow_a", {})
        monkeypatch.undo()

        assert _eventually(lambda: not manager._calls, timeout=5.0)
        started = time.monotonic()
        manager.disconnect_all(timeout=30.0)
        assert time.monotonic() - started < 8.0
    finally:
        monkeypatch.undo()
        manager.disconnect_all(timeout=0)  # idempotent; see above


def test_an_interrupted_disconnect_all_still_finishes_the_close(server, monkeypatch):
    """Ctrl+C lands in the thread waiting for the close. The close still runs
    to the end, the loop thread stops and closes its loop, and a retry waits
    for that close instead of returning while the thread is still running."""
    config, marks = server(delay=2.0)
    manager = _connected(config)
    thread = manager._thread
    outcome = {}
    worker = _in_thread(lambda: manager.call_tool("probe", "slow_a", {}), outcome, "call")

    def interrupted(timeout=None):
        raise KeyboardInterrupt

    try:
        assert _eventually(lambda: (marks / "called-slow_a").exists())
        monkeypatch.setattr(thread, "join", interrupted)
        with pytest.raises(KeyboardInterrupt):
            manager.disconnect_all(timeout=30.0)
        monkeypatch.undo()
        assert thread.is_alive()  # the close is still waiting for the call

        started = time.monotonic()
        manager.disconnect_all(timeout=5.0)
        assert time.monotonic() - started < 8.0
        assert not thread.is_alive()
        assert manager._loop.is_closed()
    finally:
        monkeypatch.undo()
        manager.disconnect_all(timeout=0)  # idempotent; see above
        worker.join(10)

    assert outcome == {"call": "slow_a"}
    assert _eventually(lambda: any(marks.glob("eof-*")))


def test_a_later_disconnect_all_keeps_the_first_ones_deadline(server, monkeypatch):
    """A retry with a shorter ``timeout`` waits to the first close's deadline.
    On its own budget it gave up while the close was still waiting for a call,
    and stopped the loop under that call."""
    monkeypatch.setattr("agentao.mcp.client._CANCEL_WAIT_S", 0.1)
    monkeypatch.setattr("agentao.mcp.client._OWNER_STOP_S", 1.0)
    # Past a retry's own budget: 0 + 0.1 + 1.0 + 1.0 seconds.
    config, marks = server(delay=4.0)
    manager = _connected(config)
    thread = manager._thread
    outcome = {}
    worker = _in_thread(lambda: manager.call_tool("probe", "slow_a", {}), outcome, "call")

    def interrupted(timeout=None):
        raise KeyboardInterrupt

    try:
        assert _eventually(lambda: (marks / "called-slow_a").exists())
        with monkeypatch.context() as patched:
            patched.setattr(thread, "join", interrupted)
            with pytest.raises(KeyboardInterrupt):
                manager.disconnect_all(timeout=30.0)
        manager.disconnect_all(timeout=0)
        worker.join(10)
        assert outcome == {"call": "slow_a"}
        assert not thread.is_alive()
    finally:
        manager.disconnect_all(timeout=0)  # idempotent; see above
        worker.join(10)


def test_a_call_the_close_could_not_cancel_does_not_wait_forever(monkeypatch):
    """A call that ignores its cancellation is still pending when the close
    stops the loop, and nothing will ever resolve it. Its caller is told the
    manager closed instead of waiting on it forever."""
    monkeypatch.setattr("agentao.mcp.client._CANCEL_WAIT_S", 0.1)
    manager = McpClientManager({})
    running = threading.Event()

    async def stubborn():
        running.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass

    outcome = {}
    worker = _in_thread(lambda: manager._run(stubborn()), outcome, "call")
    try:
        assert running.wait(5)
        manager.disconnect_all(timeout=0.1)
        worker.join(5)
        assert not worker.is_alive(), "the caller is still waiting on a call nothing will resolve"
        assert isinstance(outcome.get("call"), McpManagerClosedError), outcome
    finally:
        manager.disconnect_all(timeout=0)  # idempotent; see above


def test_connecting_and_disconnecting_log_no_teardown_error(server, caplog):
    """#243: every disconnect used to log "Attempted to exit cancel scope in a
    different task", because the connection was entered in one task and exited
    from another."""
    config, marks = server(delay=0.1)
    with caplog.at_level(logging.WARNING, logger="agentao.mcp"):
        manager = _connected(config)
        try:
            assert manager.call_tool("probe", "slow_a", {}) == "slow_a"
        finally:
            manager.disconnect_all()

    assert _teardown_warnings(caplog) == []
    assert _eventually(lambda: any(marks.glob("eof-*")))


def test_a_call_arriving_during_a_failing_reconnect_takes_its_result():
    """The attempt counter moves when a reconnect settles, not when it starts.
    Counted at the start, a call that arrived mid-attempt read the new count
    and, once the attempt failed, paid for a second one against the same dead
    server."""
    client = McpClient("svr", {"command": "unused"})
    client.status = ServerStatus.ERROR
    attempts = []

    async def failing_connect():
        attempts.append(1)
        client.status = ServerStatus.CONNECTING
        await asyncio.sleep(0.2)
        client.status = ServerStatus.ERROR
        client.error_message = "refused"
        client._session = None

    client.connect = failing_connect

    async def scenario():
        first = asyncio.create_task(client.call_tool("t", {}))
        await asyncio.sleep(0.05)  # the first call is mid-reconnect
        second = asyncio.create_task(client.call_tool("t", {}))
        return await first, await second

    results = asyncio.run(scenario())
    assert all("refused" in text for text in results), results
    assert len(attempts) == 1


def test_a_stop_after_an_abandoned_stop_still_waits_for_the_close():
    """A stop whose caller is cancelled mid-wait leaves the closing owner
    recorded, so the next stop (``disconnect_all``'s) waits for that close
    instead of returning at once and letting the loop stop under it."""
    client = McpClient("svr", {"command": "unused"})
    closed = asyncio.Event()

    async def slow_close(ready, stop, gone):
        ready.set_result(None)
        await stop.wait()
        try:
            await asyncio.sleep(0.3)  # the transport shutting down
        finally:
            closed.set()

    client._own_connection = slow_close

    async def scenario():
        await client.connect()
        abandoned = asyncio.create_task(client.disconnect())
        await asyncio.sleep(0.05)
        abandoned.cancel()
        await asyncio.wait({abandoned})
        await client.disconnect()
        return closed.is_set()

    assert asyncio.run(scenario())
    assert client._owner is None
