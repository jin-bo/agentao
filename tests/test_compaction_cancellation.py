"""A turn's cancel reaches the compaction it triggered.

The summarizer called ``llm_client.chat()`` with no cancellation token, and
since 0.5.4 a single retry wait inside that call can last a minute. Only a
token ends a wait early — the interactive CLI's Ctrl+C is a
``KeyboardInterrupt`` on the main thread and never needed one, but
``agentao run`` (SIGINT → ``token.cancel``), ACP ``session/cancel`` and a host
cancelling through a token all sat the waits out.

Threading the token in exposed a second defect: a cancel that ends a retry
wait makes ``chat()`` re-raise the provider error, the summarizer swallows it
into an empty summary, and an empty summary counts as a **summarizer
failure** — so three cancelled turns would open the breaker and pause
automatic compaction. The token is therefore read again before the failure
count and the commit, and a cancel raises ``AgentCancelledError`` (the
turn's own cancel) with history untouched.

Manual ``/compact`` runs outside a turn and passes no token.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import Mock

import pytest

from agentao.cancellation import AgentCancelledError, CancellationToken
from agentao.compaction.coordinator import CompactionCoordinator, CompactionRequest
from agentao.context_manager import ContextManager
from agentao.runtime.chat_loop import ChatLoopRunner

from tests.support.host_events import CapturingTransport
from tests.support.stop_precompact import make_bare_agent


def _history(n: int = 12) -> List[Dict[str, Any]]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"user {i} " + "x" * 200})
        out.append({"role": "assistant", "content": f"assistant {i} " + "y" * 200})
    return out


def _response(text: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=text),
        )],
        finish_reason_reported=True,
    )


def _cm(chat) -> ContextManager:
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    llm.chat = chat
    memory = Mock()
    return ContextManager(llm, Mock(), max_tokens=200_000, memory_manager=memory)


# -- ContextManager: the summarizer and the two reads ---------------------------


def test_the_summarizer_hands_the_token_to_chat():
    seen: List[Any] = []

    def chat(*, messages, tools, cancellation_token):
        seen.append(cancellation_token)
        return _response("a summary")

    cm = _cm(chat)
    token = CancellationToken()
    out = cm._run_compaction(
        _history(), is_auto=True, reason="compression_threshold",
        cancellation_token=token,
    )
    assert out.status == "success"
    assert seen == [token]


def test_a_cancel_that_ends_a_retry_wait_is_not_a_summarizer_failure():
    """``chat()`` re-raises the provider error once its wait is cancelled;
    the summarizer turns that into ``""``. Without the second read it was
    counted toward the breaker."""
    token = CancellationToken()

    def chat(*, messages, tools, cancellation_token):
        cancellation_token.cancel("sigint")
        raise RuntimeError("503 overloaded")  # what chat() re-raises

    cm = _cm(chat)
    history = _history()
    before = list(history)
    with pytest.raises(AgentCancelledError):
        cm._run_compaction(
            history, is_auto=True, reason="compression_threshold",
            cancellation_token=token,
        )
    assert cm._consecutive_compact_failures == 0
    assert cm.last_compaction_failure is None
    assert history == before
    cm.memory_manager.save_session_summary.assert_not_called()
    cm.memory_manager.crystallize_user_messages.assert_not_called()


def test_a_summary_that_arrives_after_the_cancel_is_not_committed():
    token = CancellationToken()

    def chat(*, messages, tools, cancellation_token):
        cancellation_token.cancel("acp")
        return _response("a perfectly good summary")

    cm = _cm(chat)
    with pytest.raises(AgentCancelledError):
        cm._run_compaction(
            _history(), is_auto=True, reason="compression_threshold",
            cancellation_token=token,
        )
    cm.memory_manager.save_session_summary.assert_not_called()


def test_an_already_cancelled_turn_sends_no_summarization_request():
    chat = Mock(return_value=_response("unused"))
    cm = _cm(chat)
    token = CancellationToken()
    token.cancel("sigint")
    with pytest.raises(AgentCancelledError):
        cm._run_compaction(
            _history(), is_auto=True, reason="compression_threshold",
            cancellation_token=token,
        )
    chat.assert_not_called()


def test_without_a_token_a_failed_summary_is_still_a_failure():
    """Manual ``/compact`` passes none: nothing about its path changes."""
    def chat(*, messages, tools, cancellation_token):
        assert cancellation_token is None
        raise RuntimeError("503 overloaded")

    cm = _cm(chat)
    out = cm._run_compaction(_history(), is_auto=False, reason="manual_cli")
    assert out.status == "failed"


# -- the coordinator ------------------------------------------------------------


def _agent(cm, messages):
    events: List[Any] = []
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        transport=SimpleNamespace(emit=events.append),
        llm=SimpleNamespace(logger=Mock()),
        _plugin_hook_rules=[],
        _last_session_summary_id=None,
        _turn_finish_reason_missing=False,
        _build_system_prompt=lambda: "sys",
        memory_manager=None,
    )
    agent.compaction_coordinator = CompactionCoordinator(agent)
    return agent, events


def test_a_cancelled_compaction_raises_through_the_coordinator_and_settles_nothing():
    token = CancellationToken()

    def chat(*, messages, tools, cancellation_token):
        cancellation_token.cancel("sigint")
        raise RuntimeError("503 overloaded")

    cm = _cm(chat)
    history = _history()
    agent, events = _agent(cm, history)
    with pytest.raises(AgentCancelledError):
        agent.compaction_coordinator.run(
            CompactionRequest("auto", "full", "compression_threshold"),
            system_prompt="sys",
            cancellation_token=token,
        )
    assert agent.messages is history
    kinds = {getattr(e.type, "value", e.type) for e in events}
    assert "compaction_settled" not in kinds
    assert "context_compressed" not in kinds


# -- the entry points pass the turn's token -------------------------------------


def _bare(tmp_path, monkeypatch):
    agent = make_bare_agent(tmp_path, transport=CapturingTransport())
    agent._plugin_hook_rules = []
    agent.messages = _history(3)
    agent._last_session_summary_id = None
    monkeypatch.setattr(agent, "_build_system_prompt", lambda: "")
    monkeypatch.setattr(agent, "_build_volatile_tail", lambda: "")
    seen: List[Any] = []

    def capture(msgs, *, is_auto=True, reason="", decide=None, cancellation_token=None):
        seen.append((reason, cancellation_token))
        raise AgentCancelledError("stop here")

    monkeypatch.setattr(agent.context_manager, "_run_compaction", capture)
    monkeypatch.setattr(
        type(agent.context_manager), "compaction_circuit_open", property(lambda _s: False),
    )
    return agent, seen


def test_the_threshold_entry_passes_the_turn_token(tmp_path, monkeypatch):
    agent, seen = _bare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent.context_manager, "needs_compression", lambda messages, tokens=None: True,
    )
    token = CancellationToken()
    with pytest.raises(AgentCancelledError):
        ChatLoopRunner(agent)._maybe_full_compress(
            [{"role": "system", "content": ""}] + agent.messages, "",
            cancellation_token=token,
        )
    assert seen == [("compression_threshold", token)]


def test_the_overflow_entry_passes_the_turn_token(tmp_path, monkeypatch):
    agent, seen = _bare(tmp_path, monkeypatch)

    def _always_overflow(*_a, **_k):
        raise RuntimeError("prompt is too long: 213462 tokens > 200000 maximum")

    monkeypatch.setattr(agent, "_llm_call", _always_overflow)
    token = CancellationToken()
    with pytest.raises(AgentCancelledError):
        ChatLoopRunner(agent)._call_llm_with_overflow_recovery(
            [{"role": "system", "content": ""}] + agent.messages, "", [], token,
        )
    assert seen == [("api_overflow", token)]


def test_the_turn_loop_hands_its_token_to_the_threshold_entry(tmp_path, monkeypatch):
    from tests.support.stop_precompact import make_runner_with_stub_llm

    runner, _transport, _agent_ = make_runner_with_stub_llm(tmp_path, monkeypatch, rules=[])
    seen: List[Any] = []

    def full(m, s, tokens=None, cancellation_token=None):
        seen.append(cancellation_token)
        return m, s

    monkeypatch.setattr(runner, "_maybe_full_compress", full)
    token = CancellationToken()
    runner.run("hi", max_iterations=3, token=token)
    assert seen and all(t is token for t in seen)


def test_manual_compact_passes_no_token(tmp_path, monkeypatch):
    from agentao.cli.commands.compact import handle_compact_command

    agent, seen = _bare(tmp_path, monkeypatch)
    cli = SimpleNamespace(agent=agent, _cached_ctx_pct=0.0)
    try:
        handle_compact_command(cli, "")
    except AgentCancelledError:
        pass
    assert seen and seen[0][1] is None
