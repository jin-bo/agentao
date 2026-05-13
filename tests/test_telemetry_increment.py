"""P1 telemetry increment: LLM latency / TTFT + turn-level tool count.

Pins three small additions to the existing observability event set:

  - ``LLM_CALL_COMPLETED`` carries ``model_latency_ms`` (alias of
    ``duration_ms``) and ``first_token_ms`` (TTFT, or ``None`` when the
    call produced no streamed text).
  - ``TURN_END`` carries ``tool_count`` for the turn.
  - The replay adapter mirrors ``tool_count`` onto ``TURN_COMPLETED``.

No new event types, no public host-schema change — just extra optional
fields on payloads host/replay consumers already drain.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentao.runtime.llm_call import run_llm_call
from agentao.runtime.turn import run_turn
from agentao.transport import EventType
from agentao.transport.events import AgentEvent


class _CaptureTransport:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


def _completed(transport: _CaptureTransport) -> dict:
    for ev in transport.events:
        if ev.type == EventType.LLM_CALL_COMPLETED:
            return ev.data
    raise AssertionError("no LLM_CALL_COMPLETED event emitted")


def _make_llm(chunks: list[str], *, finish: str = "stop"):
    """A stand-in ``agent.llm`` whose ``chat_stream`` streams ``chunks``."""

    def chat_stream(*, messages, tools, max_tokens, on_text_chunk, cancellation_token):
        for c in chunks:
            on_text_chunk(c)
        choice = SimpleNamespace(finish_reason=finish)
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
        return SimpleNamespace(choices=[choice], usage=usage)

    return SimpleNamespace(
        model="test-model", temperature=0.0, max_tokens=256, chat_stream=chat_stream
    )


def _make_agent(llm) -> SimpleNamespace:
    return SimpleNamespace(transport=_CaptureTransport(), llm=llm, replay_manager=None)


def test_llm_call_completed_has_latency_and_ttft_on_streamed_text():
    agent = _make_agent(_make_llm(["Hel", "lo", " world"]))
    run_llm_call(agent, messages=[{"role": "user", "content": "hi"}], tools=[])

    data = _completed(agent.transport)
    assert data["status"] == "ok"
    assert isinstance(data["model_latency_ms"], int)
    assert data["model_latency_ms"] == data["duration_ms"]
    assert isinstance(data["first_token_ms"], int)
    assert data["first_token_ms"] >= 0
    assert data["first_token_ms"] <= data["model_latency_ms"]


def test_llm_call_completed_ttft_none_when_no_text_streamed():
    agent = _make_agent(_make_llm([]))  # tool-only style response: no deltas
    run_llm_call(agent, messages=[{"role": "user", "content": "hi"}], tools=[])

    data = _completed(agent.transport)
    assert data["first_token_ms"] is None
    assert isinstance(data["model_latency_ms"], int)


def test_llm_call_completed_error_path_still_reports_latency():
    def chat_stream(*, messages, tools, max_tokens, on_text_chunk, cancellation_token):
        on_text_chunk("partial ")
        exc = RuntimeError("boom")
        exc.streamed = True
        raise exc

    llm = SimpleNamespace(
        model="test-model", temperature=0.0, max_tokens=256, chat_stream=chat_stream
    )
    agent = _make_agent(llm)
    with pytest.raises(RuntimeError):
        run_llm_call(agent, messages=[{"role": "user", "content": "hi"}], tools=[])

    data = _completed(agent.transport)
    assert data["status"] == "error"
    assert isinstance(data["model_latency_ms"], int)
    assert data["model_latency_ms"] == data["duration_ms"]
    assert isinstance(data["first_token_ms"], int)  # one chunk reached on_text_chunk


def test_turn_end_carries_tool_count():
    transport = _CaptureTransport()

    def _chat_inner(user_message, max_iterations, token):
        # Simulate the chat loop bumping the per-turn counter.
        agent._turn_tool_count += 2
        agent._turn_tool_count += 1
        return "done"

    agent = SimpleNamespace(
        transport=transport,
        memory_manager=None,
        messages=[],
        _chat_inner=_chat_inner,
    )
    run_turn(agent, "hello")

    end = next(e for e in transport.events if e.type == EventType.TURN_END)
    assert end.data["tool_count"] == 3
    assert end.data["status"] == "ok"


def test_turn_end_tool_count_zero_when_no_tools():
    transport = _CaptureTransport()
    agent = SimpleNamespace(
        transport=transport,
        memory_manager=None,
        messages=[],
        _chat_inner=lambda *a, **k: "no tools here",
    )
    run_turn(agent, "hello")

    end = next(e for e in transport.events if e.type == EventType.TURN_END)
    assert end.data["tool_count"] == 0


def test_replay_adapter_mirrors_tool_count_on_turn_completed(tmp_path):
    from agentao.replay import ReplayAdapter, ReplayReader, ReplayRecorder
    from agentao.transport import NullTransport

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(NullTransport(), rec)
    adapter.emit(AgentEvent(EventType.TURN_BEGIN, {"user_message": "hi"}))
    adapter.emit(AgentEvent(EventType.TURN_END, {
        "final_text": "bye", "status": "ok", "error": None, "tool_count": 4,
    }))
    rec.close()

    events = ReplayReader(rec.path).events()
    completed = [e for e in events if e["kind"] == "turn_completed"]
    assert completed and completed[-1]["payload"].get("tool_count") == 4
