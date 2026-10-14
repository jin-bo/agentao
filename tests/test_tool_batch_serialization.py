"""A batch runs in parallel only when every tool in it is read-only (codex #52245).

Before, every tool of a batch ran on the pool at once. A shell command that
writes a file and a ``read_file`` of it, in one model response, raced: the read
usually ran first and missed the write. Now any tool that is not read-only,
or does not say so with a real ``True``, makes the whole batch run one call at
a time, in the order the model returned them.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from agentao.agent import Agentao
from agentao.runtime.tool_executor import ToolExecutor, _all_read_only
from agentao.runtime.tool_planning import ToolCallDecision
from agentao.tools import Tool

from tests.support.host_events import NullTransport, make_plan


class _Probe(Tool):
    """Records when it starts and ends, so a test can see overlap and order."""

    def __init__(self, name: str, log: List[str], read_only: Any = False) -> None:
        self._name = name
        self._log = log
        self._read_only = read_only

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "probe"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    @property
    def is_read_only(self):
        if isinstance(self._read_only, Exception):
            raise self._read_only
        return self._read_only

    def execute(self, **_kwargs) -> str:
        self._log.append(f"start {self._name}")
        time.sleep(0.15)
        self._log.append(f"end {self._name}")
        return self._name


def _run(*tools) -> None:
    executor = ToolExecutor(NullTransport(), logging.getLogger("test.batch"))
    plans = [make_plan(t, call_id=f"c-{t.name}") for t in tools]
    results = executor.execute_batch(plans)
    assert set(results) == {f"c-{t.name}" for t in tools}


def test_a_batch_with_a_write_runs_serially_in_order():
    log: List[str] = []
    _run(_Probe("c", log), _Probe("a", log, read_only=True), _Probe("b", log, read_only=True))

    assert log == ["start c", "end c", "start a", "end a", "start b", "end b"]


def test_an_all_read_only_batch_still_runs_in_parallel():
    log: List[str] = []
    _run(_Probe("a", log, read_only=True), _Probe("b", log, read_only=True))

    assert log[:2] == ["start a", "start b"] or log[:2] == ["start b", "start a"]


@pytest.mark.parametrize(
    "answer",
    [MagicMock(), 1, "yes", None, RuntimeError("boom")],
    ids=["mock", "int", "str", "none", "raises"],
)
def test_an_unclear_answer_counts_as_not_read_only(answer):
    tools = [_Probe("a", [], read_only=True), _Probe("b", [], read_only=answer)]

    assert _all_read_only([make_plan(t) for t in tools]) is False


@pytest.mark.parametrize("decision", [ToolCallDecision.DENY, ToolCallDecision.CANCELLED])
def test_a_plan_that_never_runs_does_not_force_serial(decision):
    """Read-only mode denies the write; the reads it leaves may still overlap."""
    tools = [_Probe("a", [], read_only=True), _Probe("w", [], read_only=False)]
    plans = [make_plan(tools[0]), make_plan(tools[1], decision=decision)]

    assert _all_read_only(plans) is True


def test_a_tool_runs_on_its_own_context_when_serial():
    """The serial path keeps the parallel path's ContextVar isolation."""
    import contextvars

    var: contextvars.ContextVar[str] = contextvars.ContextVar("batch_iso", default="parent")
    seen: List[str] = []

    class _Writer(_Probe):
        def execute(self, **_kwargs) -> str:
            var.set("tool")
            return "w"

    class _Reader(_Probe):
        def execute(self, **_kwargs) -> str:
            seen.append(var.get())
            return "r"

    _run(_Writer("w", []), _Reader("r", []))

    assert seen == ["parent"]
    assert var.get() == "parent"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell command")
def test_a_shell_write_and_a_read_of_it_in_one_batch(tmp_path):
    """The codex probe, through the real ``ToolRunner`` and real tools."""
    agent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        permission_mode="full-access",
    )
    try:
        calls = [
            ChatCompletionMessageToolCall(
                id="c-write", type="function",
                function={
                    "name": "run_shell_command",
                    "arguments": '{"command": "sleep 0.3; echo written > out.txt"}',
                },
            ),
            ChatCompletionMessageToolCall(
                id="c-read", type="function",
                function={"name": "read_file", "arguments": '{"file_path": "out.txt"}'},
            ),
        ]
        _, messages = agent.tool_runner.execute(calls)
        by_id = {m["tool_call_id"]: m["content"] for m in messages}
        assert [m["tool_call_id"] for m in messages] == ["c-write", "c-read"]
        assert "written" in by_id["c-read"]
    finally:
        agent.close()


def test_ctrl_c_in_a_serial_batch_cancels_the_turn():
    from agentao.cancellation import CancellationToken

    class _Interrupter(_Probe):
        def execute(self, **_kwargs) -> str:
            raise KeyboardInterrupt

    log: List[str] = []
    token = CancellationToken()
    executor = ToolExecutor(NullTransport(), logging.getLogger("test.batch"))
    plans = [make_plan(_Interrupter("i", log), call_id="i"), make_plan(_Probe("b", log), call_id="b")]

    with pytest.raises(KeyboardInterrupt):
        executor.execute_batch(plans, cancellation_token=token)

    assert token.is_cancelled
    assert log == []  # the call after the interrupt never started
