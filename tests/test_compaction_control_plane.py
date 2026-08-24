"""The PreCompact control plane: hooks and the host controller can say no.

``PreCompact`` was notify-only. `_dispatch_lifecycle` fired it and threw the
output away, so a host watching its own context being rewritten had no way to
stop it — which is why the 2026-05 plan deferred the gate entirely: "accepting
a host deny with no fallback for *host denied and still too long* produces
unrecoverable runaway."

The answer these tests pin: a cancel is **honoured and reported**, never
ignored and never quietly fallen through. A cancelled threshold compaction is
not re-dispatched for the rest of the turn (the latch); if the API then really
overflows, the host is asked **again**, as a separate question; and a cancelled
overflow returns the provider's context-length error rather than cutting
history to the last two messages.
"""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.compaction.coordinator import CompactionCoordinator, CompactionRequest
from agentao.compaction.types import CompactionDecision
from agentao.context_manager import ContextManager
from agentao.plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
from agentao.plugins.models import ParsedHookRule


def _make_cm():
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    return ContextManager(llm, Mock(), max_tokens=200_000)


def _history(n=12):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"user {i} " + "x" * 200})
        out.append({"role": "assistant", "content": f"assistant {i} " + "y" * 200})
    return out


def _agent(cm, messages, *, rules=None, controller=None, cwd=None):
    events = []
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        transport=SimpleNamespace(emit=events.append),
        llm=SimpleNamespace(logger=Mock()),
        working_directory=cwd,
        compaction_controller=controller,
        _plugin_hook_rules=rules or [],
        _session_id="s",
        _last_session_summary_id=None,
        _turn_finish_reason_missing=False,
        _build_system_prompt=lambda: "sys",
        _emit_session_summary_if_new=lambda _prev: None,
        _emit_context_compressed=lambda **kw: events.append(
            SimpleNamespace(type="context_compressed", data=kw)
        ),
        active_permissions=lambda: SimpleNamespace(mode="workspace-write"),
    )
    agent.compaction_coordinator = CompactionCoordinator(agent)
    return agent, events


def _hook(tmp_path, body, name="hook.sh"):
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR | stat.S_IWUSR)
    return ParsedHookRule(
        event="PreCompact", hook_type="command",
        command=f"sh '{script}'", plugin_name="t",
    )


def _emits(payload):
    return "cat <<'JSON'\n" + json.dumps(payload) + "\nJSON\nexit 0"


# ---------------------------------------------------------------------------
# The wire shape
# ---------------------------------------------------------------------------

def test_a_hook_cancels_with_the_dedicated_key(tmp_path):
    """``compactionDecision``, not ``permissionDecision``.

    The key choice is the whole argument for needing no opt-in gate: it has
    never existed in agentao, so no existing script can produce it by
    accident. "Silence means allow" only proves *silent* scripts are safe.
    """
    rule = _hook(tmp_path, _emits({
        "hookSpecificOutput": {
            "compactionDecision": "cancel",
            "compactionDecisionReason": "mid-refactor",
        }
    }))
    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        cwd=tmp_path, trigger="auto", compaction_type="full",
        reason="compression_threshold",
    )
    result = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_compact_decision(
        payload=payload, rules=[rule],
    )
    assert result.decision == "cancel"
    assert result.reason == "mid-refactor"


