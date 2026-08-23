"""An open compaction circuit breaker must stop announcing compactions.

Once ``compress_messages`` has failed ``CIRCUIT_BREAKER_LIMIT`` times in a row
it returns history unchanged. Before this, ``_maybe_full_compress`` still ran
its whole preamble on every iteration — dispatching ``PreCompact`` (which forks
a hook subprocess per matching rule) and emitting ``CONTEXT_COMPRESSED`` with
``pre_msgs == post_msgs`` — for a compaction that could no longer happen.

Pairs with the split-point fix in ``test_context_manager.py``: that one stops
the breaker from being reached spuriously, this one bounds the cost once it is.
"""

from __future__ import annotations

from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import (
    make_runner_with_rules,
    write_capture_script,
)


def _arm(tmp_path, monkeypatch, *, circuit_open: bool):
    script, capture = write_capture_script(tmp_path)
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command=f"sh '{script}'",
        plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[rule])
    agent = runner._agent
    cm = agent.context_manager

    monkeypatch.setattr(cm, "needs_compression", lambda messages, tokens=None: True)
    # compress_messages is the no-op an open breaker produces.
    monkeypatch.setattr(cm, "compress_messages", lambda messages, is_auto=True: messages)
    if circuit_open:
        cm._consecutive_compact_failures = cm.CIRCUIT_BREAKER_LIMIT

    # ``run_turn`` snapshots this at turn start (``runtime/turn.py:111``);
    # ``_maybe_full_compress`` reads it directly because it only ever runs
    # inside a turn. Mirror that here since we invoke the mixin standalone.
    agent._last_session_summary_id = None
    agent.messages = [
        {"role": "user", "content": "earlier turn"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    messages_with_system = [{"role": "system", "content": "sys"}] + agent.messages
    result = runner._maybe_full_compress(messages_with_system, "sys")
    kinds = [getattr(e.type, "value", e.type) for e in transport.events]
    return result, messages_with_system, kinds, capture


def test_open_circuit_skips_precompact_dispatch_and_event(tmp_path, monkeypatch):
    result, sent, kinds, capture = _arm(tmp_path, monkeypatch, circuit_open=True)

    assert result[0] is sent, "history must be handed back untouched"
    assert not capture.exists(), "PreCompact hook subprocess was forked anyway"
    assert "plugin_hook_fired" not in kinds
    assert "context_compressed" not in kinds


def test_closed_circuit_still_dispatches(tmp_path, monkeypatch):
    """The guard must key off the breaker, not disable the path outright."""
    _result, _sent, kinds, capture = _arm(tmp_path, monkeypatch, circuit_open=False)

    assert capture.exists(), "PreCompact should still fire while the breaker is closed"
    assert "context_compressed" in kinds
