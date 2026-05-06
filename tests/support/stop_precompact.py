"""Shared fixtures/helpers for Stop / PreCompact hook tests."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import List, Tuple

from agentao.agent import Agentao
from agentao.plugins.models import ParsedHookRule
from agentao.runtime.chat_loop import ChatLoopRunner

from .host_events import CapturingTransport

__all__ = [
    "CapturingTransport",  # re-exported for tests that read the stream
    "make_bare_agent",
    "make_runner_with_rules",
    "write_capture_script",
]


def make_bare_agent(working_directory: Path, transport=None) -> Agentao:
    """Build a minimal Agentao with no plugins, suitable for driving the
    chat-loop helpers (`_dispatch_stop` / `_dispatch_pre_compact`)
    directly."""
    return Agentao(
        working_directory=working_directory,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        transport=transport,
    )


def make_runner_with_rules(
    tmp_path: Path, *, rules: List[ParsedHookRule],
) -> Tuple[ChatLoopRunner, CapturingTransport]:
    """Wire a CapturingTransport-backed agent + ChatLoopRunner for the
    no-emit-gate / event-shape tests."""
    transport = CapturingTransport()
    agent = make_bare_agent(tmp_path, transport=transport)
    agent._plugin_hook_rules = rules
    return ChatLoopRunner(agent), transport


def write_capture_script(tmp_path: Path, name: str = "capture.sh") -> Tuple[Path, Path]:
    """Write a small shell script that copies its stdin to ``stdin.json``
    next to itself, then exits 0. Returns (script_path, stdin_capture_path).
    """
    script = tmp_path / name
    capture = tmp_path / f"{name}.stdin.json"
    script.write_text(
        "#!/bin/sh\ncat > '" + str(capture) + "'\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR | stat.S_IWUSR)
    return script, capture