@pytest.mark.parametrize("body", [
    "exit 0",                                                  # prints nothing
    "echo 'not json'; exit 0",                                 # non-JSON stdout
    _emits({"hookSpecificOutput": {}}),                        # key absent
    _emits({"hookSpecificOutput": {"permissionDecision": "deny"}}),  # wrong key
    _emits({"hookSpecificOutput": {"compactionDecision": "CANCEL"}}),  # typo
    _emits({"hookSpecificOutput": {"compactionDecision": "deny"}}),    # unknown
    "echo '{\"hookSpecificOutput\": {\"compactionDecision\": \"cancel\"}}'; exit 2",
])
def test_everything_that_is_not_an_explicit_cancel_means_allow(tmp_path, body):
    """A typo must not be able to pause compaction until the context blows up.

    The last case is deliberate: exit code 2 stays unhonoured (matching the
    PreToolUse precedent) but the JSON on stdout is still read — so that one
    *does* cancel, on the strength of the JSON alone.
    """
    rule = _hook(tmp_path, body, name=f"h{abs(hash(body))}.sh")
    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        cwd=tmp_path, trigger="auto", compaction_type="full",
        reason="compression_threshold",
    )
    result = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_compact_decision(
        payload=payload, rules=[rule],
    )
    expected = "cancel" if body.endswith("exit 2") else None
    assert result.decision == expected


def test_first_cancel_wins_and_stops_the_remaining_forks(tmp_path):
    marker = tmp_path / "second_ran"
    first = _hook(tmp_path, _emits({
        "hookSpecificOutput": {"compactionDecision": "cancel", "reason": "no"}
    }), name="first.sh")
    second = _hook(tmp_path, f"touch '{marker}'; exit 0", name="second.sh")
    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        cwd=tmp_path, trigger="auto", compaction_type="full",
        reason="compression_threshold",
    )
    result = PluginHookDispatcher(cwd=tmp_path).dispatch_pre_compact_decision(
        payload=payload, rules=[first, second],
    )
    assert result.decision == "cancel"
    assert not marker.exists(), "forking stopped once a cancel was seen"


# ---------------------------------------------------------------------------
# End to end through the coordinator
# ---------------------------------------------------------------------------

def test_a_cancelled_compaction_leaves_history_byte_identical(tmp_path):
    cm = _make_cm()
    msgs = _history()
    before = [dict(m) for m in msgs]
    rule = _hook(tmp_path, _emits({
        "hookSpecificOutput": {"compactionDecision": "cancel", "reason": "busy"}
    }))
    agent, events = _agent(cm, msgs, rules=[rule], cwd=tmp_path)

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"),
        system_prompt="sys",
    )

    assert run.outcome.status == "cancelled"
    assert run.outcome.detail == "busy"
    assert agent.messages == before
    settled = [e for e in events if getattr(e.type, "value", e.type) == "compaction_settled"]
    assert len(settled) == 1 and settled[0].data["status"] == "cancelled"
    # A cancel is not a compaction: the old event stays silent.
    assert not [e for e in events if getattr(e, "type", None) == "context_compressed"]


def test_a_cancelled_threshold_is_not_re_dispatched_this_turn(tmp_path):
    """The latch — otherwise honouring a cancel means asking every iteration."""
    cm = _make_cm()
    calls = tmp_path / "calls"
    rule = _hook(tmp_path, (
        f"echo x >> '{calls}'\n"
        + _emits({"hookSpecificOutput": {"compactionDecision": "cancel"}})
    ))
    agent, events = _agent(cm, _history(), rules=[rule], cwd=tmp_path)
    coordinator = agent.compaction_coordinator

    first = coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )
    second = coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert first.outcome.status == "cancelled"
    assert second.outcome.status == "skipped"
    assert second.outcome.detail == "suppressed_by_latch"
    assert calls.read_text().count("x") == 1, "the hook was forked twice"
    # A latch hit is silent, exactly like the other skipped cases.
    assert len([e for e in events if getattr(e.type, "value", e.type) == "compaction_settled"]) == 1


def test_a_later_overflow_in_the_same_turn_is_still_asked(tmp_path):
    """The latch key carries ``reason``: overflow is a different question."""
    cm = _make_cm()
    rule = _hook(tmp_path, _emits({
        "hookSpecificOutput": {"compactionDecision": "cancel"}
    }))
    agent, _events = _agent(cm, _history(), rules=[rule], cwd=tmp_path)
    coordinator = agent.compaction_coordinator

    coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )
    overflow = coordinator.run(
        CompactionRequest("auto", "full", "api_overflow"), system_prompt="sys",
    )

    assert overflow.outcome.status == "cancelled", "overflow must be asked separately"


