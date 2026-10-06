"""A cancelled turn reaches a shell command or MCP call that is still running.

The token was handed only to tools exposing ``_cancellation_token`` (the agent
tools). A foreground shell command ran until it exited or idled out, and an
MCP call until the server answered — with no ``timeout.request``, forever —
so ACP ``session/cancel`` fired the token and the turn went on waiting.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
import time

import pytest

from agentao.cancellation import (
    AgentCancelledError,
    CancellationToken,
    bind_cancellation_token,
    current_cancellation_token,
)
from agentao.runtime.tool_executor import ToolExecutor
from agentao.tools.shell import ShellTool

from tests.support.host_events import NullTransport, make_plan

SLEEP_30 = (
    f'"{sys.executable}" -c "import time; print(\'step 1 done\', flush=True); time.sleep(30)"'
)


def _cancel_after(token: CancellationToken, seconds: float) -> None:
    threading.Timer(seconds, token.cancel, args=("user-cancel",)).start()


def test_the_token_is_bound_only_around_a_call():
    token = CancellationToken()
    assert current_cancellation_token() is None
    with bind_cancellation_token(token):
        assert current_cancellation_token() is token
    assert current_cancellation_token() is None


def test_a_cancelled_turn_kills_a_running_shell_command():
    executor = ToolExecutor(NullTransport(), logging.getLogger("t"), sandbox_policy=None)
    token = CancellationToken()
    plan = make_plan(ShellTool(), args={"command": SLEEP_30, "timeout": 60})
    _cancel_after(token, 0.5)
    started = time.monotonic()
    results = executor.execute_batch([plan], cancellation_token=token)
    elapsed = time.monotonic() - started
    (info,) = results.values()
    assert elapsed < 10, elapsed
    assert info.status == "cancelled", info.result
    assert info.result.startswith("[Operation Cancelled]")
    # What the command printed before the kill is kept for the next turn.
    assert "step 1 done" in info.result


def test_a_shell_command_without_a_token_is_unaffected():
    out = ShellTool().execute(command=f'"{sys.executable}" -c "print(42)"', timeout=30)
    assert "42" in out


def test_a_cancelled_turn_abandons_a_pending_mcp_call():
    from agentao.mcp.client import McpClientManager

    manager = McpClientManager({})
    token = CancellationToken()
    _cancel_after(token, 0.3)
    started = time.monotonic()
    try:
        with bind_cancellation_token(token):
            with pytest.raises(AgentCancelledError):
                manager._run(asyncio.sleep(30))
    finally:
        manager.disconnect_all()
    assert time.monotonic() - started < 5


def test_an_mcp_call_without_a_cancel_still_returns():
    from agentao.mcp.client import McpClientManager

    async def answer():
        return "ok"

    manager = McpClientManager({})
    try:
        with bind_cancellation_token(CancellationToken()):
            assert manager._run(answer()) == "ok"
    finally:
        manager.disconnect_all()


def test_a_tools_own_cancel_is_an_error_not_a_user_cancel():
    from typing import Any, Dict

    from agentao.tools import Tool

    class _OwnToken(Tool):
        name = "own_token"
        description = "raises a cancel from a token of its own"

        @property
        def parameters(self) -> Dict[str, Any]:
            return {"type": "object"}

        def execute(self, **_kwargs) -> str:
            raise AgentCancelledError("inner-timeout")

    executor = ToolExecutor(NullTransport(), logging.getLogger("t"), sandbox_policy=None)
    results = executor.execute_batch(
        [make_plan(_OwnToken())], cancellation_token=CancellationToken()
    )
    (info,) = results.values()
    assert info.status == "error", info
    assert "inner-timeout" in info.result


@pytest.mark.skipif(sys.platform == "win32", reason="probes the pid with os.kill(pid, 0) and ps")
def test_a_keyboard_interrupt_in_the_wait_kills_the_command(tmp_path):
    # The interactive CLI runs a lone tool call on the main thread, so Ctrl+C
    # lands inside the executor's wait loop. The child leads its own session
    # and never sees the SIGINT; the loop has to kill it on the way out.
    import os
    from types import MappingProxyType

    from agentao.capabilities.shell import LocalShellExecutor, ShellRequest
    from agentao.capabilities.shell_spec import AbsPath, LegacyLaunch

    pid_file = tmp_path / "pid"
    script = tmp_path / "wait.py"
    script.write_text(
        "import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    class _CtrlC:
        def __init__(self):
            self.reads = 0

        @property
        def is_cancelled(self):
            self.reads += 1
            if pid_file.exists() and pid_file.read_text():
                raise KeyboardInterrupt
            return False

    launch = LegacyLaunch(
        command=f'"{sys.executable}" "{script}"',
        cwd=AbsPath(str(tmp_path)),
        env=MappingProxyType(dict(os.environ)),
    )
    with pytest.raises(KeyboardInterrupt):
        LocalShellExecutor().run(
            ShellRequest(launch=launch, timeout=60, cancellation_token=_CtrlC())
        )
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return  # gone
        # Killed but not yet reaped (the raise skipped the wait) is dead too.
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        if not state or state.startswith("Z"):
            return
        time.sleep(0.1)
    pytest.fail(f"child {pid} still running after Ctrl+C")


def test_a_host_executors_own_cancel_keeps_the_output(tmp_path):
    """``cancelled=True`` with no cancelled turn: returned, output and all.

    Raised, it reached the tool executor as a generic error (the turn was not
    cancelled), and the output the executor captured was dropped.
    """
    from agentao.capabilities.shell import LocalShellExecutor, ShellResult

    class StopsOnItsOwn(LocalShellExecutor):
        def run(self, request):
            return ShellResult(
                returncode=-1, stdout=b"step 1 done\n", stderr=b"",
                timed_out=False, cancelled=True,
            )

    tool = ShellTool()
    tool.shell = StopsOnItsOwn()
    out = tool._run_foreground("echo hi", tmp_path, 5)
    assert "cancelled by the shell executor" in out
    assert "step 1 done" in out
