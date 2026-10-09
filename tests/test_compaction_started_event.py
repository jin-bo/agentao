"""``COMPACTION_STARTED``: a live signal that the summarizer is running (#491).

The coordinator used to report a compaction only once it was over, so a
CLI turn that compacted for a minute showed "Thinking…" the whole time. The
start fires just before the summarizer call and only then, so every start
has a ``COMPACTION_SETTLED`` for the same attempt, unless the turn is
cancelled.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.cancellation import AgentCancelledError, CancellationToken
from agentao.compaction.coordinator import CompactionCoordinator, CompactionRequest
from agentao.compaction.types import CompactionDecision
from agentao.context_manager import ContextManager
from agentao.transport import EventType


def _agent(summary="a summary", controller=None, history=None):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    cm = ContextManager(llm, Mock(), max_tokens=200_000)
    order = []

    def summarize(_formatted, *, cancellation_token=None):
        order.append("summarizer")
        return summary
    cm._summarize_formatted = summarize
    events = []

    def emit(event):
        order.append(event.type.value)
        events.append(event)
    agent = SimpleNamespace(
        messages=history if history is not None else [
            {"role": r, "content": f"{r} {i} " + "x" * 200}
            for i in range(12) for r in ("user", "assistant")
        ],
        context_manager=cm,
        transport=SimpleNamespace(emit=emit),
        llm=SimpleNamespace(logger=Mock()),
        _plugin_hook_rules=[],
        _last_session_summary_id=None,
        _turn_finish_reason_missing=False,
        _build_system_prompt=lambda: "sys",
        memory_manager=None,
        compaction_controller=controller,
    )
    agent.compaction_coordinator = CompactionCoordinator(agent)
    return agent, events, order


def _types(events):
    return [e.type for e in events]


@pytest.mark.parametrize("summary, status", [("a summary", "success"), ("", "failed")])
def test_a_start_comes_before_the_summarizer_and_a_settle_after_it(summary, status):
    agent, events, order = _agent(summary)

    agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert order.index("compaction_started") < order.index("summarizer") \
        < order.index("compaction_settled")
    (started,) = [e for e in events if e.type == EventType.COMPACTION_STARTED]
    assert started.data == {"trigger": "auto", "kind": "full", "reason": "compression_threshold"}
    (settled,) = [e for e in events if e.type == EventType.COMPACTION_SETTLED]
    assert settled.data["status"] == status


@pytest.mark.parametrize("case", ["history_too_short", "host_cancel", "host_summary", "circuit_open"])
def test_no_start_when_the_summarizer_is_not_called(case):
    """Each of these ends without the slow step; a start there would leave a
    UI saying "compacting" with nothing running (or, for the skipped ones,
    no settle ever coming)."""
    controller = {
        "host_cancel": lambda ctx: CompactionDecision("cancel", reason="not now"),
        "host_summary": lambda ctx: CompactionDecision("provide_summary", summary="host summary"),
    }.get(case)
    history = [{"role": "user", "content": "hi"}] if case == "history_too_short" else None
    agent, events, order = _agent(controller=controller, history=history)
    if case == "circuit_open":
        agent.context_manager._consecutive_compact_failures = 3

    agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert EventType.COMPACTION_STARTED not in _types(events)
    assert "summarizer" not in order


def test_the_kinds_that_call_no_model_announce_nothing():
    agent, events, _ = _agent()
    for kind, reason in (("microcompact", "microcompact_threshold"),
                         ("minimal_history", "api_overflow_after_compression")):
        agent.compaction_coordinator.run(
            CompactionRequest("auto", kind, reason), system_prompt="sys",
        )
    assert EventType.COMPACTION_STARTED not in _types(events)


def test_a_cancelled_turn_starts_and_then_raises_with_no_settle():
    """The documented exception to "every start settles": the turn's own
    end reports it, so a UI must not wait for a settle."""
    agent, events, _ = _agent()
    token = CancellationToken()

    def summarize(_formatted, *, cancellation_token=None):
        token.cancel("user")
        return "a summary"
    agent.context_manager._summarize_formatted = summarize

    with pytest.raises(AgentCancelledError):
        agent.compaction_coordinator.run(
            CompactionRequest("auto", "full", "compression_threshold"),
            system_prompt="sys", cancellation_token=token,
        )
    assert _types(events) == [EventType.COMPACTION_STARTED]


def test_a_transport_that_raises_does_not_stop_the_compaction():
    agent, _, _ = _agent()

    def emit(event):
        if event.type == EventType.COMPACTION_STARTED:
            raise RuntimeError("host transport is broken")
    agent.transport.emit = emit

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )
    assert run.outcome.status == "success"


# ---------------------------------------------------------------------------
# The CLI: "Thinking…" becomes "Compacting context (Ns)" and back
# ---------------------------------------------------------------------------

class _Status:
    def __init__(self):
        self.updates = []
        self.starts = 0

    def update(self, renderable):
        self.updates.append(renderable)

    def start(self):
        self.starts += 1


@pytest.fixture
def printed(monkeypatch):
    lines = []
    monkeypatch.setattr("agentao.cli._globals.console.print",
                        lambda text="", **kw: lines.append(str(text)))
    return lines


def _clock(monkeypatch, *readings):
    """One clock for the handlers and the spinner text, which binds its own."""
    import time

    import agentao.cli.commands.compact as compact
    ticks = iter(readings)
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(compact, "monotonic", time.monotonic)


def _cli():
    return SimpleNamespace(current_status=_Status(), _compaction_pending=None)


def _send(cli, etype, data):
    from agentao.cli.transport import emit_event
    from agentao.transport import AgentEvent
    emit_event(cli, AgentEvent(etype, data))


_AUTO = {"trigger": "auto", "kind": "full", "reason": "compression_threshold"}


def test_an_automatic_compaction_takes_over_the_turns_spinner(monkeypatch, printed):
    from agentao.cli.commands.compact import _CompactingStatus
    cli = _cli()
    _clock(monkeypatch, 100.0, 107.0, 165.4)

    _send(cli, EventType.COMPACTION_STARTED, _AUTO)
    (shown,) = cli.current_status.updates
    assert isinstance(shown, _CompactingStatus)
    assert str(shown.__rich__()) == "Compacting context (7s)"
    assert cli.current_status.starts == 1

    _send(cli, EventType.COMPACTION_SETTLED,
          {**_AUTO, "status": "success", "pre_msgs": 24, "post_msgs": 5})
    assert printed == ["[dim]Context compacted · 24 → 5 messages · 1m 05s[/dim]"]
    assert cli.current_status.updates[-1] == "[bold yellow]Thinking…[/bold yellow]"
    assert cli._compaction_pending is None


def test_a_failed_one_says_so_with_its_time(monkeypatch, printed):
    cli = _cli()
    _clock(monkeypatch, 10.0, 13.0)
    _send(cli, EventType.COMPACTION_STARTED, _AUTO)
    _send(cli, EventType.COMPACTION_SETTLED,
          {**_AUTO, "status": "failed", "detail": "summary_empty"})
    assert printed == ["[warning]Compaction made no change (summary_empty) · 3s[/warning]"]


def test_manual_compact_keeps_its_own_spinner(printed):
    cli = _cli()
    _send(cli, EventType.COMPACTION_STARTED, {**_AUTO, "trigger": "manual"})
    _send(cli, EventType.COMPACTION_SETTLED, {**_AUTO, "trigger": "manual", "status": "success"})
    assert cli.current_status.updates == [] and printed == []


def test_a_settle_with_no_start_prints_nothing(printed):
    """A host-cancelled compaction settles without ever starting."""
    cli = _cli()
    _send(cli, EventType.COMPACTION_SETTLED, {**_AUTO, "status": "cancelled"})
    assert printed == [] and cli.current_status.updates == []


def test_a_start_left_by_a_cancelled_turn_is_not_answered_in_the_next(monkeypatch, printed):
    """The cancelled turn sent no settle; the next turn has a new spinner,
    so a settle there must not report the old start's clock."""
    cli = _cli()
    _clock(monkeypatch, 1.0)
    _send(cli, EventType.COMPACTION_STARTED, _AUTO)
    cli.current_status = _Status()  # the next turn

    _send(cli, EventType.COMPACTION_SETTLED, {**_AUTO, "status": "cancelled"})
    assert printed == [] and cli.current_status.updates == []


def test_no_spinner_no_change(printed):
    cli = _cli()
    cli.current_status = None
    _send(cli, EventType.COMPACTION_STARTED, _AUTO)
    assert cli._compaction_pending is None
