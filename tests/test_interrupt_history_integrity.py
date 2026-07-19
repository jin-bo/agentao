"""An abnormally-ended turn must not leave an unanswered ``tool_call``.

Strict Chat-Completions APIs reject a request in which an assistant
``tool_calls`` entry has no matching ``role: "tool"`` reply. The chat loop
already backfills placeholders on the paths it controls (doom-loop halt,
length truncation), but Ctrl+C during a synchronous tool lands *between*
recording the assistant message and appending the results — the tool
executor catches ``Exception``, which ``KeyboardInterrupt`` is not.

The orphan is not a one-turn cosmetic wart: it stays in ``agent.messages``
and is re-sent on every later turn, so the session is unusable until
``/new``. These tests pin both the unit-level repair and the end-to-end
property that the *next* request goes out clean.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao import Agentao
from agentao.cancellation import AgentCancelledError
from agentao.runtime.sanitize import backfill_orphaned_tool_calls
from agentao.tools.base import Tool


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )


def _tool_call(call_id: str, name: str = "boom", args: str = "{}"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=args),
    )


def _tool_call_response(*calls):
    message = SimpleNamespace(content="", tool_calls=list(calls), reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        usage=None,
        model="test-model",
    )


def _orphans(messages):
    """ids requested by an assistant message but never answered."""
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    return [
        tc["id"]
        for m in messages
        for tc in (m.get("tool_calls") or [])
        if tc["id"] not in answered
    ]


class _RaisingTool(Tool):
    """Tool that raises the configured exception instead of running."""

    name = "boom"
    description = "raises on execute"
    parameters = {"type": "object", "properties": {}}
    requires_confirmation = False

    def __init__(self, exc: BaseException):
        super().__init__()
        self._exc = exc

    def execute(self, **kwargs):
        raise self._exc


# ---------------------------------------------------------------------------
# unit: backfill_orphaned_tool_calls
# ---------------------------------------------------------------------------


class TestBackfillOrphanedToolCalls:
    def test_no_orphans_is_a_no_op(self):
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "ls", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "ls", "content": "ok"},
        ]
        snapshot = [dict(m) for m in messages]

        assert backfill_orphaned_tool_calls(messages) == 0
        assert messages == snapshot

    def test_orphan_is_answered(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "ls", "arguments": "{}"}}],
            },
        ]

        assert backfill_orphaned_tool_calls(messages) == 1
        assert _orphans(messages) == []
        assert messages[1]["role"] == "tool"
        assert messages[1]["tool_call_id"] == "c1"
        assert messages[1]["name"] == "ls"

    def test_placeholder_is_inserted_directly_after_its_request(self):
        """Results must follow their own assistant message, not trail history."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "ls", "arguments": "{}"}}],
            },
            {"role": "assistant", "content": "[Interrupted]"},
        ]

        backfill_orphaned_tool_calls(messages)

        assert [m["role"] for m in messages] == ["assistant", "tool", "assistant"]
        assert messages[-1]["content"] == "[Interrupted]"

    def test_every_call_in_a_partially_answered_batch_is_covered(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "ls", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "cat", "arguments": "{}"}},
                    {"id": "c3", "function": {"name": "rg", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "ls", "content": "done"},
        ]

        assert backfill_orphaned_tool_calls(messages) == 2
        assert _orphans(messages) == []

    def test_empty_name_is_stamped_in_lock_step(self):
        """A call interrupted before its name streamed keeps ``name == ""``.

        Strict proxies reject both an empty name and a name mismatch between
        the assistant tool_call and its result, so the placeholder has to be
        written into both sides.
        """
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "", "arguments": "{}"}}],
            },
        ]

        backfill_orphaned_tool_calls(messages)

        assert messages[0]["tool_calls"][0]["function"]["name"] == "unknown"
        assert messages[1]["name"] == "unknown"

    def test_is_idempotent(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "ls", "arguments": "{}"}}],
            },
        ]

        assert backfill_orphaned_tool_calls(messages) == 1
        after_first = [dict(m) for m in messages]
        assert backfill_orphaned_tool_calls(messages) == 0
        assert messages == after_first

    def test_repeated_id_across_messages_is_answered_once(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "dup", "function": {"name": "ls", "arguments": "{}"}}],
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "dup", "function": {"name": "ls", "arguments": "{}"}}],
            },
        ]

        assert backfill_orphaned_tool_calls(messages) == 1

    def test_malformed_entries_do_not_raise(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": "not-a-list"},
            {"role": "assistant", "content": "", "tool_calls": [None, "junk"]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": ""}]},
            "not-a-dict",
        ]

        assert backfill_orphaned_tool_calls(messages) == 0


# ---------------------------------------------------------------------------
# end-to-end: the turn boundary repairs history
# ---------------------------------------------------------------------------


class TestTurnEndingPathsLeaveHistoryValid:
    def test_keyboard_interrupt_during_a_tool_leaves_no_orphan(self):
        agent = _make_agent()
        agent.add_tool(_RaisingTool(KeyboardInterrupt()))
        agent._llm_call = lambda messages, tools, token: _tool_call_response(
            _tool_call("call_1")
        )

        agent.chat("do it")

        assert _orphans(agent.messages) == []
        assert agent.messages[-1]["content"] == "[Interrupted]"

    def test_orphan_does_not_reach_the_next_request(self):
        """The regression that made this a session-bricking bug, not a wart."""
        agent = _make_agent()
        agent.add_tool(_RaisingTool(KeyboardInterrupt()))
        agent._llm_call = lambda messages, tools, token: _tool_call_response(
            _tool_call("call_1")
        )
        agent.chat("do it")

        sent = {}

        def capture(messages, tools, token):
            sent["messages"] = messages
            message = SimpleNamespace(content="ok", tool_calls=None, reasoning_content=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")],
                usage=None,
                model="test-model",
            )

        agent._llm_call = capture
        agent.chat("continue")

        assert _orphans(sent["messages"]) == []

    def test_multi_call_batch_interrupted_answers_every_call(self):
        agent = _make_agent()
        agent.add_tool(_RaisingTool(KeyboardInterrupt()))
        agent._llm_call = lambda messages, tools, token: _tool_call_response(
            _tool_call("call_1"), _tool_call("call_2"), _tool_call("call_3")
        )

        agent.chat("do it")

        assert _orphans(agent.messages) == []

    def test_unexpected_tool_exception_leaves_no_orphan(self):
        """A tool-phase error that escapes the executor is the same hazard."""
        agent = _make_agent()

        def explode(messages, tools, token):
            raise RuntimeError("provider exploded")

        agent._llm_call = lambda messages, tools, token: _tool_call_response(
            _tool_call("call_1")
        )
        original = agent.tool_runner.execute

        def boom(*args, **kwargs):
            raise RuntimeError("tool phase exploded")

        agent.tool_runner.execute = boom

        with pytest.raises(RuntimeError):
            agent.chat("do it")

        assert _orphans(agent.messages) == []
        agent.tool_runner.execute = original

    def test_normal_turn_history_is_untouched(self):
        """The repair must not perturb a turn that ended cleanly."""
        agent = _make_agent()
        message = SimpleNamespace(content="all good", tool_calls=None, reasoning_content=None)
        agent._llm_call = lambda messages, tools, token: SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
            model="test-model",
        )

        agent.chat("hi")

        assert [m["role"] for m in agent.messages] == ["user", "assistant"]
        assert not any(m["role"] == "tool" for m in agent.messages)
