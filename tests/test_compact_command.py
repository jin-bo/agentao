"""Tests for the manual /compact slash command.

``/compact`` no longer decides anything itself. It asks the coordinator to
run a ``manual`` / ``full`` / ``manual_cli`` compaction and reports the
``CompactionOutcome`` it gets back, so these tests drive the **real**
coordinator over a mocked ``ContextManager`` — the seam under test is
``_run_compaction``'s status, not a sniffed message marker.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from agentao.cli.commands import handle_compact_command
from agentao.compaction.coordinator import CompactionCoordinator
from agentao.compaction.types import CompactionOutcome


def _outcome(status, messages, **kw):
    return CompactionOutcome(
        status=status,
        trigger="manual",
        kind="full",
        reason="manual_cli",
        messages=messages,
        **kw,
    )


def _cli_with_messages(messages: list[dict], outcome: CompactionOutcome | None = None):
    cm = Mock()
    cm.CIRCUIT_BREAKER_LIMIT = 3
    cm.compaction_circuit_open = False
    cm.last_summary_finish_reason_missing = False
    cm.last_microcompact_mutated = True
    cm.estimate_tokens.side_effect = [1000, 250, 250]
    cm._run_compaction.return_value = (
        outcome if outcome is not None else _outcome("failed", messages)
    )
    cm.get_usage_stats.return_value = {
        "usage_percent": 12.5,
        "circuit_breaker_failures": 0,
    }
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        transport=SimpleNamespace(emit=Mock()),
        llm=SimpleNamespace(logger=Mock()),
        _plugin_hook_rules=[],
        _last_session_summary_id=None,
        _build_system_prompt=Mock(return_value="system"),
        _emit_context_compressed=Mock(),
        _emit_session_summary_if_new=Mock(return_value="summary-id"),
    )
    agent.compaction_coordinator = CompactionCoordinator(agent)
    cli = SimpleNamespace(agent=agent, _cached_ctx_pct=0.0)
    return cli, agent, cm


def test_compact_command_updates_history_and_emits_event():
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    compacted = [
        {"role": "system", "content": "[Compact Boundary | auto=False]"},
        {"role": "system", "content": "[Conversation Summary]\nsummary"},
        {"role": "user", "content": "recent"},
    ]
    cli, agent, cm = _cli_with_messages(
        messages, _outcome("success", compacted, pre_tokens=900, post_tokens=200),
    )

    handle_compact_command(cli, "")

    assert agent.messages == compacted
    cm._run_compaction.assert_called_once()
    call = cm._run_compaction.call_args
    assert call.args[0] == messages
    assert call.kwargs["is_auto"] is False
    assert call.kwargs["reason"] == "manual_cli"
    agent._emit_context_compressed.assert_called_once()
    kwargs = agent._emit_context_compressed.call_args.kwargs
    assert kwargs["compression_type"] == "full"
    assert kwargs["reason"] == "manual_cli"
    assert kwargs["pre_msgs"] == 10
    assert kwargs["post_msgs"] == 3
    assert cli._cached_ctx_pct == 12.5


def test_compact_command_keeps_history_when_the_outcome_is_not_success():
    """A non-success outcome must leave history alone and stay silent.

    The old code inferred this by looking for a freshly prepended
    ``[Compact Boundary]`` marker on ``messages[0]``, which is why a
    microcompacted copy — a *new* list with no summary in it — had to be
    special-cased. There is a ``status`` now.
    """
    for status in ("failed", "skipped", "cancelled"):
        messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        cli, agent, cm = _cli_with_messages(messages, _outcome(status, messages))

        handle_compact_command(cli, "")

        assert agent.messages == messages, status
        agent._emit_context_compressed.assert_not_called()


def test_compact_command_skips_short_history():
    messages = [{"role": "user", "content": "short"} for _ in range(4)]
    cli, agent, cm = _cli_with_messages(messages)

    handle_compact_command(cli, "")

    cm._run_compaction.assert_not_called()
    agent._emit_context_compressed.assert_not_called()


def test_compact_runs_as_a_probe_through_an_open_breaker(monkeypatch):
    """An open breaker must not block the one action that can close it.

    The breaker exists to stop the *threshold* tier re-entering every
    iteration. Manual ``/compact`` is user-driven and does not loop, so
    blocking it left the user with no way back: the only other reset is a
    successful compaction, and the open breaker is what prevents one.
    """
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    compacted = [{"role": "system", "content": "[Compact Boundary]"}] + messages[-2:]
    cli, agent, cm = _cli_with_messages(
        messages, _outcome("success", compacted, pre_tokens=900, post_tokens=100),
    )
    cm.compaction_circuit_open = True
    cm.circuit_breaker_failures = 3

    handle_compact_command(cli, "")

    cm._run_compaction.assert_called_once()
    assert agent.messages == compacted


def test_a_failed_probe_says_the_breaker_is_still_open(monkeypatch):
    """Otherwise "no change" hides that automatic compaction stays paused."""
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    cli, agent, cm = _cli_with_messages(messages, _outcome("failed", messages))
    cm.compaction_circuit_open = True
    cm.circuit_breaker_failures = 3
    printed: list[str] = []
    import agentao.cli.commands.compact as mod
    # Patched through monkeypatch, not by assignment: ``console`` is a shared
    # module-level singleton, so an unrestored stub here silently rewrites
    # every other test's console for the rest of the session.
    monkeypatch.setattr(
        mod, "console", SimpleNamespace(print=lambda text="", **kw: printed.append(str(text))),
    )

    handle_compact_command(cli, "")

    assert any("circuit breaker is still open" in line for line in printed), printed
