"""A ``PostToolUse`` stop has three frames to cross — step 4b (G2).

The hooks fire **inside a tool worker**; above that ``ToolRunner.execute``
returned ``(bool, list)`` and the chat loop read exactly those two values. A stop
computed in the worker had nowhere to go.

The invariant these tests protect is the one a mid-flight abort breaks: **every
plan still yields a result and a `role:"tool"` message**, stop or no stop.
``format_batch`` indexes ``exec_results[plan.tool_call_id]`` per plan, and an
assistant ``tool_calls`` entry with no answering tool message is rejected by
strict APIs. Ending a turn is not a rollback.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from agentao.permissions import PermissionEngine
from agentao.runtime.tool_runner import ToolRunner
from agentao.plugins.hooks._profile import PROFILE_ID
from agentao.plugins.models import ParsedHookRule
from agentao.tools.base import Tool, ToolRegistry

from ._hook_commands import as_kwargs, emits_json, emitting
from tests.support.tool_calls import make_tool_call


class _SleepyTool(Tool):
    @property
    def name(self) -> str:
        return "sleepy"

    @property
    def description(self) -> str:
        return "echoes its tag"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"tag": {"type": "string"}},
            "required": ["tag"],
        }

    def execute(self, tag: str) -> str:
        return f"ran {tag}"


class _SlowTool(Tool):
    """Always finishes last. A *separate tool* rather than a slow argument,
    because the argument route does not survive planning — measured: two calls
    to one tool with different delays completed at the same millisecond, so a
    test built on it compared plan order against itself."""

    @property
    def name(self) -> str:
        return "slowpoke"

    @property
    def description(self) -> str:
        return "sleeps, then echoes"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def execute(self) -> str:
        time.sleep(0.15)
        return "ran slow"


class _NullTransport:
    def emit(self, event):  # pragma: no cover - trivial
        pass


def _runner(hook_rules, tmp_path):
    registry = ToolRegistry()
    registry.register(_SleepyTool())
    registry.register(_SlowTool())
    runner = ToolRunner(registry, PermissionEngine(project_root=tmp_path),
                        _NullTransport(), logging.getLogger("test.hook_stop"))
    runner._plugin_hook_rules = hook_rules
    return runner


def _hook(command, event="PostToolUse", matcher=None):
    return ParsedHookRule(
        event=event, hook_type="command", **as_kwargs(command), timeout=30,
        contract=PROFILE_ID, plugin_name="p", matcher_pattern=matcher,
    )


def _call(cid, tag):
    return make_tool_call(cid, "sleepy", json.dumps({"tag": tag}))


# --------------------------------------------------------------------------
# The stop crosses the worker boundary
# --------------------------------------------------------------------------

def test_a_post_tool_use_stop_reaches_the_runner(tmp_path):
    runner = _runner(tmp_path=tmp_path, hook_rules=[_hook(emits_json('{"continue": false, "stopReason": "halt now"}'))])

    doom, messages = runner.execute([_call("c1", "a")])

    assert doom is False
    assert runner.last_hook_stop == "halt now"


def test_every_plan_still_gets_a_tool_message_when_a_hook_stops(tmp_path):
    """The invariant a mid-flight abort breaks."""
    runner = _runner(tmp_path=tmp_path, hook_rules=[_hook(emits_json('{"continue": false, "stopReason": "halt"}'))])

    _, messages = runner.execute([_call("c1", "a"), _call("c2", "b"), _call("c3", "c")])

    assert [m["tool_call_id"] for m in messages] == ["c1", "c2", "c3"]
    assert all(m["role"] == "tool" for m in messages)


def test_no_hook_means_no_stop(tmp_path):
    runner = _runner(tmp_path=tmp_path, hook_rules=[])
    runner.execute([_call("c1", "a")])
    assert runner.last_hook_stop is None


def test_a_stop_does_not_leak_into_the_next_batch(tmp_path):
    runner = _runner(tmp_path=tmp_path, hook_rules=[_hook(emits_json('{"continue": false, "stopReason": "halt"}'))])
    runner.execute([_call("c1", "a")])
    assert runner.last_hook_stop == "halt"

    runner._plugin_hook_rules = []
    runner.execute([_call("c2", "b")])
    assert runner.last_hook_stop is None


# --------------------------------------------------------------------------
# Arbitration is plan order, never completion order
# --------------------------------------------------------------------------

def test_the_surfaced_reason_is_the_plan_order_winner_not_the_completion_order(tmp_path):
    """Two stops, **one** completion order, **two** plan orders.

    Holding completion order fixed while swapping plan order is what makes this
    falsifiable: a completion-order implementation returns the fast tool's reason
    in *both* runs and therefore fails the first. Swapping completion order
    instead — the obvious construction — compares plan order against itself.
    """
    rule = _hook(
        "python3 -c \"import sys,json;d=json.load(sys.stdin);"
        "print(json.dumps({'continue': False, 'stopReason': 'stop-'+d['tool_name']}))\""
    )
    runner = _runner(hook_rules=[rule], tmp_path=tmp_path)
    slow = make_tool_call("c-slow", "slowpoke", "{}")
    fast = make_tool_call("c-fast", "sleepy", json.dumps({"tag": "f"}))

    # Slow is declared FIRST and finishes LAST.
    runner.execute([slow, fast])
    assert runner.last_hook_stop == "stop-slowpoke"

    # Fast is declared FIRST and finishes FIRST. Same completion order as above.
    runner.execute([fast, slow])
    assert runner.last_hook_stop == "stop-sleepy"


# --------------------------------------------------------------------------
# Feedback rides beside the result, never instead of it
# --------------------------------------------------------------------------

def test_exit_2_stderr_is_spliced_beside_the_preserved_tool_result(tmp_path):
    runner = _runner(tmp_path=tmp_path, hook_rules=[_hook(emitting(stderr="look at this\n", exit_code=2))])

    _, messages = runner.execute([_call("c1", "a")])

    content = messages[0]["content"]
    assert "ran a" in content                     # the original output survives
    assert "<system-reminder>" in content
    assert "look at this" in content
    assert runner.last_hook_stop is None          # feedback is not a stop


def test_additional_context_is_spliced_the_same_way(tmp_path):
    runner = _runner(tmp_path=tmp_path, hook_rules=[_hook(
        emits_json('{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "note this"}}')
    )])

    _, messages = runner.execute([_call("c1", "a")])

    assert "ran a" in messages[0]["content"]
    assert "note this" in messages[0]["content"]


def test_a_failing_tool_routes_through_the_failure_event(tmp_path):
    """`PostToolUseFailure` fires instead of `PostToolUse`, and its exit-2
    stderr also reaches the model."""
    runner = _runner(tmp_path=tmp_path, hook_rules=[_hook(emitting(stderr="failure feedback\n", exit_code=2),
                            event="PostToolUseFailure")])

    _, messages = runner.execute([make_tool_call("c1", "sleepy", "{}")])  # missing `tag`

    assert "failure feedback" in messages[0]["content"]