def test_manual_cancellation_never_latches(tmp_path):
    """``/compact`` runs outside a turn, so a turn-reset latch would strand it."""
    cm = _make_cm()
    rule = _hook(tmp_path, _emits({
        "hookSpecificOutput": {"compactionDecision": "cancel"}
    }))
    agent, _events = _agent(cm, _history(), rules=[rule], cwd=tmp_path)
    coordinator = agent.compaction_coordinator

    first = coordinator.run(
        CompactionRequest("manual", "full", "manual_cli"), system_prompt="sys",
    )
    second = coordinator.run(
        CompactionRequest("manual", "full", "manual_cli"), system_prompt="sys",
    )

    assert first.outcome.status == "cancelled"
    assert second.outcome.status == "cancelled", "an immediate retry must dispatch"


def test_the_latch_is_cleared_at_the_start_of_the_next_turn(tmp_path):
    cm = _make_cm()
    rule = _hook(tmp_path, _emits({
        "hookSpecificOutput": {"compactionDecision": "cancel"}
    }))
    agent, _events = _agent(cm, _history(), rules=[rule], cwd=tmp_path)
    coordinator = agent.compaction_coordinator

    coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )
    assert coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    ).outcome.detail == "suppressed_by_latch"

    coordinator.reset_cancellation_latch()

    assert coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    ).outcome.status == "cancelled"


@pytest.mark.parametrize("kind,reason", [
    ("microcompact", "microcompact_threshold"),
    ("minimal_history", "api_overflow_after_compression"),
])
def test_the_other_two_kinds_are_cancellable_too(tmp_path, kind, reason):
    cm = _make_cm()
    # An oversized tool result, so microcompaction has something to clip and
    # the "no targets" gate does not fire before the decision step.
    msgs = _history() + [
        {"role": "tool", "name": "read_file", "content": "z" * 50_000},
    ] + [{"role": "tool", "name": "read_file", "content": "s"} for _ in range(6)]
    before = [dict(m) for m in msgs]
    seen = []

    def _controller(ctx):
        seen.append(ctx)
        return CompactionDecision("cancel", reason="not now")

    agent, _events = _agent(cm, msgs, controller=_controller, cwd=tmp_path)
    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", kind, reason), system_prompt="sys",
    )

    assert run.outcome.status == "cancelled"
    assert agent.messages == before
    ctx = seen[0]
    assert ctx.can_provide_summary is False
    assert ctx.pre_tokens is None
    if kind == "microcompact":
        assert ctx.tool_results_to_clip is not None
    else:
        assert ctx.messages_to_keep == 2


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

def test_a_controller_can_provide_the_summary(tmp_path):
    cm = _make_cm()
    called = []
    cm._summarize_formatted = lambda _f: called.append(1) or "built-in"

    agent, _events = _agent(
        cm, _history(),
        controller=lambda ctx: CompactionDecision(
            "provide_summary", summary="the host's own summary",
        ),
        cwd=tmp_path,
    )
    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert run.outcome.status == "success"
    assert not called, "the built-in summarizer must be skipped"
    assert any("the host's own summary" in str(m.get("content", "")) for m in agent.messages)


@pytest.mark.parametrize("summary,check", [
    ("", "empty"),
    ("   ", "empty"),
    (12345, "not_a_string"),
    ("x " + ContextManager.SUMMARY_END_MARKER, "contains_end_marker"),
])
def test_an_invalid_host_summary_degrades_to_the_built_in_one(tmp_path, summary, check):
    """Reject, log, and run the built-in summarizer once — not "failed and
    also continuing". A bad controller costs one extra summarization and can
    never disable auto-compaction in three calls."""
    cm = _make_cm()
    cm._summarize_formatted = lambda _f: "the built-in summary"
    agent, _events = _agent(
        cm, _history(),
        controller=lambda ctx: CompactionDecision("provide_summary", summary=summary),
        cwd=tmp_path,
    )

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert run.outcome.status == "success"
    assert run.outcome.detail == f"host_summary_rejected:{check}"
    assert cm.circuit_breaker_failures == 0, "an invalid host summary never counts"


