"""Issue #9 — ShellTool routes through an injected ShellExecutor.

A fake executor records every shell invocation so embedded hosts that
need to redirect commands through Docker exec or a remote runner can do
so without monkey-patching ``subprocess``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from agentao.capabilities import (
    BackgroundHandle,
    ShellRequest,
    ShellResult,
)
from agentao.tools.shell import ShellTool


class FakeExecutor:
    def __init__(self):
        self.calls: List[ShellRequest] = []

    def run(self, request: ShellRequest) -> ShellResult:
        self.calls.append(request)
        return ShellResult(returncode=0, stdout=b"hello\n", stderr=b"")

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        self.calls.append(request)
        return BackgroundHandle(
            pid=4242, pgid=4242, command=request.command, cwd=request.cwd,
        )


def test_foreground_shell_routes_through_executor(tmp_path):
    fake = FakeExecutor()
    tool = ShellTool()
    tool.shell = fake
    tool.working_directory = tmp_path

    out = tool.execute(command="echo hello", working_directory=str(tmp_path))

    assert fake.calls, "executor should have been invoked"
    assert fake.calls[0].command == "echo hello"
    assert "hello" in out


def test_background_shell_routes_through_executor(tmp_path):
    fake = FakeExecutor()
    tool = ShellTool()
    tool.shell = fake
    tool.working_directory = tmp_path

    out = tool.execute(
        command="sleep 100",
        is_background=True,
        working_directory=str(tmp_path),
    )

    assert fake.calls
    assert "PID: 4242" in out
