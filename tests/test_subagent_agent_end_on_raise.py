"""A foreground sub-agent that raises still sends ``AGENT_END``.

``execute()`` sent ``AGENT_START`` and then, on a raising run, only the
public terminal event: the CLI and ACP never heard that the child ended, and
the start had no matching end.

These tests drive the real wrapper against a real parent. They record the
progress callback and the public event stream into one list, so the order of
the two is asserted along with the counts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Tuple

import pytest

from agentao.agent import Agentao
from agentao.cancellation import AgentCancelledError, CancellationToken
from agentao.host.projection import HostSubagentEmitter
from agentao.permissions import PermissionEngine
from agentao.transport import EventType


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


class _Stream:
    def __init__(self, log: List[Tuple[str, Any]]) -> None:
        self._log = log

    def publish(self, event: Any) -> None:
        self._log.append(("public", event))


@pytest.fixture
def parent(tmp_path):
    (tmp_path / "user").mkdir(exist_ok=True)
    agent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True,
        permission_engine=PermissionEngine(
            project_root=tmp_path, user_root=tmp_path / "user",
        ),
    )
    try:
        yield agent
    finally:
        agent.close()


def _watch(wrapper, *, end_raises: bool = False) -> List[Tuple[str, Any]]:
    log: List[Tuple[str, Any]] = []
    wrapper._subagent_emitter = HostSubagentEmitter(
        _Stream(log), parent_session_id_provider=lambda: "parent-s",
    )

    def sink(event):
        log.append((event.type.value, event.data))
        if end_raises and event.type is EventType.AGENT_END:
            raise RuntimeError("host sink broke")

    wrapper._event_sink = sink
    return log


def _shape(log):
    """``[(kind, state-or-phase), ...]`` in arrival order."""
    return [
        (kind, payload.phase if kind == "public" else payload.get("state", "running"))
        for kind, payload in log
    ]


def _end(log):
    ends = [payload for kind, payload in log if kind == "agent_end"]
    assert len(ends) == 1, f"expected one agent_end, got {len(ends)}"
    # Attribute access; a counter the event left out reads as ``None``.
    return SimpleNamespace(**{
        key: ends[0].get(key)
        for key in ("agent", "state", "error", "turns", "tool_calls", "tokens", "duration_ms")
    })


def _chat_raising(monkeypatch, exc: BaseException) -> None:
    def chat(self, *args, **kwargs):
        raise exc

    monkeypatch.setattr(Agentao, "chat", chat)


def test_an_ordinary_exception_ends_the_run_as_failed(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)

    def chat(self, *args, **kwargs):
        # A run that got somewhere before it raised.
        self.messages.extend([
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "data"},
        ])
        raise RuntimeError("boom")

    monkeypatch.setattr(Agentao, "chat", chat)

    with pytest.raises(RuntimeError, match="boom"):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("agent_start", "running"),
        ("public", "spawned"),
        ("agent_end", "failed"),
        ("public", "failed"),
    ]
    end = _end(log)
    # The type name, as on the public event — never the message, which can
    # carry anything the child was handed.
    assert end.error == "RuntimeError"
    assert log[-1][1].error_type == "RuntimeError"
    # No stats came back, but the history and the clock were there to read:
    # the same measures the ordinary path reports.
    assert (end.turns, end.tool_calls) == (1, 1)
    assert isinstance(end.tokens, int) and end.tokens > 0
    assert isinstance(end.duration_ms, int) and end.duration_ms >= 0
    assert end.agent == "generalist"


def test_a_construction_failure_ends_the_run_as_failed(parent):
    """The constructor raises before ``_run_sync`` has a sub-agent, so there
    is no ``stats`` and no ``finally`` usage either."""
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    wrapper._llm_config_getter = lambda: {}

    with pytest.raises(KeyError, match="api_key"):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("agent_start", "running"),
        ("public", "spawned"),
        ("agent_end", "failed"),
        ("public", "failed"),
    ]
    end = _end(log)
    assert end.error == "KeyError"
    # Nothing ran, so nothing is known — ``None``, not a run of zero.
    assert (end.turns, end.tool_calls, end.tokens, end.duration_ms) == (
        None, None, None, None,
    )


def test_a_raised_cancel_ends_the_run_as_cancelled(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _chat_raising(monkeypatch, AgentCancelledError("user-cancel"))

    with pytest.raises(AgentCancelledError):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("agent_start", "running"),
        ("public", "spawned"),
        ("agent_end", "cancelled"),
        ("public", "cancelled"),
    ]
    # A cancel is not an error; the state carries it.
    assert _end(log).error is None


def test_a_base_exception_sends_no_end_and_no_public_terminal(parent, monkeypatch):
    """``KeyboardInterrupt`` is outside both ``except`` branches. The end
    event follows the public event there, so neither is sent."""
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _chat_raising(monkeypatch, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("agent_start", "running"),
        ("public", "spawned"),
    ]


def test_a_raising_end_callback_does_not_mask_the_run_exception(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper, end_raises=True)
    _chat_raising(monkeypatch, ValueError("the real one"))

    with pytest.raises(ValueError, match="the real one"):
        wrapper.execute(task="x")

    # The public terminal event still went out after the failed send.
    assert _shape(log)[-2:] == [("agent_end", "failed"), ("public", "failed")]


def test_a_raising_end_callback_does_not_lose_a_finished_run(parent):
    """On the ordinary path too: the run's result comes back and the public
    terminal event is sent. The cancelled token stops the real ``chat()``
    before its first request, so nothing is sent to a model."""
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper, end_raises=True)
    token = CancellationToken()
    token.cancel("user-cancel")
    wrapper._cancellation_token = token

    out = wrapper.execute(task="x")

    assert "did not finish: it was cancelled" in out
    assert _shape(log)[-2:] == [
        ("agent_end", "cancelled"), ("public", "cancelled"),
    ]


# ── what the readers make of a counter that is not known ───────────────────


def _end_event(parent, state, **fields):
    """Send an end event through the production sink: the wrapper's
    ``event_sink``, which reads the parent's transport per call."""
    seen = []
    parent.transport.emit = seen.append
    wrapper = parent.tools.tools["agent_generalist"]
    wrapper._send_agent_end(state, **fields)
    (event,) = seen
    return event


