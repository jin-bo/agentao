"""The coordinator's contract: one outcome per attempt, honest events.

Five entry points used to orchestrate compaction independently, and they
disagreed about the two things that matter: whether a failure counts, and
whether "compacted" means history actually changed. These tests pin the
contract that replaced them —

* every attempt produces one ``CompactionOutcome`` with a ``status``;
* ``COMPACTION_SETTLED`` fires for ``success | cancelled | failed`` and
  **never** for ``skipped``, because three of the four skipped cases
  re-trigger on every loop iteration;
* ``CONTEXT_COMPRESSED`` fires only on ``success`` — and its payload does not
  change by a single key.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.compaction.coordinator import CompactionCoordinator, CompactionRequest
from agentao.compaction.types import CompactionOutcome
from agentao.context_manager import ContextManager, PrepareRejected


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _make_cm(max_tokens=200_000, memory_manager=None):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    return ContextManager(
        llm, Mock(), max_tokens=max_tokens, memory_manager=memory_manager,
    )


def _make_agent(cm, messages):
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
        _emit_session_summary_if_new=lambda _prev: "summary-id",
    )

    def _emit_context_compressed(**kw):
        events.append(SimpleNamespace(type="context_compressed", data=kw))

    agent._emit_context_compressed = _emit_context_compressed
    agent.compaction_coordinator = CompactionCoordinator(agent)
    return agent, events


def _kinds(events):
    return [getattr(getattr(e, "type", None), "value", getattr(e, "type", None)) for e in events]


def _settled(events):
    return [e for e in events if _kinds([e])[0] == "compaction_settled"]


def _history(n=12):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"user {i} " + "x" * 200})
        out.append({"role": "assistant", "content": f"assistant {i} " + "y" * 200})
    return out


# ---------------------------------------------------------------------------
# status -> events
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["success", "cancelled", "failed"])
def test_every_non_skipped_status_emits_exactly_one_settled_event(status):
    cm = _make_cm()
    msgs = _history()
    agent, events = _make_agent(cm, msgs)
    result = msgs + [{"role": "user", "content": "new"}] if status == "success" else msgs
    cm._run_compaction = lambda m, **kw: CompactionOutcome(
        status=status,
        trigger="auto",
        kind="full",
        reason="compression_threshold",
        messages=result,
        pre_tokens=900,
        post_tokens=100 if status == "success" else None,
        detail=None if status == "success" else "because",
    )

    agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )

    settled = _settled(events)
    assert len(settled) == 1
    payload = settled[0].data
    assert payload["status"] == status
    assert payload["kind"] == "full"
    assert payload["trigger"] == "auto"
    # CONTEXT_COMPRESSED follows only success.
    assert ("context_compressed" in _kinds(events)) is (status == "success")


def test_skipped_emits_nothing_at_all():
    """All **four** skipped cases stay silent — each re-triggers every iteration.

    Three are decided by ``_gate`` and return before ``_emit`` is reached;
    the fourth, ``history_too_short``, is decided inside the transform and so
    arrives at ``_emit`` and has to be filtered there explicitly. Covering
    only the gated ones is how it went out emitting a ``COMPACTION_SETTLED``
    per loop iteration on the one status documented to stay silent.
    """
    cm = _make_cm()

    # (a) breaker open, kind == full
    agent, events = _make_agent(cm, _history())
    cm._consecutive_compact_failures = cm.CIRCUIT_BREAKER_LIMIT
    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )
    assert run.outcome.status == "skipped"
    assert run.outcome.detail == "circuit_open"
    assert events == []

    # (b) microcompaction with nothing left to clip
    cm2 = _make_cm()
    agent2, events2 = _make_agent(cm2, [{"role": "user", "content": "short"}])
    run2 = agent2.compaction_coordinator.run(
        CompactionRequest("auto", "microcompact", "microcompact_threshold"),
        system_prompt="sys",
    )
    assert run2.outcome.status == "skipped"
    assert run2.outcome.detail == "no_microcompact_targets"
    assert events2 == []

    # (c) suppressed by the cancellation latch
    cm3 = _make_cm()
    agent3, events3 = _make_agent(cm3, _history())
    agent3.compaction_coordinator._cancel_latch.add(("full", "compression_threshold"))
    run3 = agent3.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )
    assert run3.outcome.status == "skipped"
    assert run3.outcome.detail == "suppressed_by_latch"
    assert events3 == []

    # (d) too little history to summarize — decided *inside* the transform,
    # so this is the one that reaches ``_emit``. Four large messages over the
    # threshold is enough to reach it for real.
    cm4 = _make_cm()
    agent4, events4 = _make_agent(cm4, _history(2))
    run4 = agent4.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )
    assert run4.outcome.status == "skipped"
    assert run4.outcome.detail == "history_too_short"
    assert events4 == []


def test_success_is_not_inferred_from_message_count():
    """A successful microcompaction leaves the count identical.

    Gating the event on ``pre_msgs != post_msgs`` would suppress every one of
    them: ``microcompact_messages`` builds a fresh list element by element and
    only shortens ``content``.
    """
    cm = _make_cm()
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "tool", "name": "read_file", "content": "z" * 50_000},
        {"role": "assistant", "content": "ok"},
    ] + [{"role": "tool", "name": "read_file", "content": "small"} for _ in range(6)]
    agent, events = _make_agent(cm, msgs)

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "microcompact", "microcompact_threshold"),
        system_prompt="sys",
        measure_system_tokens=True,
    )

    assert run.outcome.status == "success"
    settled = _settled(events)[0].data
    assert settled["pre_msgs"] == settled["post_msgs"] == len(msgs)
    assert "context_compressed" in _kinds(events)


def test_context_compressed_payload_keys_and_unit_are_unchanged():
    """The old event's seven keys and both token units stay put.

    Its tokens **include** the system prompt; the new event's
    ``*_tokens_history`` pair excludes it. Wiring the outcome's history-only
    numbers into the old field would change a public field's meaning without
    a schema bump, which is the one thing this split must not do.
    """
    cm = _make_cm()
    msgs = _history()
    agent, events = _make_agent(cm, msgs)
    compacted = [{"role": "system", "content": "[Compact Boundary]"}] + msgs[-2:]
    cm._run_compaction = lambda m, **kw: CompactionOutcome(
        status="success", trigger="auto", kind="full",
        reason="compression_threshold", messages=compacted,
        pre_tokens=111, post_tokens=22,
    )

    agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
        measure_system_tokens=True,
    )

    old = [e for e in events if _kinds([e])[0] == "context_compressed"][0].data
    assert set(old) == {
        "compression_type", "reason", "pre_msgs", "post_msgs",
        "pre_tokens", "post_tokens", "duration_ms",
    }
    # System-inclusive: measured over ``[system] + messages``, so strictly
    # larger than the outcome's history-only 111 / 22.
    assert old["pre_tokens"] > 111
    assert old["post_tokens"] != 22
    new = _settled(events)[0].data
    assert new["pre_tokens_history"] == 111
    assert new["post_tokens_history"] == 22


def test_overflow_rungs_carry_no_tokens_on_the_old_event():
    """Entries 3 and 4 stay ``null`` — unchanged from before the split.

    Filling them in means two new full-history estimates on precisely the
    path where the context has already blown up and the request has just
    been rejected.
    """
    cm = _make_cm()
    msgs = _history()
    agent, events = _make_agent(cm, msgs)
    cm._run_compaction = lambda m, **kw: CompactionOutcome(
        status="success", trigger="auto", kind="full", reason="api_overflow",
        messages=msgs[-2:], pre_tokens=900, post_tokens=10,
    )

    agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "api_overflow"),
        system_prompt="sys",
        measure_system_tokens=False,
    )

    old = [e for e in events if _kinds([e])[0] == "context_compressed"][0].data
    assert old["pre_tokens"] is None
    assert old["post_tokens"] is None


def test_minimal_history_goes_through_context_manager():
    cm = _make_cm()
    msgs = _history(4)
    agent, events = _make_agent(cm, msgs)

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "minimal_history", "api_overflow_after_compression"),
        system_prompt="sys",
    )

    assert run.outcome.status == "success"
    assert agent.messages == msgs[-2:]
    assert run.outcome.pre_tokens is None and run.outcome.post_tokens is None
    assert _settled(events)[0].data["kind"] == "minimal_history"


def test_apply_minimal_history_is_a_named_seam():
    cm = _make_cm()
    msgs = _history(4)
    assert cm.apply_minimal_history(msgs) == msgs[-2:]
    assert cm.apply_minimal_history(msgs, keep_tail=5) == msgs[-5:]
    # Sliced from the front, not as ``messages[-keep_tail:]``: at zero the
    # negative form is ``messages[-0:]`` — the **whole list** — so the
    # ladder's most destructive rung would silently keep everything, report
    # ``success``, and leave the retry to fail on the same overflow.
    assert cm.apply_minimal_history(msgs, keep_tail=0) == []
    # And asking for more than there is keeps what there is.
    assert cm.apply_minimal_history(msgs, keep_tail=len(msgs) + 10) == msgs


# ---------------------------------------------------------------------------
# prepare / commit
# ---------------------------------------------------------------------------

def test_prepare_rejects_short_history_without_counting_it():
    cm = _make_cm()
    prep = cm.prepare_compaction(
        [{"role": "user", "content": "a"}] * 3,
        trigger="auto", kind="full", reason="compression_threshold",
    )
    assert isinstance(prep, PrepareRejected)
    assert prep.status == "skipped"
    assert prep.detail == "history_too_short"
    assert prep.counts_as_failure is False


def test_prepare_writes_nothing_to_the_memory_store():
    """A cancelled compaction must leave no irreversible trace.

    ``crystallize_user_messages`` used to run *before* summarization, so a
    summarization that then returned nothing had already written to the
    store. It belongs to commit now, and this is the assertion that says so.
    """
    mem = Mock()
    cm = _make_cm(memory_manager=mem)
    prep = cm.prepare_compaction(
        _history(), trigger="auto", kind="full", reason="compression_threshold",
    )
    assert not isinstance(prep, PrepareRejected)
    mem.crystallize_user_messages.assert_not_called()
    mem.save_session_summary.assert_not_called()


def test_a_failed_summarization_no_longer_crystallizes():
    """The deliberate behaviour change this split carries.

    Before, crystallize ran at the old line 582 and summarization at 588, so
    a failed summarization had still written to the store.
    """
    mem = Mock()
    cm = _make_cm(memory_manager=mem)
    cm._summarize_formatted = lambda _formatted: ""

    out = cm._run_compaction(
        _history(), is_auto=True, reason="compression_threshold",
    )

    assert out.status == "failed"
    assert out.detail == "summary_empty"
    mem.crystallize_user_messages.assert_not_called()
    mem.save_session_summary.assert_not_called()


def test_a_successful_summarization_still_crystallizes_the_raw_window():
    mem = Mock()
    cm = _make_cm(memory_manager=mem)
    cm._summarize_formatted = lambda _formatted: "a summary"

    out = cm._run_compaction(
        _history(), is_auto=True, reason="compression_threshold",
    )

    assert out.status == "success"
    mem.crystallize_user_messages.assert_called_once()
    mem.save_session_summary.assert_called_once()


def test_an_empty_summary_still_increments_the_failure_counter():
    """All three counting points live in ``_run_compaction``.

    Putting them in commit would silently lose this one: commit never runs
    when summarization returns nothing.
    """
    cm = _make_cm()
    cm._summarize_formatted = lambda _formatted: ""
    assert cm.circuit_breaker_failures == 0

    for expected in (1, 2, 3):
        cm._run_compaction(_history(), is_auto=True, reason="compression_threshold")
        assert cm.circuit_breaker_failures == expected

    assert cm.compaction_circuit_open is True


def test_pins_appear_once_in_the_result_and_never_in_the_summary_input():
    cm = _make_cm()
    msgs = _history()
    msgs.insert(1, {"role": "user", "content": "[PIN] remember the ticket id"})
    prep = cm.prepare_compaction(
        msgs, trigger="auto", kind="full", reason="compression_threshold",
    )
    assert not isinstance(prep, PrepareRejected)
    assert "[PIN] remember the ticket id" not in prep.summary_input
    assert prep.pinned == [{"role": "user", "content": "[PIN] remember the ticket id"}]

    result = cm.commit_compaction(prep, "a summary")
    pins = [m for m in result if str(m.get("content", "")).startswith("[PIN]")]
    assert len(pins) == 1


# ---------------------------------------------------------------------------
# The legacy wrapper
# ---------------------------------------------------------------------------

def test_legacy_wrapper_keeps_its_gate_and_its_return_shape():
    cm = _make_cm()
    cm._consecutive_compact_failures = cm.CIRCUIT_BREAKER_LIMIT
    msgs = _history()
    assert cm.compress_messages(msgs) is msgs


def test_legacy_wrapper_derives_a_reason_from_is_auto():
    """It has no ``reason`` parameter — its signature is pinned — so the two
    values it can mean are mapped one to one."""
    cm = _make_cm()
    seen = []
    cm._run_compaction = lambda m, *, is_auto, reason, decide=None: (
        seen.append((is_auto, reason))
        or CompactionOutcome(
            status="failed", trigger="auto" if is_auto else "manual",
            kind="full", reason=reason, messages=m,
        )
    )
    msgs = _history()
    cm.compress_messages(msgs, is_auto=True)
    cm.compress_messages(msgs, is_auto=False)
    assert seen == [(True, "compression_threshold"), (False, "manual_cli")]


def test_manual_path_does_not_count_a_structural_failure():
    """Unchanged from before the split: the ``no_safe_split`` exemption.

    Manual ``/compact`` is user-driven and does not loop, so there is no
    runaway to arrest — and the breaker it would trip disables *automatic*
    compaction for the rest of the session.
    """
    cm = _make_cm()
    # Every candidate boundary is a tool message, so no safe split exists.
    msgs = [{"role": "user", "content": "go"}] + [
        {"role": "tool", "name": "read_file", "content": f"r{i}"} for i in range(10)
    ]
    out = cm._run_compaction(msgs, is_auto=False, reason="manual_cli")
    assert out.status == "failed"
    assert out.detail == "no_safe_split"
    assert cm.circuit_breaker_failures == 0

    out = cm._run_compaction(msgs, is_auto=True, reason="compression_threshold")
    assert out.status == "failed"
    assert cm.circuit_breaker_failures == 1
