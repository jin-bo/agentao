"""The identical-args doom-loop guard across writes (#543).

A potential write that is new this turn clears the other identical-args
counts, so ``read_file(X)`` after ``replace(X)`` and the same ``pytest`` after
an edit are not a loop. A repeated identical call with no new potential write
in between still trips on the third call.

The tools are the real built-ins, so the test reads the ``is_read_only``
values that production reads.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agentao.agents import AgentToolWrapper
from agentao.runtime.tool_planning import DOOM_LOOP_THRESHOLD, ToolCallPlanner
from agentao.tools import EditTool, ReadFileTool, ShellTool
from agentao.tools.base import ToolRegistry

from tests.support.tool_calls import make_tool_call

X = "/kb/wiki/page.md"


@pytest.fixture
def planner():
    registry = ToolRegistry()
    sub_agent = AgentToolWrapper(
        definition={"name": "fixer", "description": "d"},
        all_tools={},
        llm_config_getter=lambda: {},
        working_directory=Path("."),
    )
    for tool in (ReadFileTool(), EditTool(), ShellTool(), sub_agent):
        registry.register(tool)
    return ToolCallPlanner(registry, None, logging.getLogger("test.doom_novel_write"))


_n = 0


def _call(name, args):
    global _n
    _n += 1
    return make_tool_call(f"c{_n}", name, arguments=json.dumps(args))


def read():
    return _call("read_file", {"file_path": X})


def replace(old, new):
    return _call("replace", {"file_path": X, "old_string": old, "new_string": new})


def shell(command):
    return _call("run_shell_command", {"command": command})


def run(planner, calls, **kw):
    """Plan one call per iteration; return the index that tripped, or None."""
    for i, tc in enumerate(calls):
        if planner.plan([tc], **kw).doom_loop_triggered:
            return i
    return None


def delegate(task, **extra):
    return _call("agent_fixer", {"task": task, **extra})


def test_the_real_tools_carry_the_flags_the_rule_reads():
    assert ReadFileTool().is_read_only is True
    assert EditTool().is_read_only is False
    assert ShellTool().is_read_only is False
    assert DOOM_LOOP_THRESHOLD == 3


def test_the_issue_sequence_does_not_trip(planner):
    calls = [read(), replace("a", "b"), read(), replace("b", "c"), read()]
    assert run(planner, calls) is None


def test_an_identical_pytest_after_each_edit_does_not_trip(planner):
    calls = [
        shell("pytest -q"), replace("a", "b"),
        shell("pytest -q"), replace("b", "c"),
        shell("pytest -q"), replace("c", "d"),
        shell("pytest -q"),
    ]
    assert run(planner, calls) is None


def test_an_identical_pytest_with_no_edit_still_trips(planner):
    assert run(planner, [shell("pytest -q")] * 3) == 2


def test_an_identical_read_with_no_write_still_trips(planner):
    assert run(planner, [read(), read(), read()]) == 2


def test_a_repeated_identical_write_keeps_its_own_count(planner):
    assert run(planner, [replace("a", "b")] * 3) == 2


def test_a_repeated_write_does_not_clear_the_counts_again(planner):
    # The background-subagent-wake case: a sleep and a poll alternate. Only
    # the first sleep is new, so the third sleep trips.
    calls = [shell("sleep 30"), read(), shell("sleep 30"), read(), shell("sleep 30")]
    assert run(planner, calls) == 4


def test_two_alternating_repeated_writes_still_trip(planner):
    # If every write cleared the counts, A and B would clear each other forever.
    a, b = shell("sleep 30"), shell("ls /tmp/agent-output")
    assert run(planner, [a, b, a, b, a, b]) == 5


def test_a_foreground_sub_agent_call_counts_as_a_write(planner):
    # Its is_read_only is True so read-only mode can delegate, but the child
    # can edit X, as in tool_executor._all_read_only.
    calls = [read(), delegate("fix X"), read(), delegate("fix X again"), read()]
    assert run(planner, calls) is None


def test_a_background_sub_agent_launch_does_not_count(planner):
    calls = [read(), read(), delegate("fix X", run_in_background=True), read()]
    assert run(planner, calls) == 3


def test_a_sub_agent_call_in_read_only_mode_does_not_count(planner):
    calls = [read(), read(), delegate("look at X"), read()]
    assert run(planner, calls, readonly_mode=True) == 3


def test_a_denied_write_does_not_clear_the_counts(planner):
    # Read-only mode denies ``replace`` in planning.
    calls = [read(), read(), replace("a", "b"), read()]
    assert run(planner, calls, readonly_mode=True) == 3


def test_a_denied_write_is_not_recorded_as_seen(planner):
    planner.plan([replace("a", "b")], readonly_mode=True)  # DENY
    planner.plan([read()])
    planner.plan([read()])
    planner.plan([replace("a", "b")])  # the same write, now allowed: new
    assert planner.plan([read()]).doom_loop_triggered is False


def test_a_write_earlier_in_the_batch_clears_the_count(planner):
    result = planner.plan([read(), read(), replace("a", "b"), read()])
    assert result.doom_loop_triggered is False
    assert len(result.plans) == 4


def test_a_write_later_in_the_batch_comes_too_late(planner):
    result = planner.plan([read(), read(), read(), replace("a", "b")])
    assert result.doom_loop_triggered is True
    assert [p.function_name for p in result.plans] == ["read_file", "read_file"]


def test_reset_makes_a_seen_write_new_again(planner):
    planner.plan([replace("a", "b")])
    planner.reset()
    calls = [read(), read(), replace("a", "b"), read()]
    assert run(planner, calls) is None


def test_an_unknown_tool_does_not_clear_the_counts(planner):
    calls = [read(), read(), _call("no_such_tool", {"file_path": X}), read()]
    assert run(planner, calls) == 3


def test_a_parse_failure_does_not_clear_the_counts(planner):
    bad = make_tool_call("bad", "replace", arguments="<<not json>>")
    result = planner.plan([read(), read(), bad, read()])
    assert result.doom_loop_triggered is True
    assert "could not parse" in result.early_messages[0]["content"]


def test_a_new_write_does_not_reset_the_parse_failure_guard(planner):
    calls = [
        make_tool_call("p1", "replace", arguments="<<not json 1>>"),
        make_tool_call("p2", "replace", arguments="<<not json 2>>"),
        shell("echo new"),
        make_tool_call("p3", "replace", arguments="<<not json 3>>"),
    ]
    results = [planner.plan([tc]) for tc in calls]
    assert [r.doom_loop_triggered for r in results] == [False, False, False, True]
    assert "Parse-failure doom-loop" in results[-1].early_messages[0]["content"]


def test_a_new_write_does_not_reset_the_empty_name_guard(planner):
    calls = [
        make_tool_call("e1", "", arguments="{}"),
        make_tool_call("e2", "", arguments="{}"),
        shell("echo new"),
        make_tool_call("e3", "", arguments="{}"),
    ]
    assert run(planner, calls) == 3