def test_an_unknown_counter_is_left_out_of_agent_end(parent):
    event = _end_event(
        parent, "failed", error="KeyError",
        turns=None, tool_calls=None, tokens=None, duration_ms=None,
    )
    assert event.data == {"agent": "generalist", "state": "failed", "error": "KeyError"}


def test_a_known_zero_is_still_sent(parent):
    event = _end_event(
        parent, "completed", turns=0, tool_calls=0, tokens=0, duration_ms=0,
    )
    assert (event.data["turns"], event.data["duration_ms"]) == (0, 0)


def _render_end(data):
    from rich.console import Console

    from agentao.cli.display import DisplayController
    from agentao.transport import AgentEvent, EventType

    console = Console(record=True, width=200, color_system=None)
    DisplayController(console, lambda: None).on_event(
        AgentEvent(EventType.AGENT_END, data),
    )
    return console.export_text()


def test_the_cli_leaves_out_what_is_not_known():
    out = _render_end({"agent": "generalist", "state": "failed", "error": "KeyError"})
    assert "◀ [generalist]  KeyError" in out
    assert "turns" not in out and "tokens" not in out


def test_the_cli_shows_what_is_known():
    out = _render_end({
        "agent": "generalist", "state": "failed", "error": "RuntimeError",
        "turns": 1, "tool_calls": 1, "tokens": 1200, "duration_ms": 2500,
    })
    assert "1 turns · 1 tool calls · ~1,200 tokens" in out
    assert "RuntimeError" in out


def test_the_cli_labels_an_unknown_cancel():
    out = _render_end({"agent": "generalist", "state": "cancelled", "error": None})
    assert "◀ [generalist]  cancelled" in out
    assert "turns" not in out


def test_the_compat_step_callback_still_reads_zero():
    """``build_compat_transport`` is public, and its ``SubagentProgress``
    carried 0 for these counters. A missing counter keeps it there."""
    from agentao.embedding.compat import build_compat_transport
    from agentao.transport import AgentEvent, EventType

    got = []
    transport = build_compat_transport(step_callback=lambda n, p: got.append((n, p)))
    transport.emit(AgentEvent(EventType.AGENT_END, {
        "agent": "generalist", "state": "failed", "error": "KeyError",
    }))
    ((name, progress),) = got
    assert name == "__agent_end__"
    assert (progress.turns, progress.tool_calls, progress.tokens, progress.duration_ms) == (
        0, 0, 0, 0,
    )


def _render_start(data):
    from rich.console import Console

    from agentao.cli.display import DisplayController
    from agentao.transport import AgentEvent, EventType

    console = Console(record=True, width=200, color_system=None)
    DisplayController(console, lambda: None).on_event(
        AgentEvent(EventType.AGENT_START, data),
    )
    return console.export_text()


def test_the_cli_rules_keep_the_agent_name():
    """Rich read ``[generalist]`` as a markup tag and dropped it."""
    assert "▶ [generalist]: look" in _render_start(
        {"agent": "generalist", "task": "look", "max_turns": 15},
    )
    assert "◀ [generalist]  1 turns" in _render_end(
        {"agent": "generalist", "state": "completed", "turns": 1},
    )


def test_the_task_cannot_restyle_the_start_rule():
    """The task is the model's text; markup in it is shown, not applied."""
    out = _render_start(
        {"agent": "generalist", "task": "[/dim][red]x[/red] [bold]y", "max_turns": 15},
    )
    assert "[/dim][red]x[/red] [bold]y" in out


def test_an_unreadable_history_keeps_the_measured_duration(parent, monkeypatch):
    """Counting the history after a raise is best effort: when it fails, the
    counts are not known, but the run was still timed."""
    from agentao.agents.tools._wrapper import AgentToolWrapper

    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _chat_raising(monkeypatch, RuntimeError("boom"))

    def broken(sub_agent):
        raise ValueError("unreadable")

    monkeypatch.setattr(AgentToolWrapper, "_count_history", staticmethod(broken))

    with pytest.raises(RuntimeError, match="boom"):
        wrapper.execute(task="x")

    end = _end(log)
    assert (end.turns, end.tool_calls, end.tokens) == (None, None, None)
    assert isinstance(end.duration_ms, int) and end.duration_ms >= 0
