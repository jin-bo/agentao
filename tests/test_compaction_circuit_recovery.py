"""The compaction circuit breaker is a recoverable state machine.

It used to be a one-way latch. Three consecutive failures set a counter, the
counter's only reset was a successful compaction, and the short-circuit sat
*above* every branch — including the manual one — so the single action a user
could take to recover was itself blocked. ``/clear`` did not help either: it
clears messages, skills, todos, the token anchor and the token counters, but
never the failure count.

What replaces it:

* three failures pause the **threshold** tier only;
* manual ``/compact`` and an API overflow are allowed through as half-open
  probes — neither is what the breaker describes;
* a successful probe closes it, a failed one leaves it exactly as it was;
* ``/clear`` closes it, because the count measures a conversation that no
  longer exists;
* and a failed summarization on the manual path stops counting at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.compaction.coordinator import CompactionCoordinator, CompactionRequest
from agentao.context_manager import ContextManager


def _make_cm(max_tokens=200_000):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    return ContextManager(llm, Mock(), max_tokens=max_tokens)


def _history(n=12):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"user {i} " + "x" * 200})
        out.append({"role": "assistant", "content": f"assistant {i} " + "y" * 200})
    return out


def _agent(cm, messages):
    events = []
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        transport=SimpleNamespace(emit=events.append),
        llm=SimpleNamespace(logger=Mock()),
        _plugin_hook_rules=[],
        _last_session_summary_id=None,
        _turn_finish_reason_missing=False,
        _build_system_prompt=lambda: "sys",
        _emit_session_summary_if_new=lambda _prev: None,
        _emit_context_compressed=lambda **kw: events.append(
            SimpleNamespace(type="context_compressed", data=kw)
        ),
    )
    agent.compaction_coordinator = CompactionCoordinator(agent)
    return agent, events


def _trip(cm):
    """Three consecutive failed summarizations on the automatic path."""
    cm._summarize_formatted = lambda _f: ""
    for _ in range(cm.CIRCUIT_BREAKER_LIMIT):
        cm._run_compaction(_history(), is_auto=True, reason="compression_threshold")
    assert cm.compaction_circuit_open is True


# ---------------------------------------------------------------------------
# Who is paused and who probes
# ---------------------------------------------------------------------------

def test_threshold_attempts_pause_once_the_breaker_is_open():
    cm = _make_cm()
    _trip(cm)
    agent, events = _agent(cm, _history())

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )

    assert run.outcome.status == "skipped"
    assert run.outcome.detail == "circuit_open"
    assert events == []


@pytest.mark.parametrize(
    "trigger,reason",
    [("manual", "manual_cli"), ("auto", "api_overflow")],
)
def test_manual_and_overflow_are_allowed_through_as_probes(trigger, reason):
    cm = _make_cm()
    _trip(cm)
    cm._summarize_formatted = lambda _f: "a fresh summary"
    agent, events = _agent(cm, _history())

    run = agent.compaction_coordinator.run(
        CompactionRequest(trigger, "full", reason),
        system_prompt="sys",
    )

    assert run.outcome.status == "success", run.outcome.detail
    # A successful probe closes the breaker immediately.
    assert cm.compaction_circuit_open is False
    assert cm.circuit_breaker_failures == 0
    assert cm.last_compaction_failure is None


def test_a_failed_probe_leaves_the_breaker_exactly_as_it_was():
    cm = _make_cm()
    _trip(cm)
    before = cm.circuit_breaker_failures
    agent, _events = _agent(cm, _history())

    run = agent.compaction_coordinator.run(
        CompactionRequest("manual", "full", "manual_cli"),
        system_prompt="sys",
    )

    assert run.outcome.status == "failed"
    assert cm.compaction_circuit_open is True
    # Not incremented either: the manual path does not count failures.
    assert cm.circuit_breaker_failures == before


def test_threshold_resumes_after_a_probe_succeeds():
    """The whole point — the pause has to be exitable."""
    cm = _make_cm()
    _trip(cm)
    agent, _events = _agent(cm, _history())

    paused = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )
    assert paused.outcome.status == "skipped"

    cm._summarize_formatted = lambda _f: "a fresh summary"
    probe = agent.compaction_coordinator.run(
        CompactionRequest("manual", "full", "manual_cli"), system_prompt="sys",
    )
    assert probe.outcome.status == "success"

    agent.messages = _history()
    resumed = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )
    assert resumed.outcome.status == "success"


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

def test_a_manual_summarization_failure_no_longer_counts():
    """The deliberate behaviour change: the ``:590`` increment was
    unconditional, so three manual retries disabled automatic compaction."""
    cm = _make_cm()
    cm._summarize_formatted = lambda _f: ""

    for _ in range(cm.CIRCUIT_BREAKER_LIMIT + 2):
        out = cm._run_compaction(_history(), is_auto=False, reason="manual_cli")
        assert out.status == "failed"

    assert cm.circuit_breaker_failures == 0
    assert cm.compaction_circuit_open is False


def test_an_automatic_summarization_failure_still_counts_and_is_classified():
    cm = _make_cm()
    cm._summarize_formatted = lambda _f: ""

    cm._run_compaction(_history(), is_auto=True, reason="compression_threshold")

    assert cm.circuit_breaker_failures == 1
    assert cm.last_compaction_failure == "summary_empty"


def test_a_structural_failure_is_classified_separately():
    cm = _make_cm()
    msgs = [{"role": "user", "content": "go"}] + [
        {"role": "tool", "name": "read_file", "content": f"r{i}"} for i in range(10)
    ]

    cm._run_compaction(msgs, is_auto=True, reason="compression_threshold")

    assert cm.circuit_breaker_failures == 1
    assert cm.last_compaction_failure == "no_safe_split"


# ---------------------------------------------------------------------------
# Reset paths
# ---------------------------------------------------------------------------

def test_reset_closes_the_breaker_and_forgets_the_class():
    cm = _make_cm()
    _trip(cm)
    assert cm.last_compaction_failure == "summary_empty"

    cm.reset_compaction_circuit()

    assert cm.compaction_circuit_open is False
    assert cm.circuit_breaker_failures == 0
    assert cm.last_compaction_failure is None


def test_clear_history_closes_the_breaker(tmp_path):
    """``/clear`` reaches it through ``clear_history``.

    The counter measures *this* conversation's failures, so replacing the
    conversation invalidates the evidence behind it. Before, a session that
    tripped the breaker stayed unable to auto-compact across ``/clear``.
    """
    from tests.support.stop_precompact import make_bare_agent

    agent = make_bare_agent(tmp_path)
    cm = agent.context_manager
    _trip(cm)

    agent.clear_history()

    assert cm.compaction_circuit_open is False
    assert cm.last_compaction_failure is None


def test_usage_stats_exports_the_state_and_the_class():
    cm = _make_cm()
    _trip(cm)

    stats = cm.get_usage_stats(_history())

    assert stats["circuit_breaker_failures"] == cm.CIRCUIT_BREAKER_LIMIT
    assert stats["circuit_breaker_open"] is True
    assert stats["last_compaction_failure"] == "summary_empty"


# ---------------------------------------------------------------------------
# The public entry
# ---------------------------------------------------------------------------

def test_agent_compact_returns_an_outcome(tmp_path):
    from tests.support.stop_precompact import make_bare_agent

    agent = make_bare_agent(tmp_path)
    agent.messages = _history()
    agent.context_manager._summarize_formatted = lambda _f: "a summary"

    outcome = agent.compact()

    assert outcome.status == "success"
    assert outcome.trigger == "manual"
    assert outcome.kind == "full"
    assert outcome.reason == "manual_cli"
    assert agent.messages == outcome.messages


def test_agent_compact_reports_a_paused_breaker_rather_than_raising(tmp_path):
    from tests.support.stop_precompact import make_bare_agent

    agent = make_bare_agent(tmp_path)
    agent.messages = _history()
    _trip(agent.context_manager)

    # The threshold reason is the paused one; the default manual_cli probes.
    outcome = agent.compact(reason="compression_threshold")

    assert outcome.status == "skipped"
    assert outcome.detail == "circuit_open"
    assert outcome.messages is agent.messages


# ---------------------------------------------------------------------------
# A history that cannot be rendered degrades — it does not escape
# ---------------------------------------------------------------------------

def _unrenderable():
    """A history whose transcript assembly raises.

    ``_format_for_summary`` does ``role.upper()``; a ``None`` role is what a
    host that appended to ``agent.messages`` by hand can leave behind. It has
    to sit in the *summarized* half — the kept tail is spliced in verbatim and
    never rendered — so put it near the front.
    """
    msgs = _history(6)
    msgs[1] = {"role": None, "content": "cannot render"}
    return msgs


def test_an_unrenderable_history_fails_gracefully_instead_of_raising():
    """Transcript assembly used to run inside the summarization ``try``.

    Splitting prepare out of the summarization call moved it outside that
    guard, and ``prepare_compaction`` is on the API-overflow recovery ladder:
    an exception escaping there ends the very turn the ladder exists to save.
    """
    cm = _make_cm()

    outcome = cm._run_compaction(
        _unrenderable(), is_auto=True, reason="api_overflow",
    )

    assert outcome.status == "failed"
    assert outcome.detail == "summary_input_error"
    # History is handed back untouched, exactly as the other structural
    # rejection does.
    assert outcome.messages == _unrenderable()


def test_an_unrenderable_history_still_arrests_the_breaker():
    """Degrading must not mean looping: it is a *counted* failure.

    Every iteration re-enters on the same unrenderable history, so silently
    returning ``skipped`` here would re-run the whole prepare step forever.
    """
    cm = _make_cm()

    for _ in range(cm.CIRCUIT_BREAKER_LIMIT):
        cm._run_compaction(_unrenderable(), is_auto=True, reason="compression_threshold")

    assert cm.compaction_circuit_open is True
    assert cm.last_compaction_failure == "summary_input_error"


def test_the_manual_path_reports_it_without_counting_it():
    cm = _make_cm()

    outcome = cm._run_compaction(
        _unrenderable(), is_auto=False, reason="manual_cli",
    )

    assert outcome.status == "failed"
    assert outcome.detail == "summary_input_error"
    assert cm.circuit_breaker_failures == 0
