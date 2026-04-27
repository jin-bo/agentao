"""Tests for P1 of ACP_STDIO_AUTH_FIX_PLAN.

Verify ``ShellTool`` always passes ``stdin=subprocess.DEVNULL`` to its
subprocesses. Under the ACP stdio transport the parent's stdin is the
JSON-RPC channel; a ``shell=True`` child inheriting that handle can
corrupt framing or deadlock the protocol.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from agentao.tools.shell import ShellTool


class _PopenSpy:
    """Records every Popen invocation and exits cleanly without real I/O."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 4242
        self.stdout = _DummyStream()
        self.stderr = _DummyStream()
        self.returncode = 0
        _PopenSpy.instances.append(self)

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _DummyStream:
    def read(self, n=-1):
        return b""

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_spy():
    _PopenSpy.instances = []
    yield
    _PopenSpy.instances = []


def _make_tool(tmp_path) -> ShellTool:
    tool = ShellTool()
    # Bind the tool's working directory so PathPolicy roots at tmp_path.
    tool.working_directory = str(tmp_path)
    return tool


def test_foreground_popen_uses_devnull_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", _PopenSpy)
    tool = _make_tool(tmp_path)
    tool.execute(command="echo hi", working_directory=".", timeout=1)
    assert _PopenSpy.instances, "expected Popen to be invoked"
    for inst in _PopenSpy.instances:
        assert inst.kwargs.get("stdin") is subprocess.DEVNULL, (
            f"foreground Popen missing stdin=DEVNULL: {inst.kwargs}"
        )


def test_background_popen_uses_devnull_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", _PopenSpy)
    # POSIX background path calls os.getpgid(proc.pid); _PopenSpy's pid
    # isn't a real process, so stub getpgid to a no-op.
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    tool = _make_tool(tmp_path)
    tool.execute(
        command="sleep 0.01",
        working_directory=".",
        timeout=1,
        is_background=True,
    )
    assert _PopenSpy.instances, "expected Popen to be invoked"
    for inst in _PopenSpy.instances:
        assert inst.kwargs.get("stdin") is subprocess.DEVNULL, (
            f"background Popen missing stdin=DEVNULL: {inst.kwargs}"
        )


def test_real_subprocess_sees_empty_stdin(tmp_path):
    """End-to-end: spawn a real shell command that reads its own stdin and
    print it. With stdin=DEVNULL the read returns empty; if we ever
    regressed, the child would block on the test runner's stdin."""
    tool = _make_tool(tmp_path)
    out = tool.execute(
        command='python -c "import sys; sys.stdout.write(repr(sys.stdin.read()))"',
        working_directory=".",
        timeout=5,
    )
    assert "''" in out, f"child should see empty stdin; got: {out!r}"
