"""Decision-capable PreToolUse hooks.

Covers the dispatcher's ``dispatch_pre_tool_use_decision`` parsing/merge
logic and the ``ToolRunner`` Phase 1.5 wiring: a PreToolUse hook may
``deny`` a tool call outright or downgrade an ``allow`` to ``ask`` (which
then flows through the existing confirmation path). A hook ``allow`` is a
no-op. Hook-derived decisions are attributed via the ``reason`` field
(prefixed ``pre-tool-hook``) and must produce a ``PermissionDecisionEvent``.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List

from agentao.host.models import PermissionDecisionEvent, ToolLifecycleEvent
from agentao.host.projection import HostPermissionEmitter, HostToolEmitter
from agentao.permissions import PermissionEngine
from agentao.plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
from agentao.plugins.models import ParsedHookRule
from agentao.runtime.tool_planning import ToolCallDecision
from agentao.runtime.tool_runner import ToolRunner
from agentao.tools import Tool, ToolRegistry


# ---------------------------------------------------------------------------
# Dispatcher-level: parsing + merge
# ---------------------------------------------------------------------------


def _rule(command: str) -> ParsedHookRule:
    return ParsedHookRule(
        event="PreToolUse", hook_type="command", command=command, plugin_name="t",
    )


def _echo_json(obj) -> str:
    # ``shell=True`` — single-quote the JSON so braces/quotes survive.
    return "echo '" + json.dumps(obj) + "'"


def test_dispatch_decision_deny(tmp_path):
    rule = _rule(_echo_json({
        "hookSpecificOutput": {"permissionDecision": "deny", "reason": "nope"}
    }))
    payload = ClaudeHookPayloadAdapter().build_pre_tool_use(tool_name="run_shell_command")
    res = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_tool_use_decision(
        payload=payload, rules=[rule],
    )
    assert res.decision == "deny"
    assert res.reason == "nope"
    assert res.matched_rule_count == 1


def test_dispatch_decision_ask(tmp_path):
    rule = _rule(_echo_json({"hookSpecificOutput": {"permissionDecision": "ask"}}))
    payload = ClaudeHookPayloadAdapter().build_pre_tool_use(tool_name="read_file")
    res = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_tool_use_decision(
        payload=payload, rules=[rule],
    )
    assert res.decision == "ask"


def test_dispatch_decision_allow_is_none(tmp_path):
    rule = _rule(_echo_json({"hookSpecificOutput": {"permissionDecision": "allow"}}))
    payload = ClaudeHookPayloadAdapter().build_pre_tool_use(tool_name="read_file")
    res = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_tool_use_decision(
        payload=payload, rules=[rule],
    )
    assert res.decision is None


def test_dispatch_decision_deny_wins_over_ask(tmp_path):
    ask_rule = _rule(_echo_json({"hookSpecificOutput": {"permissionDecision": "ask"}}))
    deny_rule = _rule(_echo_json({"hookSpecificOutput": {"permissionDecision": "deny"}}))
    payload = ClaudeHookPayloadAdapter().build_pre_tool_use(tool_name="x")
    res = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_tool_use_decision(
        payload=payload, rules=[ask_rule, deny_rule],
    )
    assert res.decision == "deny"


def test_dispatch_decision_non_json_recorded_not_decided(tmp_path):
    rule = _rule("echo just-some-text")
    payload = ClaudeHookPayloadAdapter().build_pre_tool_use(tool_name="x")
    res = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_tool_use_decision(
        payload=payload, rules=[rule],
    )
    assert res.decision is None
    assert res.additional_contexts == ["just-some-text"]


def test_dispatch_decision_no_matching_rules(tmp_path):
    rule = ParsedHookRule(
        event="PreToolUse", hook_type="command", command=_echo_json({}),
        matcher={"toolName": "Bash"}, plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_pre_tool_use(tool_name="read_file")  # → Read
    res = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_tool_use_decision(
        payload=payload, rules=[rule],
    )
    assert res.matched_rule_count == 0
    assert res.decision is None


# ---------------------------------------------------------------------------
# ToolRunner Phase 1.5 wiring
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self) -> None:
        self.events: List = []

    def publish(self, event):
        self.events.append(event)


class _ConfirmingTransport:
    def __init__(self, confirm: bool = True) -> None:
        self._confirm = confirm
        self.emitted = []

    def emit(self, event):
        self.emitted.append(event)

    def confirm_tool(self, *_a, **_kw):
        return self._confirm

    def ask_user(self, _q):
        return ""

    def on_max_iterations(self, _c, _m):
        return {"action": "stop"}


class _ReadTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.executed = False

    @property
    def name(self) -> str: return "read_thing"
    @property
    def description(self) -> str: return "read"
    @property
    def parameters(self) -> Dict[str, Any]: return {"type": "object"}
    @property
    def is_read_only(self) -> bool: return True
    def execute(self, **kwargs) -> str:
        self.executed = True
        return "ok"


def _build_runner(tmp_path, *, hook_command: str | None, confirm: bool = True):
    stream = _FakeStream()
    registry = ToolRegistry()
    tool = _ReadTool()
    registry.register(tool)
    engine = PermissionEngine(project_root=tmp_path)
    perm_emitter = HostPermissionEmitter(
        stream,
        session_id_provider=lambda: "s-1",
        turn_id_provider=lambda: "t-1",
        active_permissions_provider=lambda: engine.active_permissions(),
    )
    tool_emitter = HostToolEmitter(
        stream, session_id_provider=lambda: "s-1", turn_id_provider=lambda: "t-1",
    )
    transport = _ConfirmingTransport(confirm=confirm)
    runner = ToolRunner(
        tools=registry,
        permission_engine=engine,
        transport=transport,
        logger=logging.getLogger("test.pre_tool_decision"),
        host_tool_emitter=tool_emitter,
        host_permission_emitter=perm_emitter,
    )
    if hook_command is not None:
        runner._plugin_hook_rules = [_rule(hook_command)]
        runner._working_directory = tmp_path
        runner._session_id = "s-1"
    return runner, stream, transport, tool


def _tool_call(name: str = "read_thing", *, call_id: str = "tc-1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments="{}"))


def test_runner_hook_deny_blocks_execution(tmp_path):
    runner, stream, _, tool = _build_runner(
        tmp_path,
        hook_command=_echo_json({
            "hookSpecificOutput": {"permissionDecision": "deny", "reason": "blocked by policy"}
        }),
    )
    doom, messages = runner.execute([_tool_call()])
    assert doom is False
    assert tool.executed is False
    assert "pretooluse hook" in messages[0]["content"].lower()

    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    assert len(perm_events) == 1
    assert perm_events[0].outcome == "deny"
    assert perm_events[0].reason == "pre-tool-hook: blocked by policy"
    # No tool ever started.
    started = [
        e for e in stream.events
        if isinstance(e, ToolLifecycleEvent) and e.phase == "started"
    ]
    assert started == []


def test_runner_hook_ask_then_user_declines(tmp_path):
    runner, stream, _, tool = _build_runner(
        tmp_path,
        hook_command=_echo_json({"hookSpecificOutput": {"permissionDecision": "ask"}}),
        confirm=False,
    )
    doom, messages = runner.execute([_tool_call()])
    assert tool.executed is False
    assert "cancelled" in messages[0]["content"].lower()
    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    # ASK projects to the "prompt" outcome on the public event.
    assert perm_events[0].outcome == "prompt"
    assert perm_events[0].reason == "pre-tool-hook"


def test_runner_hook_ask_then_user_confirms_executes(tmp_path):
    runner, stream, _, tool = _build_runner(
        tmp_path,
        hook_command=_echo_json({"hookSpecificOutput": {"permissionDecision": "ask"}}),
        confirm=True,
    )
    runner.execute([_tool_call()])
    assert tool.executed is True


def test_runner_hook_allow_is_noop(tmp_path):
    runner, stream, _, tool = _build_runner(
        tmp_path,
        hook_command=_echo_json({"hookSpecificOutput": {"permissionDecision": "allow"}}),
    )
    runner.execute([_tool_call()])
    assert tool.executed is True
    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    # Engine's own allow stands; not attributed to the hook.
    assert perm_events[0].outcome == "allow"
    assert perm_events[0].reason != "pre-tool-hook"


def test_runner_no_hook_rules_skips_dispatch(tmp_path):
    runner, stream, _, tool = _build_runner(tmp_path, hook_command=None)
    runner.execute([_tool_call()])
    assert tool.executed is True
