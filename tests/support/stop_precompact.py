"""Shared fixtures/helpers for Stop / PreCompact hook tests."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import List, Tuple
from unittest.mock import MagicMock

from agentao.agent import Agentao
from agentao.plugins.models import ParsedHookRule
from agentao.runtime.chat_loop import ChatLoopRunner

from .host_events import CapturingTransport

__all__ = [
    "CapturingTransport",  # re-exported for tests that read the stream
    "dispatch_stop_with_json_payload",
    "make_bare_agent",
    "make_runner_with_rules",
    "make_runner_with_stub_llm",
    "write_capture_script",
    "write_exit_code_hook",
    "write_json_emitting_hook",
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


def write_json_emitting_hook(
    tmp_path: Path, payload: dict, *, name: str = "hook.sh",
) -> Path:
    """Write a shell script that prints ``payload`` as JSON and exits 0."""
    script = tmp_path / name
    script.write_text(
        "#!/bin/sh\ncat <<'JSON'\n" + json.dumps(payload) + "\nJSON\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR | stat.S_IWUSR)
    return script


def write_exit_code_hook(
    tmp_path: Path, *, exit_code: int, stderr: str = "", name: str = "hook_exit.sh",
) -> Path:
    """Write a shell script that prints ``stderr`` to stderr and exits
    with ``exit_code``. Used to drive the exit-code branch of the Stop
    runner (``_run_stop_command_hook``)."""
    script = tmp_path / name
    body = "#!/bin/sh\n"
    if stderr:
        body += f"printf '%s' '{stderr}' >&2\n"
    body += f"exit {exit_code}\n"
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR | stat.S_IWUSR)
    return script


def make_runner_with_stub_llm(
    tmp_path: Path,
    monkeypatch,
    *,
    content: str = "final answer",
    stop_reentry_cap: int = 3,
    rules: List[ParsedHookRule] | None = None,
):
    """Build a ChatLoopRunner with a stubbed LLM that always returns a
    final response (no tool calls), so the natural-turn ``else`` branch
    fires every iteration. Returns ``(runner, transport, agent)``.

    Stubs out compaction/notification helpers and skill/memory/tools
    managers so ``runner.run()`` is exercisable without a real
    Agentao environment.
    """
    if rules is None:
        rules = [ParsedHookRule(
            event="Stop", hook_type="command", command="echo", plugin_name="t",
        )]
    transport = CapturingTransport()
    agent = make_bare_agent(tmp_path, transport=transport)
    agent._plugin_hook_rules = rules
    runner = ChatLoopRunner(agent, stop_reentry_cap=stop_reentry_cap)

    monkeypatch.setattr(runner, "_maybe_microcompact", lambda m, s: (m, s))
    monkeypatch.setattr(runner, "_maybe_full_compress", lambda m, s: (m, s))
    monkeypatch.setattr(runner, "_inject_background_notifications", lambda m, s: m)

    fake_message = SimpleNamespace(
        content=content, tool_calls=None, reasoning_content=None,
    )
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=fake_message)], usage=None,
    )
    fake_outcome = SimpleNamespace(
        error_return=None, response=fake_response,
        messages_with_system=[{"role": "system", "content": ""}],
        system_prompt="",
    )
    monkeypatch.setattr(
        runner, "_call_llm_with_overflow_recovery",
        lambda m, s, t, k: fake_outcome,
    )
    monkeypatch.setattr(agent, "_build_system_prompt", lambda: "")
    agent.skill_manager = MagicMock()
    agent.skill_manager.get_active_skills.return_value = {}
    agent.memory_manager = MagicMock()
    agent.memory_manager.write_version = 0
    agent.tools = MagicMock()
    agent.tools.to_openai_format.return_value = []
    agent.tool_runner = MagicMock()
    agent.tool_runner.reset = MagicMock()
    return runner, transport, agent


def dispatch_stop_with_json_payload(tmp_path: Path, hook_payload: dict):
    """Convenience: write a JSON-emitting Stop hook, dispatch a single
    rule against it, return the ``StopHookResult``. Used by parser-table
    tests that need real subprocess execution. Tests that exercise only
    the JSON parser should call ``_parse_stop_command_output`` directly.
    """
    from agentao.plugins.hooks import (
        ClaudeHookPayloadAdapter,
        PluginHookDispatcher,
    )
    script = write_json_emitting_hook(tmp_path, hook_payload)
    rule = ParsedHookRule(
        event="Stop", hook_type="command",
        command=f"sh '{script}'", plugin_name="t",
    )
    stop_payload = ClaudeHookPayloadAdapter().build_stop(
        cwd=tmp_path,
        last_assistant_message="answer",
        turn_end_reason="final_response",
    )
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    return dispatcher.dispatch_stop(payload=stop_payload, rules=[rule])
