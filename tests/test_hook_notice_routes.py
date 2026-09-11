"""A hook user-notice needs a reader on every surface, not just a payload field.

Four of the eight hook events are dispatched by a caller that owns an output —
the CLI prints what ``SessionStart`` / ``SessionEnd`` return, ``agentao run``
folds it into its warnings, ACP sends it as a ``session/update``. The other
notice-producing events are dispatched where there is no such caller: inside
the chat loop (``UserPromptSubmit``, ``Stop``), inside a tool worker
(``PostToolUse*``), inside the compaction coordinator
(``SessionStart(source="compact")``). Those ride ``PLUGIN_HOOK_FIRED``'s
``user_notices``, and until this module existed **nothing read that field** —
every one of them was computed, capped, stored and dropped.

That is the defect §5.2.1 of ``docs/design/hooks-claude-contract-conformance-
plan.md`` names: a sink is not a route. These tests pin the three routes, and
one of them end to end from the producer that first exposed the gap.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock

import pytest

from agentao.transport import AgentEvent, EventType


def _event(notices: Any, **extra: Any) -> AgentEvent:
    data: Dict[str, Any] = {"hook_name": "PostToolUse*", "outcome": "notice"}
    if notices is not None:
        data["user_notices"] = notices
    data.update(extra)
    return AgentEvent(EventType.PLUGIN_HOOK_FIRED, data)


# ── the interactive CLI ───────────────────────────────────────────────────


@pytest.fixture
def printed(monkeypatch) -> List[str]:
    """Capture what the CLI console renders, markup and all."""
    out: List[str] = []
    monkeypatch.setattr(
        "agentao.cli._globals.console.print",
        lambda *args, **kwargs: out.append(" ".join(str(a) for a in args)),
    )
    return out


def test_the_cli_prints_every_notice_on_the_event(printed):
    from agentao.cli.transport import emit_event

    emit_event(SimpleNamespace(), _event(["first", "second"]))

    assert len(printed) == 2
    assert "first" in printed[0]
    assert "second" in printed[1]


def test_a_notice_cannot_reach_the_terminal_as_rich_markup(printed):
    """A notice is a hook's stderr, so it is attacker-shaped text at a Rich
    boundary — ``[black on black]`` needs no control byte to render a warning
    invisible, and ``[/oops]`` raises out of the dispatch."""
    from agentao.cli.transport import emit_event

    emit_event(SimpleNamespace(), _event(["[black on black]hidden[/] [/oops]"]))

    assert len(printed) == 1
    assert "\\[black on black]" in printed[0]
    assert "\\[/oops]" in printed[0]


def test_the_cli_prints_nothing_when_there_is_nothing_to_print(printed):
    from agentao.cli.transport import emit_event

    emit_event(SimpleNamespace(), _event(None))
    emit_event(SimpleNamespace(), _event([]))
    emit_event(SimpleNamespace(), _event("not-a-list"))
    emit_event(SimpleNamespace(), _event([None, 42, ""]))

    assert printed == []


def test_the_lifecycle_printer_and_the_event_printer_are_one_function():
    """``cli/session.py`` prints what the lifecycle dispatches return. Two
    copies of the escape-and-strip pairing is how one of them loses it."""
    from agentao.cli import session, transport

    assert session.print_hook_notice is transport.print_hook_notice


# ── ACP ───────────────────────────────────────────────────────────────────


def _acp_transport():
    from agentao.acp.transport import ACPTransport

    server = MagicMock()
    return ACPTransport(server, "sess-acp"), server


def test_acp_maps_the_notices_onto_a_session_update():
    transport, server = _acp_transport()

    transport.emit(_event(["disk is full"]))

    server.write_notification.assert_called_once()
    _method, params = server.write_notification.call_args[0]
    assert params["sessionId"] == "sess-acp"
    assert params["update"]["sessionUpdate"] == "agent_message_chunk"
    assert params["update"]["content"]["text"] == "\u26a0 disk is full"


def test_acp_sends_one_update_carrying_every_notice():
    transport, server = _acp_transport()

    transport.emit(_event(["one", "two"]))

    _method, params = server.write_notification.call_args[0]
    assert params["update"]["content"]["text"] == "\u26a0 one\n\u26a0 two"


def test_acp_drops_a_hook_event_that_carries_no_notice():
    """Counts and verdicts are replay's business; a client is not shown an
    empty warning because a hook happened to run."""
    transport, server = _acp_transport()

    transport.emit(_event(None, matched_rule_count=3))

    server.write_notification.assert_not_called()


def test_both_acp_notice_paths_produce_the_same_wire_shape():
    """The lifecycle path writes directly; this one goes through the event
    mapping. A client recognises a notice by its shape, so they must agree."""
    from agentao.acp._transport_helpers import write_user_notice

    transport, server = _acp_transport()
    transport.emit(_event(["same text"]))
    via_event = server.write_notification.call_args[0][1]["update"]

    server.reset_mock()
    write_user_notice(server, "sess-acp", "same text")
    via_lifecycle = server.write_notification.call_args[0][1]["update"]

    # ``schema_version`` is stamped by the event mapping and is absent on every
    # direct write (replay, ``session/set_mode``, this one) — the field is
    # optional in the schema. What must not drift is how a notice *looks*.
    assert via_event["sessionUpdate"] == via_lifecycle["sessionUpdate"]
    assert via_event["content"] == via_lifecycle["content"]


# ── Stop: the profile's systemMessage, and v1's frozen behaviour ──────────


def _dispatch_stop(tmp_path, contract: str, payload: dict):
    """Run one real Stop hook subprocess under ``contract``."""
    from agentao.plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
    from agentao.plugins.models import ParsedHookRule

    from .support.stop_precompact import write_json_emitting_hook

    script = write_json_emitting_hook(tmp_path, payload)
    rule = ParsedHookRule(
        event="Stop", hook_type="command", command=f"sh '{script}'",
        plugin_name="t", contract=contract,
    )
    stop_payload = ClaudeHookPayloadAdapter().build_stop(
        cwd=tmp_path,
        last_assistant_message="answer",
        turn_end_reason="final_response",
    )
    return PluginHookDispatcher(cwd=tmp_path).dispatch_stop(
        payload=stop_payload, rules=[rule],
    )


def test_the_profiles_system_message_reaches_the_user_channel(tmp_path):
    result = _dispatch_stop(
        tmp_path, "claude-code@profile-1", {"systemMessage": "ran out of quota"},
    )

    assert result.user_notices == ["ran out of quota"]
    assert result.additional_contexts == []


def test_a_v1_hooks_system_message_still_goes_only_to_the_model(tmp_path):
    """``agentao-v1`` is frozen, and it double-writes ``systemMessage`` into
    the model's context — which is where its hooks' authors have always read
    it. Surfacing it to the terminal too would be a behaviour change."""
    result = _dispatch_stop(
        tmp_path, "agentao-v1", {"systemMessage": "ran out of quota"},
    )

    assert result.user_notices == []
    assert result.additional_contexts == ["ran out of quota"]


def test_the_stop_event_carries_its_notices_to_the_host(tmp_path):
    """The emit site is the only thing between a parsed notice and a surface."""
    from agentao.plugins.models import ParsedHookRule

    from .support.stop_precompact import make_runner_with_rules, write_json_emitting_hook

    script = write_json_emitting_hook(
        tmp_path, {"systemMessage": "ran out of quota"},
    )
    rule = ParsedHookRule(
        event="Stop", hook_type="command", command=f"sh '{script}'",
        plugin_name="t", contract="claude-code@profile-1",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[rule])
    stop_result = runner._dispatch_stop(
        turn_end_reason="final_response", last_assistant_message="answer",
    )
    runner._emit_stop_hook_fired(
        outcome="allow", turn_end_reason="final_response", stop_result=stop_result,
    )

    fired = transport.hook_fired_events("Stop")
    assert len(fired) == 1
    assert fired[0].data["user_notices"] == ["ran out of quota"]


# ── end to end: the producer that exposed the gap ─────────────────────────


def test_a_compaction_hooks_notice_reaches_the_terminal(printed, monkeypatch):
    """The whole route in one test: a ``SessionStart(source="compact")`` hook
    prints a notice, the coordinator emits it, the CLI renders it.

    Asserting the coordinator's payload proves only that the sink was written.
    """
    from agentao.compaction.coordinator import CompactionCoordinator, CompactionRequest
    from agentao.compaction.types import CompactionOutcome
    from agentao.context_manager import ContextManager
    from agentao.cli.transport import emit_event

    monkeypatch.setattr(
        "agentao.plugins.hooks.lifecycle.fire_session_start",
        lambda agent, session_id, *, source="startup": ["post-compaction notice"],
    )

    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    cm = ContextManager(llm, Mock(), max_tokens=200_000)
    messages = [{"role": "user", "content": "x" * 400}]
    cm._run_compaction = lambda m, **kw: CompactionOutcome(
        status="success", trigger="manual", kind="full", reason="manual_cli",
        messages=messages + [{"role": "user", "content": "summarized"}],
        pre_tokens=900, post_tokens=100, detail=None,
    )

    cli = SimpleNamespace()
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        transport=SimpleNamespace(emit=lambda ev: emit_event(cli, ev)),
        llm=SimpleNamespace(logger=Mock()),
        _plugin_hook_rules=[object()],
        _session_id="sess-1",
        working_directory=None,
        _last_session_summary_id=None,
        _turn_finish_reason_missing=False,
        _build_system_prompt=lambda: "sys",
        _emit_session_summary_if_new=lambda _prev: "summary-id",
    )
    agent.add_message = lambda role, content: agent.messages.append(
        {"role": role, "content": content}
    )
    agent._emit_context_compressed = lambda **kw: None
    agent.compaction_coordinator = CompactionCoordinator(agent)

    agent.compaction_coordinator.run(
        CompactionRequest("manual", "full", "manual_cli"), system_prompt="sys",
    )

    assert any("post-compaction notice" in line for line in printed)