def test_an_invalid_host_summary_and_a_failing_built_in_is_one_failure(tmp_path):
    cm = _make_cm()
    cm._summarize_formatted = lambda _f: ""
    agent, _events = _agent(
        cm, _history(),
        controller=lambda ctx: CompactionDecision("provide_summary", summary=""),
        cwd=tmp_path,
    )

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert run.outcome.status == "failed"
    assert run.outcome.detail == "host_summary_rejected:empty+summary_empty"
    # What the breaker counts is always the built-in summarizer's failure.
    assert cm.circuit_breaker_failures == 1


@pytest.mark.parametrize("bad", [
    lambda ctx: (_ for _ in ()).throw(AttributeError("boom")),
    lambda ctx: None,
    lambda ctx: "allow",
    lambda ctx: CompactionDecision("maybe"),
    lambda ctx: CompactionDecision("provide_summary", summary=None),
])
def test_a_broken_controller_is_treated_as_allow(tmp_path, bad):
    """Fail-open is a hard rule, and the reason is the overflow path.

    Two of the five entry points *are* the recovery ladder, so an
    ``AttributeError`` escaping a controller would turn "context too long"
    into "the turn crashes" — killing the path this design exists to protect.
    """
    cm = _make_cm()
    cm._summarize_formatted = lambda _f: "a summary"
    agent, _events = _agent(cm, _history(), controller=bad, cwd=tmp_path)

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert run.outcome.status == "success"


def test_an_async_controller_is_refused_rather_than_awaited(tmp_path):
    async def _async_controller(ctx):  # pragma: no cover - never awaited
        return CompactionDecision("cancel")

    cm = _make_cm()
    cm._summarize_formatted = lambda _f: "a summary"
    agent, _events = _agent(cm, _history(), controller=_async_controller, cwd=tmp_path)

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert run.outcome.status == "success", "an awaitable is an unknown return, not a cancel"


def test_a_hook_cancel_short_circuits_the_controller(tmp_path):
    """Asking a trusted host for a summary that is about to be thrown away
    is pure waste."""
    cm = _make_cm()
    consulted = []
    rule = _hook(tmp_path, _emits({
        "hookSpecificOutput": {"compactionDecision": "cancel", "reason": "hook says no"}
    }))
    agent, _events = _agent(
        cm, _history(), rules=[rule],
        controller=lambda ctx: consulted.append(ctx) or CompactionDecision("allow"),
        cwd=tmp_path,
    )

    run = agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    assert run.outcome.status == "cancelled"
    assert run.outcome.detail == "hook says no"
    assert consulted == []


def test_the_controller_context_carries_counts_not_text(tmp_path):
    cm = _make_cm()
    cm._summarize_formatted = lambda _f: "a summary"
    seen = []
    agent, _events = _agent(
        cm, _history(),
        controller=lambda ctx: seen.append(ctx) or CompactionDecision("allow"),
        cwd=tmp_path,
    )

    agent.compaction_coordinator.run(
        CompactionRequest("auto", "full", "compression_threshold"), system_prompt="sys",
    )

    ctx = seen[0]
    assert ctx.can_provide_summary is True
    assert ctx.messages_to_summarize > 0 and ctx.messages_to_keep > 0
    assert ctx.pre_tokens is not None
    assert ctx.max_summary_tokens == ctx.summary_input_budget // 2
    # No message text anywhere on it.
    assert not any(
        isinstance(v, (list, dict)) and v and not isinstance(v, tuple)
        for v in vars(ctx).values()
    )
