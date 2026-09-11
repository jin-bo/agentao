"""``SessionStart(source="compact")`` after a successful full compaction.

Scope is agentao's own, and narrow: a **successful** **full** compaction fires
once, and nothing else fires at all. `microcompact` runs on most iterations
inside its band and `minimal_history` is the overflow ladder's last rung —
neither rebuilds the session, and treating them as if they did would re-inject
the same context repeatedly on the exact path where the request is already too
large. That is also why this does not subscribe to ``CONTEXT_COMPRESSED``,
which is not gated by kind.

Design: ``docs/design/session-lifecycle-source-vs-codex.md`` §6.3.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.compaction.coordinator import CompactionCoordinator, CompactionRequest
from agentao.compaction.types import CompactionOutcome
from agentao.context_manager import ContextManager

_CONTEXT_MARK = "[hook_additional_context] context: from-compact-hook"


def _make_cm(max_tokens=200_000):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    return ContextManager(llm, Mock(), max_tokens=max_tokens)


def _history(n=6):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"user {i} " + "x" * 200})
        out.append({"role": "assistant", "content": f"assistant {i} " + "y" * 200})
    return out


def _make_agent(cm, messages, *, rules=(object(),)):
    events = []
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        transport=SimpleNamespace(emit=events.append),
        llm=SimpleNamespace(logger=Mock()),
        _plugin_hook_rules=list(rules),
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
    agent._emit_context_compressed = lambda **kw: events.append(
        SimpleNamespace(type="context_compressed", data=kw)
    )
    agent.compaction_coordinator = CompactionCoordinator(agent)
    return agent, events


@pytest.fixture
def fired(monkeypatch):
    """Record every compaction-time ``SessionStart``, and inject as a hook would."""
    calls = []

    def _start(agent, session_id, *, source="startup"):
        agent.add_message("user", _CONTEXT_MARK)
        calls.append({"source": source, "session": session_id})
        return ["compact-notice"]

    monkeypatch.setattr(
        "agentao.plugins.hooks.lifecycle.fire_session_start", _start,
    )
    return calls


def _force(cm, *, status, kind, reason, messages):
    result = (
        messages + [{"role": "user", "content": "summarized"}]
        if status == "success" else messages
    )
    cm._run_compaction = lambda m, **kw: CompactionOutcome(
        status=status, trigger="auto", kind=kind, reason=reason,
        messages=result, pre_tokens=900,
        post_tokens=100 if status == "success" else None,
        detail=None if status == "success" else "because",
    )


def _run(agent, *, trigger, kind, reason):
    return agent.compaction_coordinator.run(
        CompactionRequest(trigger, kind, reason), system_prompt="sys",
    )


# ── the three full entry points each fire once ────────────────────────────


@pytest.mark.parametrize(
    "trigger,reason",
    [
        ("manual", "manual_cli"),
        ("auto", "compression_threshold"),
        ("auto", "api_overflow"),
    ],
)
def test_every_successful_full_compaction_fires_once(fired, trigger, reason):
    cm = _make_cm()
    msgs = _history()
    agent, _ = _make_agent(cm, msgs)
    _force(cm, status="success", kind="full", reason=reason, messages=msgs)

    _run(agent, trigger=trigger, kind="full", reason=reason)
    assert fired == [{"source": "compact", "session": "sess-1"}]


# ── everything else stays silent ──────────────────────────────────────────


@pytest.mark.parametrize("kind", ["microcompact", "minimal_history"])
def test_the_other_kinds_never_fire(fired, kind):
    cm = _make_cm()
    msgs = _history()
    agent, _ = _make_agent(cm, msgs)
    _force(cm, status="success", kind=kind, reason="microcompact_threshold",
           messages=msgs)

    _run(agent, trigger="auto", kind=kind, reason="microcompact_threshold")
    assert fired == []


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_a_full_compaction_that_did_not_succeed_never_fires(fired, status):
    cm = _make_cm()
    msgs = _history()
    agent, _ = _make_agent(cm, msgs)
    _force(cm, status=status, kind="full", reason="compression_threshold",
           messages=msgs)

    _run(agent, trigger="auto", kind="full", reason="compression_threshold")
    assert fired == []


def test_a_skipped_compaction_never_fires(fired):
    cm = _make_cm()
    agent, _ = _make_agent(cm, _history())
    cm._consecutive_compact_failures = cm.CIRCUIT_BREAKER_LIMIT

    run = _run(agent, trigger="auto", kind="full", reason="compression_threshold")
    assert run.outcome.status == "skipped"
    assert fired == []


def test_a_session_without_hook_rules_dispatches_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "agentao.plugins.hooks.lifecycle.fire_session_start",
        lambda *a, **k: calls.append(1) or [],
    )
    cm = _make_cm()
    msgs = _history()
    agent, _ = _make_agent(cm, msgs, rules=())
    _force(cm, status="success", kind="full", reason="manual_cli", messages=msgs)

    _run(agent, trigger="manual", kind="full", reason="manual_cli")
    assert calls == []


# ── the properties that make it useful ────────────────────────────────────


def test_the_next_model_request_carries_the_hook_context(fired):
    """The whole reason the dispatch sits where it does.

    ``messages_with_system`` is assembled *after* history is replaced, and it
    is what the caller sends next — the two API-overflow rungs retry with it
    immediately. Injecting any later would hand that retry a request the
    hook's context never reached.
    """
    cm = _make_cm()
    msgs = _history()
    agent, _ = _make_agent(cm, msgs)
    _force(cm, status="success", kind="full", reason="api_overflow", messages=msgs)

    run = _run(agent, trigger="auto", kind="full", reason="api_overflow")

    contents = [str(m.get("content", "")) for m in run.messages_with_system]
    assert any(_CONTEXT_MARK in c for c in contents)
    # And it is in the agent's own history, not only in the snapshot.
    assert any(_CONTEXT_MARK in str(m.get("content", "")) for m in agent.messages)


def test_the_session_id_does_not_change(fired):
    # A compaction is not a new session: same id, no ``SessionEnd``, no replay
    # restart, no memory archive. Only the plugin dispatch applies.
    cm = _make_cm()
    msgs = _history()
    agent, _ = _make_agent(cm, msgs)
    _force(cm, status="success", kind="full", reason="manual_cli", messages=msgs)

    _run(agent, trigger="manual", kind="full", reason="manual_cli")
    assert agent._session_id == "sess-1"
    assert fired[0]["session"] == "sess-1"


def test_a_failing_hook_does_not_undo_the_compaction(monkeypatch):
    """History is already rewritten when this runs, and two callers are the
    overflow ladder. A hook failure must not be able to end the turn the
    compaction exists to save."""
    def _boom(*a, **k):
        raise RuntimeError("hook exploded")

    monkeypatch.setattr("agentao.plugins.hooks.lifecycle.fire_session_start", _boom)
    cm = _make_cm()
    msgs = _history()
    agent, events = _make_agent(cm, msgs)
    _force(cm, status="success", kind="full", reason="api_overflow", messages=msgs)

    run = _run(agent, trigger="auto", kind="full", reason="api_overflow")

    assert run.outcome.status == "success"
    assert agent.messages[-1]["content"] == "summarized"
    assert run.messages_with_system[0] == {"role": "system", "content": "sys"}
    kinds = [getattr(getattr(e, "type", None), "value", getattr(e, "type", None))
             for e in events]
    assert "context_compressed" in kinds


def test_the_notices_ride_the_host_hook_channel(fired):
    cm = _make_cm()
    msgs = _history()
    agent, events = _make_agent(cm, msgs)
    _force(cm, status="success", kind="full", reason="manual_cli", messages=msgs)

    _run(agent, trigger="manual", kind="full", reason="manual_cli")

    emitted = [
        e for e in events
        if getattr(getattr(e, "type", None), "value", None) == "plugin_hook_fired"
        and e.data.get("hook_name") == "SessionStart"
    ]
    assert len(emitted) == 1
    assert emitted[0].data["source"] == "compact"
    assert emitted[0].data["user_notices"] == ["compact-notice"]


# ── v1 regression (design §5) ─────────────────────────────────────────────


def test_the_new_dispatch_site_runs_an_existing_v1_hook(tmp_path):
    """A new dispatch site changes execution counts for `agentao-v1` rules too.

    v1 rules on ``SessionStart`` never filter (the flat matcher reads only
    ``toolName``, which this event has none of), so an author cannot opt out of
    a site that did not exist when they wrote the rule. Asserting the count is
    the design's own requirement for adding one.
    """
    from agentao.plugins.hooks.lifecycle import fire_session_start
    from agentao.plugins.models import ParsedHookRule

    from ._hook_commands import as_kwargs, emitting

    log = tmp_path / "fired.log"
    rule = ParsedHookRule(
        event="SessionStart", hook_type="command",
        **as_kwargs(emitting(touch=(log,))),
        timeout=30, contract="agentao-v1", plugin_name="p",
    )
    agent = SimpleNamespace(
        _plugin_hook_rules=[rule],
        working_directory=tmp_path,
        messages=[],
    )
    agent.add_message = lambda role, content: agent.messages.append(
        {"role": role, "content": content}
    )

    fire_session_start(agent, "s", source="compact")
    assert log.read_text(encoding="utf-8").count("fired") == 1
